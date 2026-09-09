"""Liveness, readiness and metrics."""

from .. import observability
from ..db.session import get_session
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import alerts
from ..db import crud
from ..runtime import (_metrics_lock, _request_counts,
                       _request_duration_seconds, log)

from ..schemas import AlertConditionOut, AlertsResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def readiness(db: Session = Depends(get_session)) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
        from ..queueing import redis_ready, workers_ready
        if not redis_ready():
            raise RuntimeError("Redis ping failed")
        if not workers_ready():
            raise RuntimeError("Required background worker is unavailable")
    except Exception as exc:
        log.warning("Readiness check failed", exc_info=True)
        raise HTTPException(status_code=503, detail="service not ready") from exc
    return {"status": "ready"}


@router.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    lines = ["# HELP aco_http_requests_total HTTP requests processed.",
             "# TYPE aco_http_requests_total counter"]
    with _metrics_lock:
        counts = dict(_request_counts); durations = dict(_request_duration_seconds)
    for (method, path, status), value in sorted(counts.items()):
        lines.append(f'aco_http_requests_total{{method="{method}",path="{path}",status="{status}"}} {value}')
    lines.extend(["# HELP aco_http_request_duration_seconds_total Total request time.",
                  "# TYPE aco_http_request_duration_seconds_total counter"])
    for (method, path), value in sorted(durations.items()):
        lines.append(f'aco_http_request_duration_seconds_total{{method="{method}",path="{path}"}} {value:.6f}')
    return "\n".join(lines) + "\n"


@router.get("/api/observability/status")
def observability_status() -> dict[str, bool | str | None]:
    return observability.status()


@router.get("/api/alerts", response_model=AlertsResponse)
def operational_alerts(db: Session = Depends(get_session)) -> AlertsResponse:
    """What currently needs a human, worst first."""
    store = crud.get_or_create_dev_store(db)
    conditions = alerts.evaluate(db, store_id=store.id)
    return AlertsResponse(
        worst=alerts.worst_severity(conditions),
        conditions=[AlertConditionOut(**c.to_dict()) for c in conditions],
    )
