"""
Persistence helpers for the AI Product Hunter.

Every evaluation is stored (`product_evaluations`) — this anonymized history of
what sellers looked at and what the engine decided is the long-term data moat
(see docs/ARCHITECTURE.md). Until auth exists, evaluations are attached to a
single dev user.
"""
from __future__ import annotations

import math
import os
import uuid
import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models
from ..security import auth_enabled, current_principal

DEV_USER_EMAIL = "dev@local"


def _tenant_id() -> uuid.UUID | None:
    principal = current_principal()
    return uuid.UUID(principal.tenant_id) if principal else None


def get_or_create_dev_user(db: Session) -> models.User:
    """Placeholder owner for evaluations until real auth lands (Phase 1)."""
    principal = current_principal()
    if principal:
        user_id = uuid.UUID(principal.user_id)
        user = db.get(models.User, user_id)
        if user is None:
            user = models.User(id=user_id, email=f"{user_id}@identity.invalid")
            db.add(user)
            db.commit()
            db.refresh(user)
        return user
    if auth_enabled():
        raise RuntimeError("Authenticated tenant context is required.")
    user = db.scalar(select(models.User).where(models.User.email == DEV_USER_EMAIL))
    if user is None:
        user = models.User(email=DEV_USER_EMAIL)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _json_safe(value: Any) -> Any:
    """Drop non-finite floats (e.g. inf ROI on zero-cost items) so JSON/JSONB stays valid."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def save_evaluation(
    db: Session,
    *,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
    name: str,
    inputs: dict,
    economics: dict,
    subscores: dict,
    score: int,
    verdict: str,
    explanation: str | None = None,
) -> models.ProductEvaluation:
    tenant_id = _tenant_id()
    if tenant_id and store_id != tenant_id:
        raise PermissionError("Cannot write data for another tenant.")
    row = models.ProductEvaluation(
        user_id=user_id,
        store_id=store_id,
        name=name,
        inputs=_json_safe(inputs),
        economics=_json_safe(economics),
        subscores=_json_safe(subscores),
        score=score,
        verdict=verdict,
        explanation=explanation,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def recent_evaluations(db: Session, limit: int = 20) -> list[models.ProductEvaluation]:
    q = select(models.ProductEvaluation)
    tenant_id = _tenant_id()
    if tenant_id:
        q = q.where(models.ProductEvaluation.store_id == tenant_id)
    q = q.order_by(models.ProductEvaluation.created_at.desc()).limit(limit)
    return list(db.scalars(q))


def get_or_create_dev_store(db: Session) -> models.Store:
    """Placeholder store for the dev user until real auth + stores exist (Phase 1)."""
    user = get_or_create_dev_user(db)
    tenant_id = _tenant_id()
    if tenant_id:
        store = db.get(models.Store, tenant_id)
        if store is None:
            store = models.Store(id=tenant_id, user_id=user.id, marketplace="US")
            db.add(store)
            db.commit()
            db.refresh(store)
        return store
    store = db.scalar(select(models.Store).where(models.Store.user_id == user.id))
    if store is None:
        store = models.Store(user_id=user.id, marketplace="US")
        db.add(store)
        db.commit()
        db.refresh(store)
    return store


def save_recommendation(
    db: Session,
    *,
    store_id: uuid.UUID,
    module: str,
    severity: str,
    title: str,
    detail: dict,
    commit: bool = True,
) -> models.Recommendation:
    tenant_id = _tenant_id()
    if tenant_id and store_id != tenant_id:
        raise PermissionError("Cannot write data for another tenant.")
    row = models.Recommendation(
        store_id=store_id,
        module=module,
        severity=severity,
        title=title,
        detail=_json_safe(detail),
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def recent_recommendations(
    db: Session, module: str | None = None, limit: int = 20
) -> list[models.Recommendation]:
    q = select(models.Recommendation)
    tenant_id = _tenant_id()
    if tenant_id:
        q = q.where(models.Recommendation.store_id == tenant_id)
    if module:
        q = q.where(models.Recommendation.module == module)
    q = q.order_by(models.Recommendation.created_at.desc()).limit(limit)
    return list(db.scalars(q))


def get_recommendation(db: Session, rec_id: str) -> models.Recommendation | None:
    try:
        rid = uuid.UUID(str(rec_id))
    except (ValueError, AttributeError):
        return None
    row = db.get(models.Recommendation, rid)
    tenant_id = _tenant_id()
    if row is not None and tenant_id and row.store_id != tenant_id:
        return None
    return row


def clear_recommendations(db: Session, module: str, status: str | None = None) -> int:
    """Delete recommendations for a module (optionally only a given status). Returns count."""
    rows = recent_recommendations(db, module=module, limit=100_000)
    if status:
        rows = [r for r in rows if (r.detail or {}).get("status") == status]
    for r in rows:
        db.delete(r)
    db.commit()
    return len(rows)


def set_recommendation_status(
    db: Session, row: models.Recommendation, status: str, *, commit: bool = True
) -> models.Recommendation:
    """Update the 'status' field inside a recommendation's JSON detail (reassign to trigger update)."""
    detail = dict(row.detail or {})
    detail["status"] = status
    row.detail = detail
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


# How long an operation may hold a claim before we conclude its process is gone.
# Generous: the longest real job here is an Amazon report, which can take many
# minutes. Anything past this did not finish slowly, it stopped existing.
STALE_CLAIM_SECONDS = int(os.getenv("IDEMPOTENCY_STALE_SECONDS", "3600"))


def _claim_is_abandoned(record: models.IdempotencyRecord) -> bool:
    """Can this claim be taken over, or is the operation genuinely running?

    A record sits in `processing` for two reasons, and they need opposite
    answers: the operation is in flight, or the process holding it died before it
    could record anything. The second leaves the key unusable forever — every
    retry answered 409 — which turns one crash into a permanently blocked
    operation.

    `failed` is reclaimable too. Refusing to retry a failed operation under the
    same key is the opposite of what an idempotency key is for.
    """
    if record.status == "failed":
        return True
    if record.status != "processing":
        return False
    started = record.created_at
    if started is None:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - started).total_seconds() > STALE_CLAIM_SECONDS


def claim_is_in_flight(record: models.IdempotencyRecord) -> bool:
    """Is this operation genuinely running right now?

    The inverse of _claim_is_abandoned, named for the callers who want to avoid
    starting a second copy of something rather than to decide whether a key may
    be reused. One rule, two questions; a second copy of the rule would drift.
    """
    return not _claim_is_abandoned(record)


def claim_idempotency(
    db: Session, *, store_id: uuid.UUID, operation: str, key: str
) -> tuple[models.IdempotencyRecord, bool]:
    """Atomically claim a tenant-scoped operation; return (record, is_new)."""
    record = models.IdempotencyRecord(
        store_id=store_id, operation=operation, key=key, status="processing"
    )
    db.add(record)
    try:
        db.commit()
        db.refresh(record)
        return record, True
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(models.IdempotencyRecord).where(
            models.IdempotencyRecord.store_id == store_id,
            models.IdempotencyRecord.operation == operation,
            models.IdempotencyRecord.key == key,
        ))
        if existing is None:  # pragma: no cover - defensive race protection
            raise
        if _claim_is_abandoned(existing):
            existing.status = "processing"
            existing.response = None
            existing.completed_at = None
            existing.created_at = dt.datetime.now(dt.timezone.utc)
            db.add(existing)
            db.commit()
            db.refresh(existing)
            return existing, True
        return existing, False


def complete_idempotency(db: Session, record: models.IdempotencyRecord, response: dict) -> None:
    record.status = "completed"
    record.response = _json_safe(response)
    record.completed_at = dt.datetime.now(dt.timezone.utc)
    db.add(record)
    db.commit()


def fail_idempotency(db: Session, record: models.IdempotencyRecord) -> None:
    record.status = "failed"
    db.add(record)
    db.commit()


def append_audit_event(
    db: Session, *, store_id: uuid.UUID, actor_id: uuid.UUID, action: str,
    resource_type: str, resource_id: str | None = None,
    idempotency_key: str | None = None, before: dict | None = None,
    after: dict | None = None, commit: bool = True,
) -> models.AuditEvent:
    event = models.AuditEvent(
        store_id=store_id, actor_id=actor_id, action=action,
        resource_type=resource_type, resource_id=resource_id,
        idempotency_key=idempotency_key, before=_json_safe(before), after=_json_safe(after),
    )
    db.add(event)
    if commit:
        db.commit()
        db.refresh(event)
    else:
        db.flush()
    return event
