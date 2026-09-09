"""
Persistence tests — run against an in-memory SQLite DB (no infrastructure).
Verifies the portable ORM types work and evaluations round-trip, including the
inf-ROI (zero-cost) edge case being stored as null rather than breaking JSON.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db import crud
from app.decision_engine import ProductInput, evaluate


def _fresh_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_save_and_read_back():
    db = _fresh_session()
    user = crud.get_or_create_dev_user(db)
    store = crud.get_or_create_dev_store(db)
    # get_or_create is idempotent
    assert crud.get_or_create_dev_user(db).id == user.id

    e = evaluate(ProductInput(name="Silicone baking molds", price=27, cogs=6.5,
                              fba_fee=3.30, monthly_sales=600, ppc_per_unit=2.5))
    crud.save_evaluation(
        db, user_id=user.id, store_id=store.id, name=e.name, inputs={"price": 27},
        economics=e.economics.__dict__, subscores=e.subscores,
        score=e.score, verdict=e.verdict.value, explanation="ok",
    )

    rows = crud.recent_evaluations(db)
    assert len(rows) == 1
    assert rows[0].name == "Silicone baking molds"
    assert rows[0].verdict == "BUY"
    assert rows[0].economics["margin"] > 0


def test_ppc_recommendation_round_trip():
    db = _fresh_session()
    store = crud.get_or_create_dev_store(db)
    assert crud.get_or_create_dev_store(db).id == store.id  # idempotent
    crud.save_recommendation(
        db, store_id=store.id, module="ppc", severity="critical",
        title="Silicone molds", detail={"summary": {"wasted_spend": 25.0}},
    )
    crud.save_recommendation(
        db, store_id=store.id, module="ppc", severity="info",
        title="Other", detail={"summary": {"wasted_spend": 0.0}},
    )
    rows = crud.recent_recommendations(db, module="ppc")
    assert len(rows) == 2
    assert rows[0].detail["summary"]["wasted_spend"] in (25.0, 0.0)
    # module filter excludes other modules
    assert crud.recent_recommendations(db, module="inventory") == []


def test_inf_roi_is_stored_as_null():
    db = _fresh_session()
    user = crud.get_or_create_dev_user(db)
    store = crud.get_or_create_dev_store(db)
    e = evaluate(ProductInput(name="freebie", price=25, cogs=0, fba_fee=3.30,
                              monthly_sales=100, ppc_per_unit=2))
    assert e.economics.roi == float("inf")
    crud.save_evaluation(
        db, user_id=user.id, store_id=store.id, name=e.name, inputs={},
        economics=e.economics.__dict__, subscores=e.subscores,
        score=e.score, verdict=e.verdict.value,
    )
    rows = crud.recent_evaluations(db)
    assert rows[0].economics["roi"] is None  # inf sanitized, JSON stays valid
