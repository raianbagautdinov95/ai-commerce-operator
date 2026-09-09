import datetime as dt

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tasks
from app.db import crud, models
from app.db.models import Base


class FakeReportsClient:
    def __init__(self):
        self.created = 0

    def create_sales_traffic_report(self, **kwargs):
        self.created += 1
        return {"reportId": "report-1"}

    def get_report(self, report_id):
        return {"processingStatus": "DONE", "reportDocumentId": "document-1"}

    def get_report_document(self, document_id):
        return {"url": "https://download.example.test/report"}

    def download_report_document(self, metadata):
        day = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()
        return {"salesAndTrafficByDate": [{
            "date": day,
            "salesByDate": {
                "orderedProductSales": {"amount": "123.45", "currencyCode": "EUR"},
                "unitsOrdered": 7, "totalOrderItems": 6,
            },
            "trafficByDate": {"pageViews": 100, "sessions": 80,
                              "buyBoxPercentage": 95.5},
        }], "salesAndTrafficByAsin": [{
            "sku": "SKU-1",
            "salesByAsin": {
                "orderedProductSales": {"amount": "123.45", "currencyCode": "EUR"},
                "unitsOrdered": 7,
            },
        }]}


def test_sales_report_is_tenant_bound_idempotent_and_normalized(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    setup = factory()
    store = crud.get_or_create_dev_store(setup)
    store.seller_id = "SELLER-1"
    setup.add(store)
    setup.commit()
    record, _ = crud.claim_idempotency(
        setup, store_id=store.id, operation="amazon.sales_report:A1:30", key="report-1"
    )
    fake = FakeReportsClient()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks.amazon_sp_api, "client_for_store", lambda *a, **k: fake)

    kwargs = dict(tenant_id=str(store.id), actor_id=str(store.user_id), region="EU",
                  marketplace_id="A1", days=30, idempotency_record_id=str(record.id))
    first = tasks.run_amazon_sales_report(**kwargs)
    second = tasks.run_amazon_sales_report(**kwargs)
    assert first == second
    assert fake.created == 1

    verify = factory()
    assert verify.scalar(select(func.count()).select_from(models.AmazonSalesDaily)) == 1
    daily = verify.scalar(select(models.AmazonSalesDaily))
    assert float(daily.ordered_sales) == 123.45
    assert daily.units_ordered == 7
    assert daily.sessions == 80
    assert verify.get(models.AmazonReportRun, record.id).status == "completed"
    assert verify.get(models.IdempotencyRecord, record.id).status == "completed"
    sku = verify.scalar(select(models.AmazonSalesSkuPeriod))
    assert sku.sku == "SKU-1" and sku.units_ordered == 7
