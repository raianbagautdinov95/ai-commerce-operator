"""
Tests for the operational alerts.

Every condition here corresponds to a failure that today's testing showed is
silent — the product keeps answering 200 while doing less than it claims. So the
thing these tests must hold is that a healthy system stays quiet and a broken one
does not.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import alerts
from app.db import crud, models
from app.db.models import Base


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
    return crud.get_or_create_dev_store(db)


def _names(db, store):
    return [c.name for c in alerts.evaluate(db, store_id=store.id)]


def _ago(seconds):
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)


def test_a_healthy_system_says_nothing(db, store):
    assert alerts.evaluate(db, store_id=store.id) == []
    assert alerts.worst_severity([]) is None


def test_revoked_access_is_critical_and_names_the_shop(db, store):
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify", external_account_id="gone.myshopify.com",
        display_name="Gone", status="disconnected", currency="USD",
        settings={"revoked_reason": "Shopify rejected the access token (401)"}))
    db.commit()

    condition = alerts.evaluate(db, store_id=store.id)[0]
    assert condition.name == "channel_access_revoked"
    assert condition.severity == alerts.CRITICAL
    assert "gone.myshopify.com" in condition.detail
    assert "reconnect" in condition.action


def test_a_merely_disconnected_channel_is_not_an_alert(db, store):
    """Disconnected on purpose is not the same as access being taken away."""
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify", external_account_id="quiet.myshopify.com",
        display_name="Quiet", status="disconnected", currency="USD", settings={}))
    db.commit()
    assert _names(db, store) == []


def test_notifications_pointing_at_a_dead_address_are_critical(db, store, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_URI", "https://now.test/api/webhooks/shopify")
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify", external_account_id="live.myshopify.com",
        display_name="Live", status="connected", currency="USD",
        settings={"webhook_uri": "https://old-tunnel.test/api/webhooks/shopify"}))
    db.commit()

    condition = alerts.evaluate(db, store_id=store.id)[0]
    assert condition.name == "notifications_point_elsewhere"
    assert condition.severity == alerts.CRITICAL
    assert "arriving nowhere" in condition.action


def test_notifications_pointing_here_are_not_an_alert(db, store, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_URI", "https://now.test/api/webhooks/shopify")
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify", external_account_id="live.myshopify.com",
        display_name="Live", status="connected", currency="USD",
        settings={"webhook_uri": "https://now.test/api/webhooks/shopify"}))
    db.commit()
    assert _names(db, store) == []


def test_a_delivery_stuck_in_flight_is_reported_but_a_fresh_one_is_not(db, store):
    db.add(models.WebhookDelivery(
        store_id=store.id, provider="shopify", delivery_id="fresh", topic="orders/create",
        payload_hash="a", status="processing", received_at=_ago(30)))
    db.commit()
    assert _names(db, store) == []

    db.add(models.WebhookDelivery(
        store_id=store.id, provider="shopify", delivery_id="stuck", topic="orders/create",
        payload_hash="b", status="processing",
        received_at=_ago(alerts.STUCK_AFTER_SECONDS + 60)))
    db.commit()
    assert "webhook_deliveries_stuck" in _names(db, store)


def test_recent_delivery_failures_are_reported(db, store):
    db.add(models.WebhookDelivery(
        store_id=store.id, provider="shopify", delivery_id="bad", topic="orders/create",
        payload_hash="c", status="failed", error_code="OperationalError",
        received_at=_ago(60)))
    db.commit()
    assert "webhook_deliveries_failing" in _names(db, store)


def test_an_old_failure_falls_out_of_the_window(db, store):
    db.add(models.WebhookDelivery(
        store_id=store.id, provider="shopify", delivery_id="ancient", topic="orders/create",
        payload_hash="d", status="failed",
        received_at=_ago(alerts.WINDOW_HOURS * 3600 + 600)))
    db.commit()
    assert "webhook_deliveries_failing" not in _names(db, store)


def test_a_claim_held_by_a_dead_worker_is_reported(db, store):
    db.add(models.IdempotencyRecord(
        store_id=store.id, operation="ads.scan", key="k", status="processing",
        created_at=_ago(alerts.STUCK_AFTER_SECONDS + 60)))
    db.commit()
    assert "operations_stuck" in _names(db, store)


def test_an_unproven_unattended_change_is_critical(db, store):
    """The Operator changed something and cannot show that it did."""
    db.add(models.OperatorAction(
        store_id=store.id, module="ppc", action_type="NEGATE_KEYWORD",
        target="cheap dog bowl", status="proposed", applied_by="operator",
        evidence_mode="unverified", applied_at=_ago(120)))
    db.commit()

    condition = alerts.evaluate(db, store_id=store.id)[0]
    assert condition.name == "changes_unproven"
    assert condition.severity == alerts.CRITICAL
    assert condition.context["targets"] == ["cheap dog bowl"]


def test_a_proven_change_is_not_an_alert(db, store):
    db.add(models.OperatorAction(
        store_id=store.id, module="ppc", action_type="NEGATE_KEYWORD", target="ok",
        status="applied", applied_by="operator", evidence_mode="real",
        applied_at=_ago(120)))
    db.commit()
    assert _names(db, store) == []


def test_a_persons_own_unverified_action_is_not_the_operators_problem(db, store):
    """A human saying "I did this" is a claim, not a failed unattended change."""
    db.add(models.OperatorAction(
        store_id=store.id, module="ppc", action_type="NEGATE_KEYWORD", target="mine",
        status="applied", applied_by="human", evidence_mode="unverified",
        applied_at=_ago(120)))
    db.commit()
    assert _names(db, store) == []


def test_a_switched_off_operator_is_worth_remembering(db, store):
    db.add(models.GuardrailPolicy(store_id=store.id, enabled=False,
                                  max_actions_per_day=20, max_change_pct=0.2,
                                  auto_apply_below=0.0, protected_spend_per_day=50.0))
    db.commit()
    condition = alerts.evaluate(db, store_id=store.id)[0]
    assert condition.name == "operator_switched_off"
    assert "easy to forget" in condition.action


def test_conditions_come_back_worst_first(db, store):
    db.add(models.GuardrailPolicy(store_id=store.id, enabled=False,
                                  max_actions_per_day=20, max_change_pct=0.2,
                                  auto_apply_below=0.0, protected_spend_per_day=50.0))
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify", external_account_id="gone.myshopify.com",
        display_name="Gone", status="disconnected", currency="USD",
        settings={"revoked_reason": "401"}))
    db.commit()

    conditions = alerts.evaluate(db, store_id=store.id)
    assert conditions[0].severity == alerts.CRITICAL
    assert alerts.worst_severity(conditions) == alerts.CRITICAL


def test_another_tenants_trouble_is_not_reported_here(db, store):
    other_user = models.User(email="other@local")
    db.add(other_user); db.commit()
    other_store = models.Store(user_id=other_user.id, marketplace="US")
    db.add(other_store); db.commit()
    db.add(models.ChannelConnection(
        store_id=other_store.id, provider="shopify", external_account_id="theirs.myshopify.com",
        display_name="Theirs", status="disconnected", currency="USD",
        settings={"revoked_reason": "401"}))
    db.commit()

    assert _names(db, store) == []
    assert [c.name for c in alerts.evaluate(db, store_id=other_store.id)] == \
        ["channel_access_revoked"]


def test_sentry_is_optional_and_never_raises(db, store, monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    condition = alerts.Condition("x", alerts.CRITICAL, "d", "a", 1)
    assert alerts.report_to_sentry([condition]) == 0
    assert alerts.report_to_sentry([]) == 0


# --- the cron entry point ----------------------------------------------------

def _run_cli(monkeypatch, factory, capsys):
    from app import alerts as module
    monkeypatch.setattr("app.db.session.SessionLocal", factory)
    monkeypatch.setattr("app.db.session.declare_tenant", lambda *a, **k: None)
    code = module.main([])
    return code, capsys.readouterr().out


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_the_cli_is_quiet_and_exits_zero_when_all_is_well(monkeypatch, factory, capsys):
    db = factory()
    crud.get_or_create_dev_store(db)
    db.close()

    code, out = _run_cli(monkeypatch, factory, capsys)
    assert code == 0
    assert "No conditions standing" in out


def test_the_cli_walks_every_tenant_not_just_one(monkeypatch, factory, capsys):
    """A monitoring job has no session; a condition nobody is logged in to see is
    exactly the kind that goes unnoticed."""
    db = factory()
    first = crud.get_or_create_dev_store(db)
    second_user = models.User(email="second@local")
    db.add(second_user); db.commit()
    second = models.Store(user_id=second_user.id, marketplace="US")
    db.add(second); db.commit()
    for store, shop in ((first, "one.myshopify.com"), (second, "two.myshopify.com")):
        db.add(models.ChannelConnection(
            store_id=store.id, provider="shopify", external_account_id=shop,
            display_name=shop, status="disconnected", currency="USD",
            settings={"revoked_reason": "401"}))
    db.commit(); db.close()

    code, out = _run_cli(monkeypatch, factory, capsys)
    assert code == 1                       # critical stands
    assert "one.myshopify.com" in out
    assert "two.myshopify.com" in out
    assert str(first.id) in out and str(second.id) in out


def test_a_warning_alone_does_not_page_anyone(monkeypatch, factory, capsys):
    """Exit 1 is for what needs waking someone. A warning belongs in the output."""
    db = factory()
    store = crud.get_or_create_dev_store(db)
    db.add(models.GuardrailPolicy(store_id=store.id, enabled=False,
                                  max_actions_per_day=20, max_change_pct=0.2,
                                  auto_apply_below=0.0, protected_spend_per_day=50.0))
    db.commit(); db.close()

    code, out = _run_cli(monkeypatch, factory, capsys)
    assert code == 0
    assert "operator_switched_off" in out


def test_a_store_that_stopped_syncing_is_noticed(db, store):
    """The failure with no error attached: notifications refused after a secret
    rotation, a worker not consuming, a token revoked quietly. Every screen goes
    on showing the last numbers it had, which looks exactly like a quiet week."""
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
        hours=alerts.STALE_SYNC_HOURS + 1)
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id="silent.myshopify.com", display_name="Silent",
        status="connected", currency="USD", settings={},
        connected_at=old, synced_at=old))
    db.commit()
    assert "store_not_synced" in _names(db, store)


def test_a_store_connected_moments_ago_is_not_overdue(db, store):
    """It has not had time to sync yet. Alerting here would greet a new customer
    with a warning about their own store, and teach them to ignore the next."""
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id="fresh.myshopify.com", display_name="Fresh",
        status="connected", currency="USD", settings={},
        connected_at=dt.datetime.now(dt.timezone.utc), synced_at=None))
    db.commit()
    assert "store_not_synced" not in _names(db, store)


def test_a_disconnected_store_is_not_nagged_about_syncing(db, store):
    """It is already reported as revoked; saying it also has not synced is the
    same news twice."""
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id="gone.myshopify.com", display_name="Gone",
        status="disconnected", currency="USD", settings={},
        connected_at=old, synced_at=old))
    db.commit()
    assert "store_not_synced" not in _names(db, store)


def test_a_notification_that_reached_nobody_is_reported(db, store):
    """The subscription state is right and the customer was not told. Silence
    here is how somebody's payment fails and nobody finds out."""
    from app import alerts

    db.add(models.EmailReceipt(
        store_id=store.id, kind="payment_attention", dedupe_key="evt_1",
        status="failed", error_code="no_recipient", attempts=1))
    db.commit()
    assert "notification_without_recipient" in _names(db, store)


def test_a_rejected_notification_is_critical(db, store):
    from app import alerts

    db.add(models.EmailReceipt(
        store_id=store.id, kind="payment_attention", dedupe_key="evt_2",
        status="failed", error_code="refused", attempts=3))
    db.commit()
    conditions = {c.name: c for c in alerts.evaluate(db, store_id=store.id)}
    assert conditions["notification_rejected"].severity == alerts.CRITICAL


def test_a_delivered_notification_is_not_an_alert(db, store):
    db.add(models.EmailReceipt(
        store_id=store.id, kind="subscription_activated", dedupe_key="evt_3",
        status="sent", attempts=1,
        sent_at=dt.datetime.now(dt.timezone.utc)))
    db.commit()
    assert _names(db, store) == []


def test_the_alert_never_names_the_recipient(db, store):
    """A log of who was emailed about a failed payment is a log worth not
    having."""
    from app import alerts

    db.add(models.EmailReceipt(
        store_id=store.id, kind="payment_attention", dedupe_key="evt_4",
        status="failed", error_code="refused", attempts=2))
    db.commit()
    rendered = " ".join(f"{c.detail} {c.action}"
                        for c in alerts.evaluate(db, store_id=store.id))
    assert "@" not in rendered
