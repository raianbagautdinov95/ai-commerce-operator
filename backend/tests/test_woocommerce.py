import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import woocommerce
from app.db import crud, models
from app.db.models import Base
from app.db.session import get_session
from app.main import app


def _configure(monkeypatch):
    monkeypatch.setenv("WOOCOMMERCE_CALLBACK_URI", "https://app.example.test/api/integrations/woocommerce/callback")
    monkeypatch.setenv("WOOCOMMERCE_RETURN_URI", "https://app.example.test/integrations/woocommerce")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS", json.dumps({
        "v1": base64.b64encode(b"w" * 32).decode(),
    }))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")


def test_store_url_blocks_ssrf_targets():
    assert woocommerce.normalize_store_url("store.example.com/path") == "https://store.example.com"
    for value in ("http://store.example.com", "https://localhost", "https://127.0.0.1",
                  "https://10.0.0.2", "https://user:pass@store.example.com"):
        with pytest.raises(woocommerce.WooCommerceAuthorizationError):
            woocommerce.normalize_store_url(value)


def test_authorization_is_read_only_and_callback_is_single_use(monkeypatch):
    _configure(monkeypatch)
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory(); store = crud.get_or_create_dev_store(db)
    url = woocommerce.create_authorization(
        db, store_id=store.id, actor_id=store.user_id, store_url="shop.example.com"
    )
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["read"]
    payload = {"user_id": query["user_id"][0], "consumer_key": "ck_public",
               "consumer_secret": "cs_private", "key_permissions": "read"}
    state, site, key, secret = woocommerce.consume_callback(db, payload)
    assert state.store_id == store.id and site == "https://shop.example.com"
    assert key == "ck_public" and secret == "cs_private"
    with pytest.raises(woocommerce.WooCommerceAuthorizationError):
        woocommerce.consume_callback(db, payload)


def test_callback_encrypts_credentials_and_rejects_write_permission(monkeypatch):
    _configure(monkeypatch)
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory(); store = crud.get_or_create_dev_store(db)
    url = woocommerce.create_authorization(
        db, store_id=store.id, actor_id=store.user_id, store_url="shop.example.com"
    )
    state = parse_qs(urlparse(url).query)["user_id"][0]

    def override():
        session = factory()
        try: yield session
        finally: session.close()

    app.dependency_overrides[get_session] = override
    try:
        client = TestClient(app)
        denied = client.post("/api/integrations/woocommerce/callback", json={
            "user_id": state, "consumer_key": "ck_key", "consumer_secret": "cs_secret",
            "key_permissions": "read_write",
        })
        assert denied.status_code == 400
        accepted = client.post("/api/integrations/woocommerce/callback", json={
            "user_id": state, "consumer_key": "ck_key", "consumer_secret": "cs_secret",
            "key_permissions": "read",
        })
        assert accepted.status_code == 200 and accepted.json()["connected"] is True
    finally:
        app.dependency_overrides.pop(get_session, None)
    verify = factory(); credential = verify.scalar(select(models.IntegrationCredential))
    assert credential is not None
    assert "ck_key" not in credential.encrypted_secret and "cs_secret" not in credential.encrypted_secret
    channel = verify.scalar(select(models.ChannelConnection))
    assert channel.settings["permissions"] == "read"


def test_read_client_requests_only_non_pii_fields(monkeypatch):
    monkeypatch.setattr(woocommerce.socket, "getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("93.184.216.34", 443)),
    ])

    class Response:
        status_code = 200
        headers = {"X-WP-TotalPages": "1"}
        def json(self): return []

    class Client:
        def __init__(self): self.request = None
        def get(self, url, **kwargs): self.request = (url, kwargs); return Response()

    transport = Client()
    client = woocommerce.ReadOnlyClient(
        "shop.example.com", "ck_key", "cs_secret", client=transport
    )
    pages = list(client.order_pages(
        after=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    ))
    assert pages == [[]]
    fields = transport.request[1]["params"]["_fields"]
    assert "billing" not in fields and "shipping" not in fields and "customer" not in fields
    assert transport.request[1]["auth"] == ("ck_key", "cs_secret")


def test_read_client_rejects_private_dns_resolution(monkeypatch):
    monkeypatch.setattr(woocommerce.socket, "getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("10.0.0.8", 443)),
    ])
    client = woocommerce.ReadOnlyClient("shop.example.com", "ck_key", "cs_secret", client=object())
    with pytest.raises(woocommerce.WooCommerceAuthorizationError):
        next(client.order_pages(after=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc)))
