"""The anonymous Hunter: reachable without a token, bounded, and stateless."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["LLM_PROVIDER"] = "none"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.models import Base
from app.db.session import get_session
from app.main import PUBLIC_PATHS, app
from app.routers import public

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_TestSession = sessionmaker(bind=_engine, expire_on_commit=False)


def _override_session():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


client = TestClient(app)

MOLDS = {"name": "Molds", "price": 27, "cogs": 6.5, "fba_fee": 3.3, "monthly_sales": 600,
         "ppc_per_unit": 2.5, "dominant_brands": 2, "median_reviews": 180}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Own database and a clean limiter for each test — and the override put
    back afterwards, because `app` is shared with every other test module."""
    previous = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = _override_session
    monkeypatch.setenv("QUEUE_ENABLED", "false")
    public.reset_for_tests()
    yield
    public.reset_for_tests()
    if previous is None:
        app.dependency_overrides.pop(get_session, None)
    else:
        app.dependency_overrides[get_session] = previous


def test_the_public_hunter_answers_without_a_token(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", "a-secure-test-secret-that-is-long-enough")
    assert public.PUBLIC_EVALUATE_PATH in PUBLIC_PATHS

    r = client.post(public.PUBLIC_EVALUATE_PATH, json={"products": [MOLDS], "explain": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["results"][0]["verdict"] == "BUY"
    assert body["results"][0]["explanation"]
    assert body["results"][0]["inputs"]["price"] == 27

    # The signed-in one still refuses.
    assert client.post("/api/product-hunter/evaluate", json={"products": [MOLDS]}).status_code == 401


def test_nothing_is_persisted_for_a_stranger():
    r = client.post(public.PUBLIC_EVALUATE_PATH, json={"products": [MOLDS]})
    assert r.status_code == 200
    with _TestSession() as db:
        assert db.scalars(select(models.ProductEvaluation)).first() is None


def test_more_than_five_candidates_is_the_paid_product():
    many = [{**MOLDS, "name": f"Item {i}"} for i in range(6)]
    r = client.post(public.PUBLIC_EVALUATE_PATH, json={"products": many})
    assert r.status_code == 422
    assert "Sign in" in r.json()["detail"]

    assert client.post(public.PUBLIC_EVALUATE_PATH, json={"products": []}).status_code == 422


def test_an_address_is_rate_limited_per_hour(monkeypatch):
    monkeypatch.setenv("PUBLIC_EVALUATE_PER_HOUR", "2")
    headers = {"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
    for _ in range(2):
        assert client.post(public.PUBLIC_EVALUATE_PATH, json={"products": [MOLDS]},
                           headers=headers).status_code == 200
    blocked = client.post(public.PUBLIC_EVALUATE_PATH, json={"products": [MOLDS]}, headers=headers)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "3600"

    # A different visitor is not punished for the first one's curiosity.
    other = {"X-Forwarded-For": "198.51.100.2"}
    assert client.post(public.PUBLIC_EVALUATE_PATH, json={"products": [MOLDS]},
                       headers=other).status_code == 200


def test_the_window_slides():
    assert public.allow("a", limit=1, now=1000.0)
    assert not public.allow("a", limit=1, now=1500.0)
    assert public.allow("a", limit=1, now=1000.0 + public.WINDOW_SECONDS + 1)
