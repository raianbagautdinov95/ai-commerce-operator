"""Finding something to sell: product scoring, discovery, suppliers, listings."""

from .. import decision_engine as de
from .. import discovery
from .. import llm
from .. import operator
from .. import suppliers
from ..db import crud
from ..db.session import get_session
from ..schemas import (
    CreativeRequest, CreativeResponse, DiscoverRequest, DiscoverResponse,
    DiscoveryHistoryItem, DiscoveryHistoryResponse, EconomicsOut, KeepaStatusResponse,
    LaunchPlanRequest, LaunchPlanResponse, EvaluateRequest, EvaluateResponse,
    EvaluationHistoryItem, EvaluationHistoryResponse, EvaluationOut, PaybackOut,
    ProductRequest, SupplierOfferOut, SupplierSearchRequest, SupplierSearchResponse,
    WhatIfOut)
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..runtime import log

from ..deps import _persist_recommendation

router = APIRouter()


@router.post("/api/product-hunter/evaluate", response_model=EvaluateResponse)
def evaluate_products(req: EvaluateRequest, db: Session = Depends(get_session)) -> EvaluateResponse:
    """Score and rank product candidates. Optionally add an LLM explanation; persist each result."""
    inputs = [de.ProductInput(**p.model_dump()) for p in req.products]
    inputs_by_name = {p.name: p.model_dump() for p in req.products}
    evals = de.rank(inputs)

    results: list[EvaluationOut] = []
    for e in evals:
        explanation = llm.explain(e) if req.explain else None
        results.append(_evaluation_out(e, explanation=explanation,
                                       inputs=inputs_by_name.get(e.name)))
        _persist(db, e, inputs_by_name.get(e.name, {}), explanation)

    return EvaluateResponse(results=results, weights=de.WEIGHTS)


def _persist(db: Session, e: de.Evaluation, inputs: dict, explanation: str | None) -> None:
    """Store one evaluation. Best-effort: a DB failure must never break scoring."""
    try:
        store = crud.get_or_create_dev_store(db)
        crud.save_evaluation(
            db,
            user_id=store.user_id,
            store_id=store.id,
            name=e.name,
            inputs=inputs,
            economics=e.economics.__dict__,
            subscores=e.subscores,
            score=e.score,
            verdict=e.verdict.value,
            explanation=explanation,
        )
    except Exception:  # pragma: no cover - defensive
        log.exception("Failed to persist evaluation for %s", e.name)
        db.rollback()


def _evaluation_out(e: de.Evaluation, explanation: str | None = None,
                    inputs: dict | None = None) -> EvaluationOut:
    return EvaluationOut(
        name=e.name,
        economics=EconomicsOut(**e.economics.__dict__),
        score=e.score,
        subscores=e.subscores,
        verdict=e.verdict.value,
        reason=e.reason,
        whatif=WhatIfOut(**e.whatif.__dict__),
        pros=e.pros,
        risks=e.risks,
        explanation=explanation,
        inputs=ProductRequest(**inputs) if inputs else None,
    )


@router.get("/api/product-hunter/history", response_model=EvaluationHistoryResponse)
def evaluation_history(limit: int = 20, db: Session = Depends(get_session)) -> EvaluationHistoryResponse:
    """Recent persisted evaluations, newest first — the start of the data moat."""
    rows = crud.recent_evaluations(db, limit=limit)
    return EvaluationHistoryResponse(results=[
        EvaluationHistoryItem(
            id=str(r.id),
            name=r.name,
            score=r.score,
            verdict=r.verdict,
            economics=EconomicsOut(**r.economics),
            explanation=r.explanation,
            created_at=r.created_at,
        )
        for r in rows
    ])


@router.post("/api/discovery/search", response_model=DiscoverResponse)
def discovery_search(req: DiscoverRequest, db: Session = Depends(get_session)) -> DiscoverResponse:
    """Find candidate products for a niche and rank them best-first through the engine."""
    try:
        source, pairs, cached = discovery.discover_detailed(
            req.niche, req.limit,
            max_price=req.max_price, min_monthly_sales=req.min_monthly_sales,
        )
    except Exception as exc:
        log.exception("Discovery failed")
        raise HTTPException(status_code=502, detail=f"Discovery source error: {exc}") from exc

    if source == "keepa":
        note = ("Live Keepa data. COGS (~30% of price) and ad cost (~12% of price) are ESTIMATES, "
                "and sales are capped to a conservative new-seller share — confirm real supplier "
                "cost in Product Hunter before buying.")
    elif source == "sample":
        note = ("Sample catalog — orientation values, not live Amazon data. "
                "Set DISCOVERY_SOURCE=keepa (with KEEPA_API_KEY) for live results.")
    else:
        note = f"Source: {source}."

    _persist_discovery(db, req.niche, source, pairs)
    return DiscoverResponse(
        source=source,
        note=note,
        cached=cached,
        results=[_evaluation_out(e, inputs=inp) for e, inp in pairs],
        weights=de.WEIGHTS,
    )


def _persist_discovery(db: Session, niche: str, source: str,
                       pairs: list[tuple[de.Evaluation, dict]]) -> None:
    top = [{"name": e.name, "verdict": e.verdict.value, "score": e.score,
            "monthly_profit": e.economics.monthly_profit} for e, _ in pairs[:3]]
    severity = "info" if any(e.verdict.value == "BUY" for e, _ in pairs) else "warning"
    _persist_recommendation(db, "discovery", severity, title=niche,
                            detail={"niche": niche, "source": source, "count": len(pairs), "top": top})


@router.get("/api/discovery/history", response_model=DiscoveryHistoryResponse)
def discovery_history(limit: int = 20, db: Session = Depends(get_session)) -> DiscoveryHistoryResponse:
    """Recent niche searches, newest first."""
    rows = crud.recent_recommendations(db, module="discovery", limit=limit)
    results: list[DiscoveryHistoryItem] = []
    for r in rows:
        d = r.detail or {}
        top = d.get("top") or []
        results.append(DiscoveryHistoryItem(
            id=str(r.id),
            niche=r.title,
            results_count=d.get("count", 0),
            top_name=top[0]["name"] if top else None,
            top_score=top[0]["score"] if top else None,
            created_at=r.created_at,
        ))
    return DiscoveryHistoryResponse(results=results)


@router.get("/api/discovery/keepa-status", response_model=KeepaStatusResponse)
def discovery_keepa_status() -> KeepaStatusResponse:
    """Keepa token balance for the UI (free to call). Disabled unless Keepa is active."""
    try:
        return KeepaStatusResponse(**discovery.keepa_status())
    except Exception:  # pragma: no cover - never break the UI over a status check
        log.exception("Keepa status check failed")
        return KeepaStatusResponse(enabled=False)


@router.post("/api/creative/generate", response_model=CreativeResponse)
def creative_generate(req: CreativeRequest) -> CreativeResponse:
    """Creative agent: persuasive listing copy + a photo shot-list + concept images."""
    attrs = ""
    if req.price:
        attrs += f"Price: ${req.price}. "
    if req.features:
        attrs += f"Key features: {req.features}."
    listing = llm.draft_listing(req.name, attrs)
    image_plan = llm.draft_image_plan(req.name, attrs)
    concept_images = llm.generate_concept_images(req.name) if req.images else []
    return CreativeResponse(listing=listing, image_plan=image_plan, concept_images=concept_images)


@router.post("/api/operator/launch-plan", response_model=LaunchPlanResponse)
def operator_launch_plan(req: LaunchPlanRequest) -> LaunchPlanResponse:
    """3-agent pipeline: find product -> find supplier -> re-score -> draft listing."""
    try:
        p = operator.plan(req.niche, req.limit)
    except Exception as exc:
        log.exception("Operator launch plan failed")
        raise HTTPException(status_code=502, detail=f"Operator error: {exc}") from exc

    steps = list(p.steps)
    eco = p.product.economics
    attrs = (f"Selling price: ${p.inputs.get('price')}. Margin: {eco.margin:.0%}. "
             f"Small/light item: {p.inputs.get('size_score', 1) >= 1}.")
    listing = llm.draft_listing(p.product.name, attrs)
    steps.append("Agent 3 (Listing): drafted an English title, 5 bullets and a description.")

    return LaunchPlanResponse(
        niche=p.niche,
        discovery_source=p.discovery_source,
        supplier_source=p.supplier_source,
        product=_evaluation_out(p.product, inputs=p.inputs),
        supplier=SupplierOfferOut(**p.supplier.__dict__) if p.supplier else None,
        suggested_cogs=p.suggested_cogs,
        payback=PaybackOut(**p.payback.__dict__) if p.payback else None,
        listing_draft=listing,
        steps=steps,
        note=("This is a draft plan for your approval. Publishing to Amazon needs a Seller "
              "account + SP-API access and a manual confirmation — it is never automatic."),
    )


@router.post("/api/suppliers/search", response_model=SupplierSearchResponse)
def suppliers_search(req: SupplierSearchRequest) -> SupplierSearchResponse:
    """Find supplier offers for a product and suggest a landed COGS for Product Hunter."""
    try:
        source, offers, suggested = suppliers.find_suppliers(
            req.query, req.limit, max_moq=req.max_moq, max_lead_time=req.max_lead_time,
        )
    except Exception as exc:
        log.exception("Supplier search failed")
        raise HTTPException(status_code=502, detail=f"Supplier source error: {exc}") from exc

    note = ("Sample suppliers — orientation values, not live data. "
            "Set SUPPLIER_SOURCE to a real provider (RapidAPI Alibaba/1688) for live offers."
            if source == "sample" else f"Source: {source}.")
    return SupplierSearchResponse(
        source=source,
        note=note,
        query=req.query,
        suggested_cogs=suggested,
        offers=[SupplierOfferOut(**o.__dict__) for o in offers],
    )
