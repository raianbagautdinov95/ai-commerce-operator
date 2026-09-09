import datetime as dt

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import billing
from app.db import crud, models
from app.db.models import Base


def _session():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_trial_is_created_once_and_has_server_owned_entitlements(monkeypatch):
    monkeypatch.setenv("TRIAL_DAYS", "14")
    db = _session(); store = crud.get_or_create_dev_store(db)
    first = billing.ensure_subscription(db, store_id=store.id)
    second = billing.ensure_subscription(db, store_id=store.id)
    assert first.id == second.id and first.status == "trialing" and first.plan == "operator"
    assert billing.entitlement(first.plan) == {
        "connected_channels": 3, "monthly_ai_actions": 1000, "autopilot": True,
    }


def test_expired_trial_is_closed_server_side():
    db = _session(); store = crud.get_or_create_dev_store(db)
    row = models.Subscription(
        store_id=store.id, plan="operator", status="trialing",
        trial_ends_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1),
    )
    db.add(row); db.commit()
    assert billing.ensure_subscription(db, store_id=store.id).status == "trial_expired"
