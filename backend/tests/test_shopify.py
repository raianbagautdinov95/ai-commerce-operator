import base64
import datetime as dt
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import credentials, shopify
from app.db import crud, models
from app.db.models import Base
from app.db.session import get_session
from app.main import app


def _configure(monkeypatch):
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "shopify-client")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "shopify-secret")
    monkeypatch.setenv("SHOPIFY_REDIRECT_URI", "https://app.example.test/shopify/callback")


def _signature(body: bytes) -> str:
    return base64.b64encode(hmac.new(b"shopify-secret", body, hashlib.sha256).digest()).decode()


def test_shop_domain_validation_blocks_arbitrary_hosts():
    assert shopify.normalize_shop("https://valid-store.myshopify.com/") == \
        "valid-store.myshopify.com"
    for value in ("evil.example.com", "valid-store.myshopify.com.evil.test", "localhost"):
        with pytest.raises(shopify.ShopifyAuthorizationError):
            shopify.normalize_shop(value)


def test_oauth_state_is_bound_to_shop_and_callback_hmac(monkeypatch):
    _configure(monkeypatch)
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    store = crud.get_or_create_dev_store(db)
    url = shopify.create_authorization(
        db, store_id=store.id, actor_id=store.user_id, shop="one.myshopify.com"
    )
    state = url.split("state=")[1].split("&")[0]
    message = f"code=code-1&shop=one.myshopify.com&state={state}&timestamp=1"
    signature = hmac.new(b"shopify-secret", message.encode(), hashlib.sha256).hexdigest()
    query = shopify.verify_callback_query(f"{message}&hmac={signature}".encode())
    assert query["shop"] == "one.myshopify.com"
    with pytest.raises(shopify.ShopifyAuthorizationError):
        shopify.consume_state(db, state, "other.myshopify.com")
    assert shopify.consume_state(db, state, "one.myshopify.com").store_id == store.id


def test_webhook_hmac_and_delivery_id_are_idempotent(monkeypatch):
    _configure(monkeypatch)
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory(); store = crud.get_or_create_dev_store(db)
    channel = models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id="demo-shop.myshopify.com", display_name="Demo Shop",
        status="connected", currency="USD", settings={},
    )
    db.add(channel); db.commit()

    def override():
        session = factory()
        try: yield session
        finally: session.close()

    app.dependency_overrides[get_session] = override
    body = json.dumps({
        "id": 999, "created_at": "2026-08-22T12:00:00Z", "total_price": "49.90",
        "currency": "USD", "email": "must-not-be-stored@example.test",
        "line_items": [{"quantity": 2}],
    }, separators=(",", ":")).encode()
    headers = {
        "X-Shopify-Hmac-Sha256": _signature(body),
        "X-Shopify-Shop-Domain": "demo-shop.myshopify.com",
        "X-Shopify-Topic": "orders/create", "X-Shopify-Webhook-Id": "delivery-1",
        "Content-Type": "application/json",
    }
    try:
        client = TestClient(app)
        invalid = client.post("/api/webhooks/shopify", content=body,
                              headers={**headers, "X-Shopify-Hmac-Sha256": "invalid"})
        assert invalid.status_code == 401
        first = client.post("/api/webhooks/shopify", content=body, headers=headers)
        second = client.post("/api/webhooks/shopify", content=body, headers=headers)
        assert first.json() == {"status": "accepted"}
        assert second.json() == {"status": "duplicate"}
    finally:
        app.dependency_overrides.pop(get_session, None)

    verify = factory()
    assert verify.scalar(select(func.count()).select_from(models.WebhookDelivery)) == 1
    metric = verify.scalar(select(models.CommerceDailyMetric))
    assert float(metric.revenue) == 49.9 and metric.units == 2 and metric.orders == 1
    assert metric.costs_complete is False
    assert "must-not-be-stored" not in repr(metric.__dict__)


def test_failed_webhook_delivery_is_reprocessed_on_shopify_retry(monkeypatch):
    _configure(monkeypatch)
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory(); store = crud.get_or_create_dev_store(db)
    channel = models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id="retry-shop.myshopify.com", display_name="Retry Shop",
        status="connected", currency="USD", settings={},
    )
    db.add(channel); db.commit()
    body = json.dumps({
        "id": 1000, "created_at": "2026-08-22T12:00:00Z",
        "total_price": "25.00", "currency": "USD", "line_items": [],
    }, separators=(",", ":")).encode()
    db.add(models.WebhookDelivery(
        store_id=store.id, provider="shopify", delivery_id="retry-delivery-1",
        topic="orders/create", payload_hash=hashlib.sha256(body).hexdigest(),
        status="failed", error_code="OperationalError",
    ))
    db.commit(); db.close()

    def override():
        session = factory()
        try: yield session
        finally: session.close()

    app.dependency_overrides[get_session] = override
    headers = {
        "X-Shopify-Hmac-Sha256": _signature(body),
        "X-Shopify-Shop-Domain": "retry-shop.myshopify.com",
        "X-Shopify-Topic": "orders/create",
        "X-Shopify-Webhook-Id": "retry-delivery-1",
        "Content-Type": "application/json",
    }
    try:
        response = TestClient(app).post("/api/webhooks/shopify", content=body,
                                        headers=headers)
        assert response.json() == {"status": "accepted"}
    finally:
        app.dependency_overrides.pop(get_session, None)

    verify = factory()
    delivery = verify.scalar(select(models.WebhookDelivery))
    metric = verify.scalar(select(models.CommerceDailyMetric))
    assert delivery.status == "completed" and delivery.error_code is None
    assert float(metric.revenue) == 25.0 and metric.orders == 1


def test_shopify_account_does_not_report_demo_channel_as_connected():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory(); store = crud.get_or_create_dev_store(db)
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify", external_account_id="DEMO-SHOPIFY",
        display_name="Demo Shopify", status="connected", currency="USD", settings={},
    ))
    db.commit()

    def override():
        session = factory()
        try: yield session
        finally: session.close()

    app.dependency_overrides[get_session] = override
    try:
        response = TestClient(app).get("/api/integrations/shopify/account")
        assert response.status_code == 200
        assert response.json() == {"connected": False, "shop": None, "channel_id": None,
                                   "notifications": "pending", "topics": [], "reason": None}
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_owner_can_disconnect_a_shop_before_connecting_another(monkeypatch):
    """Changing stores removes access, but never deletes Shopify-derived history."""
    _configure(monkeypatch)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS", json.dumps({
        "v1": base64.b64encode(b"1" * 32).decode(),
    }))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory(); store = crud.get_or_create_dev_store(db)
    channel = models.ChannelConnection(
        store_id=store.id, provider="shopify", external_account_id="old.myshopify.com",
        display_name="Old", status="connected", currency="USD", settings={},
    )
    db.add(channel); db.commit(); db.refresh(channel)
    db.add(models.CommerceDailyMetric(
        store_id=store.id, channel_id=channel.id, metric_date=dt.date(2026, 9, 1),
        revenue=12, refunds=0, fees=0, landed_cogs=0, advertising_spend=0,
        orders=1, units=1, sessions=0, currency="USD", source="shopify_sync",
        costs_complete=False,
    ))
    db.commit()
    credentials.store_credential(db, store_id=store.id,
                                 provider=shopify.credential_provider("old.myshopify.com"),
                                 secret="shpat_old")
    db.close()

    class RemovingClient:
        def __init__(self, *_args, **_kwargs): pass
        def remove_operator_webhook_subscriptions(self): pass
        def close(self): pass

    monkeypatch.setattr(shopify, "AdminGraphQLClient", RemovingClient)

    def override():
        session = factory()
        try: yield session
        finally: session.close()

    app.dependency_overrides[get_session] = override
    try:
        response = TestClient(app).post("/api/integrations/shopify/disconnect")
        assert response.status_code == 200
        assert response.json()["connected"] is False
    finally:
        app.dependency_overrides.pop(get_session, None)

    verify = factory()
    channel = verify.scalar(select(models.ChannelConnection))
    assert channel.status == "disconnected"
    assert channel.settings["webhook_setup"] == "disconnected"
    assert credentials.load_credential(
        verify, store_id=store.id, provider=shopify.credential_provider("old.myshopify.com")
    ) is None
    assert verify.scalar(select(func.count()).select_from(models.CommerceDailyMetric)) == 1
    assert "shopify.disconnected" in [event.action for event in verify.scalars(select(models.AuditEvent))]
    verify.close()


def test_disconnected_shopify_channel_rejects_late_webhooks(monkeypatch):
    _configure(monkeypatch)
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory(); store = crud.get_or_create_dev_store(db)
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify", external_account_id="old.myshopify.com",
        display_name="Old", status="disconnected", currency="USD", settings={},
    ))
    db.commit(); db.close()

    def override():
        session = factory()
        try: yield session
        finally: session.close()

    app.dependency_overrides[get_session] = override
    body = b'{"id": 1}'
    headers = {
        "X-Shopify-Hmac-Sha256": _signature(body),
        "X-Shopify-Shop-Domain": "old.myshopify.com",
        "X-Shopify-Topic": "orders/create", "X-Shopify-Webhook-Id": "late-1",
    }
    try:
        assert TestClient(app).post("/api/webhooks/shopify", content=body,
                                    headers=headers).status_code == 404
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_oauth_connection_survives_optional_webhook_registration_failure(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("SHOPIFY_WEBHOOK_URI", "https://app.example.test/webhooks/shopify")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS", json.dumps({
        "v1": base64.b64encode(b"1" * 32).decode(),
    }))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory(); store = crud.get_or_create_dev_store(db)
    authorization_url = shopify.create_authorization(
        db, store_id=store.id, actor_id=store.user_id, shop="safe.myshopify.com"
    )
    state = parse_qs(urlparse(authorization_url).query)["state"][0]

    class FailingClient:
        def __init__(self, *_args, **_kwargs): pass
        def ensure_webhook_subscriptions(self, _callback_uri):
            raise shopify.ShopifyAPIError("webhooks unavailable")
        def close(self): pass

    monkeypatch.setattr(shopify, "AdminGraphQLClient", FailingClient)
    monkeypatch.setattr(shopify, "exchange_code", lambda *_args, **_kwargs: {
        "access_token": "test-access-token", "scope": "read_orders,read_products",
    })

    def override():
        session = factory()
        try: yield session
        finally: session.close()

    app.dependency_overrides[get_session] = override
    params = {
        "code": "code-1", "shop": "safe.myshopify.com", "state": state,
        "timestamp": "1",
    }
    message = urlencode(sorted(params.items()))
    params["hmac"] = hmac.new(b"shopify-secret", message.encode(), hashlib.sha256).hexdigest()
    try:
        response = TestClient(app).get("/api/integrations/shopify/callback", params=params)
        assert response.status_code == 200
        assert response.json()["connected"] is True
    finally:
        app.dependency_overrides.pop(get_session, None)

    verify = factory()
    channel = verify.scalar(select(models.ChannelConnection).where(
        models.ChannelConnection.external_account_id == "safe.myshopify.com"
    ))
    assert channel.status == "connected"
    assert channel.settings["webhook_setup"] == "pending_configuration"


def test_browser_oauth_callback_returns_to_the_connection_screen(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.test")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS", json.dumps({
        "v1": base64.b64encode(b"1" * 32).decode(),
    }))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory(); store = crud.get_or_create_dev_store(db)
    authorization_url = shopify.create_authorization(
        db, store_id=store.id, actor_id=store.user_id, shop="return.myshopify.com"
    )
    state = parse_qs(urlparse(authorization_url).query)["state"][0]

    class Client:
        def __init__(self, *_args, **_kwargs): pass
        def ensure_webhook_subscriptions(self, _callback_uri): return []
        def close(self): pass

    monkeypatch.setattr(shopify, "AdminGraphQLClient", Client)
    monkeypatch.setattr(shopify, "exchange_code", lambda *_args, **_kwargs: {
        "access_token": "test-access-token", "scope": "read_orders",
    })

    def override():
        session = factory()
        try: yield session
        finally: session.close()

    app.dependency_overrides[get_session] = override
    params = {"code": "code-1", "shop": "return.myshopify.com", "state": state, "timestamp": "1"}
    message = urlencode(sorted(params.items()))
    params["hmac"] = hmac.new(b"shopify-secret", message.encode(), hashlib.sha256).hexdigest()
    try:
        response = TestClient(app).get("/api/integrations/shopify/callback", params=params,
                                       headers={"Accept": "text/html"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "https://app.example.test/integrations/shopify?connected=1"
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_browser_oauth_failure_returns_to_the_connection_screen(monkeypatch, caplog):
    """A browser failure must not leave a merchant reading API JSON."""
    _configure(monkeypatch)
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.test")
    response = TestClient(app).get(
        "/api/integrations/shopify/callback?shop=one.myshopify.com&state=bad",
        headers={"Accept": "text/html"}, follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == (
        "https://app.example.test/integrations/shopify?error=authorization_failed"
    )
    assert "Shopify OAuth authorization rejected: callback_signature" in caplog.text


def test_a_shop_held_by_another_tenant_says_so(monkeypatch, caplog):
    """One person, two email addresses, two tenants: the second must be told to
    sign in as the first, not to try again."""
    _configure(monkeypatch)
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.test")
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    first = crud.get_or_create_dev_store(db)
    db.add(models.ChannelConnection(
        store_id=first.id, provider="shopify", external_account_id="held.myshopify.com",
        display_name="held", status="disconnected", currency="USD", settings={},
    ))
    db.commit()
    other_user = models.User(email="second@example.test")
    db.add(other_user); db.flush()
    second = models.Store(user_id=other_user.id)
    db.add(second); db.commit()
    authorization_url = shopify.create_authorization(
        db, store_id=second.id, actor_id=other_user.id, shop="held.myshopify.com"
    )
    state = parse_qs(urlparse(authorization_url).query)["state"][0]

    def override():
        session = factory()
        try: yield session
        finally: session.close()

    app.dependency_overrides[get_session] = override
    params = {"code": "code-1", "shop": "held.myshopify.com", "state": state, "timestamp": "1"}
    message = urlencode(sorted(params.items()))
    params["hmac"] = hmac.new(b"shopify-secret", message.encode(), hashlib.sha256).hexdigest()
    try:
        response = TestClient(app).get("/api/integrations/shopify/callback", params=params,
                                       headers={"Accept": "text/html"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == (
            "https://app.example.test/integrations/shopify?error=already_connected"
        )
        assert "Shopify OAuth authorization rejected: already_connected" in caplog.text
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_oauth_callback_redeclares_tenant_after_credential_commit():
    """RLS settings are transaction-local; both later writes need a declaration."""
    import inspect
    from app.routers import shopify as shopify_router

    source = inspect.getsource(shopify_router.shopify_callback)
    credential_write = source.index("credentials.store_credential")
    audit_write = source.index("crud.append_audit_event")
    assert "declare_tenant" in source[credential_write:audit_write]
    assert "commit=False" in source[audit_write:]


def test_oauth_does_not_commit_connected_channel_before_credential():
    import inspect
    from app.routers import shopify as shopify_router

    source = inspect.getsource(shopify_router.shopify_callback)
    channel_write = source.index("db.add(channel)")
    credential_write = source.index("credentials.store_credential")
    assert "db.flush()" in source[channel_write:credential_write]
    assert "db.commit()" not in source[channel_write:credential_write]


def test_admin_graphql_order_pagination_never_requests_customer_fields(monkeypatch):
    _configure(monkeypatch)

    class Response:
        status_code = 200
        def json(self):
            return {"data": {"orders": {"nodes": [], "pageInfo": {
                "hasNextPage": False, "endCursor": None,
            }}}}

    class Client:
        def __init__(self): self.request = None
        def post(self, url, **kwargs): self.request = (url, kwargs); return Response()

    transport = Client()
    client = shopify.AdminGraphQLClient("safe.myshopify.com", "token", client=transport)
    assert list(client.order_pages(created_at_min=__import__("datetime").datetime.now(
        __import__("datetime").timezone.utc))) == [[]]
    query = transport.request[1]["json"]["query"]
    assert "customer" not in query.lower() and "email" not in query.lower()
    assert transport.request[1]["headers"]["X-Shopify-Access-Token"] == "token"


def test_refund_webhook_reduces_profit_once(monkeypatch):
    _configure(monkeypatch)
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory(); store = crud.get_or_create_dev_store(db)
    channel = models.ChannelConnection(
        store_id=store.id, provider="shopify", external_account_id="refund.myshopify.com",
        display_name="Refund", status="connected", currency="USD", settings={},
    )
    db.add(channel); db.commit()
    def override():
        session = factory()
        try: yield session
        finally: session.close()
    app.dependency_overrides[get_session] = override
    body = json.dumps({"created_at": "2026-08-22T12:00:00Z", "transactions": [
        {"kind": "refund", "status": "success", "amount": "12.50", "currency": "USD"},
        {"kind": "refund", "status": "failure", "amount": "99", "currency": "USD"},
    ]}, separators=(",", ":")).encode()
    headers = {"X-Shopify-Hmac-Sha256": _signature(body),
               "X-Shopify-Shop-Domain": "refund.myshopify.com",
               "X-Shopify-Topic": "refunds/create", "X-Shopify-Webhook-Id": "refund-1",
               "Content-Type": "application/json"}
    try:
        client = TestClient(app)
        assert client.post("/api/webhooks/shopify", content=body, headers=headers).status_code == 200
        assert client.post("/api/webhooks/shopify", content=body, headers=headers).json() == {"status": "duplicate"}
    finally:
        app.dependency_overrides.pop(get_session, None)
    row = factory().scalar(select(models.CommerceDailyMetric))
    assert float(row.refunds) == 12.5


def _connected_shop_session(monkeypatch, settings):
    """A tenant with one connected Shopify shop and a stored access token."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS",
                       json.dumps({"v1": base64.b64encode(b"b" * 32).decode()}))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    store = crud.get_or_create_dev_store(db)
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id="live-store.myshopify.com", display_name="live-store",
        status="connected", currency="USD", settings=settings,
    ))
    db.commit()
    from app import credentials
    credentials.store_credential(db, store_id=store.id,
                                 provider=shopify.credential_provider("live-store.myshopify.com"),
                                 secret="shpat_token")
    return factory, db


def _override(factory):
    def override():
        session = factory()
        try:
            yield session
        finally:
            session.close()
    return override


def test_failed_webhook_registration_keeps_the_reason(monkeypatch):
    """A shop that cannot subscribe must still connect, and say why it didn't."""
    from app.routers import shopify as shopify_routes

    def failing(self, callback_uri):
        raise shopify.ShopifyAPIError(
            "Shopify Admin API returned GraphQL errors: ACCESS_DENIED: "
            "app is not approved for read_orders")

    monkeypatch.setenv("SHOPIFY_WEBHOOK_URI", "https://tunnel.test/api/webhooks/shopify")
    monkeypatch.setattr(shopify.AdminGraphQLClient, "ensure_webhook_subscriptions", failing)
    monkeypatch.setattr(shopify.AdminGraphQLClient, "close", lambda self: None)

    settings = shopify_routes._register_shopify_webhooks(
        {"scopes": ["read_orders"]}, shop="live-store.myshopify.com", access_token="shpat_token"
    )
    assert settings["webhooks"] == []
    assert settings["webhook_setup"] == "pending_configuration"
    assert "ACCESS_DENIED" in settings["webhook_error"]
    assert settings["scopes"] == ["read_orders"]        # existing settings survive


def test_retrying_notifications_activates_them(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("SHOPIFY_WEBHOOK_URI", "https://tunnel.test/api/webhooks/shopify")
    monkeypatch.setattr(
        shopify.AdminGraphQLClient, "ensure_webhook_subscriptions",
        lambda self, uri: ["ORDERS_CREATE", "REFUNDS_CREATE", "APP_UNINSTALLED"])
    monkeypatch.setattr(shopify.AdminGraphQLClient, "close", lambda self: None)

    factory, db = _connected_shop_session(monkeypatch, {
        "webhooks": [], "webhook_setup": "pending_configuration",
        "webhook_error": "ACCESS_DENIED: app is not approved",
    })
    app.dependency_overrides[get_session] = _override(factory)
    try:
        client = TestClient(app)
        before = client.get("/api/integrations/shopify/account").json()
        assert before["notifications"] == "pending"
        assert "ACCESS_DENIED" in before["reason"]

        retried = client.post("/api/integrations/shopify/notifications/retry")
        assert retried.status_code == 200
        assert retried.json()["notifications"] == "active"
        assert retried.json()["topics"] == ["ORDERS_CREATE", "REFUNDS_CREATE", "APP_UNINSTALLED"]
        assert retried.json()["reason"] is None

        assert client.get("/api/integrations/shopify/account").json()["notifications"] == "active"
    finally:
        app.dependency_overrides.clear()

    audited = db.scalar(select(func.count()).select_from(models.AuditEvent).where(
        models.AuditEvent.action == "shopify.notifications.retry"))
    assert audited == 1


def test_retrying_notifications_without_a_shop_is_rejected(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.dependency_overrides[get_session] = _override(factory)
    try:
        response = TestClient(app).post("/api/integrations/shopify/notifications/retry")
        assert response.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_a_restarted_tunnel_shows_notifications_as_stale(monkeypatch):
    """The failure this catches is otherwise silent: Shopify retries into the void."""
    monkeypatch.setenv("SHOPIFY_WEBHOOK_URI", "https://new-tunnel.test/api/webhooks/shopify")
    factory, _ = _connected_shop_session(monkeypatch, {
        "webhooks": ["ORDERS_CREATE"],
        "webhook_uri": "https://old-tunnel.test/api/webhooks/shopify",
    })
    app.dependency_overrides[get_session] = _override(factory)
    try:
        body = TestClient(app).get("/api/integrations/shopify/account").json()
    finally:
        app.dependency_overrides.clear()

    assert body["notifications"] == "stale"
    assert "old-tunnel.test" in body["reason"] and "new-tunnel.test" in body["reason"]


def test_notifications_are_active_when_they_point_where_we_listen(monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_URI", "https://tunnel.test/api/webhooks/shopify")
    factory, _ = _connected_shop_session(monkeypatch, {
        "webhooks": ["ORDERS_CREATE"],
        "webhook_uri": "https://tunnel.test/api/webhooks/shopify",
    })
    app.dependency_overrides[get_session] = _override(factory)
    try:
        body = TestClient(app).get("/api/integrations/shopify/account").json()
    finally:
        app.dependency_overrides.clear()
    assert body["notifications"] == "active"


def test_subscriptions_aimed_elsewhere_are_removed_before_resubscribing(monkeypatch):
    """Otherwise every tunnel restart leaves another dead subscription behind."""
    deleted, created = [], []

    def fake_execute(self, query, variables):
        if "webhookSubscriptions(first" in query:
            return {"webhookSubscriptions": {"edges": [
                {"node": {"id": "gid://1", "topic": "ORDERS_CREATE",
                          "endpoint": {"callbackUrl": "https://old.test/hook"}}},
                {"node": {"id": "gid://2", "topic": "REFUNDS_CREATE",
                          "endpoint": {"callbackUrl": "https://new.test/hook"}}},
            ]}}
        if "webhookSubscriptionDelete" in query:
            deleted.append(variables["id"])
            return {"webhookSubscriptionDelete": {"userErrors": []}}
        created.append(variables["topic"])
        return {"webhookSubscriptionCreate": {"userErrors": []}}

    monkeypatch.setattr(shopify.AdminGraphQLClient, "execute", fake_execute)
    client = shopify.AdminGraphQLClient("live-store.myshopify.com", "token")
    topics = client.ensure_webhook_subscriptions("https://new.test/hook")

    assert deleted == ["gid://1"]          # the stale one only
    assert topics == ["ORDERS_CREATE", "REFUNDS_CREATE", "APP_UNINSTALLED"]
    assert created == topics


def test_a_failure_to_list_does_not_stop_us_subscribing(monkeypatch):
    created = []

    def fake_execute(self, query, variables):
        if "webhookSubscriptions(first" in query:
            raise shopify.ShopifyAPIError("listing not permitted")
        created.append(variables["topic"])
        return {"webhookSubscriptionCreate": {"userErrors": []}}

    monkeypatch.setattr(shopify.AdminGraphQLClient, "execute", fake_execute)
    client = shopify.AdminGraphQLClient("live-store.myshopify.com", "token")
    assert len(client.ensure_webhook_subscriptions("https://new.test/hook")) == 3
    assert len(created) == 3


def test_a_plain_http_callback_is_refused():
    client = shopify.AdminGraphQLClient("live-store.myshopify.com", "token")
    with pytest.raises(shopify.ShopifyAPIError):
        client.ensure_webhook_subscriptions("http://insecure.test/hook")


# --- A process that dies mid-delivery ----------------------------------------

def _shop_with_delivery(monkeypatch, *, status, age_seconds, delivery_id="crash-1"):
    """A shop whose only delivery is stuck in `status`, `age_seconds` old."""
    _configure(monkeypatch)
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    store = crud.get_or_create_dev_store(db)
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id="crash-shop.myshopify.com", display_name="Crash Shop",
        status="connected", currency="USD", settings={}))
    db.commit()
    body = json.dumps({
        "id": 4242, "created_at": "2026-08-22T12:00:00Z",
        "total_price": "30.00", "currency": "USD",
        "line_items": [{"quantity": 2}],
    }, separators=(",", ":")).encode()
    db.add(models.WebhookDelivery(
        store_id=store.id, provider="shopify", delivery_id=delivery_id,
        topic="orders/create", payload_hash=hashlib.sha256(body).hexdigest(),
        status=status,
        received_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age_seconds),
    ))
    db.commit()
    db.close()
    return factory, body, delivery_id


def _deliver(factory, body, delivery_id):
    def override():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override
    try:
        return TestClient(app).post("/api/webhooks/shopify", content=body, headers={
            "X-Shopify-Hmac-Sha256": _signature(body),
            "X-Shopify-Shop-Domain": "crash-shop.myshopify.com",
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Webhook-Id": delivery_id,
            "Content-Type": "application/json",
        })
    finally:
        app.dependency_overrides.clear()


def test_an_order_is_not_lost_when_the_process_dies_mid_delivery(monkeypatch):
    """The dangerous case: the row says `processing` because nobody survived to
    say otherwise. Answering "duplicate" tells Shopify we handled an order we
    never recorded, and it stops retrying."""
    factory, body, delivery_id = _shop_with_delivery(
        monkeypatch, status="processing", age_seconds=3600)

    response = _deliver(factory, body, delivery_id)
    assert response.json() == {"status": "accepted"}

    db = factory()
    metric = db.scalar(select(models.CommerceDailyMetric))
    assert metric is not None, "the order was lost"
    assert float(metric.revenue) == 30.00
    assert metric.orders == 1
    assert db.scalar(select(models.WebhookDelivery)).status == "completed"
    db.close()


def test_a_concurrent_redelivery_is_refused_without_claiming_it_was_handled(monkeypatch):
    """A delivery that started moments ago may still be in flight, and running it
    again would double-count the order. But "duplicate" is a 200, and a 200 ends
    the matter: Shopify stops retrying a delivery it believes was handled, so if
    the attempt in flight never finishes, the order is gone with nothing marked
    failed anywhere. That is how order #1002 disappeared — its container was
    replaced mid-delivery and the retry 184 seconds later was answered 200.

    So the order is still not written twice, and Shopify is still told to come
    back: by then the row is either completed, or stale enough to reclaim.
    """
    factory, body, delivery_id = _shop_with_delivery(
        monkeypatch, status="processing", age_seconds=2)

    response = _deliver(factory, body, delivery_id)
    assert response.status_code == 409, "a 200 would end Shopify's retries"

    db = factory()
    assert db.scalar(select(models.CommerceDailyMetric)) is None, "must not double-count"
    db.close()


def test_a_completed_delivery_is_never_reprocessed(monkeypatch):
    factory, body, delivery_id = _shop_with_delivery(
        monkeypatch, status="completed", age_seconds=86400)

    assert _deliver(factory, body, delivery_id).json() == {"status": "duplicate"}

    db = factory()
    assert db.scalar(select(models.CommerceDailyMetric)) is None
    db.close()


def test_the_staleness_threshold_is_configurable(monkeypatch):
    """Two minutes suits a handler that finishes in under a second; another
    platform, or a slower handler, may need a different window."""
    from app.routers import shopify as shopify_routes

    monkeypatch.setattr(shopify_routes, "STALE_PROCESSING_SECONDS", 1)
    factory, body, delivery_id = _shop_with_delivery(
        monkeypatch, status="processing", age_seconds=5)
    assert _deliver(factory, body, delivery_id).json() == {"status": "accepted"}


def test_webhook_receipt_and_metrics_share_one_transaction():
    """A commit/refresh between receipt and processing breaks PostgreSQL RLS."""
    import inspect
    from app.routers import shopify as shopify_router

    source = inspect.getsource(shopify_router.shopify_webhook)
    receipt = source.index("db.add(delivery)")
    processing = source.index('if topic == "orders/create"')
    assert "db.flush()" in source[receipt:processing]
    assert "db.commit()" not in source[receipt:processing]
    assert "db.refresh(delivery)" not in source
