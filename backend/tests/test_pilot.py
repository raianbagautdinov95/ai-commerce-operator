from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app import pilot
from app.db import models
from app.db.models import Base
from app.db.session import get_session
from app.main import app


def _session():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _shop(db, number: int):
    user = models.User(email=f"pilot-{number}@example.test")
    db.add(user); db.flush()
    store = models.Store(user_id=user.id)
    db.add(store); db.flush()
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id=f"pilot-{number}.myshopify.com",
        display_name=f"pilot-{number}", status="connected", currency="USD",
    ))
    db.commit()


def test_feedback_pilot_admits_first_ten_connected_shops(monkeypatch):
    monkeypatch.setenv("PILOT_MAX_SHOPIFY_STORES", "10")
    db = _session()
    for number in range(10):
        _shop(db, number)
    assert pilot.has_space_for_shopify(db, shop="pilot-0.myshopify.com") is True
    assert pilot.has_space_for_shopify(db, shop="new-shop.myshopify.com") is False
    assert pilot.enrolled_shopify_stores(db) == 10


def test_feedback_pilot_allows_a_disconnected_participant_to_reconnect(monkeypatch):
    monkeypatch.setenv("PILOT_MAX_SHOPIFY_STORES", "10")
    db = _session()
    for number in range(10):
        _shop(db, number)

    user = models.User(email="returning@example.test")
    db.add(user); db.flush()
    store = models.Store(user_id=user.id)
    db.add(store); db.flush()
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id="returning-shop.myshopify.com",
        display_name="returning shop", status="disconnected", currency="USD",
    ))
    db.commit()

    assert pilot.has_space_for_shopify(db, shop="returning-shop.myshopify.com") is True


def test_public_pilot_status_shows_places_before_oauth(monkeypatch):
    monkeypatch.setenv("PILOT_MAX_SHOPIFY_STORES", "10")
    monkeypatch.setenv("TRIAL_DAYS", "15")
    db = _session()
    _shop(db, 1)

    def override():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_session] = override
    try:
        response = TestClient(app).get("/api/integrations/shopify/pilot")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    assert response.json() == {
        "maximum_stores": 10,
        "enrolled_stores": 1,
        "remaining_stores": 9,
        "available": True,
        "trial_days": 15,
    }
