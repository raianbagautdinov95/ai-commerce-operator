"""Delivering the one email this application sends.

Two things are worth holding here. A sign-in code must never reach a production
log, because log access would become account access. And when delivery fails,
the log has to say *why* — the first time a provider is configured the answer
is almost always "domain is not verified" or a bad key, and an exception class
name sends somebody to read the code instead of their DNS.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest

from app import mailer


CODE = "123456"
ADDRESS = "seller@example.com"


def _configure(monkeypatch, env="production"):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("LOGIN_EMAIL_FROM", "login@example.com")
    monkeypatch.setenv("APP_ENV", env)


# --- the code never reaches a production log --------------------------------


def test_development_logs_the_code_because_nobody_can_receive_it(monkeypatch, caplog):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("LOGIN_EMAIL_FROM", raising=False)
    monkeypatch.setenv("APP_ENV", "development")

    with caplog.at_level("WARNING"):
        mailer.send_login_code(ADDRESS, CODE)
    assert CODE in caplog.text


@pytest.mark.parametrize("env", ["staging", "production"])
def test_a_real_deployment_refuses_rather_than_logging_the_code(monkeypatch, caplog, env):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("LOGIN_EMAIL_FROM", raising=False)
    monkeypatch.setenv("APP_ENV", env)

    with caplog.at_level("WARNING"):
        with pytest.raises(mailer.MailerNotConfigured):
            mailer.send_login_code(ADDRESS, CODE)
    assert CODE not in caplog.text, "a sign-in code must never reach a real log"


# --- a failure has to be diagnosable ----------------------------------------


def test_the_providers_reason_reaches_the_log(monkeypatch, caplog):
    """The most likely first failure, verbatim from Resend."""
    _configure(monkeypatch)

    def refuse(*args, **kwargs):
        request = httpx.Request("POST", mailer.RESEND_ENDPOINT)
        response = httpx.Response(
            403, request=request,
            json={"statusCode": 403, "name": "validation_error",
                  "message": "The example.com domain is not verified."})
        raise httpx.HTTPStatusError("403", request=request, response=response)

    monkeypatch.setattr(mailer.httpx, "post", refuse)

    with caplog.at_level("ERROR"):
        with pytest.raises(mailer.MailerNotConfigured):
            mailer.send_login_code(ADDRESS, CODE)

    assert "403" in caplog.text
    assert "domain is not verified" in caplog.text
    assert CODE not in caplog.text


def test_a_connection_failure_says_it_never_completed(monkeypatch, caplog):
    """No response to quote, so say that rather than pretending to know more."""
    _configure(monkeypatch)

    def unreachable(*args, **kwargs):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(mailer.httpx, "post", unreachable)

    with caplog.at_level("ERROR"):
        with pytest.raises(mailer.MailerNotConfigured):
            mailer.send_login_code(ADDRESS, CODE)
    assert "never completed" in caplog.text
    assert CODE not in caplog.text


def test_an_unparseable_error_body_still_gives_the_status(monkeypatch, caplog):
    _configure(monkeypatch)

    def refuse(*args, **kwargs):
        request = httpx.Request("POST", mailer.RESEND_ENDPOINT)
        response = httpx.Response(502, request=request, text="<html>bad gateway</html>")
        raise httpx.HTTPStatusError("502", request=request, response=response)

    monkeypatch.setattr(mailer.httpx, "post", refuse)

    with caplog.at_level("ERROR"):
        with pytest.raises(mailer.MailerNotConfigured):
            mailer.send_login_code(ADDRESS, CODE)
    assert "HTTP 502" in caplog.text


# --- what actually goes on the wire -----------------------------------------


def test_the_message_carries_the_code_its_life_and_a_way_out(monkeypatch):
    _configure(monkeypatch)
    sent = {}

    def capture(url, **kwargs):
        sent.update(kwargs["json"])
        return httpx.Response(200, request=httpx.Request("POST", url), json={"id": "x"})

    monkeypatch.setattr(mailer.httpx, "post", capture)
    mailer.send_login_code(ADDRESS, CODE)

    assert sent["to"] == [ADDRESS]
    assert sent["from"] == "login@example.com"
    assert CODE in sent["subject"], "the code in the subject saves opening the mail"
    assert CODE in sent["text"]
    assert "once" in sent["text"], "single use has to be stated"
    # Somebody who did not ask for this must be told nothing is wrong.
    assert "did not ask" in sent["text"]


def test_login_is_possible_when_either_route_exists(monkeypatch):
    _configure(monkeypatch)
    assert mailer.login_email_possible() is True

    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert mailer.login_email_possible() is False, "production without a mailer cannot"

    monkeypatch.setenv("APP_ENV", "development")
    assert mailer.login_email_possible() is True, "development falls back to the log"


# --- classifying a failure --------------------------------------------------
#
# Four outcomes because they are fixed in four different places: the API key
# page, the DNS records, the plan, and the network. "It didn't send" sends
# somebody to all four.


def _http_error(status, payload=None, text=None):
    request = httpx.Request("POST", mailer.RESEND_ENDPOINT)
    if payload is not None:
        response = httpx.Response(status, request=request, json=payload)
    else:
        response = httpx.Response(status, request=request, text=text or "")
    return httpx.HTTPStatusError(str(status), request=request, response=response)


@pytest.mark.parametrize("status,payload,expected", [
    (401, {"message": "API key is invalid"}, mailer.DELIVERY_BAD_KEY),
    (403, {"message": "Invalid API key provided"}, mailer.DELIVERY_BAD_KEY),
    (403, {"message": "The example.com domain is not verified."},
     mailer.DELIVERY_SENDER_REJECTED),
    (422, {"message": "The from address is not a verified domain"},
     mailer.DELIVERY_SENDER_REJECTED),
    (429, {"message": "Too many requests"}, mailer.DELIVERY_RATE_LIMITED),
    (500, {"message": "Internal error"}, mailer.DELIVERY_REFUSED),
])
def test_each_kind_of_refusal_is_named(status, payload, expected):
    outcome, detail = mailer.classify_delivery_error(_http_error(status, payload))
    assert outcome == expected
    assert str(status) in detail


def test_a_network_failure_is_not_mistaken_for_a_refusal():
    outcome, detail = mailer.classify_delivery_error(httpx.ConnectTimeout("nope"))
    assert outcome == mailer.DELIVERY_UNREACHABLE
    assert "never completed" in detail


def test_every_outcome_has_advice():
    """A named failure with no next step is only half an answer."""
    for outcome in (mailer.DELIVERY_BAD_KEY, mailer.DELIVERY_SENDER_REJECTED,
                    mailer.DELIVERY_RATE_LIMITED, mailer.DELIVERY_UNREACHABLE,
                    mailer.DELIVERY_REFUSED):
        assert mailer.DELIVERY_FIX.get(outcome), outcome


def test_the_probe_never_carries_a_code(monkeypatch):
    """It proves the path works; it is not a sign-in."""
    _configure(monkeypatch)
    sent = {}

    def capture(url, **kwargs):
        sent.update(kwargs["json"])
        return httpx.Response(200, request=httpx.Request("POST", url), json={"id": "x"})

    monkeypatch.setattr(mailer.httpx, "post", capture)
    outcome, _ = mailer.send_probe("check@example.com")

    assert outcome == mailer.DELIVERY_OK
    assert "code" not in sent["text"].lower() or "no code" in sent["text"].lower()
    assert "Nobody asked to sign in" in sent["text"]
