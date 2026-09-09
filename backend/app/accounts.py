"""One place that turns a verified email address into an account.

Two doors now lead into this deployment — Sign in with Google, and a six-digit
code sent to an email address — and the Shopify listing will add a third. They
have to arrive at the *same* user and the same tenant for one person, or a
seller who installs from Shopify and later signs in with Google ends up owning
two stores with half their history in each, and no way to merge them.

So the rule is: an account is identified by its email address, trimmed and
lowercased, and by nothing else. Every entry point resolves through
`ensure_account`; none of them constructs a User or a Store of its own.

**Only verified addresses may be passed here.** `users.email` is unique, which
means resolving an unverified address hands the caller somebody else's tenant.
The two callers each carry a proof: Google's `email_verified` claim, and
possession of a code that was sent to the address and nowhere else.

Deliberately *not* done here: normalising away dots or `+tags` in the local
part. That behaviour is specific to Gmail, and applying it to a Workspace or
custom domain silently merges two different people into one account.
"""
from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import models

# How long a self-service session lasts. Shorter than the 30 days a hand-issued
# CLI token gets, because signing in again is now one click rather than an email
# to whoever runs the server — and because no token here can be revoked
# individually, so a short life is the only revocation there is.
DEFAULT_SESSION_DAYS = 7
MAX_SESSION_DAYS = 30


class InvalidEmailError(ValueError):
    """The address is not one we can key an account on."""


def normalise_email(value: str) -> str:
    """The single spelling of an address that the database is keyed on."""
    email = (value or "").strip().lower()
    if len(email) > 255 or email.count("@") != 1:
        raise InvalidEmailError("Enter a valid email address.")
    local, _, domain = email.partition("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise InvalidEmailError("Enter a valid email address.")
    if any(character.isspace() for character in email):
        raise InvalidEmailError("Enter a valid email address.")
    return email


def session_days() -> int:
    """Lifetime of a token issued by a login, clamped to something defensible."""
    try:
        days = int(os.getenv("SESSION_TOKEN_DAYS", str(DEFAULT_SESSION_DAYS)))
    except ValueError:
        return DEFAULT_SESSION_DAYS
    return max(1, min(days, MAX_SESSION_DAYS))


def ensure_account(db: Session, *, email: str) -> tuple[models.User, models.Store]:
    """Find or create the person, and the one store their token is scoped to.

    Idempotent by email: a second sign-in through a different door returns the
    same user and the same tenant rather than creating a parallel one.
    """
    address = normalise_email(email)
    user = db.scalar(select(models.User).where(models.User.email == address))
    if user is None:
        user = models.User(email=address)
        db.add(user)
        db.commit()
        db.refresh(user)
    store = db.scalar(select(models.Store).where(models.Store.user_id == user.id))
    if store is None:
        store = models.Store(user_id=user.id,
                             marketplace=os.getenv("DEFAULT_MARKETPLACE", "US"))
        db.add(store)
        db.commit()
        db.refresh(store)
    return user, store
