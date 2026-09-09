"""
The loop end to end: an advertising report becomes priced, bounded actions.

This is the test that says whether the Operator is an operator. Nobody presses a
button per keyword: it reads, the engine judges, the guardrails decide what may
happen unattended, and the ledger records every branch.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import base64
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import amazon_ads_api, tasks
from app.db import crud, models
from app.db.models import Base

# Three wasteful terms of very different sizes, plus one that converts fine.
REPORT = [
    {"searchTerm": "cheap dog bowl", "keyword": "dog bowl", "matchType": "BROAD",
     "campaignId": 111, "campaignName": "SP", "keywordId": 9, "adGroupId": 222,
     "impressions": 4100, "clicks": 96, "cost": 12.00, "purchases7d": 0, "sales7d": 0.0},
    {"searchTerm": "free dog bowl", "keyword": "dog bowl", "matchType": "BROAD",
     "campaignId": 111, "campaignName": "SP", "keywordId": 9, "adGroupId": 222,
     "impressions": 900, "clicks": 40, "cost": 8.00, "purchases7d": 0, "sales7d": 0.0},
    {"searchTerm": "luxury dog bowl", "keyword": "dog bowl", "matchType": "BROAD",
     "campaignId": 111, "campaignName": "SP", "keywordId": 9, "adGroupId": 222,
     "impressions": 5000, "clicks": 300, "cost": 900.00, "purchases7d": 0, "sales7d": 0.0},
    {"searchTerm": "stainless dog bowl", "keyword": "dog bowl", "matchType": "PHRASE",
     "campaignId": 111, "campaignName": "SP", "keywordId": 9, "adGroupId": 222,
     "impressions": 2200, "clicks": 60, "cost": 48.00, "purchases7d": 9, "sales7d": 216.0},
]


class FakeAdsClient:
    """An ad account whose negative list only moves when a write really lands."""

    def __init__(self, rows, *, writes_land=True):
        self._rows = rows
        self._writes_land = writes_land
        self.negatives = []
        self.write_calls = 0

    def search_terms(self, days=14, **kwargs):
        return self._rows

    def apply_negative_keyword(self, *, campaign_id, ad_group_id, keyword_text,
                               match_type="NEGATIVE_EXACT"):
        from app.change_verification import verify_presence
        self.write_calls += 1
        before = list(self.negatives)
        if self._writes_land:
            self.negatives.append(keyword_text)
        return verify_presence(expected=keyword_text, before=before,
                               after=list(self.negatives), external_id="neg-1")


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS",
                       json.dumps({"v1": base64.b64encode(b"d" * 32).decode()}))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("PPC_BREAK_EVEN_ACOS", "0.30")

    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "SessionLocal", factory)

    rows = amazon_ads_api.parse_search_term_rows(REPORT)
    monkeypatch.setattr(tasks, "SessionLocal", factory)

    def use(client):
        monkeypatch.setattr("app.amazon_ads_api.client_for_store", lambda *a, **k: client)
        return client

    use(FakeAdsClient(rows))
    factory.rows = rows
    factory.use_client = use
    return factory


def _prepare(factory, policy: dict | None = None):
    db = factory()
    store = crud.get_or_create_dev_store(db)
    if policy is not None:
        db.add(models.GuardrailPolicy(store_id=store.id, **policy))
    record = models.IdempotencyRecord(store_id=store.id, operation="ads.scan",
                                      key="k-1", status="processing")
    db.add(record)
    db.commit()
    ids = (str(store.id), str(store.user_id), str(record.id))
    db.close()
    return ids


def _scan(store_id, actor_id, record_id, days=14):
    return tasks.run_ads_scan(tenant_id=store_id, actor_id=actor_id, region="EU",
                              days=days, profile_id="42",
                              idempotency_record_id=record_id)


ALLOWING_POLICY = {
    "enabled": True, "max_actions_per_day": 20, "max_change_pct": 0.2,
    "auto_apply_below": 25.0, "protected_spend_per_day": 50.0,
}


def test_without_write_access_nothing_is_applied_and_nothing_is_claimed(env):
    """The default. It reads and proposes; it does not pretend to have acted."""
    store_id, actor_id, record_id = _prepare(env, ALLOWING_POLICY)
    result = _scan(store_id, actor_id, record_id)

    assert result["opened"] == 3
    assert result["auto_applied"] == 0
    assert result["unproven"] == 2          # allowed, but nothing was changed
    assert result["needs_approval"] == 1

    db = env()
    rows = list(db.scalars(select(models.OperatorAction)))
    assert {r.status for r in rows} == {"proposed"}
    assert {r.evidence_mode for r in rows} == {"unverified"}
    assert any("no advertising write access" in (r.note or "") for r in rows)


def test_the_scan_opens_actions_and_applies_only_what_it_may(env, monkeypatch):
    monkeypatch.setenv("ADS_WRITE_ENABLED", "true")
    client = env.use_client(FakeAdsClient(env.rows))
    store_id, actor_id, record_id = _prepare(env, ALLOWING_POLICY)
    result = _scan(store_id, actor_id, record_id)

    assert result["terms"] == 4
    assert result["opened"] == 3            # the converting term is not negated
    # The 900.00 term is both above the money ceiling and a big spender.
    assert result["needs_approval"] == 1
    assert result["auto_applied"] == 2
    assert client.write_calls == 2          # only what the guardrails allowed

    db = env()
    rows = list(db.scalars(select(models.OperatorAction).order_by(
        models.OperatorAction.projected_impact.desc())))
    assert [r.target for r in rows] == ["luxury dog bowl", "cheap dog bowl", "free dog bowl"]
    assert rows[0].status == "proposed"     # left for a human
    assert rows[0].evidence_mode == "unverified"
    assert rows[1].status == "applied" and rows[1].applied_by == "operator"
    assert rows[1].evidence_mode == "real"  # read back out of the account
    assert rows[1].outcome["verification"]["verified"] is True
    assert rows[1].revert_to["remove_negative_keyword"] == "cheap dog bowl"
    assert rows[1].baseline == {"days": 14, "spend": 12.00, "revenue": 0.0}


def test_an_api_that_accepts_and_changes_nothing_is_never_counted(env, monkeypatch):
    """A 200 is not evidence. Nothing here may reach the payroll."""
    monkeypatch.setenv("ADS_WRITE_ENABLED", "true")
    client = env.use_client(FakeAdsClient(env.rows, writes_land=False))
    store_id, actor_id, record_id = _prepare(env, ALLOWING_POLICY)
    result = _scan(store_id, actor_id, record_id)

    assert client.write_calls == 2          # it really tried
    assert result["auto_applied"] == 0
    assert result["unproven"] == 2

    db = env()
    rows = list(db.scalars(select(models.OperatorAction)))
    assert {r.status for r in rows} == {"proposed"}
    assert all(r.evidence_mode == "unverified" for r in rows)
    assert any("did not take effect" in (r.note or "") for r in rows)
    audited = [row.action for row in db.scalars(select(models.AuditEvent))]
    assert "action.unproven" in audited


def test_a_switched_off_operator_still_reports_but_changes_nothing(env):
    store_id, actor_id, record_id = _prepare(env, {
        "enabled": False, "max_actions_per_day": 20, "max_change_pct": 0.2,
        "auto_apply_below": 1000.0, "protected_spend_per_day": 10000.0,
    })
    result = _scan(store_id, actor_id, record_id)

    assert result["opened"] == 3
    assert result["auto_applied"] == 0
    assert result["needs_approval"] == 3

    db = env()
    assert {r.status for r in db.scalars(select(models.OperatorAction))} == {"proposed"}


def test_the_daily_budget_is_not_talked_past_within_one_scan(env, monkeypatch):
    """Each proposal is judged separately, counting what this run already applied."""
    monkeypatch.setenv("ADS_WRITE_ENABLED", "true")
    env.use_client(FakeAdsClient(env.rows))
    store_id, actor_id, record_id = _prepare(env, {
        "enabled": True, "max_actions_per_day": 1, "max_change_pct": 0.2,
        "auto_apply_below": 10000.0, "protected_spend_per_day": 10000.0,
    })
    result = _scan(store_id, actor_id, record_id)

    assert result["auto_applied"] == 1
    assert result["needs_approval"] == 2

    db = env()
    applied = [r for r in db.scalars(select(models.OperatorAction)) if r.status == "applied"]
    assert len(applied) == 1
    # The budget spends itself on the biggest waste first.
    assert applied[0].target == "luxury dog bowl"


def test_an_empty_report_is_recorded_rather_than_treated_as_a_failure(env, monkeypatch):
    env.use_client(FakeAdsClient([]))
    store_id, actor_id, record_id = _prepare(env)
    assert _scan(store_id, actor_id, record_id) == {
        "terms": 0, "opened": 0, "auto_applied": 0, "needs_approval": 0, "unproven": 0}


def test_rerunning_the_same_scan_returns_the_first_result(env):
    """Idempotency: a retried job must not open the same actions twice."""
    store_id, actor_id, record_id = _prepare(env)
    first = _scan(store_id, actor_id, record_id)
    second = _scan(store_id, actor_id, record_id)
    assert first == second

    db = env()
    assert db.scalar(select(models.OperatorAction).where(
        models.OperatorAction.target == "cheap dog bowl")) is not None
    assert len(list(db.scalars(select(models.OperatorAction)))) == first["opened"]


def test_every_branch_of_the_scan_is_audited(env, monkeypatch):
    monkeypatch.setenv("ADS_WRITE_ENABLED", "true")
    env.use_client(FakeAdsClient(env.rows))
    store_id, actor_id, record_id = _prepare(env, ALLOWING_POLICY)
    _scan(store_id, actor_id, record_id)

    db = env()
    actions = [row.action for row in db.scalars(select(models.AuditEvent))]
    assert "ads.scan" in actions
    assert "action.applied" in actions        # what it did unattended
    assert "guardrails.refused" in actions    # what it declined to do


def test_a_failing_report_leaves_no_half_written_ledger(env, monkeypatch):
    class Exploding:
        def search_terms(self, days=14, **kwargs):
            raise amazon_ads_api.AdsAPIError("Amazon could not produce the report")

    monkeypatch.setattr("app.amazon_ads_api.client_for_store", lambda *a, **k: Exploding())
    store_id, actor_id, record_id = _prepare(env)
    with pytest.raises(amazon_ads_api.AdsAPIError):
        _scan(store_id, actor_id, record_id)

    db = env()
    assert list(db.scalars(select(models.OperatorAction))) == []
    record = db.get(models.IdempotencyRecord, __import__("uuid").UUID(record_id))
    assert record.status == "failed"
