"""
What deserves a human's attention right now.

Not a metrics dashboard. The conditions here are the ones today's failure
testing showed are both real and silent: each one lets the product keep answering
200 while quietly doing less than it claims. A store whose access was revoked
still shows a screen. A change the Operator could not prove still sits in the
ledger. A queue with no worker accepts jobs forever.

Everything is derived from the database, deterministically, so it can be polled
from anywhere and asserted in tests:

    GET /api/alerts                  for a screen or a monitoring probe
    python -m app.alerts             for cron; exit 1 while anything critical stands

Deliberately not wired to a particular pager. An alert nobody routes is noise,
and which service to route it to is not this module's business — it produces the
conditions, and Sentry, a cron job or a probe decides what to do with them.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import models

CRITICAL = "critical"
WARNING = "warning"

# A delivery or a claim in flight is normal; one in flight for this long is not.
STUCK_AFTER_SECONDS = int(os.getenv("ALERT_STUCK_AFTER_SECONDS", "3600"))
# How far back a rate-style condition looks.
WINDOW_HOURS = int(os.getenv("ALERT_WINDOW_HOURS", "24"))


@dataclass
class Condition:
    name: str
    severity: str
    detail: str
    action: str
    count: int = 0
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _since(hours: int) -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)


def _stuck_before() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=STUCK_AFTER_SECONDS)


def _revoked_channels(db: Session, store_id) -> list[Condition]:
    rows = list(db.scalars(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store_id,
        models.ChannelConnection.status == "disconnected",
    )))
    revoked = [row for row in rows if (row.settings or {}).get("revoked_reason")]
    if not revoked:
        return []
    names = ", ".join(row.external_account_id for row in revoked[:5])
    return [Condition(
        "channel_access_revoked", CRITICAL,
        f"{len(revoked)} channel(s) can no longer be reached: {names}",
        "Ask the merchant to reconnect. Nothing syncs until they do.",
        len(revoked), {"channels": [row.external_account_id for row in revoked[:5]]},
    )]


def _stale_notifications(db: Session, store_id) -> list[Condition]:
    configured = os.getenv("SHOPIFY_WEBHOOK_URI")
    if not configured:
        return []
    rows = list(db.scalars(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store_id,
        models.ChannelConnection.status == "connected",
    )))
    stale = [row for row in rows
             if (row.settings or {}).get("webhook_uri")
             and (row.settings or {}).get("webhook_uri") != configured]
    if not stale:
        return []
    return [Condition(
        "notifications_point_elsewhere", CRITICAL,
        f"{len(stale)} channel(s) are notifying an address this server no longer answers",
        "Re-point them from the channel screen; orders are arriving nowhere.",
        len(stale),
    )]


#: How long a connected store may go without a successful sync before somebody
#: should look. The sync is meant to run daily; two days allows one to fail and
#: be retried without waking anyone.
STALE_SYNC_HOURS = int(os.getenv("ALERT_STALE_SYNC_HOURS", "48"))


def _silent_stores(db: Session, store_id) -> list[Condition]:
    """A connected store whose data stopped arriving.

    This is the failure with no error attached: notifications refused for a
    rotated secret, a worker not consuming, a token revoked in a way nothing
    noticed. Every screen keeps showing the last numbers it had, which look
    exactly like a quiet week.
    """
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=STALE_SYNC_HOURS)
    rows = list(db.scalars(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store_id,
        models.ChannelConnection.status == "connected",
    )))
    silent = []
    for row in rows:
        # A store that has never synced is measured from when it was connected,
        # not treated as overdue on arrival: the first sync has not had time to
        # happen yet, and greeting a new customer with an alert about their own
        # store teaches them to ignore the next one.
        last = row.synced_at or row.connected_at
        if last is None:
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=dt.timezone.utc)
        if last < cutoff:
            silent.append(row)
    if not silent:
        return []
    return [Condition(
        "store_not_synced", WARNING,
        f"{len(silent)} connected channel(s) have not synced for over "
        f"{STALE_SYNC_HOURS}h",
        "Run a sync from the channel screen. If it succeeds, ask why nothing "
        "triggered it; if it fails, the answer is in the worker log.",
        len(silent),
    )]


def _undelivered_notifications(db: Session, store_id) -> list[Condition]:
    """Emails about this customer's money that never reached them.

    Counted rather than named: which address was written to, and what it said,
    are not things an operational screen needs — and a log of who was emailed
    about a failed payment is a log worth not having.
    """
    rows = list(db.scalars(select(models.EmailReceipt).where(
        models.EmailReceipt.store_id == store_id,
        models.EmailReceipt.status == "failed",
    )))
    if not rows:
        return []
    missing = [row for row in rows if row.error_code == "no_recipient"]
    conditions = []
    if missing:
        conditions.append(Condition(
            "notification_without_recipient", WARNING,
            f"{len(missing)} billing notification(s) had nobody to send to",
            "The subscription state is correct; the customer was not told. "
            "Check the account has a confirmed email address.",
            len(missing),
        ))
    rejected = [row for row in rows if row.error_code != "no_recipient"]
    if rejected:
        conditions.append(Condition(
            "notification_rejected", CRITICAL,
            f"{len(rejected)} billing notification(s) were never delivered",
            "A customer has not been told something about their subscription — "
            "most likely a failed payment. Check the mail provider.",
            len(rejected),
        ))
    return conditions


def _stalled_notifications(db: Session, store_id) -> list[Condition]:
    """A send reserved and never finished. The row exists, the email does not."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6)
    stuck = list(db.scalars(select(models.EmailReceipt).where(
        models.EmailReceipt.store_id == store_id,
        models.EmailReceipt.status == "reserved",
        models.EmailReceipt.created_at < cutoff,
    )))
    if not stuck:
        return []
    return [Condition(
        "notification_stuck", WARNING,
        f"{len(stuck)} billing notification(s) reserved and never sent",
        "A worker took them and did not finish. They are recoverable — the "
        "reservation is reclaimed once it goes stale.",
        len(stuck),
    )]


#: What each daily job's silence costs, in the words of somebody who has to
#: decide whether to get up. Keyed by job so a stage that stopped is named
#: rather than reported as "the scheduler".
_SCHEDULER_CONSEQUENCE = {
    "trial_warnings": ("Nobody is being told their trial is about to end, and "
                       "there will be no error anywhere saying so."),
    "shopify_sync": ("No shop is being read. Every screen keeps showing the "
                     "numbers it already had, which look exactly like a quiet "
                     "week, and every measurement waiting on those days waits."),
    "measurement": ("Applied actions are not being priced. The work is still "
                    "happening; nothing is saying whether it was worth it."),
}


def _scheduler_stopped(db: Session, store_id) -> list[Condition]:
    """A daily stage has not completed for too long.

    Deployment-wide rather than per store, but reported here because this is
    where somebody looks. A cron that stops firing produces no errors at all,
    and one stage stopping is invisible while the other two keep succeeding —
    which is why each is asked about separately.
    """
    from . import scheduler

    conditions = []
    for job in scheduler.JOBS:
        if not scheduler.is_stale(db, job=job):
            continue
        hours = scheduler.hours_since_success(db, job=job)
        conditions.append(Condition(
            "scheduler_not_running", CRITICAL,
            f"{job.replace('_', ' ')} has not run for {int(hours or 0)}h",
            "Check the cron service. "
            + _SCHEDULER_CONSEQUENCE.get(job, "This stage has stopped."),
            1, {"job": job},
        ))
    return conditions


def _scheduler_failing(db: Session, store_id) -> list[Condition]:
    """The job is firing and failing, which freshness alone will not show.

    A stage that fails still leaves a row, so `hours_since_success` keeps
    counting up quietly until it crosses the staleness line hours later. The
    failure itself is worth saying the moment it happens.
    """
    from . import scheduler

    conditions = []
    for job in scheduler.JOBS:
        latest = db.scalar(select(models.SchedulerRun).where(
            models.SchedulerRun.job == job,
        ).order_by(models.SchedulerRun.started_at.desc()))
        if latest is None or latest.status != "failed":
            continue
        conditions.append(Condition(
            "scheduler_failed", CRITICAL,
            f"the last {job.replace('_', ' ')} run failed ({latest.error_code})",
            "It is firing, so the cron service is fine. The work inside it is "
            "not; the reason is in that container's log.",
            1, {"job": job},
        ))
    return conditions


#: How long a closed window may go unpriced before it is worth saying. One day
#: covers the ordinary case — the window closes, the next daily run prices it —
#: without reporting every action that is simply waiting for tonight.
MEASUREMENT_GRACE_HOURS = int(os.getenv("ALERT_MEASUREMENT_GRACE_HOURS", "30"))


def _measurement_stalled(db: Session, store_id) -> list[Condition]:
    """An applied action whose window closed and which nobody has priced.

    This is the state the product cannot afford to leave somebody in: the change
    was made, the fortnight passed, and the question it was all for — was it
    worth anything — has no answer. It says which of the two reasons applies,
    because they need opposite responses: missing days are a broken sync, and
    anything else is the job not running.
    """
    from . import actions_engine, commerce_measurement

    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=MEASUREMENT_GRACE_HOURS)
    rows = list(db.scalars(select(models.OperatorAction).where(
        models.OperatorAction.store_id == store_id,
        models.OperatorAction.status == actions_engine.ActionStatus.APPLIED.value,
        models.OperatorAction.measured_at.is_(None),
        models.OperatorAction.applied_at.is_not(None),
    ).limit(100)))

    waiting_on_data = ready_and_unpriced = 0
    for row in rows:
        state = commerce_measurement.readiness(db, row, now=now)
        if state.window is None or not state.window.complete:
            continue
        closed_at = dt.datetime.combine(
            state.window.last + dt.timedelta(days=1), dt.time.min,
            tzinfo=dt.timezone.utc)
        if closed_at > cutoff:
            continue                      # closed, but only just
        if state.state == commerce_measurement.AWAITING_DATA:
            waiting_on_data += 1
        else:
            ready_and_unpriced += 1

    conditions = []
    if ready_and_unpriced:
        conditions.append(Condition(
            "measurement_overdue", CRITICAL,
            f"{ready_and_unpriced} action(s) could be priced and have not been",
            "The days are there and the window is closed. Whatever should have "
            "measured them is not running.",
            ready_and_unpriced,
        ))
    if waiting_on_data:
        conditions.append(Condition(
            "measurement_waiting_on_data", WARNING,
            f"{waiting_on_data} closed window(s) are missing the days to price them",
            "Not a measurement fault: the sync did not cover those days. Fix the "
            "sync and they price themselves on the next run.",
            waiting_on_data,
        ))
    return conditions


def _webhook_trouble(db: Session, store_id) -> list[Condition]:
    conditions = []
    stuck = db.scalar(select(func.count()).select_from(models.WebhookDelivery).where(
        models.WebhookDelivery.store_id == store_id,
        models.WebhookDelivery.status == "processing",
        models.WebhookDelivery.received_at < _stuck_before(),
    )) or 0
    if stuck:
        conditions.append(Condition(
            "webhook_deliveries_stuck", WARNING,
            f"{stuck} delivery(s) have been processing for over "
            f"{STUCK_AFTER_SECONDS // 60} minutes",
            "Their processes died mid-flight. A Shopify retry reclaims them; "
            "if none arrives, the event is lost.",
            stuck,
        ))
    failed = db.scalar(select(func.count()).select_from(models.WebhookDelivery).where(
        models.WebhookDelivery.store_id == store_id,
        models.WebhookDelivery.status == "failed",
        models.WebhookDelivery.received_at >= _since(WINDOW_HOURS),
    )) or 0
    if failed:
        conditions.append(Condition(
            "webhook_deliveries_failing", WARNING,
            f"{failed} delivery(s) failed in the last {WINDOW_HOURS}h",
            "Check the error codes on webhook_deliveries; repeated failures mean "
            "orders are not being counted.",
            failed,
        ))
    return conditions


def _stuck_operations(db: Session, store_id) -> list[Condition]:
    stuck = db.scalar(select(func.count()).select_from(models.IdempotencyRecord).where(
        models.IdempotencyRecord.store_id == store_id,
        models.IdempotencyRecord.status == "processing",
        models.IdempotencyRecord.created_at < _stuck_before(),
    )) or 0
    if not stuck:
        return []
    return [Condition(
        "operations_stuck", WARNING,
        f"{stuck} operation(s) have held a claim for over {STUCK_AFTER_SECONDS // 60} minutes",
        "Their workers are gone. A retry takes the claim over; if nothing retries, "
        "the work never happened.",
        stuck,
    )]


def _unproven_changes(db: Session, store_id) -> list[Condition]:
    """The Operator tried to change the account and could not show that it did."""
    rows = list(db.scalars(select(models.OperatorAction).where(
        models.OperatorAction.store_id == store_id,
        models.OperatorAction.applied_by == "operator",
        models.OperatorAction.evidence_mode == "unverified",
        models.OperatorAction.applied_at >= _since(WINDOW_HOURS),
    )))
    if not rows:
        return []
    return [Condition(
        "changes_unproven", CRITICAL,
        f"{len(rows)} unattended change(s) could not be read back from the account",
        "Either they did not take effect or the account cannot be read. Neither "
        "counts toward the payroll, and both need a look.",
        len(rows), {"targets": [row.target for row in rows[:5]]},
    )]


def _operator_switched_off(db: Session, store_id) -> list[Condition]:
    policy = db.scalar(select(models.GuardrailPolicy).where(
        models.GuardrailPolicy.store_id == store_id))
    if policy is None or policy.enabled:
        return []
    return [Condition(
        "operator_switched_off", WARNING,
        "The Operator is switched off; it is reading nothing and changing nothing",
        "Intentional after an incident, easy to forget afterwards.",
        1,
    )]


def evaluate(db: Session, *, store_id) -> list[Condition]:
    """Every condition currently standing, worst first."""
    conditions: list[Condition] = []
    for check in (_revoked_channels, _stale_notifications, _webhook_trouble,
                  _silent_stores, _stuck_operations, _unproven_changes,
                  _undelivered_notifications, _stalled_notifications,
                  _scheduler_stopped, _scheduler_failing, _measurement_stalled,
                  _operator_switched_off):
        conditions.extend(check(db, store_id))
    order = {CRITICAL: 0, WARNING: 1}
    conditions.sort(key=lambda c: (order.get(c.severity, 2), -c.count))
    return conditions


def worst_severity(conditions: list[Condition]) -> str | None:
    if any(c.severity == CRITICAL for c in conditions):
        return CRITICAL
    if conditions:
        return WARNING
    return None


def report_to_sentry(conditions: list[Condition]) -> int:
    """Hand the critical ones to Sentry if it is configured. Never raises."""
    critical = [c for c in conditions if c.severity == CRITICAL]
    if not critical or not os.getenv("SENTRY_DSN"):
        return 0
    try:
        import sentry_sdk
    except ImportError:                          # pragma: no cover - optional dependency
        return 0
    for condition in critical:
        sentry_sdk.capture_message(
            f"[{condition.name}] {condition.detail}", level="error")
    return len(critical)


def main(argv: list[str] | None = None) -> int:
    """Print the standing conditions for every tenant. Exit 1 on anything critical.

    Written for cron and probes: an exit code is the one interface every
    monitoring system already speaks, so this is useful with no integration at
    all. It walks every store rather than "the current one" — a monitoring job
    has no session, and a condition nobody is logged in to see is exactly the
    kind that goes unnoticed.
    """
    from .db import models as _models
    from .db.session import SessionLocal, declare_tenant
    from .security import Principal, bind_principal, reset_principal

    db = SessionLocal()
    findings: list[tuple[str, Condition]] = []
    try:
        stores = list(db.scalars(select(_models.Store)))
        for store in stores:
            token = bind_principal(Principal(user_id=str(store.user_id),
                                             tenant_id=str(store.id), role="owner"))
            try:
                declare_tenant(db, store.id)
                findings.extend((str(store.id), c)
                                for c in evaluate(db, store_id=store.id))
            finally:
                reset_principal(token)
                db.rollback()          # end the transaction the tenant was set on
    finally:
        db.close()

    if not findings:
        print(f"No conditions standing across {len(stores)} store(s).")
        return 0

    for store_id, condition in findings:
        mark = "CRITICAL" if condition.severity == CRITICAL else "warning"
        print(f"[{mark}] {condition.name} (store {store_id}): {condition.detail}")
        print(f"           {condition.action}")
    report_to_sentry([condition for _, condition in findings])
    return 1 if worst_severity([c for _, c in findings]) == CRITICAL else 0


if __name__ == "__main__":
    raise SystemExit(main())
