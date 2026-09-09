"""
Tests for the pilot gate.

The thing a readiness check is for is refusing, so most of these are about what
it declines to call ready. The specific failure being guarded against is a gate
that reads configuration, finds every value present, and reports a deployment
fit to charge somebody — when the leaked key was never rotated, no backup has
ever left the platform, and the Operator has not once shown it earns its fee.
"""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import pilot_gate, preflight

CONFIGURED = {
    "APP_ENV": "production",
    "AUTH_ENABLED": "true",
    "JWT_SECRET": "s" * 48,
    "DATABASE_URL": "postgresql+psycopg://aco_app:pw@db.internal:5432/aco",
    "QUEUE_ENABLED": "true",
    "REDIS_URL": "redis://redis.internal:6379/0",
    "CREDENTIAL_ENCRYPTION_KEYS": json.dumps({"v1": base64.b64encode(b"k" * 32).decode()}),
    "CREDENTIAL_ACTIVE_KEY_VERSION": "v1",
    "CORS_ALLOWED_ORIGINS": "https://app.example.com",
    "SHOPIFY_CLIENT_ID": "328a0000",
    "SHOPIFY_CLIENT_SECRET": "shpss_placeholder_value",
    "SHOPIFY_REDIRECT_URI": "https://api.example.com/api/integrations/shopify/callback",
    "SHOPIFY_WEBHOOK_URI": "https://api.example.com/api/webhooks/shopify",
    "SENTRY_DSN": "https://k@sentry.example/1",
    "LEGAL_ENTITY": "PetRoller Store",
    "LEGAL_ADDRESS": "Otanmaki, Finland",
    "PRIVACY_CONTACT": "privacy@example.test",
    "ADS_WRITE_ENABLED": "false",
    "STRIPE_SECRET_KEY": "sk_live_placeholder",
    "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
    "STRIPE_PRICE_OPERATOR": "price_placeholder",
    "STRIPE_SUCCESS_URL": "https://app.example.com/billing/done",
    "STRIPE_CANCEL_URL": "https://app.example.com/billing",
    "STRIPE_PORTAL_RETURN_URL": "https://app.example.com/billing",
    "SUPPORT_EMAIL": "help@example.test",
    "SUPPORT_RESPONSE_TIME": "same business day",
    "RESEND_API_KEY": "re_placeholder",
    "LOGIN_EMAIL_FROM": "Operator <no-reply@example.com>",
    "PUBLIC_APP_URL": "https://app.example.com",
}

ATTESTED = {name: "true" for name, _, _ in pilot_gate.ATTESTATIONS}


def _blocked(env):
    checked, attested, _ = pilot_gate.run(env)
    return ([c.name for c in checked if c.blocking],
            [c.name for c in attested if c.blocking])


# --- the failure this exists to prevent -------------------------------------

def test_perfect_configuration_alone_is_not_ready():
    """Every variable present, every service would answer. Nobody has rotated
    the leaked key or taken a backup off the platform."""
    technical, manual = _blocked(CONFIGURED)
    assert technical == []
    assert manual, "a fully configured deployment was called ready to charge"


def test_it_says_which_half_is_missing():
    """The two are never added together: a technical blocker is somebody's
    afternoon, an outstanding attestation is somebody's decision."""
    technical, manual = _blocked({**CONFIGURED, **ATTESTED,
                                  "CORS_ALLOWED_ORIGINS": "http://localhost:3000"})
    assert "Browser origins" in technical
    assert manual == []


def test_everything_done_passes():
    technical, manual = _blocked({**CONFIGURED, **ATTESTED})
    assert technical == [] and manual == []


# --- what it refuses to accept ----------------------------------------------

def test_a_tunnel_callback_is_a_technical_blocker():
    env = {**CONFIGURED, **ATTESTED,
           "SHOPIFY_REDIRECT_URI": "https://abc.trycloudflare.com/cb"}
    technical, _ = _blocked(env)
    assert "Callback: SHOPIFY_REDIRECT_URI" in technical


def test_missing_stripe_configuration_stops_a_paid_pilot():
    env = {k: v for k, v in {**CONFIGURED, **ATTESTED}.items()
           if k != "STRIPE_PRICE_OPERATOR"}
    technical, _ = _blocked(env)
    assert "Stripe configured" in technical


def test_a_test_key_in_production_is_refused():
    """It takes no money at all, which is a quieter failure than taking too
    much and just as fatal to a pilot."""
    env = {**CONFIGURED, **ATTESTED, "STRIPE_SECRET_KEY": "sk_test_x"}
    technical, _ = _blocked(env)
    assert "Stripe key matches the environment" in technical


def test_a_live_key_outside_production_is_refused():
    """The expensive direction: real cards charged while somebody is testing."""
    env = {**CONFIGURED, **ATTESTED, "APP_ENV": "staging",
           "STRIPE_SECRET_KEY": "sk_live_x"}
    technical, _ = _blocked(env)
    assert "Stripe key matches the environment" in technical


def test_each_attestation_is_outstanding_until_claimed():
    for variable, name, _ in pilot_gate.ATTESTATIONS:
        env = {**CONFIGURED, **ATTESTED}
        env.pop(variable)
        _, manual = _blocked(env)
        assert name in manual, variable


def test_an_attestation_says_it_is_a_claim_and_not_a_test():
    """Wording matters here: a checkbox that reads like a verified result is how
    a gate ends up certifying nothing."""
    _, attested, _ = pilot_gate.run(CONFIGURED)
    assert all(c.detail in {"attested", "not attested"} for c in attested)


# --- the line that is neither -------------------------------------------------

def test_the_measured_result_is_reported_as_unproven_whatever_else_is_true():
    """No configuration and no uptime can make this true. It is the product's
    own claim, and it is answered by a measurement or not at all."""
    _, _, measurement = pilot_gate.run({**CONFIGURED, **ATTESTED})
    assert measurement.ok is False
    assert "not proven" in measurement.detail
    assert measurement.severity == preflight.WARNING, \
        "it must not block a pilot sold knowingly, only refuse to claim otherwise"


def test_the_gate_judges_as_production_even_from_a_laptop():
    """This question is only ever asked about a deployment, so APP_ENV saying
    development must not soften it."""
    technical, _ = _blocked({**CONFIGURED, **ATTESTED, "APP_ENV": "development"})
    assert "Environment" in technical


# --- a pilot customer must be able to reach somebody ------------------------

def test_a_paid_launch_without_a_support_address_is_refused():
    """A customer who hits a problem and has nowhere to go is how a pilot ends.
    The address is readable, so this is checked rather than attested."""
    env = {k: v for k, v in {**CONFIGURED, **ATTESTED}.items()
           if k != "SUPPORT_EMAIL"}
    technical, _ = _blocked(env)
    assert "Support address" in technical


def test_an_unstated_response_time_is_refused():
    """Unstated is promised as instant and remembered as never."""
    env = {k: v for k, v in {**CONFIGURED, **ATTESTED}.items()
           if k != "SUPPORT_RESPONSE_TIME"}
    technical, _ = _blocked(env)
    assert "Support response time" in technical


def test_somebody_reading_it_is_still_only_a_claim():
    """Configuration proves the address exists, never that anyone answers."""
    env = {k: v for k, v in {**CONFIGURED, **ATTESTED}.items()
           if k != "PILOT_SUPPORT_CONTACT"}
    technical, manual = _blocked(env)
    assert technical == []
    assert "Somebody is reading the support address" in manual


# --- the commercial half ----------------------------------------------------

def test_a_tunnel_return_url_is_refused():
    """Stripe sends the customer here right after taking their money. A hostname
    reissued overnight lands them on nothing."""
    env = {**CONFIGURED, **ATTESTED,
           "STRIPE_SUCCESS_URL": "https://abc.trycloudflare.com/done"}
    technical, _ = _blocked(env)
    assert "Return URL: STRIPE_SUCCESS_URL" in technical


def test_an_http_return_url_is_refused():
    env = {**CONFIGURED, **ATTESTED,
           "STRIPE_PORTAL_RETURN_URL": "http://app.example.com/billing"}
    technical, _ = _blocked(env)
    assert "Return URL: STRIPE_PORTAL_RETURN_URL" in technical


def test_the_gate_checks_that_paid_work_is_actually_gated():
    """Structurally, because a gate that trusts a variable saying yes-we-gate
    gates nothing."""
    checks = {c.name: c for c in pilot_gate.run({**CONFIGURED, **ATTESTED})[0]}
    assert checks["Paid work is gated"].ok is True


# --- being able to tell a customer their payment failed ---------------------

def test_a_paid_launch_without_an_email_provider_is_refused():
    env = {k: v for k, v in {**CONFIGURED, **ATTESTED}.items() if k != "RESEND_API_KEY"}
    technical, _ = _blocked(env)
    assert "Email provider" in technical


def test_a_tunnel_billing_link_in_emails_is_refused():
    """A link in an email outlives the deploy that sent it."""
    env = {**CONFIGURED, **ATTESTED,
           "PUBLIC_APP_URL": "https://abc.trycloudflare.com"}
    technical, _ = _blocked(env)
    assert "Billing link in emails" in technical


def test_the_four_templates_must_render():
    checks = {c.name: c for c in pilot_gate.run({**CONFIGURED, **ATTESTED})[0]}
    assert checks["Subscription email templates"].ok is True
    assert checks["Worker consumes the email queue"].ok is True
