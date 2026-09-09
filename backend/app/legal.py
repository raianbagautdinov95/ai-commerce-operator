"""
The facts a privacy policy has to state, from one place.

A privacy policy is the one document where a plausible-looking placeholder is
worse than a blank: "Example Ltd, 1 Example Street" reads as finished and is a
false statement about who is accountable for someone's data. So the identity
comes from configuration, the page says plainly when it is missing, and preflight
refuses a production deployment that never filled it in.

The subprocessor list is derived from what is actually configured rather than
hand-maintained, because a hand-maintained list is wrong the first time someone
adds an integration and forgets the document.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Subprocessor:
    name: str
    purpose: str
    data: str


# Every third party that can receive data, and what reaches it. Keyed by the
# setting whose presence means the integration is live.
_POSSIBLE_SUBPROCESSORS: tuple[tuple[str, Subprocessor], ...] = (
    ("OPENAI_API_KEY", Subprocessor(
        "OpenAI", "Writes the plain-English explanation of an already-computed result",
        "The business figures for the feature in use. No customer data, no credentials.")),
    ("ANTHROPIC_API_KEY", Subprocessor(
        "Anthropic", "Alternative provider for the same explanations",
        "The business figures for the feature in use. No customer data, no credentials.")),
    ("KEEPA_API_KEY", Subprocessor(
        "Keepa", "Supplies public marketplace data for product research",
        "Search terms you enter. Nothing about your store leaves with them.")),
    ("SHOPIFY_CLIENT_ID", Subprocessor(
        "Shopify", "The store being operated",
        "Aggregate order totals are read. Customer names, emails and addresses are not stored.")),
    ("SP_API_CLIENT_ID", Subprocessor(
        "Amazon Selling Partner API", "The store being operated",
        "Listings, inventory and sales totals.")),
    ("ADS_API_CLIENT_ID", Subprocessor(
        "Amazon Advertising API", "Advertising performance and negative keywords",
        "Campaign and search-term performance.")),
    ("STRIPE_SECRET_KEY", Subprocessor(
        "Stripe", "Subscription billing",
        "Billing identifiers. Card details never reach this service.")),
    ("SENTRY_DSN", Subprocessor(
        "Sentry", "Error monitoring",
        "Stack traces with request bodies, query strings, cookies, auth headers "
        "and user identifiers stripped before sending.")),
    ("RAPIDAPI_KEY", Subprocessor(
        "RapidAPI supplier source", "Supplier search",
        "The product description you search for.")),
)

# Retention, taken from what the code actually does rather than aspiration.
RETENTION = (
    ("Encrypted channel credentials", "Until you disconnect the channel, or immediately "
                                      "when the provider revokes access."),
    ("Aggregate daily commerce metrics", "For the life of the workspace; deleted on an "
                                         "approved erasure request."),
    ("Product evaluations and recommendations", "For the life of the workspace."),
    ("Audit events", "For the life of the workspace. These record who changed what, and "
                     "are what makes an erasure or a disputed action checkable."),
    ("Webhook delivery records", "Retained to reject duplicates; they hold a hash of the "
                                 "payload, never the payload."),
    ("Background job results", "24 hours; failures 7 days."),
    ("OAuth state", "5 minutes, single use."),
    ("Database backups", "14 days by default (BACKUP_RETENTION_DAYS)."),
)

REQUIRED_SETTINGS = ("LEGAL_ENTITY", "LEGAL_ADDRESS", "PRIVACY_CONTACT")


@dataclass
class LegalDetails:
    entity: str | None
    address: str | None
    privacy_contact: str | None
    representative: str | None
    # A registration number turns "somebody called this" into an entity a
    # regulator or a merchant can look up in a public register. Optional,
    # because not every deployment is a registered business — but where one
    # exists, omitting it makes the controller harder to identify than the law
    # intends.
    business_id: str | None
    business_type: str | None
    country: str | None
    complete: bool
    missing: list[str] = field(default_factory=list)
    subprocessors: list[dict] = field(default_factory=list)
    retention: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def active_subprocessors(env=None) -> list[Subprocessor]:
    env = os.environ if env is None else env
    return [sub for setting, sub in _POSSIBLE_SUBPROCESSORS if (env.get(setting) or "").strip()]


def details(env=None) -> LegalDetails:
    env = os.environ if env is None else env

    def get(name: str) -> str | None:
        return (env.get(name) or "").strip() or None

    missing = [name for name in REQUIRED_SETTINGS if not get(name)]
    return LegalDetails(
        entity=get("LEGAL_ENTITY"),
        address=get("LEGAL_ADDRESS"),
        privacy_contact=get("PRIVACY_CONTACT"),
        representative=get("LEGAL_EU_REPRESENTATIVE"),
        business_id=get("LEGAL_BUSINESS_ID"),
        business_type=get("LEGAL_BUSINESS_TYPE"),
        country=get("LEGAL_COUNTRY"),
        complete=not missing,
        missing=missing,
        subprocessors=[asdict(sub) for sub in active_subprocessors(env)],
        retention=[{"category": category, "period": period} for category, period in RETENTION],
    )
