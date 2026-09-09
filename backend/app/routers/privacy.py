"""Data export and deletion requests (GDPR)."""

from .. import billing
from ..db import crud, models
from .. import legal
from ..db.session import get_session
from ..schemas import LegalDetailsResponse, DeletionRequest, PrivacyRequestResponse
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
import datetime as dt
import uuid

from ..deps import _actor_id

router = APIRouter()


@router.get("/api/privacy/export")
def privacy_export(db: Session = Depends(get_session)) -> dict:
    store = crud.get_or_create_dev_store(db); user = db.get(models.User, store.user_id)
    channels = list(db.scalars(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store.id)))
    subscription = billing.ensure_subscription(db, store_id=store.id)
    # Costs are the seller's own working data — what they pay for their stock,
    # and in many cases typed in by hand. Leaving it out of an export would mean
    # the one thing they cannot get back from Shopify is the one thing we keep.
    costs = list(db.scalars(select(models.ProductCost).where(
        models.ProductCost.store_id == store.id
    ).order_by(models.ProductCost.external_product_id,
               models.ProductCost.effective_from)))
    return {"exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "account": {"user_id": str(user.id), "email": user.email,
                        "workspace_id": str(store.id), "created_at": store.created_at.isoformat()},
            "subscription": {"plan": subscription.plan, "status": subscription.status},
            "channels": [{"provider": row.provider, "display_name": row.display_name,
                          "status": row.status, "connected_at": row.connected_at.isoformat()}
                         for row in channels],
            "product_costs": [{"product_id": row.external_product_id,
                               "variant_id": row.external_variant_id or None,
                               "variant_title": row.variant_title,
                               "amount": str(row.amount), "currency": row.currency,
                               "effective_from": row.effective_from.isoformat(),
                               "source": row.source, "verification": row.verification,
                               "note": row.note,
                               "observed_at": row.observed_at.isoformat()}
                              for row in costs],
            "notice": "Secrets, tokens, customer payloads and internal security records are excluded."}


@router.post("/api/privacy/deletion-request", response_model=PrivacyRequestResponse)
def privacy_deletion_request(req: DeletionRequest, db: Session = Depends(get_session)) \
        -> PrivacyRequestResponse:
    if req.confirmation != "DELETE MY WORKSPACE":
        raise HTTPException(status_code=400, detail="Deletion confirmation does not match")
    store = crud.get_or_create_dev_store(db); request_id = str(uuid.uuid4())
    crud.append_audit_event(db, store_id=store.id, actor_id=_actor_id(store.user_id),
                            action="privacy.deletion_requested", resource_type="workspace",
                            resource_id=str(store.id), after={"request_id": request_id,
                            "status": "pending_review"})
    return PrivacyRequestResponse(request_id=request_id, status="pending_review")


@router.get("/api/legal", response_model=LegalDetailsResponse)
def legal_details() -> LegalDetailsResponse:
    """Who is accountable, who receives data, and for how long it is kept.

    Public on purpose: a privacy policy nobody can read without an account is
    not a privacy policy.
    """
    return LegalDetailsResponse(**legal.details().to_dict())
