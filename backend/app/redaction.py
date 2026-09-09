"""Keeping secrets out of the places nobody thinks to check.

A secret does not have to be printed to leak. This project has leaked one twice
over without a single line that logs it:

- `httpx` builds `https://api.keepa.com/search?key=...`, and when the response is
  an error `raise_for_status()` puts that whole URL into the exception message.
  The traceback then reaches the log, the error reporter and anyone reading
  either — carrying a long-lived API key that was never meant to travel.
- Uvicorn's access log writes the request path verbatim, so a Shopify OAuth
  callback recorded its `code` and `hmac` next to the timestamp. The redaction
  filter that existed covered the Amazon callback and nothing else, because it
  matched one hard-coded path.

Both are the same mistake: guarding the places we remember instead of the shape
of the thing. What is redacted here is the *parameter name*, wherever it appears
— in a URL, in a query string, or in the middle of an exception message — so a
new endpoint or a new client is covered the day it is written rather than the
day someone notices.

Redaction is not a substitute for rotation. A key that has already reached a log
is compromised, and scrubbing later output does not un-compromise it.
"""
from __future__ import annotations

import re

#: Parameter names whose value must never be readable. Deliberately broad: the
#: cost of redacting something harmless is an unreadable log line, and the cost
#: of missing one is a live credential in a file somebody ships to support.
SENSITIVE_PARAMS = frozenset({
    "key", "api_key", "apikey", "access_key", "secret", "client_secret",
    "password", "passwd", "token", "access_token", "refresh_token", "id_token",
    "code", "hmac", "signature", "sig", "state", "session", "auth",
    "authorization", "credential", "credentials",
})

#: Endings that make a name a credential whatever it is prefixed with. An exact
#: list is a list of the ones somebody remembered: `spapi_oauth_code` was not on
#: it, and Amazon's authorization code went into the access log because of that.
SENSITIVE_SUFFIXES = ("_key", "_token", "_secret", "_code", "_password",
                      "_signature", "_hmac", "_state", "_credential")

#: Substrings that are never innocent wherever they appear in a name.
SENSITIVE_FRAGMENTS = ("secret", "password", "passwd")

REDACTED = "[REDACTED]"


def is_sensitive(name: str) -> bool:
    """Whether a parameter of this name must have its value hidden."""
    lowered = name.lower().lstrip("-")
    return (lowered in SENSITIVE_PARAMS
            or lowered.endswith(SENSITIVE_SUFFIXES)
            or any(fragment in lowered for fragment in SENSITIVE_FRAGMENTS))

#: `name=value` where value runs to the next separator. Matches inside a URL, a
#: bare query string, or prose that happens to quote one.
_PARAM = re.compile(
    r"(?P<name>[A-Za-z0-9_.-]+)(?P<sep>=)(?P<value>[^&\s'\"<>\\]*)"
)


def _redact_match(match: re.Match) -> str:
    name = match.group("name")
    if not is_sensitive(name):
        return match.group(0)
    return f"{name}{match.group('sep')}{REDACTED}"


def redact(text: str) -> str:
    """Return `text` with the value of every sensitive parameter replaced.

    Safe to call on anything: a URL, an exception message, a whole traceback.
    Non-strings are returned unchanged so callers need no defensive checks.
    """
    if not isinstance(text, str) or "=" not in text:
        return text
    return _PARAM.sub(_redact_match, text)


def redacted_message(exc: BaseException) -> str:
    """What an exception may safely say."""
    return redact(str(exc))
