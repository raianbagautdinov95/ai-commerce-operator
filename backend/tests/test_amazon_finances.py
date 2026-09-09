import datetime as dt

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tasks
from app.db import crud, models
from app.db.models import Base


class FakeFinancesClient:
    def transaction_pages(self, **kwargs):
        posted = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
        yield {"transactions": [{
            "postedDate": posted,
            "breakdowns": [{
                "breakdownType": "Expenses",
                "breakdownAmount": {"currencyCode": "EUR", "currencyAmount": -12.50},
                "breakdowns": [{"breakdownType": "Commission",
                                "breakdownAmount": {"currencyCode": "EUR", "currencyAmount": -12.50}}],
            }],
        }]}


def test_finance_sync_normalizes_negative_expense_to_positive_cost(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    store = crud.get_or_create_dev_store(db)
    record, _ = crud.claim_idempotency(
        db, store_id=store.id, operation="amazon.finances_sync:A1:30", key="finance-1"
    )
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks.amazon_sp_api, "client_for_store",
                        lambda *args, **kwargs: FakeFinancesClient())
    result = tasks.run_amazon_finances_sync(
        tenant_id=str(store.id), actor_id=str(store.user_id), region="EU",
        marketplace_id="A1", days=30, idempotency_record_id=str(record.id),
    )
    assert result["amazon_fees"] == 12.5
    verify = factory()
    cost = verify.scalar(select(models.AmazonCostDaily))
    assert float(cost.amount) == 12.5
    assert cost.category == "amazon_fees"
    assert cost.source == "sp_api_finances"
    assert verify.get(models.IdempotencyRecord, record.id).status == "completed"
