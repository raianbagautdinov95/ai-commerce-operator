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
