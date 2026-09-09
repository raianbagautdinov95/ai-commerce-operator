"""Unattended niche scanning and its approval queue."""

from ..autopilot_service import (meets_money_criteria as _meets_money_criteria,
                                operator_to_detail as _operator_to_detail)
from ..db import crud
from ..db.session import get_session
from ..queueing import queue_enabled
from ..schemas import (
    AutopilotScanRequest, AutopilotScanResponse, BackgroundJobResponse, DecisionRequest,
    DecisionResponse, QueueItem, QueueResponse, PortfolioResponse)
from ..security import auth_enabled, current_principal
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from ..runtime import log
from ..deps import _actor_id, _idempotency_key

router = APIRouter()


@router.post("/api/autopilot/scan", response_model=AutopilotScanResponse,
          response_model_exclude_none=True)
def autopilot_scan(req: AutopilotScanRequest, db: Session = Depends(get_session),
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> AutopilotScanResponse:
    """Run the agents over each niche; queue only plans that clear the money bar."""
    store = crud.get_or_create_dev_store(db)
    key = _idempotency_key(idempotency_key)
    record, is_new = crud.claim_idempotency(
        db, store_id=store.id, operation="autopilot.scan", key=key
    )
    if not is_new:
        if record.status == "completed" and record.response:
            return AutopilotScanResponse(**record.response)
        if queue_enabled() and record.status == "processing":
            return AutopilotScanResponse(job_id=str(record.id), status="queued")
        raise HTTPException(status_code=409, detail="Operation is already in progress or failed")
    if queue_enabled():
        try:
            from ..queueing import enqueue_autopilot_scan
            job_id = enqueue_autopilot_scan(
                job_id=str(record.id), payload=req.model_dump(), tenant_id=str(store.id),
                actor_id=str(_actor_id(store.user_id)),
            )
            return AutopilotScanResponse(job_id=job_id, status="queued")
        except Exception as exc:
            db.rollback()
            crud.fail_idempotency(db, record)
            log.exception("Failed to enqueue Autopilot scan")
            raise HTTPException(status_code=503, detail="Background queue unavailable") from exc
    queued = skipped = 0
    try:
        for niche in req.niches:
            detail = _operator_to_detail(niche, req.limit)
            if not _meets_money_criteria(detail, req):
                skipped += 1
                continue
            crud.save_recommendation(db, store_id=store.id, module="autopilot",
                                     severity=detail["severity"], title=niche, detail=detail,
                                     commit=False)
            queued += 1
        result = {"queued": queued, "skipped": skipped}
        crud.append_audit_event(
            db, store_id=store.id, actor_id=_actor_id(store.user_id), action="autopilot.scan",
            resource_type="autopilot_queue", idempotency_key=key,
            after={**result, "niches": req.niches}, commit=False,
        )
        crud.complete_idempotency(db, record, result)
        return AutopilotScanResponse(**result)
    except Exception:
        log.exception("Autopilot scan failed")
        db.rollback()
        crud.fail_idempotency(db, record)
        raise


@router.get("/api/jobs/{job_id}", response_model=BackgroundJobResponse)
def background_job_status(job_id: str,
                          db: Session = Depends(get_session)) -> BackgroundJobResponse:
    principal = current_principal()
    if not queue_enabled():
        raise HTTPException(status_code=404, detail="job not found")
    if principal is not None:
        tenant_id = principal.tenant_id
    elif not auth_enabled():
        # Local/demo mode has no authenticated principal. Use the same isolated
        # development store that the enqueue endpoints use.
        tenant_id = str(crud.get_or_create_dev_store(db).id)
    else:
        raise HTTPException(status_code=404, detail="job not found")
    from ..queueing import job_status
    status = job_status(job_id, tenant_id=tenant_id)
    if status is None:
        raise HTTPException(status_code=404, detail="job not found")
    return BackgroundJobResponse(**status)


def _to_queue_item(row) -> QueueItem:
    d = row.detail or {}
    prod = d.get("product", {})
    pay = d.get("payback") or {}
    return QueueItem(
        id=str(row.id), niche=d.get("niche", row.title), status=d.get("status", "pending"),
        product_name=prod.get("name", "?"), verdict=prod.get("verdict", "?"),
        score=prod.get("score", 0), margin=prod.get("margin", 0.0),
        roi=prod.get("roi", 0.0), competition=prod.get("competition", 0.0),
        monthly_profit=prod.get("monthly_profit", 0.0),
        suggested_cogs=d.get("suggested_cogs"), supplier=d.get("supplier"),
        upfront_investment=pay.get("upfront_investment"), payback_months=pay.get("payback_months"),
        break_even_units=pay.get("break_even_units"),
        listing_draft=d.get("listing_draft", ""), steps=d.get("steps", []),
        created_at=row.created_at,
    )


@router.get("/api/autopilot/queue", response_model=QueueResponse)
def autopilot_queue(status: str | None = "pending", limit: int = 50,
                    db: Session = Depends(get_session)) -> QueueResponse:
    """List queued launch plans, optionally filtered by status (pending by default)."""
    items, seen = [], set()
    for r in crud.recent_recommendations(db, module="autopilot", limit=limit):  # newest first
        if status and (r.detail or {}).get("status") != status:
            continue
        it = _to_queue_item(r)
        if it.product_name in seen:   # dedup repeats of the same product, keep the newest
            continue
        seen.add(it.product_name)
        items.append(it)
    items.sort(key=lambda i: -i.monthly_profit)   # money first
    total = round(sum(i.monthly_profit for i in items), 2)
    return QueueResponse(results=items, total_monthly_profit=total)


@router.delete("/api/autopilot/queue")
def autopilot_clear(status: str | None = None, db: Session = Depends(get_session)) -> dict[str, int]:
    """Clear queued plans (all, or only a given status). Lets the user reset stale data."""
    return {"deleted": crud.clear_recommendations(db, "autopilot", status)}


@router.get("/api/autopilot/portfolio", response_model=PortfolioResponse)
def autopilot_portfolio(db: Session = Depends(get_session)) -> PortfolioResponse:
    """Capital picture across APPROVED plans: total to invest, total monthly profit, blended payback."""
    items = [_to_queue_item(r) for r in crud.recent_recommendations(db, module="autopilot", limit=200)
             if (r.detail or {}).get("status") == "approved"]
    items.sort(key=lambda i: -i.monthly_profit)
    total_invest = round(sum(i.upfront_investment or 0 for i in items), 2)
    total_profit = round(sum(i.monthly_profit for i in items), 2)
    blended = round(total_invest / total_profit, 1) if total_profit > 0 else None
    return PortfolioResponse(
        count=len(items), total_upfront_investment=total_invest,
        total_monthly_profit=total_profit, blended_payback_months=blended, items=items,
    )


@router.post("/api/autopilot/{plan_id}/decision", response_model=DecisionResponse)
def autopilot_decision(plan_id: str, req: DecisionRequest,
                       db: Session = Depends(get_session),
                       idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> DecisionResponse:
    """Approve or dismiss a queued plan (the human-in-the-loop step)."""
    if req.action not in ("approve", "dismiss"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'dismiss'")
    store = crud.get_or_create_dev_store(db)
    key = _idempotency_key(idempotency_key)
    record, is_new = crud.claim_idempotency(
        db, store_id=store.id, operation=f"autopilot.decision:{plan_id}", key=key
    )
    if not is_new:
        if record.status == "completed" and record.response:
            return DecisionResponse(**record.response)
        raise HTTPException(status_code=409, detail="Operation is already in progress or failed")
    row = crud.get_recommendation(db, plan_id)
    if row is None or row.module != "autopilot":
        crud.fail_idempotency(db, record)
        raise HTTPException(status_code=404, detail="plan not found")
    current = (row.detail or {}).get("status", "pending")
    new_status = "approved" if req.action == "approve" else "dismissed"
    if current != "pending":
        # The idempotency key does not help here: the browser mints a fresh one
        # per click, so two clicks are two operations. A decision is a one-time
        # transition, and a stale tab must not be able to overturn one made
        # elsewhere. Repeating the SAME decision is harmless and answered as
        # such; contradicting it is refused.
        crud.complete_idempotency(db, record, {"status": current})
        if current == new_status:
            return DecisionResponse(status=current)
        raise HTTPException(
            status_code=409,
            detail=f"This plan was already {current}; reload before deciding again.",
        )
    before = {"status": current}
    crud.set_recommendation_status(db, row, new_status, commit=False)
    result = {"status": new_status}
    crud.append_audit_event(
        db, store_id=store.id, actor_id=_actor_id(store.user_id),
        action=f"autopilot.{req.action}", resource_type="recommendation",
        resource_id=plan_id, idempotency_key=key,
        before=before, after=result, commit=False,
    )
    crud.complete_idempotency(db, record, result)
    return DecisionResponse(**result)
