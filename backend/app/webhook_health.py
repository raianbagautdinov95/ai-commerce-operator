"""Noticing that order notifications have stopped verifying.

Rotating `SHOPIFY_CLIENT_SECRET` has a failure mode with no symptom. Shopify
starts signing with the new secret the moment it is saved there; if the
deployment is still holding the old one, every delivery is refused with a 401
and the Shopify screen goes on reporting `active`, because the subscriptions
really do exist and really do point here. Orders quietly stop arriving and the
first sign is a revenue figure that stopped moving.

So the refusals are counted. A rejection more recent than the last delivery that
actually verified means the signature check is failing now, which after a
rotation means one side has the wrong secret.

Attribution is deliberately absent. A rejected request has proved nothing about
who sent it — its `X-Shopify-Shop-Domain` header is as unverified as its
signature — so the count is global rather than per shop. It answers "signatures
are being refused", never "this shop's signatures are being refused".

Kept out of the database on purpose: this is operational noise with a lifetime
of hours, and a failed write here must never affect the 401 that matters.
"""
from __future__ import annotations

import datetime as dt

from .runtime import log

#: How long a rejection stays interesting. Long enough to survive a night, short
#: enough that a secret fixed yesterday stops being reported today.
REJECTION_TTL_SECONDS = 36 * 3600

_REJECTED_AT = "shopify:webhook:signature_rejected_at"
_ACCEPTED_AT = "shopify:webhook:signature_accepted_at"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _connection():
    from .queueing import queue_enabled, redis_connection

    if not queue_enabled():
        return None
    return redis_connection()


def _set(key: str) -> None:
    """Best effort, always. Recording an observation must not break serving."""
    try:
        connection = _connection()
        if connection is None:
            return
        connection.set(key, _now(), ex=REJECTION_TTL_SECONDS)
    except Exception:  # noqa: BLE001 - diagnostics must not affect the response
        log.debug("Could not record webhook signature outcome", exc_info=True)


def record_rejected() -> None:
    _set(_REJECTED_AT)


def record_accepted() -> None:
    _set(_ACCEPTED_AT)


def _read(connection, key: str) -> dt.datetime | None:
    raw = connection.get(key)
    if not raw:
        return None
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        stamp = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=dt.timezone.utc)


def signatures_failing(connection=None) -> bool:
    """True when the most recent thing that happened was a refused signature.

    False when nothing has been refused, when something verified more recently,
    or when the answer cannot be read at all — an unknown must not be reported
    to a seller as a fault in their store.
    """
    try:
        connection = _connection() if connection is None else connection
        if connection is None:
            return False
        rejected = _read(connection, _REJECTED_AT)
        if rejected is None:
            return False
        accepted = _read(connection, _ACCEPTED_AT)
        return accepted is None or rejected > accepted
    except Exception:  # noqa: BLE001
        log.debug("Could not read webhook signature health", exc_info=True)
        return False
