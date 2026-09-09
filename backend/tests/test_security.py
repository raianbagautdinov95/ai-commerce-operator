import base64
import hashlib
import hmac
import json
import time

import pytest

from app.security import (
    AuthenticationError,
    AuthorizationError,
    Principal,
    authenticate_bearer,
    require_role,
    required_role,
    validate_auth_config,
)


def _segment(value: dict) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _token(secret: str, **overrides) -> str:
    header = _segment({"alg": "HS256", "typ": "JWT"})
    claims = {
        "sub": "11111111-1111-4111-8111-111111111111",
        "tenant_id": "22222222-2222-4222-8222-222222222222",
        "role": "operator",
        # Every token names a session now; without a jti nothing could revoke it.
        "jti": "33333333-3333-4333-8333-333333333333",
        "exp": int(time.time()) + 300,
        **overrides,
    }
    payload = _segment(claims)
    signature = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return f"{header}.{payload}.{encoded_signature}"


def test_valid_token_returns_principal(monkeypatch):
    secret = "a-secure-test-secret-that-is-long-enough"
    monkeypatch.setenv("JWT_SECRET", secret)
    principal = authenticate_bearer("Bearer " + _token(secret))
    assert principal.user_id == "11111111-1111-4111-8111-111111111111"
    assert principal.tenant_id == "22222222-2222-4222-8222-222222222222"
    assert principal.role == "operator"


def test_invalid_signature_is_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "a-secure-test-secret-that-is-long-enough")
    with pytest.raises(AuthenticationError, match="signature"):
        authenticate_bearer("Bearer " + _token("another-secure-secret-that-is-long-enough"))


def test_expired_token_is_rejected(monkeypatch):
    secret = "a-secure-test-secret-that-is-long-enough"
    monkeypatch.setenv("JWT_SECRET", secret)
    with pytest.raises(AuthenticationError, match="expired"):
        authenticate_bearer("Bearer " + _token(secret, exp=int(time.time()) - 1))


def test_weak_auth_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="32 bytes"):
        validate_auth_config()


@pytest.mark.parametrize(
    ("role", "minimum", "allowed"),
    [
        ("viewer", "viewer", True),
        ("viewer", "operator", False),
        ("operator", "operator", True),
        ("operator", "admin", False),
        ("admin", "admin", True),
        ("admin", "owner", False),
        ("owner", "viewer", True),
        ("owner", "owner", True),
    ],
)
def test_role_hierarchy(role, minimum, allowed):
    principal = Principal(
        user_id="11111111-1111-4111-8111-111111111111",
        tenant_id="22222222-2222-4222-8222-222222222222",
        role=role,
    )
    if allowed:
        require_role(principal, minimum)
    else:
        with pytest.raises(AuthorizationError):
            require_role(principal, minimum)


def test_route_permissions_are_fail_closed():
    assert required_role("GET", "/api/report/daily") == "viewer"
    assert required_role("POST", "/api/ppc/analyze") == "operator"
    assert required_role("POST", "/api/autopilot/id/decision") == "admin"
    assert required_role("PUT", "/api/guardrails") == "admin"
    assert required_role("POST", "/api/actions/id/applied") == "admin"
    assert required_role("POST", "/api/actions/id/revert") == "admin"
    assert required_role("DELETE", "/api/autopilot/queue") == "owner"
    assert required_role("PATCH", "/api/future-setting") == "admin"
