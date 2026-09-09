"""
Mint an access token for one operator.

Signing in now has two proper doors — Google and a code sent to an email
address — so this is no longer the way in. It is the way in when those are not
configured yet, or when email delivery is down and somebody still has to reach
their own store.

    python -m app.issue_token --email owner@example.com --role owner --days 30

It lands on the same account as the other two doors (`accounts.ensure_account`)
and records a session like they do, so a token minted here is listed and
revoked with all the rest. There is no such thing as a token this server cannot
take back.

Because it can grant any role to anyone, the ability to run it IS the ability to
be an owner. Keep it where shell access already implies that.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import time
import uuid

from .accounts import ensure_account
from .db import models
from .db.session import SessionLocal
from .security import ROLES

MAX_DAYS = 365


class TokenConfigurationError(RuntimeError):
    pass


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def sign_token(*, user_id: str, tenant_id: str, role: str, days: int,
               secret: str | None = None, now: int | None = None,
               session_id: str | None = None) -> str:
    """Produce exactly the token `security.authenticate_bearer` expects.

    `session_id` becomes the `jti` claim and must name a `token_sessions` row,
    or the token authenticates against nothing and every request is refused.
    Callers that mint a token are the ones that record the session.
    """
    secret = os.getenv("JWT_SECRET", "") if secret is None else secret
    if len(secret.encode()) < 32:
        raise TokenConfigurationError(
            "JWT_SECRET must be at least 32 bytes before a token can be signed.")
    if role not in ROLES:
        raise TokenConfigurationError(
            f"Unknown role '{role}'. Known roles: {', '.join(ROLES)}.")
    if not 1 <= days <= MAX_DAYS:
        raise TokenConfigurationError(f"Lifetime must be between 1 and {MAX_DAYS} days.")

    issued = int(time.time()) if now is None else now
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "sub": str(uuid.UUID(str(user_id))),
        "tenant_id": str(uuid.UUID(str(tenant_id))),
        "role": role,
        "jti": str(uuid.UUID(str(session_id))) if session_id else str(uuid.uuid4()),
        "iat": issued,
        "exp": issued + days * 86400,
    }
    for name, key in (("JWT_ISSUER", "iss"), ("JWT_AUDIENCE", "aud")):
        value = os.getenv(name)
        if value:
            claims[key] = value

    def segment(payload: dict) -> str:
        return _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())

    signing_input = f"{segment(header)}.{segment(claims)}"
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


def ensure_operator(db, *, email: str) -> tuple[models.User, models.Store]:
    """Find or create the person and the store their token will be scoped to.

    A thin alias for `accounts.ensure_account`. This CLI is now one of three
    doors into the same accounts — Google and the email code are the others —
    and every one of them has to land on the same user, so none of them may own
    its own copy of this logic.
    """
    return ensure_account(db, email=email)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.issue_token",
        description="Mint an access token for one operator.")
    parser.add_argument("--email", required=True, help="who the token is for")
    parser.add_argument("--role", default="owner", choices=sorted(ROLES),
                        help="viewer reads, operator runs analyses, admin applies "
                             "changes, owner does everything (default: owner)")
    parser.add_argument("--days", type=int, default=30,
                        help=f"lifetime in days, 1-{MAX_DAYS} (default: 30)")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    db = SessionLocal()
    try:
        user, store = ensure_operator(db, email=args.email)
        # Imported here rather than at module scope: `sessions` imports this
        # module for `sign_token`, and the cycle only resolves at call time.
        from .sessions import open_session
        token, row = open_session(db, user_id=user.id, store_id=store.id,
                                  role=args.role, days=args.days, method="cli")
    except TokenConfigurationError as exc:
        print(f"Cannot issue a token: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=args.days)
    print(f"\nToken for {args.email} as {args.role}, valid until "
          f"{expires:%Y-%m-%d %H:%M} UTC:\n")
    print(token)
    print("\nPaste it into the app when it asks, or send it as a header:")
    print('  curl -H "Authorization: Bearer <token>" .../api/actions')
    print(f"\nThis is session {row.id}. Ending it — from the app's access screen, "
          "or with POST /api/auth/sessions/revoke-others from another session — "
          "refuses this token on its very next request.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
