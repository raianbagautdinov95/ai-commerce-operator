"""
What an idempotency key does after the process holding it dies.

A key exists so a retry is safe. If a crash can leave the key permanently
unusable, it does the opposite: one dead process turns into an operation that
can never be run again.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import datetime as dt
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import crud, models
from app.db.models import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def _claim(db, store, key="k-1"):
    return crud.claim_idempotency(db, store_id=store.id, operation="test.op", key=key)


def _age(db, record, seconds):
    record.created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)
    db.add(record); db.commit(); db.refresh(record)


def test_the_first_claim_is_new_and_the_second_is_not(db):
    store = crud.get_or_create_dev_store(db)
    record, is_new = _claim(db, store)
    assert is_new
    again, is_new_again = _claim(db, store)
    assert not is_new_again
    assert again.id == record.id


def test_a_completed_operation_keeps_returning_its_answer(db):
    store = crud.get_or_create_dev_store(db)
    record, _ = _claim(db, store)
    crud.complete_idempotency(db, record, {"queued": 3})

    again, is_new = _claim(db, store)
    assert not is_new
    assert again.status == "completed"
    assert again.response == {"queued": 3}


def test_a_crashed_claim_can_be_taken_over_rather_than_blocking_forever(db):
    """The process died before it could record anything. The key must still work."""
    store = crud.get_or_create_dev_store(db)
    record, _ = _claim(db, store)
    _age(db, record, crud.STALE_CLAIM_SECONDS + 60)

    again, is_new = _claim(db, store)
    assert is_new, "the key was left permanently unusable"
    assert again.id == record.id
    assert again.status == "processing"


def test_an_operation_that_is_genuinely_running_is_not_stolen(db):
    """Reclaiming eagerly would run the same operation twice, which is the point
    of the key in the first place."""
    store = crud.get_or_create_dev_store(db)
    record, _ = _claim(db, store)
    _age(db, record, 5)

    _, is_new = _claim(db, store)
    assert not is_new


def test_a_failed_operation_is_retryable_under_the_same_key(db):
    """Refusing that is the opposite of what an idempotency key is for."""
    store = crud.get_or_create_dev_store(db)
    record, _ = _claim(db, store)
    crud.fail_idempotency(db, record)

    again, is_new = _claim(db, store)
    assert is_new
    assert again.status == "processing"


def test_taking_over_clears_the_previous_attempts_leftovers(db):
    store = crud.get_or_create_dev_store(db)
    record, _ = _claim(db, store)
    record.response = {"half": "written"}
    record.completed_at = dt.datetime.now(dt.timezone.utc)
    db.add(record); db.commit()
    _age(db, record, crud.STALE_CLAIM_SECONDS + 60)

    again, is_new = _claim(db, store)
    assert is_new
    assert again.response is None
    assert again.completed_at is None


def test_one_tenants_key_never_touches_anothers(db):
    store = crud.get_or_create_dev_store(db)
    other_user = models.User(email="other@local")
    db.add(other_user); db.commit()
    other_store = models.Store(user_id=other_user.id, marketplace="US")
    db.add(other_store); db.commit()

    mine, _ = _claim(db, store, key="shared-key")
    theirs, is_new = crud.claim_idempotency(
        db, store_id=other_store.id, operation="test.op", key="shared-key")
    assert is_new
    assert theirs.id != mine.id

    rows = list(db.scalars(select(models.IdempotencyRecord)))
    assert len(rows) == 2
