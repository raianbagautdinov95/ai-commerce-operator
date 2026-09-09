"""
Tests for rotating SHOPIFY_CLIENT_SECRET.

The rotation itself happens in two places nobody here controls — Shopify's
Partner Dashboard and the deployment's secret store — so what is testable is
whether this side survives it: that the new value is picked up without a code
change, that the old one cannot be read back out of anything, and above all that
the window where the two sides disagree is visible instead of silent.

That window is the dangerous part. Shopify signs with the new secret the moment
it is saved there; a deployment still holding the old one refuses every delivery
with a 401 while the Shopify screen goes on saying the subscriptions are active,
because they are. Orders stop arriving and nothing says so.
"""
import base64
import hashlib
import hmac
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import datetime as dt

import pytest

from app import preflight, shopify, webhook_health

OLD = "shpss_old_secret_value"
NEW = "shpss_new_secret_value"


def _sign(body: bytes, secret: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


# --- the new value takes effect without a code change -----------------------

def test_the_secret_is_read_at_verification_time_not_at_import(monkeypatch):
    """If it were captured at import, rotation would need a rebuild rather than
    a restart, and a restart is what the runbook promises."""
    body = b'{"id": 1}'
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", OLD)
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "client")
    monkeypatch.setenv("SHOPIFY_REDIRECT_URI", "https://app.example.com/cb")
    assert shopify.verify_webhook(body, _sign(body, OLD)) is True

    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", NEW)
    assert shopify.verify_webhook(body, _sign(body, NEW)) is True


def test_a_signature_from_the_old_secret_is_refused_after_rotation(monkeypatch):
    body = b'{"id": 1}'
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "client")
    monkeypatch.setenv("SHOPIFY_REDIRECT_URI", "https://app.example.com/cb")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", NEW)
    assert shopify.verify_webhook(body, _sign(body, OLD)) is False


def test_an_absent_signature_is_refused_rather_than_assumed(monkeypatch):
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "client")
    monkeypatch.setenv("SHOPIFY_REDIRECT_URI", "https://app.example.com/cb")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", NEW)
    assert shopify.verify_webhook(b"{}", None) is False


# --- preflight confirms presence and says nothing more ----------------------

def test_preflight_reports_the_secret_without_reciting_it():
    checks = preflight._check_channel_secrets(
        {"SHOPIFY_CLIENT_ID": "328a0000", "SHOPIFY_CLIENT_SECRET": NEW})
    rendered = preflight.render(checks)
    assert NEW not in rendered
    assert "set (" in rendered           # length only
    assert all(check.ok for check in checks)


def test_a_missing_secret_is_a_blocker_that_says_what_breaks():
    checks = {c.name: c for c in preflight._check_channel_secrets(
        {"SHOPIFY_CLIENT_ID": "328a0000"})}
    secret = checks["Shopify client secret"]
    assert secret.blocking
    assert "every delivery is refused" in secret.fix


def test_a_store_that_uses_no_channel_is_not_nagged():
    assert preflight._check_channel_secrets({}) == []


# --- the disagreement window is visible -------------------------------------

class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    def set(self, key, value, ex=None):
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)


def _at(minutes_ago: float) -> str:
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(minutes=minutes_ago)).isoformat()


def test_a_refused_signature_after_a_good_one_reads_as_failing():
    conn = FakeRedis()
    conn.set("shopify:webhook:signature_accepted_at", _at(30))
    conn.set("shopify:webhook:signature_rejected_at", _at(1))
    assert webhook_health.signatures_failing(conn) is True


def test_a_good_signature_after_a_refused_one_reads_as_recovered():
    """The half that matters after the deployment catches up: it must stop
    warning on its own, or nobody believes the warning next time."""
    conn = FakeRedis()
    conn.set("shopify:webhook:signature_rejected_at", _at(30))
    conn.set("shopify:webhook:signature_accepted_at", _at(1))
    assert webhook_health.signatures_failing(conn) is False


def test_nothing_refused_is_not_a_fault():
    conn = FakeRedis()
    conn.set("shopify:webhook:signature_accepted_at", _at(1))
    assert webhook_health.signatures_failing(conn) is False


def test_an_unreadable_answer_is_not_reported_as_a_fault():
    """An unknown must not be shown to a seller as something wrong with their
    shop. Diagnostics that cannot read themselves stay quiet."""
    class Broken:
        def get(self, key):
            raise RuntimeError("no redis here")

    assert webhook_health.signatures_failing(Broken()) is False


def test_recording_an_outcome_never_raises(monkeypatch):
    """The 401 is the part that matters; noting it is not."""
    def explode():
        raise RuntimeError("redis is down")

    monkeypatch.setattr(webhook_health, "_connection", explode)
    webhook_health.record_rejected()      # must not raise
    webhook_health.record_accepted()


def test_the_failing_state_cannot_be_attributed_to_a_shop():
    """A refused request proved nothing about who sent it — its shop header is
    as unverified as its signature. Recording it per shop would let anyone put a
    scary warning on somebody else's screen by posting rubbish, so the record
    takes no shop and structurally cannot."""
    import inspect

    for name in ("record_rejected", "record_accepted", "signatures_failing"):
        parameters = inspect.signature(getattr(webhook_health, name)).parameters
        assert "shop" not in parameters and "store_id" not in parameters, name
