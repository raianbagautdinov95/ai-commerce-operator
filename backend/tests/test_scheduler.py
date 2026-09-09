"""
Tests for the daily trial warning and the lock that keeps it single.

Nothing here reaches Resend, Stripe or the working database: every test builds
its own SQLite database, and the queue is replaced at its boundary so a "queued"
job is an assertion rather than a side effect.

The failures being guarded are the quiet ones. Two instances firing at once send
two warnings about somebody's money. A cron that stops firing produces no errors
at all, and the first sign is a customer whose trial ended without notice — which
is why the absence of a run is itself a condition rather than a gap in a log.
"""
import base64
import datetime as dt
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import notifications, scheduler
from app.db import models
from app.db.models import Base


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS",
                       json.dumps({"v1": base64.b64encode(b"k" * 32).decode()}))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.test")


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def queued(monkeypatch):
    """Every enqueue lands here instead of in Redis."""
    jobs = []
    from app import tasks

    monkeypatch.setattr("app.queueing.enqueue_notification",
                        lambda **kw: jobs.append(kw) or "job-1")
    return jobs


def _trial(db, *, days_left: float, status="trialing", email="a@example.test"):
    user = models.User(email=f"{uuid.uuid4().hex[:8]}-{email}")
    db.add(user); db.flush()
    store = models.Store(user_id=user.id, marketplace="US")
    db.add(store); db.flush()
    db.add(models.Subscription(
        store_id=store.id, plan="operator", status=status,
        trial_ends_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days_left)))
    db.commit()
    return store


def _run(db, monkeypatch, jobs):
    """Run the job against this test's database."""
    from app import tasks

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    return tasks.warn_about_ending_trials()


# --- who gets warned --------------------------------------------------------

def test_a_trial_ending_soon_is_warned(db, monkeypatch, queued):
    _trial(db, days_left=2)
    result = _run(db, monkeypatch, queued)
    assert result["queued"] == 1
    assert len(queued) == 1
    assert queued[0]["kind"] == notifications.TRIAL_ENDING


def test_a_trial_ending_much_later_is_left_alone(db, monkeypatch, queued):
    _trial(db, days_left=20)
    assert _run(db, monkeypatch, queued)["queued"] == 0
    assert queued == []


@pytest.mark.parametrize("status", ["active", "canceled", "trial_expired", "past_due"])
def test_only_a_running_trial_is_warned(db, monkeypatch, queued, status):
    """Warning a paying customer that their trial ends is a small thing that
    makes a product look like it is not paying attention."""
    _trial(db, days_left=2, status=status)
    assert _run(db, monkeypatch, queued)["queued"] == 0


def test_the_same_day_twice_warns_once(db, monkeypatch, queued):
    """The receipt is keyed on the trial's end date, which does not move, so a
    second firing the same day finds it and queues nothing."""
    _trial(db, days_left=2)
    _run(db, monkeypatch, queued)
    second = _run(db, monkeypatch, queued)
    assert second["queued"] == 0 and second["already_warned"] == 1
    assert len(queued) == 1


def test_one_tenant_is_not_warned_about_another(db, monkeypatch, queued):
    mine = _trial(db, days_left=2)
    theirs = _trial(db, days_left=2)
    _run(db, monkeypatch, queued)
    assert {job["tenant_id"] for job in queued} == {str(mine.id), str(theirs.id)}
    receipts = list(db.scalars(select(models.EmailReceipt)))
    assert {r.store_id for r in receipts} == {mine.id, theirs.id}


def test_the_scheduler_does_not_touch_the_subscription(db, monkeypatch, queued):
    """It warns and nothing else. A scheduler outage must not silently extend
    everybody's trial, and a scheduler bug must not end one early."""
    store = _trial(db, days_left=2)
    before = db.scalar(select(models.Subscription))
    ends_before, status_before = before.trial_ends_at, before.status

    _run(db, monkeypatch, queued)

    after = db.scalar(select(models.Subscription))
    assert after.status == status_before
    assert after.trial_ends_at == ends_before


# --- the lock ---------------------------------------------------------------

def test_only_one_run_claims_a_day(db):
    """Railway usually runs one instance. Usually is not a property to build a
    mailing on, and a retry is the same problem."""
    key = scheduler.run_key_for()
    first = scheduler.claim(db, job=scheduler.TRIAL_WARNINGS, run_key=key)
    db.commit()
    assert first is not None
    assert scheduler.claim(db, job=scheduler.TRIAL_WARNINGS, run_key=key) is None


def test_a_run_abandoned_by_a_dead_process_is_reclaimable(db):
    """Without this, one crash stops the job for ever and the only symptom is
    silence."""
    key = scheduler.run_key_for()
    run = scheduler.claim(db, job=scheduler.TRIAL_WARNINGS, run_key=key)
    run.started_at = (dt.datetime.now(dt.timezone.utc)
                      - dt.timedelta(hours=scheduler.STALE_RUN_HOURS + 1))
    db.add(run); db.commit()
    assert scheduler.claim(db, job=scheduler.TRIAL_WARNINGS, run_key=key) is not None


def test_a_run_in_flight_is_not_stolen(db):
    key = scheduler.run_key_for()
    scheduler.claim(db, job=scheduler.TRIAL_WARNINGS, run_key=key)
    db.commit()
    assert scheduler.claim(db, job=scheduler.TRIAL_WARNINGS, run_key=key) is None


def test_a_completed_run_is_not_repeated(db):
    key = scheduler.run_key_for()
    run = scheduler.claim(db, job=scheduler.TRIAL_WARNINGS, run_key=key)
    db.commit()
    scheduler.finish(db, run, found=3, queued=2, skipped=1)
    assert scheduler.claim(db, job=scheduler.TRIAL_WARNINGS, run_key=key) is None


def test_tomorrow_is_a_different_run(db):
    today = scheduler.run_key_for()
    tomorrow = scheduler.run_key_for(
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1))
    run = scheduler.claim(db, job=scheduler.TRIAL_WARNINGS, run_key=today)
    db.commit(); scheduler.finish(db, run)
    assert scheduler.claim(db, job=scheduler.TRIAL_WARNINGS, run_key=tomorrow) is not None


def test_a_failed_run_is_recorded_rather_than_hidden(db):
    run = scheduler.claim(db, job=scheduler.TRIAL_WARNINGS,
                          run_key=scheduler.run_key_for())
    db.commit()
    scheduler.finish(db, run, error_code="RuntimeError")
    row = db.scalar(select(models.SchedulerRun))
    assert row.status == "failed" and row.error_code == "RuntimeError"


def test_the_run_record_counts_and_never_names(db):
    """Counts, not identities: how many trials, not whose."""
    run = scheduler.claim(db, job=scheduler.TRIAL_WARNINGS,
                          run_key=scheduler.run_key_for())
    db.commit()
    scheduler.finish(db, run, found=5, queued=3, skipped=2)
    row = db.scalar(select(models.SchedulerRun))
    assert (row.found, row.queued, row.skipped) == (5, 3, 2)
    assert not any(column.name in {"store_id", "email", "recipient"}
                   for column in models.SchedulerRun.__table__.columns)


# --- freshness --------------------------------------------------------------

def test_a_deployment_that_has_never_run_is_not_called_stale(db):
    """A first day is not a failure, and reporting it as one teaches somebody to
    ignore the alert that matters."""
    assert scheduler.hours_since_success(db, job=scheduler.TRIAL_WARNINGS) is None
    assert scheduler.is_stale(db, job=scheduler.TRIAL_WARNINGS) is False


def test_a_recent_run_is_fresh(db):
    run = scheduler.claim(db, job=scheduler.TRIAL_WARNINGS,
                          run_key=scheduler.run_key_for())
    db.commit(); scheduler.finish(db, run)
    assert scheduler.is_stale(db, job=scheduler.TRIAL_WARNINGS) is False


def test_a_scheduler_that_stopped_firing_is_stale(db):
    """The failure with no error attached."""
    run = scheduler.claim(db, job=scheduler.TRIAL_WARNINGS, run_key="2020-01-01")
    db.commit(); scheduler.finish(db, run)
    run.finished_at = (dt.datetime.now(dt.timezone.utc)
                       - dt.timedelta(hours=scheduler.STALE_AFTER_HOURS + 2))
    db.add(run); db.commit()
    assert scheduler.is_stale(db, job=scheduler.TRIAL_WARNINGS) is True


def test_a_failed_run_does_not_count_as_a_success(db):
    run = scheduler.claim(db, job=scheduler.TRIAL_WARNINGS, run_key="2020-01-02")
    db.commit()
    scheduler.finish(db, run, error_code="RuntimeError")
    assert scheduler.last_success(db, job=scheduler.TRIAL_WARNINGS) is None


# --- failures on either side of the queue -----------------------------------

def test_a_store_with_no_address_does_not_stop_the_others(db, monkeypatch, queued):
    """One tenant must not take the batch down with it."""
    broken = _trial(db, days_left=2)
    user = db.get(models.User, broken.user_id)
    user.email = ""
    db.add(user); db.commit()
    _trial(db, days_left=2)

    result = _run(db, monkeypatch, queued)
    # Both are reserved and queued; the missing address is discovered by the
    # worker and recorded on the receipt rather than here.
    assert result["queued"] == 2


def test_a_failure_to_queue_leaves_the_reservation_recoverable(db, monkeypatch):
    """Nothing was sent, so the warning must still be sendable tomorrow."""
    from app import tasks

    _trial(db, days_left=2)

    def explode(**_kw):
        raise RuntimeError("redis is down")

    monkeypatch.setattr("app.queueing.enqueue_notification", explode)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    result = tasks.warn_about_ending_trials()
    assert result["queued"] == 0

    receipts = list(db.scalars(select(models.EmailReceipt)))
    assert all(r.status == "reserved" for r in receipts)
