from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db import crud, models
from app.db.models import Base


def _fresh_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_idempotency_replays_completed_result():
    db = _fresh_session()
    store = crud.get_or_create_dev_store(db)
    record, is_new = crud.claim_idempotency(
        db, store_id=store.id, operation="autopilot.scan", key="request-1"
    )
    assert is_new is True

    crud.append_audit_event(
        db, store_id=store.id, actor_id=store.user_id, action="autopilot.scan",
        resource_type="autopilot_queue", idempotency_key="request-1",
        after={"queued": 1}, commit=False,
    )
    crud.complete_idempotency(db, record, {"queued": 1, "skipped": 0})

    replay, is_new = crud.claim_idempotency(
        db, store_id=store.id, operation="autopilot.scan", key="request-1"
    )
    assert is_new is False
    assert replay.status == "completed"
    assert replay.response == {"queued": 1, "skipped": 0}
    assert db.scalar(select(func.count()).select_from(models.IdempotencyRecord)) == 1
    assert db.scalar(select(func.count()).select_from(models.AuditEvent)) == 1


def test_same_key_is_independent_between_operations():
    db = _fresh_session()
    store = crud.get_or_create_dev_store(db)
    _, first_new = crud.claim_idempotency(
        db, store_id=store.id, operation="autopilot.scan", key="same-key"
    )
    _, second_new = crud.claim_idempotency(
        db, store_id=store.id, operation="autopilot.decision:123", key="same-key"
    )
    assert first_new is True
    assert second_new is True
