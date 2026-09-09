"""Request-scoped helpers shared by every router."""
from __future__ import annotations

import uuid

from fastapi import HTTPException

from sqlalchemy.orm import Session

from .db import crud
from .runtime import log
from .security import auth_enabled, current_principal


def _idempotency_key(value: str | None) -> str:
    """Every mutating route takes one, so a retry cannot repeat the work.

    Generated when auth is off, because a single-user development install has
    nobody to blame for a duplicate; required otherwise.
    """
    if not value:
        if auth_enabled():
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        return str(uuid.uuid4())
    if len(value) > 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key is too long")
    return value


def _actor_id(default: uuid.UUID) -> uuid.UUID:
    """Who is doing this: the authenticated principal, or the dev user."""
    principal = current_principal()
    return uuid.UUID(principal.user_id) if principal else default


def _persist_recommendation(db: Session, module: str, severity: str, title: str, detail: dict) -> None:
    """Store one module analysis in `recommendations`. Best-effort: never breaks the response."""
    try:
        store = crud.get_or_create_dev_store(db)
        crud.save_recommendation(db, store_id=store.id, module=module,
                                 severity=severity, title=title, detail=detail)
    except Exception:  # pragma: no cover - defensive
        log.exception("Failed to persist %s analysis (%s)", module, title)
        db.rollback()
