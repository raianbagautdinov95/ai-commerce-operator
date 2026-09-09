"""Advertising: analysis, history, and the scan that opens actions."""

from .. import amazon_ads_api
from .. import llm
from .. import ppc_engine as ppc
from ..db import crud
from ..db.session import get_session
from ..queueing import queue_enabled
from ..schemas import (
    BackgroundJobResponse, KeywordFindingOut, PpcAnalyzeRequest, PpcAnalyzeResponse,
    PpcHistoryItem, PpcHistoryResponse, PpcSummaryOut)
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session
import os

from ..runtime import _SEVERITY_RANK, log
from ..deps import _actor_id, _idempotency_key, _persist_recommendation

router = APIRouter()


@router.post("/api/ppc/analyze", response_model=PpcAnalyzeResponse)
def analyze_ppc(req: PpcAnalyzeRequest, db: Session = Depends(get_session)) -> PpcAnalyzeResponse:
    """Analyze Amazon Ads keywords: ACOS, wasted spend, negative keywords, bid moves."""
    campaign = ppc.CampaignInput(
        name=req.name,
        break_even_acos=req.break_even_acos,
        target_acos=req.target_acos,
        keywords=[ppc.KeywordInput(**k.model_dump()) for k in req.keywords],
    )
    summary, findings = ppc.analyze(campaign)
    summary_out = PpcSummaryOut(**summary.__dict__)
    findings_out = [KeywordFindingOut(**f.to_dict()) for f in findings]
    explanation = (
        llm.explain_ppc(summary_out.model_dump(), [f.model_dump() for f in findings_out])
        if req.explain else None
    )
    _persist_ppc(db, summary_out, findings_out)
    return PpcAnalyzeResponse(summary=summary_out, findings=findings_out, explanation=explanation)


def _persist_ppc(db: Session, summary: PpcSummaryOut, findings: list[KeywordFindingOut]) -> None:
    severity = min((f.severity for f in findings), key=lambda s: _SEVERITY_RANK[s], default="info")
    _persist_recommendation(db, "ppc", severity, title=summary.campaign,
                            detail={"summary": summary.model_dump(),
                                    "findings": [f.model_dump() for f in findings]})


@router.get("/api/ppc/history", response_model=PpcHistoryResponse)
def ppc_history(limit: int = 20, db: Session = Depends(get_session)) -> PpcHistoryResponse:
    """Recent persisted PPC analyses, newest first."""
    rows = crud.recent_recommendations(db, module="ppc", limit=limit)
    results: list[PpcHistoryItem] = []
    for r in rows:
        s = (r.detail or {}).get("summary", {})
        results.append(PpcHistoryItem(
            id=str(r.id),
            campaign=r.title,
            severity=r.severity,
            overall_acos=s.get("overall_acos"),
            wasted_spend=s.get("wasted_spend", 0.0),
            potential_savings=s.get("potential_savings", 0.0),
            created_at=r.created_at,
        ))
    return PpcHistoryResponse(results=results)


@router.post("/api/ppc/scan", response_model=BackgroundJobResponse)
def ads_scan(days: int = Query(default=14, ge=3, le=60),
             region: str = Query(default=None),
             profile_id: str | None = None,
             db: Session = Depends(get_session),
             idempotency_key: str | None = Header(
                 default=None, alias="Idempotency-Key")) -> BackgroundJobResponse:
    """Read the advertising report, price the waste, and open actions for it.

    Runs inline when no queue is configured so a single-machine install still
    works; production runs it on the worker, where a slow report cannot hold a
    request open.
    """
    store = crud.get_or_create_dev_store(db)
    region = region or os.getenv("ADS_API_REGION", "EU")
    key = _idempotency_key(idempotency_key)
    record, is_new = crud.claim_idempotency(
        db, store_id=store.id, operation="ads.scan", key=key
    )
    if not is_new:
        status = "finished" if record.status == "completed" else record.status
        return BackgroundJobResponse(job_id=str(record.id), status=status,
                                     result=record.response)
    actor = str(_actor_id(store.user_id))
    if queue_enabled():
        try:
            from ..queueing import enqueue_ads_scan
            job_id = enqueue_ads_scan(job_id=str(record.id), tenant_id=str(store.id),
                                      actor_id=actor, region=region, days=days,
                                      profile_id=profile_id)
            return BackgroundJobResponse(job_id=job_id, status="queued")
        except Exception as exc:
            crud.fail_idempotency(db, record)
            log.exception("Failed to enqueue the advertising scan")
            raise HTTPException(status_code=503, detail="Background queue unavailable") from exc
    try:
        from ..tasks import run_ads_scan
        result = run_ads_scan(tenant_id=str(store.id), actor_id=actor, region=region,
                              days=days, profile_id=profile_id,
                              idempotency_record_id=str(record.id))
    except amazon_ads_api.AdsAuthorizationError as exc:
        crud.fail_idempotency(db, record)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        crud.fail_idempotency(db, record)
        log.exception("Advertising scan failed")
        raise HTTPException(status_code=502, detail="Could not read the advertising report") from exc
    return BackgroundJobResponse(job_id=str(record.id), status="finished", result=result)
