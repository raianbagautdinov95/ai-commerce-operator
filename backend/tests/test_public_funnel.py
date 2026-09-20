"""Our side of the click: what the /try page records, and what it never does."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LLM_PROVIDER"] = "none"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import funnel
from app.db import models
from app.db.models import Base
from app.db.session import get_session
from app.main import PUBLIC_PATHS, app
from app.routers import public

MOLDS = {"name": "Molds", "price": 27, "cogs": 6.5, "fba_fee": 3.3, "monthly_sales": 600}


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        s = factory()
        try: yield s
        finally: s.close()

    previous = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = override
    monkeypatch.setenv("QUEUE_ENABLED", "false")
    public.reset_for_tests()
    yield factory()
    public.reset_for_tests()
    if previous is None: app.dependency_overrides.pop(get_session, None)
    else: app.dependency_overrides[get_session] = previous


client = TestClient(app)
FROM_AD = {"X-Forwarded-For": "203.0.113.9", "User-Agent": "phone"}


def test_a_visit_is_recorded_with_its_source_and_without_its_address(db):
    assert public.PUBLIC_VISIT_PATH in PUBLIC_PATHS
    r = client.post(public.PUBLIC_VISIT_PATH, headers=FROM_AD,
                    json={"source": "Facebook", "medium": "paid", "campaign": "try-us"})
    assert r.status_code == 204
    row = db.scalar(select(models.PublicFunnelEvent))
    assert row.kind == "visit"
    assert (row.source, row.medium, row.campaign) == ("facebook", "paid", "try-us")
    assert "203.0.113.9" not in row.visitor and len(row.visitor) == 64


def test_the_same_visitor_reloading_is_one_visit(db):
    for _ in range(4):
        client.post(public.PUBLIC_VISIT_PATH, headers=FROM_AD, json={"source": "facebook"})
    client.post(public.PUBLIC_VISIT_PATH, json={"source": "facebook"},
                headers={"X-Forwarded-For": "198.51.100.4", "User-Agent": "laptop"})
    rows = funnel.funnel(db, days=1)
    fb = next(r for r in rows if r["source"] == "facebook")
    assert fb["visits"] == 2
    assert db.scalar(select(models.PublicFunnelEvent).where(
        models.PublicFunnelEvent.visitor == "")) is None


def test_an_evaluation_carries_the_attribution_it_was_given(db):
    r = client.post(public.PUBLIC_EVALUATE_PATH, headers=FROM_AD,
                    json={"products": [MOLDS], "attribution": {"source": "instagram"}})
    assert r.status_code == 200
    r = client.post(public.PUBLIC_EVALUATE_PATH, headers=FROM_AD, json={"products": [MOLDS]})
    assert r.status_code == 200
    rows = funnel.funnel(db, days=1)
    assert {(x["source"], x["evaluations"]) for x in rows} >= {("instagram", 1), ("(direct)", 1)}


def test_signups_appear_in_the_funnel_without_a_source(db):
    db.add(models.User(email="new@example.test")); db.commit()
    rows = funnel.funnel(db, days=1)
    assert any(r["source"] == "(signups, any source)" and r["signups"] == 1 for r in rows)


def test_an_oversized_source_is_refused_not_stored(db):
    r = client.post(public.PUBLIC_VISIT_PATH, headers=FROM_AD, json={"source": "x" * 65})
    assert r.status_code == 422
    assert db.scalar(select(models.PublicFunnelEvent)) is None
