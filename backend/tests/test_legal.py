"""
Tests for the legal details behind the privacy policy.

A privacy policy is the one document where a plausible-looking placeholder is
worse than a blank: "Example Ltd, 1 Example Street" reads as finished while being
a false statement about who is accountable for someone's data. So the two things
tested hardest are that nothing is invented, and that an unfinished policy cannot
reach production quietly.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import legal
from app.preflight import run_checks

FILLED = {
    "LEGAL_ENTITY": "Northwind Commerce OÜ",
    "LEGAL_ADDRESS": "Tartu mnt 67, 10115 Tallinn, Estonia",
    "PRIVACY_CONTACT": "privacy@northwind.example",
}


def test_an_unconfigured_deployment_names_nobody_rather_than_inventing_one():
    found = legal.details({})
    assert found.entity is None
    assert found.privacy_contact is None
    assert found.complete is False
    assert set(found.missing) == set(legal.REQUIRED_SETTINGS)


def test_a_configured_deployment_is_complete():
    found = legal.details(FILLED)
    assert found.complete is True
    assert found.missing == []
    assert found.entity == "Northwind Commerce OÜ"


def test_blank_is_treated_as_missing_not_as_an_answer():
    found = legal.details({**FILLED, "PRIVACY_CONTACT": "   "})
    assert found.complete is False
    assert found.missing == ["PRIVACY_CONTACT"]


def test_an_eu_representative_is_optional():
    assert legal.details(FILLED).representative is None
    assert legal.details({**FILLED, "LEGAL_EU_REPRESENTATIVE": "Someone GmbH"}
                         ).representative == "Someone GmbH"


def test_the_subprocessor_list_follows_what_is_switched_on():
    """A hand-maintained list is wrong the first time somebody adds an
    integration and forgets the document."""
    assert legal.active_subprocessors({}) == []

    names = [s.name for s in legal.active_subprocessors(
        {"OPENAI_API_KEY": "sk-x", "SENTRY_DSN": "https://x@y/1"})]
    assert names == ["OpenAI", "Sentry"]

    names = [s.name for s in legal.active_subprocessors(
        {"SHOPIFY_CLIENT_ID": "id", "STRIPE_SECRET_KEY": "sk", "KEEPA_API_KEY": "k"})]
    assert set(names) == {"Keepa", "Shopify", "Stripe"}


def test_a_blank_key_does_not_list_a_provider():
    assert legal.active_subprocessors({"OPENAI_API_KEY": "  "}) == []


def test_every_listed_provider_says_what_reaches_it():
    """Naming a recipient without saying what they get is not disclosure."""
    for _, sub in legal._POSSIBLE_SUBPROCESSORS:
        assert sub.purpose and sub.data
        assert len(sub.data) > 20


def test_retention_is_stated_for_every_category():
    found = legal.details(FILLED)
    assert len(found.retention) == len(legal.RETENTION)
    assert all(row["period"] for row in found.retention)


def test_preflight_refuses_a_production_deployment_with_an_unfinished_policy():
    """An unnamed controller means a data subject has nobody to exercise their
    rights against, so this is a blocker rather than a warning."""
    base = {
        "APP_ENV": "production", "AUTH_ENABLED": "true", "JWT_SECRET": "s" * 48,
        "DATABASE_URL": "postgresql+psycopg://a:b@db/aco", "QUEUE_ENABLED": "true",
        "REDIS_URL": "redis://redis:6379/0",
        "CREDENTIAL_ENCRYPTION_KEYS": '{"v1":"' + "a" * 42 + 'GA=="}',
        "CREDENTIAL_ACTIVE_KEY_VERSION": "v1",
        "CORS_ALLOWED_ORIGINS": "https://app.example.com",
    }
    blocked = [c for c in run_checks(base) if c.blocking]
    assert "Privacy policy" in [c.name for c in blocked]

    check = next(c for c in run_checks(base) if c.name == "Privacy policy")
    assert "LEGAL_ENTITY" in check.fix

    passed = [c for c in run_checks({**base, **FILLED}) if c.name == "Privacy policy"]
    assert passed[0].ok is True
    assert passed[0].detail == "Northwind Commerce OÜ"


# --- the registration behind the name ---------------------------------------
#
# A trade name identifies nobody. A registration number is what lets a merchant
# or a regulator look the controller up rather than take the page on trust, so
# it is carried through when it exists — and its absence is never filled in.


def test_the_registration_is_shown_when_there_is_one():
    details = legal.details({
        **FILLED,
        "LEGAL_BUSINESS_ID": "3635858-1",
        "LEGAL_BUSINESS_TYPE": "Yksityinen elinkeinonharjoittaja",
        "LEGAL_COUNTRY": "Finland",
    })
    assert details.business_id == "3635858-1"
    assert details.business_type == "Yksityinen elinkeinonharjoittaja"
    assert details.country == "Finland"


def test_a_deployment_without_a_registration_is_still_complete():
    """Not every deployment is a registered business; only the three named
    settings decide whether the policy can be shipped."""
    details = legal.details(FILLED)
    assert details.business_id is None
    assert details.business_type is None
    assert details.country is None
    assert details.complete is True
    assert details.missing == []


def test_a_blank_registration_is_absent_rather_than_empty_string():
    """An empty string would render as "business ID ." on the page."""
    details = legal.details({**FILLED, "LEGAL_BUSINESS_ID": "   ",
                             "LEGAL_BUSINESS_TYPE": "", "LEGAL_COUNTRY": ""})
    assert details.business_id is None
    assert details.business_type is None
    assert details.country is None


def test_the_registration_reaches_the_response_shape():
    """The dataclass is serialised straight to the API, so a field that never
    reaches to_dict is a field the privacy page cannot show."""
    payload = legal.details({**FILLED, "LEGAL_BUSINESS_ID": "3635858-1"}).to_dict()
    assert payload["business_id"] == "3635858-1"
    assert "business_type" in payload and "country" in payload
