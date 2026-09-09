"""Issued tokens, and taking them back.

A signed token proves it was minted here. It cannot prove it is *still* meant to
work, and that difference is the whole of this module. Every token names a row
in `token_sessions`; the middleware checks that row on each request, so
revoking is one UPDATE that takes effect on the next call rather than at the
end of the week.

What it deliberately does not do:

* **Refresh.** A longer-lived refresh token is a second credential with a
  second theft story, and it buys convenience the seven-day session already
  provides. When sessions need to outlive a week the answer is to raise
  SESSION_TOKEN_DAYS, not to invent a second token.
* **Write on every request.** `last_seen_at` exists so the sessions list can
  say "last used an hour ago", which nobody needs to the second. It is written
  at most once a minute, so an idle tab does not generate a write per poll.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import models
from .issue_token import sign_token

#: How stale `last_seen_at` may get before a request bothers to update it.
TOUCH_AFTER_SECONDS = 60


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    """SQLite hands back naive datetimes; treat those as the UTC they were."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def open_session(db: Session, *, user_id: uuid.UUID, store_id: uuid.UUID, role: str,
                 days: int, method: str) -> tuple[str, models.TokenSession]:
    """Record a session and return the token that names it.

    The row is written before the token is signed, so a token can never refer
    to a session that does not exist. A token that fails to sign leaves an
    unused row behind, which expires on its own and lets nobody in.
    """
    now = _now()
    row = models.TokenSession(
        id=uuid.uuid4(),
        user_id=user_id,
        store_id=store_id,
        role=role,
        method=method,
        issued_at=now,
        expires_at=now + dt.timedelta(days=days),
        last_seen_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    token = sign_token(user_id=str(user_id), tenant_id=str(store_id), role=role,
                       days=days, session_id=str(row.id))
    return token, row


def live_session(db: Session, session_id: str) -> models.TokenSession | None:
    """The session this token names, if it is still meant to work.

    Expiry is checked here as well as in the token because the two can disagree:
    a session revoked and then re-examined must not be resurrected by a token
    whose `exp` has not arrived yet.
    """
    try:
        key = uuid.UUID(session_id)
    except (ValueError, AttributeError, TypeError):
        return None

    row = db.get(models.TokenSession, key)
    if row is None or row.revoked_at is not None:
        return None

    now = _now()
    expires_at = _aware(row.expires_at)
    if expires_at is not None and expires_at <= now:
        return None

    last_seen = _aware(row.last_seen_at)
    if last_seen is None or (now - last_seen).total_seconds() >= TOUCH_AFTER_SECONDS:
        row.last_seen_at = now
        db.add(row)
        db.commit()
    return row


def revoke(db: Session, session_id: str | uuid.UUID) -> models.TokenSession | None:
    """End one session. Revoking an already-revoked one is not an error."""
    try:
        key = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
    except (ValueError, TypeError):
        return None
    row = db.get(models.TokenSession, key)
    if row is None:
        return None
    if row.revoked_at is None:
        row.revoked_at = _now()
        db.add(row)
        db.commit()
    return row


def revoke_all_for_user(db: Session, user_id: uuid.UUID, *,
                        keep: str | uuid.UUID | None = None) -> int:
    """End every live session a user has, optionally sparing the current one.

    Returns how many were actually ended, so the caller can say so rather than
    claiming a number it did not check.
    """
    spared: uuid.UUID | None = None
    if keep is not None:
        try:
            spared = keep if isinstance(keep, uuid.UUID) else uuid.UUID(str(keep))
        except (ValueError, TypeError):
            spared = None

    now = _now()
    ended = 0
    for row in db.scalars(
        select(models.TokenSession).where(
            models.TokenSession.user_id == user_id,
            models.TokenSession.revoked_at.is_(None),
        )
    ):
        if spared is not None and row.id == spared:
            continue
        row.revoked_at = now
        db.add(row)
        ended += 1
    if ended:
        db.commit()
    return ended


def list_for_user(db: Session, user_id: uuid.UUID) -> list[models.TokenSession]:
    """Live sessions, newest first. Revoked and expired ones are not shown:
    the question this answers is "what can currently reach my account"."""
    now = _now()
    rows = db.scalars(
        select(models.TokenSession)
        .where(models.TokenSession.user_id == user_id,
               models.TokenSession.revoked_at.is_(None))
        .order_by(models.TokenSession.issued_at.desc())
    ).all()
    return [row for row in rows
            if (_aware(row.expires_at) or now) > now]
