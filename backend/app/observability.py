"""Privacy-safe optional Sentry initialization and monitoring status.

Two things travel to Sentry that no amount of `send_default_pii=False` stops: the
text of an exception, and the breadcrumbs leading to it. Both are strings the
application composed, and this application composes strings out of URLs — a
failed Keepa call names the URL it called, and a rejected OAuth callback names
the query it was given. So the same redaction that guards the log guards the
event.
"""
from __future__ import annotations
import os

from . import redaction

_initialized = False

def scrub_event(event: dict, hint: dict | None = None) -> dict:
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None); request.pop("query_string", None); request.pop("cookies", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for key in list(headers):
                if key.lower() in {"authorization", "cookie", "x-shopify-hmac-sha256",
                                    "stripe-signature"}:
                    headers[key] = "[Filtered]"
    user = event.get("user")
    if isinstance(user, dict):
        for key in ("email", "username", "ip_address"): user.pop(key, None)

    # The message and the exception text are strings we wrote, and we write URLs
    # into them. A credential-shaped parameter loses its value here exactly as it
    # does in the log.
    message = event.get("message")
    if isinstance(message, str):
        event["message"] = redaction.redact(message)
    elif isinstance(message, dict) and isinstance(message.get("formatted"), str):
        message["formatted"] = redaction.redact(message["formatted"])
    for entry in ((event.get("exception") or {}).get("values") or []):
        if isinstance(entry, dict) and isinstance(entry.get("value"), str):
            entry["value"] = redaction.redact(entry["value"])
    crumbs = event.get("breadcrumbs")
    values = crumbs.get("values") if isinstance(crumbs, dict) else crumbs
    for crumb in (values or []):
        if isinstance(crumb, dict) and isinstance(crumb.get("message"), str):
            crumb["message"] = redaction.redact(crumb["message"])
    return event

def initialize() -> bool:
    global _initialized
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn: return False
    try:
        import sentry_sdk
    except ImportError as exc:
        raise RuntimeError("SENTRY_DSN is set but sentry-sdk is not installed.") from exc
    try: sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))
    except ValueError as exc: raise RuntimeError("Invalid SENTRY_TRACES_SAMPLE_RATE.") from exc
    if not 0 <= sample_rate <= 1: raise RuntimeError("SENTRY_TRACES_SAMPLE_RATE must be 0..1.")
    sentry_sdk.init(dsn=dsn, environment=os.getenv("APP_ENV", "development"),
                    release=os.getenv("APP_RELEASE") or None, send_default_pii=False,
                    traces_sample_rate=sample_rate, before_send=scrub_event)
    _initialized = True
    return True

def status() -> dict[str, bool | str | None]:
    return {"sentry_configured": bool(os.getenv("SENTRY_DSN")), "sentry_initialized": _initialized,
            "environment": os.getenv("APP_ENV", "development"),
            "release": os.getenv("APP_RELEASE") or None}
