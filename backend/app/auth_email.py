"""Sign in with a six-digit code sent to an email address.

Not everyone selling on Amazon has a Google account, and nobody should have to
create one to try a tool. This is the second door, and it is deliberately the
smaller one: a short numeric code, a short life, a hard attempt limit.

Three things it refuses to do, each because the obvious shortcut is a hole:

* **Store the code.** Only an HMAC of it is written, keyed on `JWT_SECRET`, so a
  copy of the database is not a way to sign in as whoever has a code pending.
  Rotating that secret invalidates outstanding codes along with every token,
  which is the correct blast radius for a secret rotation.
* **Say whether an address is known.** Requesting a code answers the same way
  for an address that has an account and one that does not. Otherwise this
  endpoint is a free membership oracle for anyone with a list of emails.
* **Let a code be guessed.** Six digits is a million possibilities, which is
  only safe with a ceiling on attempts. Five wrong answers burn the code, and
  five requests an hour stop someone from farming fresh ones to guess against.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from .accounts import normalise_email
from .db import models

CODE_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
MAX_CODES_PER_HOUR = 5
CODE_DIGITS = 6


class LoginCodeError(ValueError):
    """The code cannot be issued or cannot be accepted."""

    status_code = 400


class TooManyLoginCodes(LoginCodeError):
    """The address has asked for more codes than the limit allows."""

    status_code = 429


class LoginCodesUnavailable(LoginCodeError):
    """This server cannot issue codes at all, whatever anybody sends it.

    A 400 would blame the caller for a deployment's missing secret, and the
    person reading it would go looking at their own email address. 503 says
    what is true: the request was fine, the server is not ready to answer it.
    """

    status_code = 503


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(value: dt.datetime) -> dt.datetime:
    """SQLite hands back naive datetimes; treat those as the UTC they were."""
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def _fingerprint(email: str, code: str) -> str:
    secret = os.getenv("JWT_SECRET", "").encode()
    if len(secret) < 32:
        raise LoginCodesUnavailable(
            "This server cannot issue login codes: JWT_SECRET is missing or too short.")
    return hmac.new(secret, f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def request_login_code(db: Session, *, email: str) -> tuple[str, str]:
    """Issue a code for `email`, returning (normalised address, plain code).

    The caller delivers it. Nothing else in the system ever sees it again.
    """
    address = normalise_email(email)

    recent = db.scalars(
        select(models.LoginCode)
        .where(models.LoginCode.email == address)
        .order_by(models.LoginCode.created_at.desc())
        .limit(MAX_CODES_PER_HOUR)
    ).all()
    hour_ago = _now() - dt.timedelta(hours=1)
    if (len(recent) >= MAX_CODES_PER_HOUR
            and all(_aware(row.created_at) > hour_ago for row in recent)):
        raise TooManyLoginCodes(
            "Too many codes requested for this address. Try again in an hour.")

    # Only the newest code works: asking for a second one is usually what
    # someone does when the first went astray, and two live codes double the
    # guessing surface for no benefit.
    for row in db.scalars(
        select(models.LoginCode).where(
            models.LoginCode.email == address,
            models.LoginCode.consumed_at.is_(None),
        )
    ):
        row.consumed_at = _now()
        db.add(row)

    code = _generate_code()
    db.add(models.LoginCode(
        email=address,
        code_hash=_fingerprint(address, code),
        expires_at=_now() + dt.timedelta(minutes=CODE_TTL_MINUTES),
    ))
    db.commit()
    return address, code


def verify_login_code(db: Session, *, email: str, code: str) -> str:
    """Return the address the code proves, or raise `LoginCodeError`.

    Every failure says the same thing. Distinguishing "wrong code" from "no code
    for that address" tells an attacker which half to keep working on.
    """
    wrong = LoginCodeError("That code is wrong or has expired. Ask for a new one.")
    address = normalise_email(email)
    code = (code or "").strip()
    if not code.isdigit() or len(code) != CODE_DIGITS:
        raise wrong

    row = db.scalar(
        select(models.LoginCode)
        .where(models.LoginCode.email == address, models.LoginCode.consumed_at.is_(None))
        .order_by(models.LoginCode.created_at.desc())
        .limit(1)
    )
    if row is None:
        raise wrong

    now = _now()
    if _aware(row.expires_at) <= now or row.attempts >= MAX_ATTEMPTS:
        row.consumed_at = now
        db.add(row)
        db.commit()
        raise wrong

    # Count the attempt before judging it, so a crash between the two cannot
    # hand out a free guess.
    row.attempts += 1
    db.add(row)
    db.commit()

    if not hmac.compare_digest(row.code_hash, _fingerprint(address, code)):
        if row.attempts >= MAX_ATTEMPTS:
            row.consumed_at = _now()
            db.add(row)
            db.commit()
        raise wrong

    row.consumed_at = _now()
    db.add(row)
    db.commit()
    return address
