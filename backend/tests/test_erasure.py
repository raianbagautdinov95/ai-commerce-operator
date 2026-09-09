"""
Tests for carrying out a deletion request.

Every one of these runs against a database created inside the test and thrown
away with it. Nothing here touches a deployment: an erasure test that could
point at the wrong database is a worse risk than the bug it would catch.

What is being guarded is the pair of failures either side of the right answer.
Erasing too little leaves the customer's data behind after they were told it was
gone — a token that still works, a session that still resolves, an audit trail
naming their shop. Erasing too much takes somebody else's tenant with it.
"""
import base64
import datetime as dt
import json
import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import credentials, erasure, shopify
from app.db import models
from app.db.models import Base


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS",
                       json.dumps({"v1": base64.b64encode(b"k" * 32).decode()}))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")


@pytest.fixture
def db():
    """A database that exists only for this test."""
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def _tenant(db, email: str, shop: str):
    """A tenant with something in every table erasure is meant to reach."""
    user = models.User(email=email)
    db.add(user); db.flush()
    store = models.Store(user_id=user.id, marketplace="US")
    db.add(store); db.flush()

    channel = models.ChannelConnection(
        store_id=store.id, provider="shopify", external_account_id=shop,
        display_name=shop, status="connected", currency="USD", settings={})
    db.add(channel); db.flush()
    credentials.store_credential(
        db, store_id=store.id, provider=shopify.credential_provider(shop),
        secret="shpat_secret_token")

    today = dt.datetime.now(dt.timezone.utc)
    db.add_all([
        models.CommerceDailyMetric(
            store_id=store.id, channel_id=channel.id, metric_date=today.date(),
            revenue=Decimal("40.00"), refunds=0, fees=0, landed_cogs=0,
            advertising_spend=0, orders=1, units=1, sessions=0, currency="USD",
            source="shopify_graphql", costs_complete=False),
        models.ChannelProduct(
            store_id=store.id, channel_id=channel.id,
            external_product_id="gid://1", title="A Snowboard", status="ACTIVE",
            on_hand=3, is_gift_card=False, requires_shipping=True),
        models.ProductDailyMetric(
            store_id=store.id, channel_id=channel.id,
            external_product_id="gid://1", metric_date=today.date(),
            units=1, revenue=Decimal("40.00"), currency="USD",
            source="shopify_graphql"),
        models.WebhookDelivery(
            store_id=store.id, provider="shopify", delivery_id=f"d-{shop}",
            topic="orders/create", payload_hash="x", status="completed"),
        models.IdempotencyRecord(
            store_id=store.id, operation="shopify.sync:1:30", key=f"k-{shop}",
            status="completed"),
        models.OperatorAction(
            store_id=store.id, module="commerce", action_type="RESTOCK_PRODUCT",
            target="A Snowboard", status="proposed", evidence_mode="unverified",
            currency="USD", measurement_days=14),
        models.AuditEvent(
            store_id=store.id, actor_id=user.id, action="shopify.connected",
            resource_type="channel_connection", after={"shop": shop}),
        models.TokenSession(
            user_id=user.id, store_id=store.id, role="operator", method="email",
            expires_at=today + dt.timedelta(days=7)),
    ])
    db.commit()
    return store, user


# --- the list cannot silently fall behind the schema ------------------------

def test_every_tenant_table_is_named_or_handled(db):
    """A table added later and forgotten here would survive an erasure the
    customer was told had happened."""
    covered = {m.__tablename__ for m in erasure.TENANT_TABLES}
    # audit_events and token_sessions are deleted explicitly rather than by the
    # loop, the first because one new row replaces them and the second because
    # it is keyed on the user.
    handled = covered | {"audit_events", "token_sessions"}
    tenant_tables = {c.__tablename__ for c in models.Base.__subclasses__()
                     if hasattr(c, "store_id")}
    assert tenant_tables - handled == set()


# --- erasing the right amount -----------------------------------------------

def test_a_dry_run_changes_nothing(db):
    store, _ = _tenant(db, "dry@example.test", "dry.myshopify.com")
    counts = erasure.plan(db, store.id)
    assert counts["commerce_daily_metrics"] == 1
    assert db.get(models.Store, store.id) is not None
    assert list(db.scalars(select(models.IntegrationCredential)))


def test_erasure_removes_every_trace_of_the_tenant(db):
    store, user = _tenant(db, "gone@example.test", "gone.myshopify.com")
    erasure.erase(db, store.id, request_id="req-1")

    for model in erasure.TENANT_TABLES:
        rows = list(db.scalars(select(model).where(model.store_id == store.id)))
        assert rows == [], f"{model.__tablename__} survived"
    assert db.get(models.Store, store.id) is None
    assert db.get(models.User, user.id) is None


def test_the_access_token_does_not_survive(db):
    """The one that matters most: a token left behind is a live key to somebody
    else's shop, held by a system they have left."""
    store, _ = _tenant(db, "tok@example.test", "tok.myshopify.com")
    erasure.erase(db, store.id)
    assert list(db.scalars(select(models.IntegrationCredential))) == []


def test_a_session_cannot_outlive_the_account(db):
    """A token that still resolves after erasure is a door into an account that
    no longer exists."""
    store, user = _tenant(db, "sess@example.test", "sess.myshopify.com")
    erasure.erase(db, store.id)
    assert list(db.scalars(select(models.TokenSession).where(
        models.TokenSession.user_id == user.id))) == []


def test_only_a_record_that_it_happened_remains(db):
    """Not a copy of what was erased: an event naming the date and the request,
    holding nothing personal."""
    store, _ = _tenant(db, "rec@example.test", "rec.myshopify.com")
    erasure.erase(db, store.id, request_id="req-7")

    events = list(db.scalars(select(models.AuditEvent)))
    assert len(events) == 1
    event = events[0]
    assert event.action == "privacy.erased"
    assert event.after["request_id"] == "req-7"
    assert "rec.myshopify.com" not in json.dumps(event.after)


# --- and no more than that --------------------------------------------------

def test_erasing_one_tenant_leaves_the_other_untouched(db):
    """The expensive mistake in the other direction."""
    mine, _ = _tenant(db, "mine@example.test", "mine.myshopify.com")
    theirs, their_user = _tenant(db, "theirs@example.test", "theirs.myshopify.com")

    erasure.erase(db, mine.id)

    assert db.get(models.Store, theirs.id) is not None
    assert db.get(models.User, their_user.id) is not None
    # Only the tables the fixture actually fills; the Amazon ones are empty for
    # both tenants, and "still empty" would prove nothing either way.
    for model in (models.ChannelConnection, models.IntegrationCredential,
                  models.CommerceDailyMetric, models.ChannelProduct,
                  models.ProductDailyMetric, models.WebhookDelivery,
                  models.IdempotencyRecord, models.OperatorAction):
        rows = list(db.scalars(select(model).where(model.store_id == theirs.id)))
        assert rows, f"{model.__tablename__} was taken from the wrong tenant"


def test_their_payroll_and_measurements_are_still_theirs(db):
    mine, _ = _tenant(db, "m2@example.test", "m2.myshopify.com")
    theirs, _ = _tenant(db, "t2@example.test", "t2.myshopify.com")
    erasure.erase(db, mine.id)

    actions = list(db.scalars(select(models.OperatorAction)))
    assert len(actions) == 1
    assert actions[0].store_id == theirs.id


# --- the command refuses to be run carelessly -------------------------------

def test_the_command_refuses_without_the_exact_confirmation(monkeypatch, db):
    """Typing the wrong id twice is harder than typing yes."""
    store, _ = _tenant(db, "cmd@example.test", "cmd.myshopify.com")
    monkeypatch.setattr(erasure, "SessionLocal", lambda: db)

    assert erasure.main(["--store", str(store.id), "--confirm", "yes"]) == 2
    assert db.get(models.Store, store.id) is not None, "it deleted anyway"


def test_the_command_refuses_a_confirmation_naming_another_store(monkeypatch, db):
    store, _ = _tenant(db, "cmd2@example.test", "cmd2.myshopify.com")
    monkeypatch.setattr(erasure, "SessionLocal", lambda: db)

    other = uuid.uuid4()
    assert erasure.main(["--store", str(store.id),
                         "--confirm", f"ERASE {other}"]) == 2
    assert db.get(models.Store, store.id) is not None


def test_the_dry_run_flag_never_deletes(monkeypatch, db):
    store, _ = _tenant(db, "cmd3@example.test", "cmd3.myshopify.com")
    monkeypatch.setattr(erasure, "SessionLocal", lambda: db)

    assert erasure.main(["--store", str(store.id), "--dry-run"]) == 0
    assert db.get(models.Store, store.id) is not None


def test_the_right_confirmation_carries_it_out(monkeypatch, db):
    store, _ = _tenant(db, "cmd4@example.test", "cmd4.myshopify.com")
    monkeypatch.setattr(erasure, "SessionLocal", lambda: db)

    assert erasure.main(["--store", str(store.id),
                         "--confirm", f"ERASE {store.id}"]) == 0
    assert db.get(models.Store, store.id) is None


def test_an_unknown_store_is_reported_not_guessed_at(monkeypatch, db):
    monkeypatch.setattr(erasure, "SessionLocal", lambda: db)
    assert erasure.main(["--store", str(uuid.uuid4()), "--dry-run"]) == 1


def test_a_customer_has_no_route_to_this():
    """Erasure is a command, not an endpoint. There is no combination of session
    and dialog that makes an irreversible cross-table delete safe to expose."""
    from app.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert not [p for p in paths if "eras" in p.lower()]
    assert "/api/privacy/deletion-request" in paths
