"""Turn a Google identity token into the email address it proves.

The rest of this codebase verifies HS256 tokens it signed itself, with a hundred
lines and no dependencies, and that is the right trade for a secret we own.
Google's token is a different problem: RS256, signed by a key we do not hold,
rotated on Google's schedule and fetched from a JWKS endpoint that has to be
cached and re-fetched correctly. Hand-rolling that is how authentication gets
broken quietly, so this module delegates to `google-auth` and confines itself to
the checks the library does *not* make for us.

The one that matters is `email_verified`. Google issues identity tokens for
federated and Workspace accounts where the address has not been proven, and
`accounts.ensure_account` keys an account on the address. Accepting an
unverified one is a tenant takeover, not a papercut.

The verifier is injectable so the tests can exercise every refusal without a
network call or a Google project.
"""
from __future__ import annotations

import os
from typing import Any, Callable

# Google mints tokens under both spellings and treats them as equivalent.
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class GoogleIdentityError(ValueError):
    """The credential is missing, malformed, or proves nothing we can use."""


def google_client_id() -> str | None:
    value = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    return value or None


def google_enabled() -> bool:
    return google_client_id() is not None


def _library_verifier(credential: str, client_id: str) -> dict[str, Any]:
    """Signature, expiry, issuer and audience, checked by Google's own library."""
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise GoogleIdentityError(
            "Sign in with Google is configured but the google-auth package is "
            "not installed on this server."
        ) from exc
    try:
        return google_id_token.verify_oauth2_token(
            credential, google_requests.Request(), client_id)
    except ValueError as exc:
        raise GoogleIdentityError("Google could not confirm this sign-in.") from exc


def verify_google_credential(
    credential: str,
    *,
    verifier: Callable[[str, str], dict[str, Any]] | None = None,
) -> str:
    """Return the verified email address, or raise `GoogleIdentityError`."""
    client_id = google_client_id()
    if client_id is None:
        raise GoogleIdentityError("Sign in with Google is not configured on this server.")
    credential = (credential or "").strip()
    if not credential:
        raise GoogleIdentityError("Google did not return a sign-in token.")

    claims = (verifier or _library_verifier)(credential, client_id)
    if not isinstance(claims, dict):
        raise GoogleIdentityError("Google could not confirm this sign-in.")

    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise GoogleIdentityError("This sign-in token was not issued by Google.")

    # Belt and braces: the library checks the audience, and a misconfigured
    # verifier that did not would let another project's tokens in here.
    audience = claims.get("aud")
    if audience != client_id:
        raise GoogleIdentityError("This sign-in token was issued for another application.")

    email = claims.get("email")
    if not isinstance(email, str) or not email:
        raise GoogleIdentityError(
            "This Google account did not share an email address, so there is "
            "nothing to attach the workspace to.")

    # Google sends this as a bool, and as the string "true" in some flows.
    verified = claims.get("email_verified")
    if verified is not True and str(verified).lower() != "true":
        raise GoogleIdentityError(
            "Google has not verified this email address. Verify it with Google "
            "first, or sign in with a code sent to your email instead.")

    return email
