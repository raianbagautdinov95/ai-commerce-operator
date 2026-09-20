"""The one door that needs no account: score a product before signing up.

Every other business endpoint sits behind a bearer token, and that is right —
they read a store's own orders. This one reads nothing. It takes numbers a
visitor typed, runs the same deterministic engine the signed-in Hunter runs,
and answers. Nothing is persisted: an anonymous evaluation has no tenant to
belong to, and the history screen would otherwise fill with strangers' guesses.

What limits it is the only thing worth protecting here — the model call that
writes the explanation costs money per request. So each address gets a fixed
number of evaluations an hour, counted in Redis when the queue is configured
(the API may run more than one replica) and in this process when it is not.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import decision_engine as de
from .. import llm
from ..db import models
from ..db.session import get_session
from ..runtime import log
from ..schemas import EvaluateRequest, EvaluateResponse
from .research import _evaluation_out

router = APIRouter()

PUBLIC_EVALUATE_PATH = "/api/public/product-hunter/evaluate"
PUBLIC_VISIT_PATH = "/api/public/visit"

#: Candidates per request. Twenty at once is a catalogue, and a catalogue is
#: what the signed-in product is for.
MAX_PUBLIC_CANDIDATES = 5

WINDOW_SECONDS = 3600


def hourly_limit() -> int:
    return max(1, int(os.getenv("PUBLIC_EVALUATE_PER_HOUR", "20")))


# --- rate limiting --------------------------------------------------------

_local: dict[str, list[float]] = {}
_local_lock = threading.Lock()


def client_address(request: Request) -> str:
    """Railway's edge terminates TLS and forwards the visitor's address in the
    usual header; the first entry is the client, the rest are proxies."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _allow_in_process(key: str, limit: int, now: float) -> bool:
    with _local_lock:
        stamps = [t for t in _local.get(key, []) if now - t < WINDOW_SECONDS]
        if len(stamps) >= limit:
            _local[key] = stamps
            return False
        stamps.append(now)
        _local[key] = stamps
        # Addresses that stopped calling should not live here forever.
        if len(_local) > 10_000:
            for stale in [k for k, v in _local.items() if not v or now - v[-1] >= WINDOW_SECONDS]:
                _local.pop(stale, None)
        return True


def _allow_in_redis(key: str, limit: int) -> bool | None:
    """None means Redis was not usable; the caller falls back."""
    from .. import queueing

    if not queueing.queue_enabled():
        return None
    try:
        conn = queueing.redis_connection()
        redis_key = f"aco:public-evaluate:{key}"
        pipe = conn.pipeline()
        pipe.incr(redis_key)
        pipe.expire(redis_key, WINDOW_SECONDS, nx=True)
        count, _ = pipe.execute()
        return int(count) <= limit
    except Exception:  # pragma: no cover - a limiter must never take the door down
        log.exception("Public evaluate rate limiter could not reach Redis")
        return None


def allow(address: str, *, limit: int | None = None, now: float | None = None) -> bool:
    limit = hourly_limit() if limit is None else limit
    verdict = _allow_in_redis(address, limit)
    if verdict is not None:
        return verdict
    return _allow_in_process(address, limit, time.time() if now is None else now)


def reset_for_tests() -> None:
    with _local_lock:
        _local.clear()


# --- who came, and from where ---------------------------------------------

class Attribution(BaseModel):
    """The utm_* values the link carried. Free text from a URL, so bounded."""
    source: str | None = Field(default=None, max_length=64)
    medium: str | None = Field(default=None, max_length=64)
    campaign: str | None = Field(default=None, max_length=64)

    def clean(self) -> "Attribution":
        norm = lambda v: (v or "").strip().lower()[:64] or None  # noqa: E731
        return Attribution(source=norm(self.source), medium=norm(self.medium),
                           campaign=norm(self.campaign))


class PublicEvaluateRequest(EvaluateRequest):
    attribution: Attribution | None = None


def visitor_key(request: Request) -> str:
    """One value per person per day, and nothing that turns back into them.

    A keyed hash of the address and browser with today's date folded in: the
    same visitor reloading counts once, and the key is different tomorrow, so
    the table never becomes a record of who was where. The address itself is
    not stored anywhere."""
    salt = os.getenv("PUBLIC_FUNNEL_SALT") or os.getenv("JWT_SECRET") or "development"
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    material = f"{client_address(request)}|{request.headers.get('user-agent', '')}|{today}"
    return hmac.new(salt.encode(), material.encode(), hashlib.sha256).hexdigest()


def record(db: Session, *, kind: str, visitor: str, attribution: Attribution | None) -> None:
    """Best effort: the funnel must never take the door down."""
    a = (attribution or Attribution()).clean()
    try:
        db.add(models.PublicFunnelEvent(kind=kind, visitor=visitor, source=a.source,
                                        medium=a.medium, campaign=a.campaign))
        db.commit()
    except Exception:  # pragma: no cover - defensive
        log.exception("Could not record a public funnel event")
        db.rollback()


@router.post(PUBLIC_VISIT_PATH, status_code=204)
def public_visit(attribution: Attribution, request: Request,
                 db: Session = Depends(get_session)) -> None:
    """The /try page loaded in somebody's browser. Called once per page view;
    counted once per visitor per day."""
    if not allow(client_address(request), limit=max(hourly_limit() * 5, 60)):
        return None
    record(db, kind="visit", visitor=visitor_key(request), attribution=attribution)
    return None


# --- the endpoint -----------------------------------------------------------

@router.post(PUBLIC_EVALUATE_PATH, response_model=EvaluateResponse)
def evaluate_products_public(req: PublicEvaluateRequest, request: Request,
                             db: Session = Depends(get_session)) -> EvaluateResponse:
    """Score up to five candidates for somebody with no account. Same engine,
    same verdict scale; nothing stored."""
    if not req.products:
        raise HTTPException(status_code=422, detail="Add at least one product.")
    if len(req.products) > MAX_PUBLIC_CANDIDATES:
        raise HTTPException(
            status_code=422,
            detail=f"Up to {MAX_PUBLIC_CANDIDATES} products at a time here. "
                   "Sign in to evaluate a whole catalogue.",
        )
    if not allow(client_address(request)):
        raise HTTPException(
            status_code=429,
            detail="That is enough evaluations for one hour from this address. "
                   "Sign in for an unlimited Hunter.",
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )

    record(db, kind="evaluate", visitor=visitor_key(request), attribution=req.attribution)

    inputs = [de.ProductInput(**p.model_dump()) for p in req.products]
    inputs_by_name = {p.name: p.model_dump() for p in req.products}
    evals = de.rank(inputs)
    results = [
        _evaluation_out(e, explanation=llm.explain(e) if req.explain else None,
                        inputs=inputs_by_name.get(e.name))
        for e in evals
    ]
    return EvaluateResponse(results=results, weights=de.WEIGHTS)
