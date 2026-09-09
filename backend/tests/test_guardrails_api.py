"""API tests for the guardrails and the undo path."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.models import Base
from app.db.session import get_session
from app.main import app

SMALL_BASELINE = {"days": 14, "spend": 14.0, "revenue": 2.0}     # 1.00/day
BIG_BASELINE = {"days": 14, "spend": 2800.0, "revenue": 100.0}   # 200.00/day


@pytest.fixture
def api():
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


def _propose(client, projected=10.0):
    response = client.post("/api/actions", json={
        "module": "ppc", "action_type": "LOWER_BID", "target": "dog bowl",
        "projected_impact": projected, "currency": "EUR"})
    assert response.status_code == 201
    return response.json()["id"]


def test_defaults_are_served_before_anything_is_configured(api):
    client, _ = api
    policy = client.get("/api/guardrails").json()
    assert policy["enabled"] is True
    assert policy["max_actions_per_day"] == 20
    assert policy["remaining_today"] == 20
    assert policy["applied_today"] == 0


def test_the_kill_switch_stops_the_operator_dead(api):
    client, _ = api
    assert client.put("/api/guardrails", json={"enabled": False}).json()["enabled"] is False
    action = _propose(client)
    response = client.post(f"/api/actions/{action}/applied", json={"baseline": SMALL_BASELINE})
    assert response.status_code == 403
    assert response.json()["detail"]["limits_hit"] == ["enabled"]
    assert "switched off" in response.json()["detail"]["reason"]


def test_the_kill_switch_stops_a_human_too(api):
    client, _ = api
    client.put("/api/guardrails", json={"enabled": False})
    action = _propose(client)
    response = client.post(f"/api/actions/{action}/applied", json={"baseline": SMALL_BASELINE})
    assert response.status_code == 403


def test_an_oversized_change_is_refused(api):
    client, _ = api
    action = _propose(client)
    response = client.post(f"/api/actions/{action}/applied", json={
        "baseline": SMALL_BASELINE, "change_pct": -0.55})
    assert response.status_code == 403
    assert response.json()["detail"]["limits_hit"] == ["max_change_pct"]


def test_a_caller_cannot_declare_itself_the_operator(api):
    """The bypass this closes: choosing your own guardrail limits from the body.

    `applied_by` used to come from the request, so a caller could send
    "operator" or "human" and pick which limits applied to it. The server now
    decides, and this route is by definition attended.
    """
    client, _ = api
    action = _propose(client)
    response = client.post(f"/api/actions/{action}/applied", json={
        "baseline": BIG_BASELINE, "applied_by": "operator"})   # ignored
    assert response.status_code == 200
    assert response.json()["applied_by"] == "human"


def test_the_owner_is_not_rationed_by_the_operators_limits(api):
    """The unattended budget bounds the Operator working alone, not the owner.

    A person pressing the button is attended by definition, so a big spender and
    a spent daily budget both go through. The same limits are enforced where they
    belong, on the worker — see test_ads_scan.
    """
    client, _ = api
    client.put("/api/guardrails", json={"max_actions_per_day": 1})
    for baseline in (BIG_BASELINE, SMALL_BASELINE, SMALL_BASELINE):
        action = _propose(client)
        response = client.post(f"/api/actions/{action}/applied", json={"baseline": baseline})
        assert response.status_code == 200, response.text

    # None of that spent the Operator's own budget: it applied nothing itself.
    assert client.get("/api/guardrails").json()["remaining_today"] == 1


def test_a_refusal_is_audited(api):
    client, factory = api
    client.put("/api/guardrails", json={"enabled": False})
    action = _propose(client)
    client.post(f"/api/actions/{action}/applied", json={"baseline": SMALL_BASELINE})
    db = factory()
    actions = [row.action for row in db.scalars(select(models.AuditEvent))]
    assert "guardrails.refused" in actions
    assert "guardrails.updated" in actions


def test_undo_restores_the_recorded_previous_value(api):
    client, factory = api
    action = _propose(client)
    client.post(f"/api/actions/{action}/applied", json={
        "baseline": SMALL_BASELINE, "revert_to": {"bid": 0.75}})
    response = client.post(f"/api/actions/{action}/revert", json={"note": "sales dropped"})
    assert response.status_code == 200
    assert response.json()["status"] == "reverted"
    assert response.json()["revert_to"] == {"bid": 0.75}

    db = factory()
    event = db.scalar(select(models.AuditEvent).where(
        models.AuditEvent.action == "action.reverted"))
    assert event.after == {"restore_to": {"bid": 0.75}}


def test_undo_is_refused_when_no_previous_value_was_recorded(api):
    client, _ = api
    action = _propose(client)
    client.post(f"/api/actions/{action}/applied", json={"baseline": SMALL_BASELINE})
    response = client.post(f"/api/actions/{action}/revert", json={})
    assert response.status_code == 409
    assert "cannot be undone automatically" in response.json()["detail"]


def test_an_action_that_never_took_effect_cannot_be_undone(api):
    client, _ = api
    action = _propose(client)
    assert client.post(f"/api/actions/{action}/revert", json={}).status_code == 409


def test_a_reverted_action_can_be_applied_again(api):
    client, _ = api
    action = _propose(client)
    client.post(f"/api/actions/{action}/applied", json={
        "baseline": SMALL_BASELINE, "revert_to": {"bid": 0.75}})
    client.post(f"/api/actions/{action}/revert", json={})
    again = client.post(f"/api/actions/{action}/applied", json={
        "baseline": SMALL_BASELINE, "revert_to": {"bid": 0.75}})
    assert again.status_code == 200
