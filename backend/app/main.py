"""
FastAPI entrypoint for AI Commerce Operator.

This module owns the application object and nothing else: configuration checks
that must fail closed, the middleware stack, and the routers it mounts. Every
endpoint lives in `app/routers/`, one module per domain, so that finding the code
behind a URL is a matter of reading its path.

Run locally:
    uvicorn app.main:app --reload
Then open http://localhost:8000/docs for the interactive API.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from . import entitlement, observability, sessions
from .autopilot_service import operator_to_detail
from .credential_crypto import validate_credential_encryption_config
from .db import crud
from .db import session as db_session
from .db.session import SessionLocal, init_db
from .queueing import validate_queue_config
from .routers import (account, ads, amazon, auth, autopilot, commerce, costs, dashboards,
                      health, inventory, money, privacy, public, report, research, shopify,
                      woocommerce)
from .runtime import (_OAuthQueryRedactionFilter, _metrics_lock, _request_counts,
                      _request_duration_seconds, log)
from .security import (AuthenticationError, AuthorizationError, auth_enabled,
                       authenticate_bearer, bind_principal, require_role, required_role,
                       reset_principal, validate_auth_config)

observability.initialize()
# On the access log, and on every root handler: a credential reaches the log as
# often through an HTTP client's exception message as through a request path.
_redaction_filter = _OAuthQueryRedactionFilter()
logging.getLogger("uvicorn.access").addFilter(_redaction_filter)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_redaction_filter)


def _validate_runtime_safety() -> None:
    """Refuse an accidentally insecure production deployment.

    Authentication and tenant scoping are not implemented in this MVP yet.  A
    production process must therefore fail closed instead of exposing the dev
    user's data and mutation endpoints to the internet.
    """
    validate_auth_config()
    environment = os.getenv("APP_ENV", "development").lower()
    if environment not in {"development", "test", "staging", "production"}:
        raise RuntimeError("APP_ENV must be development, test, staging, or production.")
    if environment not in {"staging", "production"}:
        return
    if os.getenv("AUTH_ENABLED", "false").lower() != "true":
        raise RuntimeError(
            "Unsafe production configuration: authentication and tenant scoping "
            "must be enabled before deployment."
        )
    if os.getenv("DATABASE_URL", "sqlite:///./aco_dev.db").startswith("sqlite"):
        raise RuntimeError("Unsafe production configuration: SQLite is not supported.")
    validate_credential_encryption_config(required=True)
    validate_queue_config(required=True)
    origins = [value.strip() for value in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
               if value.strip()]
    if not origins or any(not value.startswith("https://") for value in origins):
        raise RuntimeError("CORS_ALLOWED_ORIGINS must contain explicit HTTPS origins.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_runtime_safety()
    init_db()  # create tables if missing (dev convenience; prod uses migrations)
    task = None
    if os.getenv("AUTOPILOT_ENABLED", "false").lower() == "true":
        task = asyncio.create_task(_autopilot_loop())
        log.info("Autopilot scheduler started.")
    try:
        yield
    finally:
        if task:
            task.cancel()


async def _autopilot_loop() -> None:
    """Opt-in background loop: periodically scan AUTOPILOT_NICHES into the approval queue.

    Off by default. Each niche scan spends discovery API tokens (Keepa), so this is
    deliberately conservative and only runs when explicitly enabled.
    """
    interval = float(os.getenv("AUTOPILOT_INTERVAL_HOURS", "24")) * 3600
    niches = [n.strip() for n in os.getenv("AUTOPILOT_NICHES", "").split(",") if n.strip()]
    while True:
        if niches:
            db = SessionLocal()
            try:
                store = crud.get_or_create_dev_store(db)
                for niche in niches:
                    try:
                        detail = await asyncio.to_thread(operator_to_detail, niche, 8)
                        crud.save_recommendation(db, store_id=store.id, module="autopilot",
                                                 severity=detail["severity"], title=niche, detail=detail)
                    except Exception:  # pragma: no cover - background best-effort
                        log.exception("Autopilot scan failed for '%s'", niche)
            finally:
                db.close()
        await asyncio.sleep(interval)


app = FastAPI(
    title="AI Commerce Operator API",
    version="0.1.0",
    description="MVP: AI Product Hunter — deterministic decision engine + LLM explanations.",
    lifespan=lifespan,
)


@app.exception_handler(entitlement.PaymentRequired)
async def payment_required(request: Request, exc: entitlement.PaymentRequired):
    """402 with the sentence the customer's own billing screen would show.

    A bare "payment required" sends somebody to support to be told what their
    screen already knows, and the state name lets the client offer the right
    button rather than guessing between Checkout and the Portal.
    """
    return JSONResponse(status_code=402, content={
        "detail": exc.state.explanation,
        "status": exc.state.status,
        "action": exc.state.action,
    })


@app.middleware("http")
async def request_metrics(request: Request, call_next):
    started = time.perf_counter(); status = 500
    try:
        response = await call_next(request); status = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", None) or request.url.path
        elapsed = time.perf_counter() - started
        with _metrics_lock:
            _request_counts[(request.method, path, status)] += 1
            _request_duration_seconds[(request.method, path)] += elapsed


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    request_id = request.headers.get("X-Request-ID", "")
    try: request_id = str(uuid.UUID(request_id))
    except ValueError: request_id = str(uuid.uuid4())
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if os.getenv("APP_ENV", "development").lower() in {"staging", "production"}:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# Paths that answer without a bearer token. Two kinds only, and nothing else
# belongs here: infrastructure that must stay reachable when nobody is signed
# in, and the endpoints by which somebody *becomes* signed in. Every callback
# and webhook below authenticates itself another way — a signed state, an HMAC —
# and the sign-in endpoints each demand a proof of an email address.
PUBLIC_PATHS = {
    "/health", "/health/ready", "/docs", "/openapi.json",
    "/api/legal",
    "/api/integrations/shopify/pilot",
    "/api/auth/config",
    "/api/auth/google",
    "/api/auth/email/request",
    "/api/auth/email/verify",
    "/api/integrations/amazon/callback",
    "/api/integrations/shopify/callback",
    "/api/integrations/woocommerce/callback",
    "/api/webhooks/shopify", "/api/webhooks/stripe",
    # The one business endpoint open to strangers: the Hunter, bounded and
    # stateless, so a visitor can see a verdict before deciding to sign up.
    "/api/public/product-hunter/evaluate",
    "/api/public/visit",
}


@app.middleware("http")
async def authenticate_request(request: Request, call_next):
    """Protect business APIs while keeping health checks publicly reachable."""
    if (
        not auth_enabled()
        or request.method == "OPTIONS"
        or request.url.path in PUBLIC_PATHS
    ):
        return await call_next(request)
    try:
        request.state.principal = authenticate_bearer(request.headers.get("Authorization"))
    except AuthenticationError as exc:
        return JSONResponse(
            status_code=401,
            content={"detail": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # The signature proves the token was minted here. It cannot prove the token
    # is still meant to work, so the session it names is checked too: signing
    # out on a lost laptop takes effect on the very next request instead of
    # whenever the week happens to run out. `token_sessions` is outside the
    # row-level policies, which is what lets this run before a tenant is known.
    revocation_db = db_session.SessionLocal()
    try:
        if sessions.live_session(revocation_db, request.state.principal.session_id) is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "This session has ended. Sign in again."},
                headers={"WWW-Authenticate": "Bearer"},
            )
    finally:
        revocation_db.close()

    try:
        require_role(
            request.state.principal,
            required_role(request.method, request.url.path),
        )
    except AuthorizationError as exc:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    principal_token = bind_principal(request.state.principal)
    try:
        return await call_next(request)
    finally:
        reset_principal(principal_token)

# Allow the Next.js dev server to call the API. The dev server falls back to
# 3001+ when 3000 is taken, so match both loopback spellings on any dev port.
_explicit_origins = [value.strip() for value in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
                     if value.strip()]
_local_origin_regex = (r"http://(?:localhost|127\.0\.0\.1):\d+"
                       if os.getenv("APP_ENV", "development").lower() in {"development", "test"}
                       else None)
app.add_middleware(CORSMiddleware, allow_origins=_explicit_origins,
                   allow_origin_regex=_local_origin_regex,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"])


# One router per domain. The order here is the order they appear in /docs.
for _router in (health, auth, privacy, shopify, woocommerce, amazon, account, dashboards,
                commerce, costs, research, public, ads, inventory, report, autopilot,
                money):
    app.include_router(_router.router)
