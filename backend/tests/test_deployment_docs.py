"""The deployment document has to name everything the gates block on.

This is a test rather than a habit because the failure it guards is silent and
one-sided: the gate refuses, the person deploying reads the document, and the
document does not mention the variable at all. There is nothing to search for
and nothing that says which of the two is out of date.

It had already happened. `RESEND_API_KEY` and `LOGIN_EMAIL_FROM` were missing
while sign-in depends on them entirely — deploy by that document and nobody,
including the owner, can get past the sign-in page. `PUBLIC_APP_URL` was missing
from both the document and `.env.example`, and it is the link inside every
email. `STRIPE_PRICE_OPERATOR` appeared only as a glob, under "Optional", while
the paid gate blocks on it.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app import pilot_gate

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "railway" / "VARIABLES.md"
EXAMPLE = ROOT / ".env.example"

#: Attestations are documented in the deployment guide on purpose and kept out
#: of the file people copy. A list of eight "set this to true" lines in a
#: template is an invitation to set all eight at once, and a gate everybody
#: switches off certifies nothing.
EXEMPT_FROM_EXAMPLE = {name for name, _, _ in pilot_gate.ATTESTATIONS}


def _gate_variables() -> set[str]:
    """Every environment name the gates read, from their own source.

    Read from the source rather than by calling them, because a name that only
    appears in a branch nobody exercises is exactly the one that goes
    undocumented.
    """
    names: set[str] = set()
    for module in ("pilot_gate", "preflight"):
        source = (ROOT / "backend" / "app" / f"{module}.py").read_text(encoding="utf-8")
        names |= set(re.findall(r'env\.get\("([A-Z][A-Z0-9_]{2,})"', source))
        names |= set(re.findall(r'_get\(env,\s*"([A-Z][A-Z0-9_]{2,})"', source))
    names |= {name for name, _, _ in pilot_gate.ATTESTATIONS}
    return names


def test_the_gates_read_something_at_all():
    """A scan that silently matches nothing would pass every assertion below."""
    assert len(_gate_variables()) > 10


def test_every_variable_the_gates_block_on_is_documented():
    document = DOC.read_text(encoding="utf-8")
    missing = sorted(name for name in _gate_variables() if name not in document)
    assert missing == [], (
        f"{DOC.name} does not mention {missing}. Somebody deploying by this "
        f"document will be refused by a check they cannot look up.")


def test_every_deployment_variable_is_in_the_example_file():
    example = EXAMPLE.read_text(encoding="utf-8")
    missing = sorted(
        name for name in _gate_variables() - EXEMPT_FROM_EXAMPLE
        if not re.search(rf"^#?\s*{name}=", example, re.M))
    assert missing == [], f".env.example does not offer {missing}"


def test_the_scheduler_is_named_as_a_service():
    """It is the one that is easiest to forget, because nothing looks broken
    without it: no trial warnings, no daily read, nothing priced."""
    document = DOC.read_text(encoding="utf-8")
    assert "scheduler" in document.lower()
    assert "app.daily" in document


def test_stripe_is_not_filed_as_optional():
    """It is optional for a free pilot and blocking for a paid one, and the
    document said only the first half."""
    document = DOC.read_text(encoding="utf-8")
    optional = document.split("## Optional", 1)[-1]
    assert "STRIPE_SECRET_KEY" not in optional


def test_the_sign_in_variables_are_marked_required():
    """Without them the service starts, the screens load, and nobody can get in."""
    document = DOC.read_text(encoding="utf-8")
    required = document.split("## Optional", 1)[0]
    for name in ("RESEND_API_KEY", "LOGIN_EMAIL_FROM", "PUBLIC_APP_URL"):
        assert name in required, f"{name} is not in a required section"


def test_railway_iac_declares_the_complete_environment():
    config = (ROOT / ".railway" / "railway.ts").read_text(encoding="utf-8")
    for service_name in ("ai-commerce-operator", "worker", "frontend", "scheduler"):
        assert f'service("{service_name}"' in config
    assert 'const api = service("ai-commerce-operator"' in config
    assert 'postgres("postgres")' in config
    assert 'redis("redis")' in config
    assert 'cronSchedule: "0 7 * * *"' in config


def test_deprecated_railway_configs_are_not_present():
    assert list((ROOT / "railway").glob("*.toml")) == []


# --- the deployment that actually reaches people -----------------------------

IAC = ROOT / ".railway" / "railway.ts"


def _iac() -> str:
    return IAC.read_text(encoding="utf-8")


def test_the_production_frontend_build_keeps_its_guard():
    """The API address is compiled into the JavaScript a browser downloads, so a
    wrong one ships and no restart corrects it. It has shipped wrong twice here.

    `frontend/Dockerfile` refuses http, localhost and throwaway tunnels — but
    only when `REQUIRE_PUBLIC_API_BASE` is "true", and that was set solely in
    `docker-compose.production.yml`. Which is not the build that reaches
    anybody: the one build that ships to real browsers had the guard switched
    off, which is exactly the wrong way round.
    """
    assert 'REQUIRE_PUBLIC_API_BASE: "true"' in _iac(), (
        "the Railway frontend build does not enable the guard that refuses a "
        "development API address")


def test_the_deployed_api_address_is_one_a_browser_can_reach():
    """Checked at the source as well as at build time. The build refuses these
    too, but a refused deploy is found later than a failing test."""
    import re

    match = re.search(r'NEXT_PUBLIC_API_BASE:\s*"([^"]+)"', _iac())
    assert match, "the frontend has no API address in the IaC"
    address = match.group(1)
    assert address.startswith("https://"), address
    assert not any(bad in address for bad in
                   ("localhost", "127.0.0.1", "trycloudflare", "ngrok")), address


def test_every_service_the_product_needs_is_defined():
    """A database and a queue are services here too, and the scheduler is the
    one nothing looks broken without."""
    iac = _iac()
    for fragment in ('postgres("postgres")', 'redis("redis")',
                     'service("worker"', 'service("scheduler"',
                     'service("frontend"'):
        assert fragment in iac, f"{fragment} is missing from the deployment"


def test_migrations_belong_to_exactly_one_service():
    """Two services racing the same revision is how a migration half-applies."""
    assert _iac().count("preDeployCommand") == 1


def test_the_deployed_ports_match_what_the_containers_listen_on():
    """A custom domain is routed to a port somebody types in by hand.

    Railway may inject a `PORT` of its own and both Dockerfiles obey whatever
    they are given, so the day the platform picks a different number the domain
    keeps pointing at the old one — and the edge answers 404 with nothing wrong
    in any log, which is exactly how this presented. Pinning it is only useful
    if the pin and the container agree, so that is what this checks.
    """
    import re

    iac = _iac()
    for service, dockerfile in (("8000", ROOT / "backend" / "Dockerfile"),
                                ("3000", ROOT / "frontend" / "Dockerfile")):
        assert f'PORT: "{service}"' in iac, f"port {service} is not pinned in the IaC"
        body = dockerfile.read_text(encoding="utf-8")
        assert re.search(rf"^EXPOSE {service}$", body, re.M), (
            f"{dockerfile.parent.name}/Dockerfile does not expose {service}")
        assert f"ENV PORT={service}" in body


def test_every_variable_the_paid_gate_blocks_on_is_in_the_deployment():
    """This file is the whole set of variables, not a subset of them.

    One that exists in Railway and not here is one an apply can remove — and the
    day it removes a Stripe key is the day Checkout starts answering 503 with
    nothing in the diff to explain it. Secrets are `preserve()`d rather than
    written down; what matters is that they are named at all.
    """
    iac = _iac()
    for name in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_OPERATOR",
                 "STRIPE_SUCCESS_URL", "STRIPE_CANCEL_URL", "STRIPE_PORTAL_RETURN_URL"):
        assert name in iac, f"{name} is not in the deployment definition"


def test_no_secret_is_written_into_the_deployment_file():
    """It is committed. Everything sensitive is set in Railway and preserved."""
    import re

    iac = _iac()
    for name in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "JWT_SECRET",
                 "SHOPIFY_CLIENT_SECRET", "CREDENTIAL_ENCRYPTION_KEYS",
                 "RESEND_API_KEY", "APP_DB_PASSWORD"):
        assert re.search(rf"{name}:\s*preserve\(\)", iac), (
            f"{name} must be preserve(), not a literal in a committed file")


def test_the_stripe_return_urls_are_addresses_a_customer_can_reach():
    """Stripe sends somebody here right after taking their money."""
    import re

    for name in ("STRIPE_SUCCESS_URL", "STRIPE_CANCEL_URL", "STRIPE_PORTAL_RETURN_URL"):
        match = re.search(rf'{name}:\s*"([^"]+)"', _iac())
        assert match, f"{name} has no address"
        url = match.group(1)
        assert url.startswith("https://"), url
        assert not any(bad in url for bad in
                       ("localhost", "127.0.0.1", "trycloudflare", "ngrok")), url
