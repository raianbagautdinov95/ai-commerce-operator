import base64
import json
import uuid
import logging
import gzip
import datetime as dt
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import amazon_sp_api, credentials
from app.db import crud
from app.db.models import Base
from app.main import _OAuthQueryRedactionFilter


def _fresh_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _configure(monkeypatch):
    monkeypatch.setenv("SP_API_APPLICATION_ID", "amzn1.sellerapps.app.test")
    monkeypatch.setenv("SP_API_CLIENT_ID", "client-id")
    monkeypatch.setenv("SP_API_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SP_API_REDIRECT_URI", "https://example.test/amazon/callback")
    monkeypatch.setenv("SP_API_AUTHORIZATION_URL", "https://sellercentral.example/authorize")
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEYS",
        json.dumps({"v1": base64.b64encode(b"a" * 32).decode()}),
    )
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")


def test_oauth_state_is_hashed_and_single_use(monkeypatch):
    _configure(monkeypatch)
    db = _fresh_session()
    store = crud.get_or_create_dev_store(db)
    url = amazon_sp_api.create_authorization(db, store_id=store.id, actor_id=store.user_id)
    state = parse_qs(urlparse(url).query)["state"][0]
    assert state not in repr(db.query(amazon_sp_api.models.OAuthState).first())
    consumed = amazon_sp_api.consume_authorization_state(db, state)
    assert consumed.store_id == store.id
    with pytest.raises(amazon_sp_api.AmazonAuthorizationError):
        amazon_sp_api.consume_authorization_state(db, state)


def test_lwa_code_exchange_does_not_expose_secret_in_error(monkeypatch):
    _configure(monkeypatch)
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "invalid_client"}))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(amazon_sp_api.AmazonAuthorizationError) as error:
            amazon_sp_api.exchange_authorization_code("one-time-code", client=client)
    assert "client-secret" not in str(error.value)
    assert "one-time-code" not in str(error.value)


def test_refresh_and_read_only_api_retry(monkeypatch):
    _configure(monkeypatch)
    calls = {"token": 0, "api": 0}

    def handler(request):
        if request.url.host == "api.amazon.com":
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "access", "expires_in": 3600})
        calls["api"] += 1
        if calls["api"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"payload": {"marketplaceParticipations": []}})

    monkeypatch.setattr(amazon_sp_api.time, "sleep", lambda _: None)
    db = _fresh_session()
    store = crud.get_or_create_dev_store(db)
    credentials.store_credential(
        db, store_id=store.id, provider="amazon-sp-api", secret="refresh-token"
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sp = amazon_sp_api.client_for_store(db, store_id=store.id, region="EU", http_client=client)
        assert sp.marketplace_participations()["payload"]["marketplaceParticipations"] == []
        second = amazon_sp_api.client_for_store(db, store_id=store.id, region="EU", http_client=client)
        assert second.access_token == "access"
    assert calls == {"token": 1, "api": 3}


def test_post_is_not_retried_without_explicit_safety(monkeypatch):
    _configure(monkeypatch)
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, headers={"x-amzn-RequestId": "request-1"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sp = amazon_sp_api.SPAPIClient("access", client=client)
        with pytest.raises(amazon_sp_api.AmazonAPIError):
            sp.request("POST", "/unsafe")
    assert calls == 1


def test_sales_report_flow_and_gzip_document():
    calls = []
    document = gzip.compress(json.dumps({"salesAndTrafficByDate": []}).encode())

    def handler(request):
        calls.append((request.method, str(request.url), request.headers.get("x-amz-access-token")))
        if request.url.host == "download.example.test":
            return httpx.Response(200, content=document)
        if request.method == "POST":
            return httpx.Response(202, json={"reportId": "report-1"})
        if "/documents/" in request.url.path:
            return httpx.Response(200, json={
                "url": "https://download.example.test/report", "compressionAlgorithm": "GZIP"
            })
        return httpx.Response(200, json={
            "processingStatus": "DONE", "reportDocumentId": "document-1"
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sp = amazon_sp_api.SPAPIClient("access", client=client)
        created = sp.create_sales_traffic_report(
            marketplace_id="A1",
            start=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            end=dt.datetime(2026, 1, 8, tzinfo=dt.timezone.utc),
        )
        report = sp.get_report(created["reportId"])
        metadata = sp.get_report_document(report["reportDocumentId"])
        assert sp.download_report_document(metadata) == {"salesAndTrafficByDate": []}
    assert calls[-1][2] is None  # never send the LWA token to the pre-signed host


def test_report_download_error_does_not_expose_presigned_url():
    secret_url = "https://download.example.test/report?signature=secret"
    with httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(403))) as client:
        sp = amazon_sp_api.SPAPIClient("access", client=client)
        with pytest.raises(amazon_sp_api.AmazonAPIError) as error:
            sp.download_report_document({"url": secret_url})
    assert "secret" not in str(error.value)


def test_finances_transactions_pagination_uses_next_token_only():
    requests = []
    def handler(request):
        requests.append(dict(request.url.params))
        if len(requests) == 1:
            return httpx.Response(200, json={"payload": {
                "transactions": [], "nextToken": "next-1"
            }})
        return httpx.Response(200, json={"payload": {"transactions": []}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sp = amazon_sp_api.SPAPIClient("access", client=client)
        pages = list(sp.transaction_pages(
            marketplace_id="A1",
            posted_after=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            posted_before=dt.datetime(2026, 1, 8, tzinfo=dt.timezone.utc),
        ))
    assert len(pages) == 2
    assert requests[0]["marketplaceId"] == "A1"
    assert requests[1] == {"nextToken": "next-1"}


def test_oauth_callback_query_is_redacted_from_access_log():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        (("127.0.0.1", 1), "GET",
         "/api/integrations/amazon/callback?state=secret&spapi_oauth_code=code", "1.1", 200),
        None,
    )
    assert _OAuthQueryRedactionFilter().filter(record)
    # The values go; the parameter names stay, which is the point. A log that
    # says an authorization code was present and unreadable is worth reading;
    # one that erases the whole query says nothing about what happened.
    assert "secret" not in record.args[2]
    assert "code" not in record.args[2].split("spapi_oauth_code=")[1]
    assert "[REDACTED]" in record.args[2]


def test_reconnecting_a_different_account_does_not_reuse_the_cached_token(monkeypatch):
    """A rotated refresh token must not keep serving the previous account's access token."""
    _configure(monkeypatch)
    issued = []

    def handler(request):
        if request.url.host == "api.amazon.com":
            body = parse_qs(request.content.decode())
            token = f"access-for-{body['refresh_token'][0]}"
            issued.append(token)
            return httpx.Response(200, json={"access_token": token, "expires_in": 3600})
        return httpx.Response(200, json={"payload": {"marketplaceParticipations": []}})

    amazon_sp_api._ACCESS_TOKEN_CACHE.clear()
    db = _fresh_session()
    store = crud.get_or_create_dev_store(db)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        credentials.store_credential(
            db, store_id=store.id, provider="amazon-sp-api", secret="refresh-seller-one"
        )
        first = amazon_sp_api.client_for_store(
            db, store_id=store.id, region="EU", http_client=client
        )
        assert first.access_token == "access-for-refresh-seller-one"

        # The same store reconnects to a DIFFERENT Amazon account.
        credentials.store_credential(
            db, store_id=store.id, provider="amazon-sp-api", secret="refresh-seller-two"
        )
        second = amazon_sp_api.client_for_store(
            db, store_id=store.id, region="EU", http_client=client
        )

    assert second.access_token == "access-for-refresh-seller-two"
    assert issued == ["access-for-refresh-seller-one", "access-for-refresh-seller-two"]


def test_unchanged_credential_still_serves_from_cache(monkeypatch):
    _configure(monkeypatch)
    calls = {"token": 0}

    def handler(request):
        if request.url.host == "api.amazon.com":
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "access", "expires_in": 3600})
        return httpx.Response(200, json={"payload": {}})

    amazon_sp_api._ACCESS_TOKEN_CACHE.clear()
    db = _fresh_session()
    store = crud.get_or_create_dev_store(db)
    credentials.store_credential(
        db, store_id=store.id, provider="amazon-sp-api", secret="refresh-token"
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for _ in range(3):
            amazon_sp_api.client_for_store(
                db, store_id=store.id, region="EU", http_client=client
            )
    assert calls["token"] == 1


def test_client_rejects_a_retry_budget_that_would_skip_the_request():
    with pytest.raises(ValueError):
        amazon_sp_api.SPAPIClient("access", max_attempts=0)
