"""Reorder planning."""

from .. import inventory_engine as inv
from .. import llm
from ..db.session import get_session
from ..schemas import (
    InventoryAnalyzeRequest, InventoryAnalyzeResponse, InventoryFindingOut,
    InventorySummaryOut)
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..runtime import _SEVERITY_RANK

from ..deps import _persist_recommendation

router = APIRouter()


@router.post("/api/inventory/analyze", response_model=InventoryAnalyzeResponse)
def analyze_inventory(req: InventoryAnalyzeRequest, db: Session = Depends(get_session)) -> InventoryAnalyzeResponse:
    """Analyze stock: days of cover, reorder point/date, order quantity, stockout risk."""
    skus = [inv.StockInput(**s.model_dump()) for s in req.skus]
    summary, findings = inv.analyze(skus)
    summary_out = InventorySummaryOut(**summary.__dict__)
    findings_out = [InventoryFindingOut(**f.to_dict()) for f in findings]
    explanation = (
        llm.explain_inventory(summary_out.model_dump(), [f.model_dump() for f in findings_out])
        if req.explain else None
    )
    _persist_recommendation(db, "inventory",
                            min((f.severity for f in findings_out), key=lambda s: _SEVERITY_RANK[s], default="info"),
                            title=f"{summary_out.total_skus} SKUs",
                            detail={"summary": summary_out.model_dump(),
                                    "findings": [f.model_dump() for f in findings_out]})
    return InventoryAnalyzeResponse(summary=summary_out, findings=findings_out, explanation=explanation)
