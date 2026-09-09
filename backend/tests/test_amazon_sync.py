from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tasks
from app.db import crud, models
from app.db.models import Base


class FakeAmazonClient:
    def marketplace_participations(self):
        return {"payload": {"marketplaceParticipations": [{"marketplace": {"id": "A1"}}]}}

    def listing_pages(self, **kwargs):
        yield {"items": [{
            "sku": "SKU-1", "status": ["BUYABLE"], "issues": [],
            "summaries": [{"itemName": "Test product"}],
            "identifiers": [{"identifiers": [{"identifierType": "ASIN", "identifier": "B000TEST"}]}],
        }]}

    def inventory_pages(self, **kwargs):
        yield {"payload": {"inventorySummaries": [{
            "sellerSku": "SKU-1", "asin": "B000TEST", "fnSku": "FN-1",
            "totalQuantity": 7, "inventoryDetails": {"fulfillableQuantity": 5},
        }]}}


def test_read_only_sync_is_idempotent_and_persists_snapshots(monkeypatch):
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
        setup, store_id=store.id, operation="amazon.sync", key="sync-1"
    )
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks.amazon_sp_api, "client_for_store", lambda *a, **k: FakeAmazonClient())

    result = tasks.run_amazon_sync(
        tenant_id=str(store.id), actor_id=str(store.user_id), region="EU",
        idempotency_record_id=str(record.id),
    )
    assert result == {"listings": 1, "inventory": 1, "marketplaces": 1}

    verify = factory()
    assert verify.scalar(select(func.count()).select_from(models.AmazonListing)) == 1
    inventory = verify.scalar(select(models.AmazonInventory))
    assert inventory.fulfillable_quantity == 5
    assert inventory.total_quantity == 7
    completed = verify.get(models.IdempotencyRecord, record.id)
    assert completed.status == "completed"
