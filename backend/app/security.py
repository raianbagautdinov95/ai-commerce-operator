"""Small, dependency-free JWT authentication boundary.

The API accepts HS256 access tokens issued by the configured auth service.  The
implementation deliberately supports only the algorithm and claims we use; it
does not trust token-provided algorithm choices or silently accept weak config.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any


class AuthenticationError(ValueError):
    """Raised when an access token cannot be trusted."""


class AuthorizationError(PermissionError):
    """Raised when an authenticated principal lacks the required role."""


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    role: str
    # The `jti` claim: which issued session this token is. Verifying the
    # signature proves the token was minted here; the row this names is what
    # proves it is still meant to work. The lookup belongs to the middleware,
    # so this module stays pure and testable without a database.
    session_id: str = ""


_current_principal: ContextVar[Principal | None] = ContextVar("current_principal", default=None)
_ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2, "owner": 3}
ROLES = tuple(_ROLE_RANK)          # the roles a token may carry, weakest first


def current_principal() -> Principal | None:
    return _current_principal.get()


def bind_principal(principal: Principal) -> Token:
    return _current_principal.set(principal)


def reset_principal(token: Token) -> None:
    _current_principal.reset(token)


def require_role(principal: Principal, minimum_role: str) -> None:
    """Enforce a minimum role using the fixed privilege hierarchy."""
    required = _ROLE_RANK.get(minimum_role)
    actual = _ROLE_RANK.get(principal.role)
    if required is None:
        raise RuntimeError(f"Unknown required role: {minimum_role}")
    if actual is None or actual < required:
        raise AuthorizationError(f"Role '{minimum_role}' or higher is required.")


def required_role(method: str, path: str) -> str:
    """Return the minimum role for a protected API operation.

    Read access is broadly available. Analyses may spend external API budget, so
    they require an operator. Approval changes business state and requires an
    admin. Destructive operations remain owner-only.
    """
    method = method.upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "viewer"
    if method == "DELETE":
        return "owner"
    if method == "POST" and path == "/api/integrations/amazon/authorize":
        return "admin"
    if method == "POST" and path.startswith("/api/billing/"):
        return "owner"
    if method == "POST" and path == "/api/privacy/deletion-request":
        return "owner"
    if method == "POST" and path.startswith("/api/autopilot/") and path.endswith("/decision"):
        return "admin"
    if method == "PUT" and path == "/api/guardrails":
        return "admin"
    if method == "POST" and path.startswith("/api/actions/") and (
        path.endswith("/applied") or path.endswith("/revert")
    ):
        return "admin"
    if method == "POST":
        return "operator"
    return "admin"


def auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").lower() == "true"


def validate_auth_config() -> None:
    if not auth_enabled():
        return
    secret = os.getenv("JWT_SECRET", "")
    if len(secret.encode()) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 bytes when auth is enabled.")


def _decode_segment(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise AuthenticationError("Malformed access token.") from exc


def _claims_object(segment: str) -> dict[str, Any]:
    try:
        value = json.loads(_decode_segment(segment))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthenticationError("Malformed access token.") from exc
    if not isinstance(value, dict):
        raise AuthenticationError("Malformed access token.")
    return value


def authenticate_bearer(authorization: str | None) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("Bearer token required.")
    token = authorization[7:].strip()
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthenticationError("Malformed access token.")

    header = _claims_object(parts[0])
    claims = _claims_object(parts[1])
    if header.get("alg") != "HS256" or header.get("typ", "JWT") != "JWT":
        raise AuthenticationError("Unsupported access token.")

    secret = os.environ.get("JWT_SECRET", "").encode()
    expected = hmac.new(secret, f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
    supplied = _decode_segment(parts[2])
    if not hmac.compare_digest(expected, supplied):
        raise AuthenticationError("Invalid access token signature.")

    now = int(time.time())
    try:
        expires_at = int(claims["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthenticationError("Access token expiry is required.") from exc
    if expires_at <= now:
        raise AuthenticationError("Access token expired.")

    configured_issuer = os.getenv("JWT_ISSUER")
    configured_audience = os.getenv("JWT_AUDIENCE")
    if configured_issuer and claims.get("iss") != configured_issuer:
        raise AuthenticationError("Invalid access token issuer.")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if configured_audience and configured_audience not in audiences:
        raise AuthenticationError("Invalid access token audience.")

    user_id = claims.get("sub")
    tenant_id = claims.get("tenant_id")
    role = claims.get("role")
    if not all(isinstance(v, str) and v for v in (user_id, tenant_id, role)):
        raise AuthenticationError("Access token identity claims are required.")
    try:
        user_id = str(uuid.UUID(user_id))
        tenant_id = str(uuid.UUID(tenant_id))
    except ValueError as exc:
        raise AuthenticationError("Access token identity claims must be UUIDs.") from exc
    if role not in {"owner", "admin", "operator", "viewer"}:
        raise AuthenticationError("Invalid access token role.")

    # A token with no `jti` names no session, so nothing can revoke it. Those
    # were minted before sessions existed and are refused rather than
    # grandfathered: keeping them would leave the hole open while looking shut.
    session_id = claims.get("jti")
    if not isinstance(session_id, str) or not session_id:
        raise AuthenticationError("Access token has no session. Sign in again.")
    try:
        session_id = str(uuid.UUID(session_id))
    except ValueError as exc:
        raise AuthenticationError("Access token session id must be a UUID.") from exc

    return Principal(user_id=user_id, tenant_id=tenant_id, role=role,
                     session_id=session_id)
