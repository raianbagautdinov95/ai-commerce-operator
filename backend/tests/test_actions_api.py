"""API tests for the operator action ledger and the payroll it produces."""
import sys, os, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import crud, models
from app.db.models import Base
from app.db.session import get_session
from app.main import app

BASELINE = {"days": 14, "spend": 120.50, "revenue": 12.00}
OUTCOME = {"days": 14, "spend": 8.00, "revenue": 6.00}


@pytest.fixture
def api():
    """A client on a private in-memory DB.

    The previous override is restored rather than cleared: other test modules
    install their own at import time, and clearing would silently break them.
    """
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    previous = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = override
    try:
        yield TestClient(app), factory
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous


def _propose(client, **overrides):
    body = {"module": "ppc", "action_type": "NEGATE_KEYWORD",
            "target": "cheap dog bowl", "projected_impact": 112.50,
            "measurement_days": 14, "currency": "EUR"}
    body.update(overrides)
    response = client.post("/api/actions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _set_evidence(factory, action_id, mode):
    db = factory()
    row = db.get(models.OperatorAction, uuid.UUID(action_id))
    row.evidence_mode = mode
    row.source_type = "amazon_ads_report" if mode == "real" else None
    row.source_id = "report-123" if mode == "real" else None
    db.add(row); db.commit(); db.close()


def test_full_lifecycle_prices_the_action_and_pays_the_payroll(api):
    client, factory = api
    action = _propose(client)
    _set_evidence(factory, action["id"], "real")
    assert action["status"] == "proposed"
    assert action["impact"] is None

    applied = client.post(f"/api/actions/{action['id']}/applied",
                          json={"baseline": BASELINE, "applied_by": "human",
                                "revert_to": {"bid": 0.75}})
    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    assert applied.json()["revert_to"] == {"bid": 0.75}

    measured = client.post(f"/api/actions/{action['id']}/measure",
                           json={"outcome": OUTCOME})
    assert measured.status_code == 200
    impact = measured.json()["impact"]
    assert impact["cost_avoided"] == 112.50
    assert impact["revenue_gained"] == -6.00
    assert impact["net"] == 106.50           # lost revenue netted off
    assert impact["provisional"] is False

    payroll = client.get("/api/dashboard/payroll?days=30").json()
    assert payroll["settled_impact"] == 106.50
    assert payroll["currency"] == "EUR"
    # A trialing tenant is not charged, so impact stands alone.
    assert payroll["operator_cost"] == 0.0
    assert payroll["paid_for_itself"] is True
    assert payroll["awaiting_measurement"] == 0


def test_provisional_impact_is_reported_but_not_counted_as_proven(api):
    client, factory = api
    action = _propose(client)
    _set_evidence(factory, action["id"], "real")
    client.post(f"/api/actions/{action['id']}/applied", json={"baseline": BASELINE})
    measured = client.post(f"/api/actions/{action['id']}/measure",
                           json={"outcome": {"days": 5, "spend": 2.0, "revenue": 1.0}})
    assert measured.json()["impact"]["provisional"] is True

    payroll = client.get("/api/dashboard/payroll?days=30").json()
    assert payroll["settled_impact"] == 0.0
    assert payroll["provisional_impact"] > 0
    assert payroll["paid_for_itself"] is False


def test_demo_and_unverified_actions_never_inflate_real_payroll(api):
    client, factory = api
    for mode in ("demo", "unverified"):
        action = _propose(client)
        _set_evidence(factory, action["id"], mode)
        client.post(f"/api/actions/{action['id']}/applied", json={"baseline": BASELINE})
        client.post(f"/api/actions/{action['id']}/measure", json={"outcome": OUTCOME})

    payroll = client.get("/api/dashboard/payroll?days=30").json()
    assert payroll["settled_impact"] == 0.0
    assert payroll["paid_for_itself"] is False
    assert payroll["evidence_mode"] == "real"
    assert payroll["excluded_demo"] == 1
    assert payroll["excluded_unverified"] == 1


def test_public_action_creation_cannot_claim_verified_evidence(api):
    client, _ = api
    action = _propose(client, evidence_mode="real", source_type="shopify_order",
                      source_id="order-1")
    assert action["evidence_mode"] == "unverified"
    assert action["source_type"] is None
    assert action["source_id"] is None


def test_an_unmeasurably_short_window_is_refused(api):
    client, _ = api
    action = _propose(client)
    client.post(f"/api/actions/{action['id']}/applied", json={"baseline": BASELINE})
    response = client.post(f"/api/actions/{action['id']}/measure",
                           json={"outcome": {"days": 1, "spend": 0.0, "revenue": 0.0}})
    assert response.status_code == 422
    assert "proves nothing" in response.json()["detail"]


def test_an_unapplied_action_cannot_be_measured(api):
    client, _ = api
    action = _propose(client)
    response = client.post(f"/api/actions/{action['id']}/measure",
                           json={"outcome": OUTCOME})
    assert response.status_code == 409


def test_applying_twice_is_rejected_so_the_baseline_cannot_be_rewritten(api):
    client, _ = api
    action = _propose(client)
    client.post(f"/api/actions/{action['id']}/applied", json={"baseline": BASELINE})
    second = client.post(f"/api/actions/{action['id']}/applied",
                         json={"baseline": {"days": 14, "spend": 0.0, "revenue": 999.0}})
    assert second.status_code == 409
    assert client.get("/api/actions").json()["actions"][0]["baseline"]["spend"] == 120.50


def test_another_tenants_action_is_not_reachable(api):
    client, factory = api
    db = factory()
    other_user = models.User(email="other@local")
    db.add(other_user); db.commit()
    other_store = models.Store(user_id=other_user.id, marketplace="US")
    db.add(other_store); db.commit()
    foreign = models.OperatorAction(
        store_id=other_store.id, module="ppc", action_type="NEGATE_KEYWORD",
        target="not yours", status="proposed",
    )
    db.add(foreign); db.commit()
    foreign_id = str(foreign.id)
    db.close()

    assert client.post(f"/api/actions/{foreign_id}/applied",
                       json={"baseline": BASELINE}).status_code == 404
    assert client.post(f"/api/actions/{foreign_id}/measure",
                       json={"outcome": OUTCOME}).status_code == 404
    targets = [a["target"] for a in client.get("/api/actions").json()["actions"]]
    assert "not yours" not in targets


def test_a_malformed_action_id_is_a_clean_404(api):
    client, _ = api
    assert client.post("/api/actions/not-a-uuid/measure",
                       json={"outcome": OUTCOME}).status_code == 404


def test_every_step_of_the_ledger_is_audited(api):
    client, factory = api
    action = _propose(client)
    client.post(f"/api/actions/{action['id']}/applied", json={"baseline": BASELINE})
    client.post(f"/api/actions/{action['id']}/measure", json={"outcome": OUTCOME})
    db = factory()
    actions = [row.action for row in db.scalars(select(models.AuditEvent))]
    assert {"action.proposed", "action.applied", "action.measured"} <= set(actions)


# --- declining a proposal ---------------------------------------------------
#
# The queue used to have one exit. A proposal could be applied or it could sit
# there, so the only way to answer "no" was to ignore it — and a list nobody can
# empty stops being read, which costs more than any single bad suggestion.


def test_a_proposal_can_be_declined_and_says_why(api):
    client, _ = api
    action = _propose(client)

    response = client.post(f"/api/actions/{action['id']}/dismiss",
                           json={"note": "We bid on that term on purpose."})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "dismissed"
    assert body["note"] == "We bid on that term on purpose."


def test_declining_without_a_reason_is_allowed(api):
    """Demanding a reason is how a queue stays full instead of getting answered."""
    client, _ = api
    action = _propose(client)
    response = client.post(f"/api/actions/{action['id']}/dismiss", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "dismissed"


def test_a_declined_proposal_never_reaches_the_payroll(api, ):
    client, factory = api
    action = _propose(client)
    _set_evidence(factory, action["id"], "real")
    client.post(f"/api/actions/{action['id']}/dismiss", json={})

    payroll = client.get("/api/dashboard/payroll").json()
    assert payroll["settled_impact"] == 0
    assert payroll["provisional_impact"] == 0
    # It was never applied, so there is nothing waiting to be measured either.
    assert payroll["awaiting_measurement"] == 0


def test_an_action_already_in_effect_cannot_be_declined(api):
    client, _ = api
    action = _propose(client)
    applied = client.post(f"/api/actions/{action['id']}/applied",
                          json={"baseline": BASELINE, "applied_by": "human",
                                "revert_to": {"bid": 0.75}})
    assert applied.status_code == 200

    refused = client.post(f"/api/actions/{action['id']}/dismiss", json={})
    assert refused.status_code == 409
    assert "nobody has acted" in refused.json()["detail"]


def test_declining_twice_is_refused_rather_than_silently_repeated(api):
    client, _ = api
    action = _propose(client)
    assert client.post(f"/api/actions/{action['id']}/dismiss", json={}).status_code == 200
    assert client.post(f"/api/actions/{action['id']}/dismiss", json={}).status_code == 409


def test_declining_is_on_the_record(api):
    client, factory = api
    action = _propose(client)
    client.post(f"/api/actions/{action['id']}/dismiss", json={"note": "not for us"})

    db = factory()
    actions = [row.action for row in db.scalars(select(models.AuditEvent))]
    db.close()
    assert "action.dismissed" in actions


def test_a_declined_proposal_is_still_listed(api):
    """Declining is an answer, not a delete: a pattern of them is worth seeing."""
    client, _ = api
    action = _propose(client)
    client.post(f"/api/actions/{action['id']}/dismiss", json={})

    dismissed = client.get("/api/actions", params={"status": "dismissed"}).json()["actions"]
    assert [row["id"] for row in dismissed] == [action["id"]]
    assert client.get("/api/actions", params={"status": "proposed"}).json()["actions"] == []


def test_another_tenants_proposal_cannot_be_declined(api):
    client, factory = api
    db = factory()
    other_user = models.User(email="stranger@local")
    db.add(other_user); db.commit()
    other_store = models.Store(user_id=other_user.id, marketplace="US")
    db.add(other_store); db.commit()
    theirs = models.OperatorAction(
        store_id=other_store.id, module="ppc", action_type="NEGATE_KEYWORD",
        target="their keyword", status="proposed")
    db.add(theirs); db.commit()
    action_id = str(theirs.id)
    db.close()

    assert client.post(f"/api/actions/{action_id}/dismiss", json={}).status_code == 404
