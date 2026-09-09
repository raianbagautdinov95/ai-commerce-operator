"""
Tests for the Amazon Ads API client.

This module has never met a live Amazon account, so these tests are the only
thing standing behind it. They lean on the parts that will still be true when it
does: the write path is never replayed, a failed report raises rather than
returning empty, and the parsing maps onto what the PPC engine already eats.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import base64
import datetime as dt
import gzip
import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import amazon_ads_api, amazon_sp_api, credentials
from app.db import crud
from app.db.models import Base

REPORT_ROWS = [
    {"searchTerm": "cheap dog bowl", "keyword": "dog bowl", "matchType": "BROAD",
     "campaignId": 111, "campaignName": "SP - bowls", "keywordId": 999, "adGroupId": 222,
     "impressions": 4100, "clicks": 96, "cost": 120.50, "purchases7d": 0, "sales7d": 0.0},
    {"searchTerm": "stainless dog bowl", "keyword": "dog bowl", "matchType": "PHRASE",
     "campaignId": 111, "campaignName": "SP - bowls", "keywordId": 999, "adGroupId": 222,
     "impressions": 2200, "clicks": 60, "cost": 48.00, "purchases7d": 9, "sales7d": 216.0},
    {"searchTerm": "", "keyword": "dog bowl", "campaignId": 111},   # unusable, dropped
]


def _configure(monkeypatch):
    monkeypatch.setenv("ADS_API_CLIENT_ID", "ads-client")
    monkeypatch.setenv("ADS_API_CLIENT_SECRET", "ads-secret")
    monkeypatch.setenv("SP_API_APPLICATION_ID", "amzn1.sellerapps.app.test")
    monkeypatch.setenv("SP_API_CLIENT_ID", "client-id")
    monkeypatch.setenv("SP_API_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SP_API_REDIRECT_URI", "https://example.test/amazon/callback")
    monkeypatch.setenv("SP_API_AUTHORIZATION_URL", "https://sellercentral.example/authorize")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS",
                       json.dumps({"v1": base64.b64encode(b"c" * 32).decode()}))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")


def _fresh_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_parsing_maps_a_report_onto_the_ppc_engine_input(monkeypatch):
    rows = amazon_ads_api.parse_search_term_rows(REPORT_ROWS)
    assert len(rows) == 2                       # the term-less row is dropped

    wasteful = rows[0]
    assert wasteful.search_term == "cheap dog bowl"
    assert wasteful.spend == 120.50 and wasteful.orders == 0 and wasteful.sales == 0.0
    assert wasteful.to_keyword_input() == {
        "keyword": "cheap dog bowl", "clicks": 96, "spend": 120.50, "sales": 0.0,
        "orders": 0, "impressions": 4100, "match_type": "broad",
    }


def test_parsed_rows_feed_the_ppc_engine_and_it_finds_the_waste(monkeypatch):
    """The point of the whole read path: real spend reaching the existing engine."""
    from app import ppc_engine

    rows = amazon_ads_api.parse_search_term_rows(REPORT_ROWS)
    summary, findings = ppc_engine.analyze(ppc_engine.CampaignInput(
        name="SP - bowls", break_even_acos=0.30,
        keywords=[ppc_engine.KeywordInput(**row.to_keyword_input()) for row in rows],
    ))
    negated = [f for f in findings if f.action == "NEGATE"]
    assert any(f.keyword == "cheap dog bowl" for f in negated)
    assert summary.wasted_spend >= 120.50


def test_a_missing_search_term_column_does_not_crash_the_parser():
    rows = amazon_ads_api.parse_search_term_rows([
        {"searchTerm": "x", "cost": None, "clicks": "not a number", "purchases7d": None},
    ])
    assert rows[0].spend == 0.0 and rows[0].clicks == 0


def test_gzipped_and_plain_reports_both_download(monkeypatch):
    _configure(monkeypatch)
    body = json.dumps(REPORT_ROWS).encode()

    for payload in (gzip.compress(body), body):
        def handler(request, payload=payload):
            return httpx.Response(200, content=payload)

        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            client = amazon_ads_api.AdsClient("token", profile_id="1", client=http)
            rows = client.download_report("https://s3.example.test/report.gz")
        assert len(rows) == 2


def test_a_report_wrapped_in_an_envelope_is_unwrapped(monkeypatch):
    _configure(monkeypatch)

    def handler(request):
        return httpx.Response(200, content=json.dumps({"rows": REPORT_ROWS}).encode())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = amazon_ads_api.AdsClient("token", profile_id="1", client=http)
        assert len(client.download_report("https://s3.example.test/r")) == 2


def test_the_full_read_path_requests_polls_and_downloads(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(amazon_ads_api.time, "sleep", lambda _: None)
    calls = {"create": 0, "status": 0, "download": 0}

    def handler(request):
        if request.url.path == "/reporting/reports" and request.method == "POST":
            calls["create"] += 1
            body = json.loads(request.content)
            assert body["configuration"]["reportTypeId"] == "spSearchTerm"
            assert body["startDate"] < body["endDate"]
            assert request.headers["Amazon-Advertising-API-Scope"] == "42"
            assert request.headers["Amazon-Advertising-API-ClientId"] == "ads-client"
            return httpx.Response(200, json={"reportId": "rep-1", "status": "PENDING"})
        if request.url.path == "/reporting/reports/rep-1":
            calls["status"] += 1
            if calls["status"] < 3:
                return httpx.Response(200, json={"status": "PROCESSING"})
            return httpx.Response(200, json={"status": "COMPLETED",
                                             "url": "https://s3.example.test/r.gz"})
        calls["download"] += 1
        return httpx.Response(200, content=gzip.compress(json.dumps(REPORT_ROWS).encode()))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = amazon_ads_api.AdsClient("token", profile_id="42", client=http)
        rows = client.search_terms(days=14, poll_s=0)

    assert [r.search_term for r in rows] == ["cheap dog bowl", "stainless dog bowl"]
    assert calls == {"create": 1, "status": 3, "download": 1}


def test_a_failed_report_raises_rather_than_returning_nothing(monkeypatch):
    _configure(monkeypatch)

    def handler(request):
        return httpx.Response(200, json={"status": "FAILURE",
                                         "failureReason": "date range too large"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = amazon_ads_api.AdsClient("token", profile_id="1", client=http)
        with pytest.raises(amazon_ads_api.AdsAPIError) as exc:
            client.await_report("rep-1", poll_s=0)
    assert "date range too large" in str(exc.value)


def test_a_report_that_never_finishes_times_out(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(amazon_ads_api.time, "sleep", lambda _: None)

    def handler(request):
        return httpx.Response(200, json={"status": "PROCESSING"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = amazon_ads_api.AdsClient("token", profile_id="1", client=http)
        with pytest.raises(amazon_ads_api.AdsReportTimeout):
            client.await_report("rep-1", timeout_s=0, poll_s=0)


def test_reads_are_retried_and_writes_are_not(monkeypatch):
    """A replayed negative-keyword create would duplicate negatives in the account."""
    _configure(monkeypatch)
    monkeypatch.setattr(amazon_ads_api.time, "sleep", lambda _: None)
    seen = {"GET": 0, "POST": 0}

    def handler(request):
        seen[request.method] = seen.get(request.method, 0) + 1
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = amazon_ads_api.AdsClient("token", profile_id="1", client=http)
        with pytest.raises(amazon_ads_api.AdsAPIError):
            client.profiles()
        with pytest.raises(amazon_ads_api.AdsAPIError):
            client.create_negative_keywords([
                {"campaignId": "1", "adGroupId": "2", "keywordText": "cheap",
                 "matchType": "NEGATIVE_EXACT"}])

    assert seen["GET"] == 4          # retried to the attempt limit
    assert seen["POST"] == 1         # sent exactly once


def test_an_incomplete_negative_keyword_is_refused_before_any_request(monkeypatch):
    _configure(monkeypatch)
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = amazon_ads_api.AdsClient("token", profile_id="1", client=http)
        with pytest.raises(ValueError) as exc:
            client.create_negative_keywords([{"campaignId": "1", "keywordText": "cheap"}])
        assert client.create_negative_keywords([]) == {"negativeKeywords": []}

    assert "adGroupId" in str(exc.value) and "matchType" in str(exc.value)
    assert sent == []                # nothing left the process


def test_profiles_are_read_from_either_response_shape(monkeypatch):
    _configure(monkeypatch)
    raw = [{"profileId": 42, "countryCode": "DE", "currencyCode": "EUR",
            "accountInfo": {"marketplaceStringId": "A1PA6795UKMFR9", "name": "Test DE"}}]

    for payload in (raw, {"profiles": raw}):
        def handler(request, payload=payload):
            return httpx.Response(200, json=payload)

        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            profiles = amazon_ads_api.AdsClient("token", client=http).profiles()
        assert profiles[0].profile_id == "42"
        assert profiles[0].country_code == "DE" and profiles[0].currency == "EUR"
        assert profiles[0].marketplace_id == "A1PA6795UKMFR9"


def test_rejected_credentials_are_reported_as_an_auth_failure(monkeypatch):
    _configure(monkeypatch)

    def handler(request):
        return httpx.Response(401)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(amazon_ads_api.AdsAuthorizationError):
            amazon_ads_api.AdsClient("stale", client=http).profiles()


def test_an_unconnected_store_says_so_plainly(monkeypatch):
    _configure(monkeypatch)
    db = _fresh_session()
    store = crud.get_or_create_dev_store(db)
    with pytest.raises(amazon_ads_api.AdsAuthorizationError) as exc:
        amazon_ads_api.client_for_store(db, store_id=store.id)
    assert "not connected" in str(exc.value)


def test_a_connected_store_gets_a_client_bound_to_its_own_credential(monkeypatch):
    _configure(monkeypatch)
    amazon_sp_api._ACCESS_TOKEN_CACHE.clear()

    def handler(request):
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "ads-access", "expires_in": 3600})
        return httpx.Response(200, json=[])

    db = _fresh_session()
    store = crud.get_or_create_dev_store(db)
    credentials.store_credential(db, store_id=store.id,
                                 provider=amazon_ads_api.CREDENTIAL_PROVIDER,
                                 secret="ads-refresh-token")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = amazon_ads_api.client_for_store(db, store_id=store.id, region="EU",
                                                 profile_id="42", http_client=http)
    assert client.access_token == "ads-access"
    assert client.profile_id == "42"


def test_an_unsupported_region_is_refused(monkeypatch):
    _configure(monkeypatch)
    with pytest.raises(ValueError):
        amazon_ads_api.AdsClient("token", region="ZZ").request("GET", "/v2/profiles")


def test_a_retry_budget_that_would_skip_the_request_is_refused():
    with pytest.raises(ValueError):
        amazon_ads_api.AdsClient("token", max_attempts=0)


# --- read-after-write: proving the account actually changed -------------------

def _negatives_handler(state, monkeypatch):
    """A fake ad group whose negative list only changes when a create succeeds."""
    def handler(request):
        if request.url.path == "/sp/negativeKeywords/list":
            if state.get("list_fails"):
                return httpx.Response(500)
            return httpx.Response(200, json={"negativeKeywords": [
                {"keywordText": t, "negativeKeywordId": f"neg-{i}"}
                for i, t in enumerate(state["negatives"])]})
        if request.url.path == "/sp/negativeKeywords":
            body = json.loads(request.content)
            state["creates"] = state.get("creates", 0) + 1
            if not state.get("create_is_a_lie"):
                for entry in body["negativeKeywords"]:
                    state["negatives"].append(entry["keywordText"])
            return httpx.Response(200, json={"negativeKeywords": [
                {"negativeKeywordId": "neg-new"}]})
        return httpx.Response(404)
    return handler


def test_a_negation_is_only_real_once_read_back_from_the_account(monkeypatch):
    _configure(monkeypatch)
    state = {"negatives": ["free dog bowl"]}
    with httpx.Client(transport=httpx.MockTransport(_negatives_handler(state, monkeypatch))) as http:
        client = amazon_ads_api.AdsClient("t", profile_id="1", client=http)
        outcome = client.apply_negative_keyword(
            campaign_id="111", ad_group_id="222", keyword_text="cheap dog bowl")
    assert outcome.changed and outcome.verified
    assert outcome.evidence_mode == "real"
    assert outcome.external_id == "neg-new"
    assert state["creates"] == 1


def test_an_api_that_accepts_and_does_nothing_is_not_counted(monkeypatch):
    """The quiet failure: 200 on the create, account unchanged."""
    _configure(monkeypatch)
    state = {"negatives": ["free dog bowl"], "create_is_a_lie": True}
    with httpx.Client(transport=httpx.MockTransport(_negatives_handler(state, monkeypatch))) as http:
        client = amazon_ads_api.AdsClient("t", profile_id="1", client=http)
        outcome = client.apply_negative_keyword(
            campaign_id="111", ad_group_id="222", keyword_text="cheap dog bowl")
    assert outcome.evidence_mode == "unverified"
    assert "did not take effect" in outcome.reason


def test_an_already_negated_term_costs_no_write_and_claims_nothing(monkeypatch):
    _configure(monkeypatch)
    state = {"negatives": ["cheap dog bowl"]}
    with httpx.Client(transport=httpx.MockTransport(_negatives_handler(state, monkeypatch))) as http:
        client = amazon_ads_api.AdsClient("t", profile_id="1", client=http)
        outcome = client.apply_negative_keyword(
            campaign_id="111", ad_group_id="222", keyword_text="Cheap Dog Bowl")
    assert outcome.countable is False
    assert "already in place" in outcome.reason
    assert state.get("creates", 0) == 0        # the precondition spent no write


def test_a_read_back_that_fails_leaves_the_change_unproven(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(amazon_ads_api.time, "sleep", lambda _: None)
    state = {"negatives": []}
    calls = {"n": 0}

    def handler(request):
        if request.url.path == "/sp/negativeKeywords/list":
            calls["n"] += 1
            if calls["n"] > 1:            # the post-read fails, the pre-read did not
                return httpx.Response(500)
            return httpx.Response(200, json={"negativeKeywords": []})
        return httpx.Response(200, json={"negativeKeywords": [{"negativeKeywordId": "neg-9"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = amazon_ads_api.AdsClient("t", profile_id="1", client=http)
        outcome = client.apply_negative_keyword(
            campaign_id="111", ad_group_id="222", keyword_text="cheap dog bowl")
    assert outcome.evidence_mode == "unverified"
    assert "could not be read back" in outcome.reason
    assert outcome.external_id == "neg-9"      # recorded, so a human can check
