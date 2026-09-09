import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import crud
from app.db.models import Base
from app.security import Principal, bind_principal, reset_principal


def _fresh_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _principal():
    return Principal(user_id=str(uuid.uuid4()), tenant_id=str(uuid.uuid4()), role="operator")


def test_recommendations_are_isolated_between_tenants(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    db = _fresh_session()
    first, second = _principal(), _principal()

    token = bind_principal(first)
    first_store = crud.get_or_create_dev_store(db)
    first_row = crud.save_recommendation(
        db, store_id=first_store.id, module="ppc", severity="info",
        title="first tenant", detail={"status": "pending"},
    )
    reset_principal(token)

    token = bind_principal(second)
    second_store = crud.get_or_create_dev_store(db)
    crud.save_recommendation(
        db, store_id=second_store.id, module="ppc", severity="info",
        title="second tenant", detail={"status": "pending"},
    )
    assert [row.title for row in crud.recent_recommendations(db)] == ["second tenant"]
    assert crud.get_recommendation(db, str(first_row.id)) is None
    assert crud.clear_recommendations(db, "ppc") == 1
    reset_principal(token)

    token = bind_principal(first)
    assert [row.title for row in crud.recent_recommendations(db)] == ["first tenant"]
    reset_principal(token)


def test_evaluations_are_isolated_between_tenants(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    db = _fresh_session()
    first, second = _principal(), _principal()

    for principal, name in ((first, "first"), (second, "second")):
        token = bind_principal(principal)
        store = crud.get_or_create_dev_store(db)
        crud.save_evaluation(
            db, user_id=store.user_id, store_id=store.id, name=name,
            inputs={}, economics={}, subscores={}, score=1, verdict="CAUTION",
        )
        reset_principal(token)

    token = bind_principal(first)
    assert [row.name for row in crud.recent_evaluations(db)] == ["first"]
    reset_principal(token)
