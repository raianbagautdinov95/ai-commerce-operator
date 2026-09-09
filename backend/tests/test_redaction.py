"""
Tests for secret redaction.

Two leaks happened here without a single line that logs a secret. `httpx` puts
the request URL into the message of whatever `raise_for_status` raises, and the
Keepa key travels in that URL; uvicorn writes the request path verbatim, and a
Shopify OAuth callback carries `code` and `hmac` in its path.

So these tests are about the shapes, not the two endpoints that were caught:
anything named like a credential must lose its value wherever it appears, and
everything else must stay readable, because a log that redacts the shop domain
is a log nobody can debug from.
"""
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app import redaction

SECRET = "sk-live-000-not-a-real-key"


# --- what must disappear ----------------------------------------------------

def test_a_keepa_url_does_not_carry_its_key():
    url = f"https://api.keepa.com/search?key={SECRET}&domain=1&term=roller"
    out = redaction.redact(url)
    assert SECRET not in out
    assert "domain=1" in out and "term=roller" in out


def test_a_shopify_callback_loses_code_hmac_and_state():
    path = ("/api/integrations/shopify/callback?code=abc123&hmac=deadbeef"
            "&shop=example.myshopify.com&state=xyz789&timestamp=1700000000")
    out = redaction.redact(path)
    for secret in ("abc123", "deadbeef", "xyz789"):
        assert secret not in out
    # The parts that make a log worth reading survive.
    assert "shop=example.myshopify.com" in out
    assert "timestamp=1700000000" in out


@pytest.mark.parametrize("name", [
    "key", "api_key", "apikey", "token", "access_token", "refresh_token",
    "secret", "client_secret", "password", "code", "hmac", "signature", "state",
])
def test_every_credential_shaped_parameter_is_covered(name):
    assert SECRET not in redaction.redact(f"https://x.test/a?{name}={SECRET}")


def test_the_parameter_name_is_matched_whatever_its_case():
    assert SECRET not in redaction.redact(f"?API_KEY={SECRET}")
    assert SECRET not in redaction.redact(f"?Code={SECRET}")


def test_a_secret_in_the_middle_of_a_sentence_is_still_removed():
    """An exception message is prose with a URL in it, not a tidy query string."""
    message = (f"Client error '401 Unauthorized' for url "
               f"'https://api.keepa.com/token?key={SECRET}'")
    assert SECRET not in redaction.redact(message)


def test_several_secrets_in_one_string_all_go():
    out = redaction.redact(f"?code={SECRET}&keep=yes&token={SECRET}")
    assert SECRET not in out
    assert "keep=yes" in out


# --- what must survive ------------------------------------------------------

def test_ordinary_parameters_are_left_alone():
    url = "https://api.example.test/orders?shop=a.myshopify.com&days=30&limit=100"
    assert redaction.redact(url) == url


def test_text_without_parameters_is_returned_unchanged():
    assert redaction.redact("Worker started with PID 1") == "Worker started with PID 1"


def test_a_non_string_passes_through():
    """The log filter hands it whatever the caller logged."""
    assert redaction.redact(None) is None
    assert redaction.redact(42) == 42


# --- the two places it is actually wired in ---------------------------------

def test_the_log_filter_scrubs_the_access_log_path():
    """Uvicorn logs the path as an argument, not in the message."""
    from app.runtime import _OAuthQueryRedactionFilter

    record = logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname=__file__, lineno=1,
        msg='%s - "%s %s HTTP/1.1" %d',
        args=("127.0.0.1", "GET", f"/api/integrations/shopify/callback?code={SECRET}", 200),
        exc_info=None,
    )
    assert _OAuthQueryRedactionFilter().filter(record) is True
    assert SECRET not in record.getMessage()


def test_the_log_filter_scrubs_a_message_that_carries_a_url():
    """A client's traceback reaches the log as the message itself."""
    from app.runtime import _OAuthQueryRedactionFilter

    record = logging.LogRecord(
        name="aco", level=logging.ERROR, pathname=__file__, lineno=1,
        msg=f"request failed: https://api.keepa.com/search?key={SECRET}",
        args=(), exc_info=None,
    )
    _OAuthQueryRedactionFilter().filter(record)
    assert SECRET not in record.getMessage()


def test_a_failed_keepa_call_reports_without_the_key(monkeypatch):
    """The fix at the source: the key never enters the exception at all.

    Redaction downstream is defence in depth; this is the part that means a
    traceback printed by something that never heard of this project is still
    safe.
    """
    from app import discovery

    def explode(*_args, **_kwargs):
        raise RuntimeError(
            f"Server error '500' for url 'https://api.keepa.com/search?key={SECRET}'")

    monkeypatch.setenv("KEEPA_API_KEY", SECRET)
    monkeypatch.setenv("DISCOVERY_SOURCE", "keepa")
    import httpx
    monkeypatch.setattr(httpx, "get", explode)

    with pytest.raises(discovery.DiscoveryRequestError) as caught:
        discovery.KeepaDataSource().search("roller", 5)

    assert SECRET not in str(caught.value)
    # And the discarded original must not be reachable through the chain.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None or SECRET not in str(caught.value.__context__)
