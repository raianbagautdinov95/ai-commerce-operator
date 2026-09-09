"""The cross-module daily digest."""

from .. import llm
from .. import report_engine as report
from ..db import crud
from ..db.session import get_session
from ..schemas import DailyReportResponse, ReportItemOut
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("/api/report/daily", response_model=DailyReportResponse)
def daily_report(explain: bool = True, db: Session = Depends(get_session)) -> DailyReportResponse:
    """Cross-module 'what matters today' digest, prioritized by severity then $ impact."""
    evals = [
        {"name": e.name, "score": e.score, "verdict": e.verdict, "economics": e.economics}
        for e in crud.recent_evaluations(db, limit=10)
    ]
    ppc_rows = crud.recent_recommendations(db, module="ppc", limit=1)
    inv_rows = crud.recent_recommendations(db, module="inventory", limit=1)
    ppc_findings = (ppc_rows[0].detail or {}).get("findings", []) if ppc_rows else []
    inv_findings = (inv_rows[0].detail or {}).get("findings", []) if inv_rows else []

    rep = report.build_report(
        evaluations=evals, ppc_findings=ppc_findings, inventory_findings=inv_findings,
    )
    rep_dict = {
        "items": [i.to_dict() for i in rep.items],
        "money_at_stake_usd": rep.money_at_stake_usd,
        "wasted_spend_usd": rep.wasted_spend_usd,
        "reorder_cost_usd": rep.reorder_cost_usd,
        "counts": rep.counts,
    }
    briefing = llm.explain_report(rep_dict) if explain else None
    return DailyReportResponse(
        items=[ReportItemOut(**i) for i in rep_dict["items"]],
        money_at_stake_usd=rep.money_at_stake_usd,
        wasted_spend_usd=rep.wasted_spend_usd,
        reorder_cost_usd=rep.reorder_cost_usd,
        counts=rep.counts,
        briefing=briefing,
    )
