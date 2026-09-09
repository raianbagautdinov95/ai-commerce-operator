"""The four emails this product sends about money, and the record of sending them.

All four are service messages: something changed about a subscription and the
customer needs to know. None of them is marketing, none carries an order or a
customer of theirs, and none promises a return — a billing email is the worst
possible place to make a claim about earnings.

**Delivery semantics, stated rather than implied.** This is *at-least-once with
deduplication*, not exactly-once, because Resend does not offer exactly-once and
nothing built on top of it can. A receipt row is reserved before the send and
marked after it, so:

- a redelivered Stripe event finds the receipt and sends nothing;
- a crash *before* the send leaves a `reserved` row, which is recoverable —
  it can be retried, because nothing went out;
- a crash *after* the provider accepted but before the mark can, in the worst
  case, send a second copy. That window is one HTTP round trip wide and the
  alternative is losing the message entirely, which for "your payment failed" is
  the worse trade.

The recipient is the address the tenant signed in with, never one taken from
Stripe's payload: a webhook body is attacker-shaped input for this purpose even
when its signature checks out, and emailing wherever it says would be a way to
send our own transactional mail to somebody else's inbox.
"""
from __future__ import annotations

import datetime as dt
import os
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import billing, mailer
from .db import models
from .runtime import log

TRIAL_ENDING = "trial_ending"
ACTIVATED = "subscription_activated"
PAYMENT_ATTENTION = "payment_attention"
CANCELED = "subscription_canceled"

KINDS = (TRIAL_ENDING, ACTIVATED, PAYMENT_ATTENTION, CANCELED)

#: How many days before a trial ends the warning goes out.
TRIAL_WARNING_DAYS = int(os.getenv("TRIAL_WARNING_DAYS", "3"))

#: A `reserved` row older than this was abandoned by a process that died, and
#: may be retried. Comfortably longer than any send takes.
STALE_RESERVATION_MINUTES = 15

#: Attempts before a transient failure is treated as permanent. Retrying for
#: ever costs the queue and never reaches anybody.
MAX_ATTEMPTS = 5


def billing_url() -> str:
    """Where every one of these emails points. One stable address, from
    configuration, because a link in an email outlives the deploy that sent
    it."""
    base = (os.getenv("PUBLIC_APP_URL") or "").strip().rstrip("/")
    return f"{base}/billing" if base else "/billing"


def support_email() -> str | None:
    return (os.getenv("SUPPORT_EMAIL") or "").strip() or None


@dataclass
class Message:
    subject: str
    text: str
    html: str


def _shell(title: str, paragraphs: list[str], action: str) -> Message:
    """Every message has the same shape: why you got it, one thing to do, and
    who to ask. No images — a mail client that blocks them must still show the
    whole message, so nothing here depends on one loading."""
    support = support_email()
    tail = (f"\n\nQuestions about a charge? {support}" if support else "")
    text = "\n\n".join(paragraphs) + f"\n\n{action}\n{billing_url()}" + tail
    body = "".join(
        f'<p style="margin:0 0 14px;line-height:1.6">{p}</p>' for p in paragraphs)
    support_html = (
        f'<p style="margin:24px 0 0;font-size:13px;color:#666">'
        f'Questions about a charge? <a href="mailto:{support}">{support}</a></p>'
        if support else "")
    html = (
        '<div style="font-family:system-ui,sans-serif;max-width:560px;color:#111">'
        f'<p style="margin:0 0 18px;font-size:13px;color:#666">AI Commerce Operator</p>'
        f'<h1 style="margin:0 0 16px;font-size:20px">{title}</h1>'
        f"{body}"
        f'<p style="margin:22px 0 0"><a href="{billing_url()}" '
        f'style="display:inline-block;padding:10px 16px;background:#111;color:#fff;'
        f'border-radius:6px;text-decoration:none">{action}</a></p>'
        f"{support_html}"
        "</div>")
    return Message(subject=title, text=text, html=html)


def render(kind: str, *, context: dict) -> Message:
    """The four templates. Nothing here interpolates an identifier, a Stripe
    reference or anything about the seller's own customers."""
    plan = billing.PILOT_PLAN_NAME
    if kind == TRIAL_ENDING:
        ends = context.get("ends_on", "shortly")
        return _shell(
            f"Your {plan} trial ends on {ends}",
            [f"Your free trial of {plan} ends on {ends}.",
             "Nothing will be deleted. Everything the Operator has found stays "
             "where it is, and you can read or export it whether or not you "
             "subscribe.",
             "If you do subscribe, it carries on importing your orders and "
             "proposing changes without a gap."],
            "Choose a plan")
    if kind == ACTIVATED:
        return _shell(
            f"Your {plan} subscription is active",
            ["Your subscription is active — thank you.",
             "The Operator is watching your store, reading what arrives and "
             "proposing what looks worth changing. It still changes nothing "
             "without you approving it.",
             "You can see or cancel your subscription at any time."],
            "Manage your subscription")
    if kind == PAYMENT_ATTENTION:
        return _shell(
            "Your payment needs attention",
            ["The last payment for your subscription did not go through.",
             "Nothing has been deleted. Everything the Operator has found is "
             "still here and still exportable; it has paused importing new "
             "orders until the payment is settled.",
             "Updating your card resolves it straight away."],
            "Update your payment details")
    if kind == CANCELED:
        return _shell(
            "Your subscription has ended",
            ["Your subscription has been cancelled.",
             "The Operator has stopped importing new orders and proposing "
             "changes. Everything it found before now is still here — you can "
             "read it, export it, or ask for it to be deleted, at any time.",
             "You can start again whenever you like, and your history will "
             "still be there."],
            "Start again")
    raise ValueError(f"Unknown notification kind: {kind}")


def recipient_for(db: Session, *, store) -> str | None:
    """The address this tenant signed in with — never one from a webhook body.

    Returns None rather than raising: a missing address must not break the state
    change that prompted the email.
    """
    user = db.get(models.User, store.user_id)
    address = (getattr(user, "email", "") or "").strip()
    return address or None


def reserve(db: Session, *, store_id: uuid.UUID, kind: str, dedupe_key: str):
    """Claim the right to send this exactly once. Returns the receipt, or None
    when somebody already has it.

    A `reserved` row left behind by a dead process is reclaimed after
    `STALE_RESERVATION_MINUTES`, because the alternative is a message that is
    never sent and never explained.
    """
    receipt = models.EmailReceipt(
        store_id=store_id, kind=kind, dedupe_key=dedupe_key[:128], status="reserved")
    db.add(receipt)
    try:
        db.flush()
        return receipt
    except IntegrityError:
        db.rollback()

    from .db.session import declare_tenant
    declare_tenant(db, store_id)
    existing = db.scalar(select(models.EmailReceipt).where(
        models.EmailReceipt.store_id == store_id,
        models.EmailReceipt.kind == kind,
        models.EmailReceipt.dedupe_key == dedupe_key[:128],
    ))
    if existing is None:
        return None
    if existing.status == "sent":
        return None
    created = existing.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=dt.timezone.utc)
    abandoned = created is None or (
        dt.datetime.now(dt.timezone.utc) - created
        > dt.timedelta(minutes=STALE_RESERVATION_MINUTES))
    if existing.status == "reserved" and abandoned:
        existing.created_at = dt.datetime.now(dt.timezone.utc)
        db.add(existing)
        return existing
    if existing.status == "failed" and existing.attempts < MAX_ATTEMPTS:
        existing.status = "reserved"
        existing.created_at = dt.datetime.now(dt.timezone.utc)
        db.add(existing)
        return existing
    return None


def deliver(db: Session, *, receipt_id: uuid.UUID, store, kind: str,
            context: dict) -> str:
    """Send one reserved message and record what happened. Returns the outcome.

    Never raises for a delivery problem: the caller is a queued job, and the
    receipt is where the answer belongs.
    """
    from .db.session import declare_tenant

    declare_tenant(db, store.id)
    receipt = db.get(models.EmailReceipt, receipt_id)
    if receipt is None or receipt.status == "sent":
        return "already_sent"

    address = recipient_for(db, store=store)
    if not address:
        # The subscription state has already changed; this only means nobody can
        # be told. Recorded so it shows up as an operational condition rather
        # than as silence.
        receipt.status = "failed"
        receipt.error_code = "no_recipient"
        receipt.attempts += 1
        db.add(receipt); db.commit()
        log.warning("No confirmed address for a %s notification on store %s",
                    kind, store.id)
        return "no_recipient"

    message = render(kind, context=context)
    receipt.attempts += 1
    try:
        mailer.send_email(to=address, subject=message.subject,
                          text=message.text, html=message.html)
    except mailer.MailerRejected as exc:
        # The provider will answer the same way next time. Stop.
        receipt.status = "failed"
        receipt.error_code = str(exc)[:48]
        db.add(receipt); db.commit()
        log.error("Notification %s permanently rejected: %s", kind, exc)
        return "rejected"
    except Exception as exc:  # noqa: BLE001 - transient; the queue will retry
        receipt.status = "failed" if receipt.attempts >= MAX_ATTEMPTS else "reserved"
        receipt.error_code = str(exc)[:48]
        db.add(receipt); db.commit()
        log.warning("Notification %s failed (attempt %d): %s",
                    kind, receipt.attempts, exc)
        return "retry" if receipt.status == "reserved" else "exhausted"

    receipt.status = "sent"
    receipt.sent_at = dt.datetime.now(dt.timezone.utc)
    receipt.error_code = None
    db.add(receipt); db.commit()
    log.info("Notification %s sent for store %s", kind, store.id)
    return "sent"
