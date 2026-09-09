"""
Tests for the four subscription emails.

Resend is never called. `mailer.send_email` is replaced at its boundary in every
test here, so what is proved is our half: that a redelivered Stripe event does
not produce a second message about somebody's money, that a crash between
reserving and sending is recoverable, that a permanent rejection stops rather
than looping, and that nothing in a template names a Stripe id, a store or one
of the seller's own customers.
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

from app import mailer, notifications
from app.db import models
from app.db.models import Base


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setenv("SUPPORT_EMAIL", "help@example.test")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.test")
    monkeypatch.setenv("RESEND_API_KEY", "re_not_a_real_key")
    monkeypatch.setenv("LOGIN_EMAIL_FROM", "Operator <no-reply@example.test>")


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def _store(db, email="owner@example.test"):
    user = models.User(email=email)
    db.add(user); db.flush()
    store = models.Store(user_id=user.id, marketplace="US")
    db.add(store); db.commit()
    return store


@pytest.fixture
def sent(monkeypatch):
    """Every send lands here instead of at Resend."""
    outbox = []
    monkeypatch.setattr(mailer, "send_email",
                        lambda **kw: outbox.append(kw))
    return outbox


def _deliver(db, store, kind, *, key="k1", context=None):
    receipt = notifications.reserve(db, store_id=store.id, kind=kind, dedupe_key=key)
    if receipt is None:
        return None
    db.commit()
    return notifications.deliver(db, receipt_id=receipt.id, store=store, kind=kind,
                                 context=context or {})


# --- one message per thing that happened ------------------------------------

@pytest.mark.parametrize("kind", list(notifications.KINDS))
def test_each_event_sends_exactly_one_message(db, sent, kind):
    store = _store(db)
    assert _deliver(db, store, kind, context={"ends_on": "2026-10-01"}) == "sent"
    assert len(sent) == 1


@pytest.mark.parametrize("kind", list(notifications.KINDS))
def test_the_same_event_twice_sends_once(db, sent, kind):
    """Stripe redelivers, and the scheduler runs every day. A duplicate 'your
    payment failed' is not a cosmetic problem."""
    store = _store(db)
    _deliver(db, store, kind, key="evt_1", context={"ends_on": "2026-10-01"})
    second = _deliver(db, store, kind, key="evt_1", context={"ends_on": "2026-10-01"})
    assert second is None
    assert len(sent) == 1


def test_a_new_incident_earns_a_new_message(db, sent):
    """A second failed payment is a second thing to be told about. The key is
    the Stripe event id, so a genuinely new incident is a new key."""
    store = _store(db)
    _deliver(db, store, notifications.PAYMENT_ATTENTION, key="evt_1")
    _deliver(db, store, notifications.PAYMENT_ATTENTION, key="evt_2")
    assert len(sent) == 2


# --- crashes on either side of the send -------------------------------------

def test_a_crash_before_sending_is_recoverable(db, sent, monkeypatch):
    """The reservation exists and nothing went out. Losing the message for ever
    would be the worse failure, so a stale reservation is reclaimable."""
    store = _store(db)
    receipt = notifications.reserve(db, store_id=store.id,
                                    kind=notifications.ACTIVATED, dedupe_key="evt_9")
    receipt.created_at = (dt.datetime.now(dt.timezone.utc)
                          - dt.timedelta(minutes=notifications.STALE_RESERVATION_MINUTES + 5))
    db.add(receipt); db.commit()

    again = notifications.reserve(db, store_id=store.id,
                                  kind=notifications.ACTIVATED, dedupe_key="evt_9")
    assert again is not None
    db.commit()
    assert notifications.deliver(db, receipt_id=again.id, store=store,
                                 kind=notifications.ACTIVATED, context={}) == "sent"
    assert len(sent) == 1


def test_a_fresh_reservation_is_not_stolen_by_a_second_worker(db, sent):
    """Two workers on the same event must not both send. Only an abandoned
    reservation is reclaimable, and one made moments ago is not abandoned."""
    store = _store(db)
    notifications.reserve(db, store_id=store.id, kind=notifications.ACTIVATED,
                          dedupe_key="evt_x")
    db.commit()
    assert notifications.reserve(db, store_id=store.id,
                                 kind=notifications.ACTIVATED,
                                 dedupe_key="evt_x") is None


def test_a_sent_message_is_never_reserved_again(db, sent):
    store = _store(db)
    _deliver(db, store, notifications.CANCELED, key="evt_done")
    assert notifications.reserve(db, store_id=store.id, kind=notifications.CANCELED,
                                 dedupe_key="evt_done") is None


# --- failures, transient and permanent --------------------------------------

def test_a_transient_failure_asks_to_be_retried(db, monkeypatch):
    def boom(**_kw):
        raise mailer.MailerNotConfigured("unreachable")

    monkeypatch.setattr(mailer, "send_email", boom)
    store = _store(db)
    assert _deliver(db, store, notifications.ACTIVATED) == "retry"
    receipt = db.scalar(select(models.EmailReceipt))
    assert receipt.status == "reserved" and receipt.attempts == 1


def test_a_permanent_rejection_stops_rather_than_looping(db, monkeypatch):
    """A refused address answers the same way every time. Retrying spends the
    queue on an answer that will not change."""
    def refused(**_kw):
        raise mailer.MailerRejected("refused")

    monkeypatch.setattr(mailer, "send_email", refused)
    store = _store(db)
    assert _deliver(db, store, notifications.ACTIVATED) == "rejected"
    receipt = db.scalar(select(models.EmailReceipt))
    assert receipt.status == "failed" and receipt.error_code == "refused"


def test_retrying_stops_after_a_bounded_number_of_attempts(db, monkeypatch):
    def boom(**_kw):
        raise mailer.MailerNotConfigured("unreachable")

    monkeypatch.setattr(mailer, "send_email", boom)
    store = _store(db)
    receipt = notifications.reserve(db, store_id=store.id,
                                    kind=notifications.ACTIVATED, dedupe_key="evt_r")
    db.commit()
    outcome = None
    for _ in range(notifications.MAX_ATTEMPTS):
        outcome = notifications.deliver(db, receipt_id=receipt.id, store=store,
                                        kind=notifications.ACTIVATED, context={})
        receipt.status = "reserved" if outcome == "retry" else receipt.status
    assert outcome == "exhausted"


# --- the recipient ----------------------------------------------------------

def test_a_store_with_no_address_is_recorded_rather_than_guessed_at(db, sent):
    """The subscription state has already changed; this only means nobody can be
    told. Recorded so it shows as a condition rather than as silence."""
    store = _store(db, email="")
    assert _deliver(db, store, notifications.ACTIVATED) == "no_recipient"
    assert sent == []
    assert db.scalar(select(models.EmailReceipt)).error_code == "no_recipient"


def test_the_recipient_is_the_signed_in_owner(db, sent):
    store = _store(db, email="owner@example.test")
    _deliver(db, store, notifications.ACTIVATED)
    assert sent[0]["to"] == "owner@example.test"


def test_a_webhook_payload_cannot_choose_the_recipient(db, sent):
    """A webhook body is attacker-shaped input for this purpose even when its
    signature checks out, so the address never comes from `context`."""
    store = _store(db, email="owner@example.test")
    _deliver(db, store, notifications.ACTIVATED,
             context={"email": "attacker@example.test", "to": "attacker@example.test"})
    assert sent[0]["to"] == "owner@example.test"


# --- what the templates may say ---------------------------------------------

@pytest.mark.parametrize("kind", list(notifications.KINDS))
def test_no_template_names_an_identifier(db, kind):
    message = notifications.render(kind, context={"ends_on": "2026-10-01"})
    blob = f"{message.subject} {message.text} {message.html}"
    for forbidden in ("cus_", "sub_", "evt_", "price_", "store_id", "tenant",
                      "shpat_", "Traceback"):
        assert forbidden not in blob, forbidden


@pytest.mark.parametrize("kind", list(notifications.KINDS))
def test_every_template_says_who_it_is_from_and_where_to_go(db, kind):
    message = notifications.render(kind, context={"ends_on": "2026-10-01"})
    assert message.subject
    assert "AI Commerce Operator" in message.html
    assert "https://app.example.test/billing" in message.text
    assert "help@example.test" in message.text
    # Readable with images blocked, because many clients block them.
    assert "<img" not in message.html


@pytest.mark.parametrize("kind", list(notifications.KINDS))
def test_no_template_promises_a_return(db, kind):
    blob = notifications.render(kind, context={"ends_on": "2026-10-01"}).text.lower()
    for forbidden in ("guarantee", "roi", "you will earn", "profit of"):
        assert forbidden not in blob


def test_the_cancellation_message_says_the_data_stayed(db):
    text = notifications.render(notifications.CANCELED, context={}).text
    assert "still here" in text
    assert "export" in text


def test_the_trial_warning_names_the_date_and_says_nothing_is_deleted(db):
    text = notifications.render(notifications.TRIAL_ENDING,
                                context={"ends_on": "2026-10-01"}).text
    assert "2026-10-01" in text
    assert "Nothing will be deleted" in text


# --- one tenant's receipts are not another's --------------------------------

def test_one_tenant_cannot_see_or_reuse_another_receipt(db, sent):
    mine = _store(db, "mine@example.test")
    theirs = _store(db, "theirs@example.test")
    _deliver(db, mine, notifications.ACTIVATED, key="evt_shared")

    # The same key for a different tenant is a different notification.
    assert _deliver(db, theirs, notifications.ACTIVATED, key="evt_shared") == "sent"
    assert len(sent) == 2
    assert {row.store_id for row in db.scalars(select(models.EmailReceipt))} == \
        {mine.id, theirs.id}
