"""Running something once a day, and knowing whether it ran.

The mechanism is a Railway cron service calling this, not a job that reschedules
itself in Redis. Both work until something goes wrong, and then they fail
differently: a self-rescheduling chain lives in a key, and a flushed Redis or a
job that died between firings breaks the chain permanently while everything
still looks healthy. A cron entry lives in configuration, and when it stops
firing the absence is visible here.

    python -m app.trial_warnings          # what the cron service runs
    python -m app.trial_warnings --dry-run

Two firings at once are handled by the database rather than by hoping. Railway
usually runs one instance; "usually" is not a property to build a mailing on, and
a retry or somebody running it by hand is the same problem. The unique key on
(job, run_key) means the second one loses the insert and stops.

Deduplication of the emails themselves stays where it is, in the receipts, as
the last line rather than the only one. Neither layer is trusted alone.
"""
from __future__ import annotations

import datetime as dt
import os
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import models
from .runtime import log

TRIAL_WARNINGS = "trial_warnings"
SHOPIFY_SYNC = "shopify_sync"
MEASUREMENT = "measurement"

#: Every job the daily run performs. Each claims its own key and keeps its own
#: freshness, because they fail for different reasons and one of them stopping
#: must not be hidden by the other two still working.
JOBS = (TRIAL_WARNINGS, SHOPIFY_SYNC, MEASUREMENT)

#: How long a run may be `running` before it is presumed dead and its key may be
#: claimed again. A daily job that takes an hour is already wrong; six is a
#: window nothing legitimate reaches.
STALE_RUN_HOURS = 6

#: Past this with no successful run, somebody should look. A day and a half
#: allows one missed firing without waking anybody, and catches a cron that has
#: genuinely stopped.
STALE_AFTER_HOURS = int(os.getenv("SCHEDULER_STALE_AFTER_HOURS", "36"))

#: Per job, because the cost of a late run is not the same for each. A missed
#: trial warning reaches somebody whose trial has already ended. A missed sync
#: is a day of sales nobody read, which also delays every measurement waiting on
#: it — so it is held to the same standard rather than a looser one. Measurement
#: is the most forgiving: a result priced a day late is still the right result.
STALE_AFTER_HOURS_BY_JOB = {
    TRIAL_WARNINGS: STALE_AFTER_HOURS,
    SHOPIFY_SYNC: int(os.getenv("SYNC_STALE_AFTER_HOURS", str(STALE_AFTER_HOURS))),
    MEASUREMENT: int(os.getenv("MEASUREMENT_STALE_AFTER_HOURS", "48")),
}


def stale_after_hours(job: str) -> int:
    return STALE_AFTER_HOURS_BY_JOB.get(job, STALE_AFTER_HOURS)


@dataclass
class RunOutcome:
    claimed: bool
    found: int = 0
    queued: int = 0
    skipped: int = 0
    reason: str = ""


def run_key_for(now: dt.datetime | None = None) -> str:
    """The day, in UTC. Dates are computed here from the clock — never inferred
    by anything that could answer differently twice."""
    moment = now or dt.datetime.now(dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc).date().isoformat()


def claim(db: Session, *, job: str, run_key: str) -> models.SchedulerRun | None:
    """Take the right to perform this run, or return None because somebody has it.

    A row left `running` by a process that died is reclaimed after
    `STALE_RUN_HOURS`; without that, one crash would stop the job for ever and
    the only symptom would be silence.
    """
    row = models.SchedulerRun(job=job, run_key=run_key, status="running")
    db.add(row)
    try:
        db.flush()
        return row
    except IntegrityError:
        db.rollback()

    existing = db.scalar(select(models.SchedulerRun).where(
        models.SchedulerRun.job == job, models.SchedulerRun.run_key == run_key))
    if existing is None or existing.status != "running":
        return None
    started = existing.started_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=dt.timezone.utc)
    stale = started is None or (
        dt.datetime.now(dt.timezone.utc) - started > dt.timedelta(hours=STALE_RUN_HOURS))
    if not stale:
        return None
    existing.started_at = dt.datetime.now(dt.timezone.utc)
    db.add(existing)
    return existing


def finish(db: Session, run: models.SchedulerRun, *, found: int = 0, queued: int = 0,
           skipped: int = 0, error_code: str | None = None) -> None:
    run.status = "failed" if error_code else "completed"
    run.finished_at = dt.datetime.now(dt.timezone.utc)
    run.found, run.queued, run.skipped = found, queued, skipped
    run.error_code = error_code
    db.add(run)
    db.commit()
    # Counts, never identities: how many trials, not whose.
    log.info("Scheduler %s %s: found=%d queued=%d skipped=%d", run.job, run.status,
             found, queued, skipped)


def last_success(db: Session, *, job: str) -> dt.datetime | None:
    row = db.scalar(select(models.SchedulerRun).where(
        models.SchedulerRun.job == job,
        models.SchedulerRun.status == "completed",
    ).order_by(models.SchedulerRun.finished_at.desc()))
    if row is None or row.finished_at is None:
        return None
    return (row.finished_at if row.finished_at.tzinfo
            else row.finished_at.replace(tzinfo=dt.timezone.utc))


def hours_since_success(db: Session, *, job: str) -> float | None:
    """None when it has never run, which is a different thing from stale and is
    reported as such: a deployment on its first day has not failed at anything."""
    last = last_success(db, job=job)
    if last is None:
        return None
    return (dt.datetime.now(dt.timezone.utc) - last).total_seconds() / 3600.0


def is_stale(db: Session, *, job: str) -> bool:
    hours = hours_since_success(db, job=job)
    return hours is not None and hours > stale_after_hours(job)
