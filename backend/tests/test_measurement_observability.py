"""Noticing that the cycle has stopped turning.

Every failure guarded here is silent by nature. A cron that stops firing raises
nothing. A stage that fails inside a run that keeps firing looks like a healthy
schedule. An action that could be priced and is not produces no error at all —
just a question nobody answers, on a screen nobody has a reason to revisit.

So the absence of work is treated as a condition, and each stage is asked about
on its own: two of three succeeding says nothing about the third.
"""
import base64
import datetime as dt
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import actions_engine, alerts, scheduler
from app.db import models
from app.db.models import Base

PRODUCT = "gid://shopify/Product/1"
DAY = dt.timedelta(days=1)


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS",
                       json.dumps({"v1": base64.b64encode(b"k" * 32).decode()}))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def store(db):
    user = models.User(email=f"{uuid.uuid4().hex[:8]}@example.test")
    db.add(user); db.flush()
    row = models.Store(user_id=user.id, marketplace="US")
    db.add(row); db.commit()
    return row


def _ran(db, job, *, hours_ago, status="completed"):
    finished = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
    db.add(models.SchedulerRun(
        job=job, run_key=f"{job}-{hours_ago}", status=status,
        started_at=finished, finished_at=finished,
        error_code="RuntimeError" if status == "failed" else None))
    db.commit()


def _codes(db, store):
    return {c.name for c in alerts.evaluate(db, store_id=store.id)}


def _applied(db, store, *, applied_at, channel=None, measured_at=None):
    row = models.OperatorAction(
        store_id=store.id, module="commerce", action_type="RESTOCK_PRODUCT",
        target="Snowboard", status=actions_engine.ActionStatus.APPLIED.value,
        evidence_mode="unverified",
        baseline={"on_hand": 4, "product_id": PRODUCT, "verified_on_hand": 30,
                  "days": 10, "revenue": 400.0},
        currency="USD", measurement_days=14, applied_by="human",
        applied_at=applied_at, measured_at=measured_at)
    db.add(row); db.commit()
    return row


def _channel(db, store, *, synced_at=None, synced_from=None):
    channel = models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id=f"{uuid.uuid4().hex[:6]}.myshopify.com",
        display_name="shop", status="connected", currency="USD",
        settings={"synced_from": synced_from.isoformat()} if synced_from else {},
        synced_at=synced_at, connected_at=dt.datetime.now(dt.timezone.utc))
    db.add(channel); db.commit()
    return channel


# --- each stage is asked about on its own -----------------------------------

def test_a_stage_that_stopped_is_named_rather_than_lumped_in(db, store):
    """Two of three succeeding is exactly when this is easiest to miss."""
    _ran(db, scheduler.TRIAL_WARNINGS, hours_ago=1)
    _ran(db, scheduler.SHOPIFY_SYNC, hours_ago=1)
    _ran(db, scheduler.MEASUREMENT,
         hours_ago=scheduler.stale_after_hours(scheduler.MEASUREMENT) + 5)

    stopped = [c for c in alerts.evaluate(db, store_id=store.id)
               if c.name == "scheduler_not_running"]
    assert len(stopped) == 1
    assert stopped[0].context["job"] == scheduler.MEASUREMENT
    assert "measurement" in stopped[0].detail


def test_all_three_running_raises_nothing(db, store):
    for job in scheduler.JOBS:
        _ran(db, job, hours_ago=1)
    assert "scheduler_not_running" not in _codes(db, store)


def test_a_deployment_that_has_never_run_is_not_reported_as_stopped(db, store):
    """A first day is not a failure, and reporting it as one teaches somebody to
    ignore the alert that matters."""
    assert "scheduler_not_running" not in _codes(db, store)


def test_a_stage_that_fires_and_fails_is_reported_before_it_goes_stale(db, store):
    """Freshness alone would stay quiet for hours: a failed run still leaves a
    row, so `hours_since_success` climbs silently until it crosses the line."""
    _ran(db, scheduler.SHOPIFY_SYNC, hours_ago=1, status="failed")
    conditions = [c for c in alerts.evaluate(db, store_id=store.id)
                  if c.name == "scheduler_failed"]
    assert len(conditions) == 1
    assert conditions[0].context["job"] == scheduler.SHOPIFY_SYNC
    assert "RuntimeError" in conditions[0].detail


def test_a_failure_followed_by_a_success_is_not_still_reported(db, store):
    _ran(db, scheduler.SHOPIFY_SYNC, hours_ago=3, status="failed")
    _ran(db, scheduler.SHOPIFY_SYNC, hours_ago=1)
    assert "scheduler_failed" not in _codes(db, store)


# --- the action nobody priced -----------------------------------------------

def test_a_closed_window_nobody_priced_is_critical(db, store):
    """The change was made, the fortnight passed, and the question it was all
    for has no answer."""
    now = dt.datetime.now(dt.timezone.utc)
    _channel(db, store, synced_at=now, synced_from=(now - dt.timedelta(days=30)).date())
    _applied(db, store, applied_at=now - dt.timedelta(days=20))

    conditions = [c for c in alerts.evaluate(db, store_id=store.id)
                  if c.name == "measurement_overdue"]
    assert len(conditions) == 1 and conditions[0].severity == alerts.CRITICAL


def test_a_window_that_only_just_closed_is_left_alone(db, store):
    """It closes, and tonight's run prices it. Alerting in between reports the
    schedule working as if it were broken."""
    now = dt.datetime.now(dt.timezone.utc)
    _channel(db, store, synced_at=now, synced_from=(now - dt.timedelta(days=30)).date())
    _applied(db, store, applied_at=now - dt.timedelta(days=15))
    assert "measurement_overdue" not in _codes(db, store)


def test_an_open_window_is_not_an_alert(db, store):
    now = dt.datetime.now(dt.timezone.utc)
    _channel(db, store, synced_at=now, synced_from=(now - dt.timedelta(days=30)).date())
    _applied(db, store, applied_at=now - dt.timedelta(days=3))
    assert _codes(db, store) & {"measurement_overdue", "measurement_waiting_on_data"} == set()


def test_missing_days_are_reported_as_a_sync_fault_not_a_measurement_one(db, store):
    """Opposite responses: one means the job is not running, the other means the
    job is running and has nothing to work with."""
    now = dt.datetime.now(dt.timezone.utc)
    _channel(db, store, synced_at=None)
    _applied(db, store, applied_at=now - dt.timedelta(days=20))

    codes = _codes(db, store)
    assert "measurement_waiting_on_data" in codes
    assert "measurement_overdue" not in codes


def test_a_priced_action_stops_being_reported(db, store):
    now = dt.datetime.now(dt.timezone.utc)
    _channel(db, store, synced_at=now, synced_from=(now - dt.timedelta(days=30)).date())
    _applied(db, store, applied_at=now - dt.timedelta(days=20), measured_at=now)
    assert "measurement_overdue" not in _codes(db, store)


def test_another_tenants_stuck_action_is_not_reported_here(db, store):
    now = dt.datetime.now(dt.timezone.utc)
    other = models.User(email=f"{uuid.uuid4().hex[:8]}@example.test")
    db.add(other); db.flush()
    theirs = models.Store(user_id=other.id, marketplace="US")
    db.add(theirs); db.commit()
    _channel(db, theirs, synced_at=now, synced_from=(now - dt.timedelta(days=30)).date())
    _applied(db, theirs, applied_at=now - dt.timedelta(days=20))

    assert "measurement_overdue" not in _codes(db, store)


# --- the gate in front of charging anybody ----------------------------------

def test_the_pilot_gate_blocks_a_paid_launch_until_every_stage_has_fired(db, monkeypatch):
    from app import pilot_gate

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: db)
    _ran(db, scheduler.TRIAL_WARNINGS, hours_ago=1)

    checks = {check.name: check for check in pilot_gate._scheduler_checks()}
    assert checks["Trial warnings have run"].ok is True
    assert checks["The daily Shopify read has run"].ok is False
    assert checks["The measurement run has fired"].ok is False
    assert all(check.severity == "blocker" for check in checks.values())


def test_the_gate_passes_once_all_three_have_fired(db, monkeypatch):
    from app import pilot_gate

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: db)
    for job in scheduler.JOBS:
        _ran(db, job, hours_ago=1)
    assert all(check.ok for check in pilot_gate._scheduler_checks())
