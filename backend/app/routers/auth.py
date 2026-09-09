"""Signing in: Google, or a code sent to an email address.

Until now the API verified tokens and nothing issued them, so a fresh
deployment answered 401 to everyone including its owner and the only way in was
a shell command. That is a defensible position for one pilot store and an
impossible one for a hundred, because there is no step between "somebody wants
to try this" and "somebody has shell access to the server".

These four endpoints are the step. They are the only ones in the application
that answer without a bearer token, which is the whole of their risk, so each
one is narrow on purpose: it takes a proof of an email address, and it returns
exactly the token `security.authenticate_bearer` already knows how to check.
No new session concept, no second notion of identity.

Two decisions worth keeping visible:

* **Everyone who signs in is `owner` — of their own tenant.** The role travels
  in the token and the client never proposes it. `ensure_account` gives a new
  address a store of its own, so owning it grants nothing over anybody else.
* **No subscription row is created here.** `subscriptions` is under row-level
  security keyed on the tenant, and these endpoints run before a tenant context
  exists. The trial starts on the first authenticated request instead, where
  `billing.ensure_subscription` can see whose it is.
"""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import sessions as token_sessions
from ..accounts import InvalidEmailError, ensure_account, session_days
from ..auth_email import LoginCodeError, request_login_code, verify_login_code
from ..auth_google import GoogleIdentityError, google_client_id, verify_google_credential
from ..db import crud, models
from ..db.session import declare_tenant, get_session
from ..issue_token import TokenConfigurationError
from ..mailer import MailerNotConfigured, login_email_possible, send_login_code
from ..runtime import log
from ..schemas import (AuthConfigResponse, EmailCodeRequest, EmailCodeVerifyRequest,
                       GoogleSignInRequest, PrincipalResponse, SessionResponse,
                       SessionsEndedResponse, TokenSessionResponse)
from ..security import auth_enabled, current_principal

router = APIRouter(prefix="/api/auth", tags=["auth"])

SIGN_IN_ROLE = "owner"


#: How each door reads in a log line and in the list of sessions.
METHOD_NAMES = {"google": "Google", "email": "an email code", "cli": "the command line"}


def _open_session(db: Session, *, email: str, method: str) -> SessionResponse:
    """Everything after 'we believe this address' is the same for both doors."""
    days = session_days()
    user, store = ensure_account(db, email=email)
    try:
        token, row = token_sessions.open_session(
            db, user_id=user.id, store_id=store.id, role=SIGN_IN_ROLE,
            days=days, method=method)
    except TokenConfigurationError as exc:
        # A deployment that cannot sign a token is misconfigured, not a bad
        # request. Say so plainly instead of blaming the person signing in.
        log.error("Refused to open a session: %s", exc)
        raise HTTPException(status_code=503,
                            detail="This server cannot issue access tokens yet.") from exc

    # Signing in is a thing that happened to an account, so it belongs in the
    # same trail as every other change. The tenant has to be declared by hand:
    # this runs before the middleware has bound a principal, so the row-level
    # policies have nothing to key on yet.
    #
    # `commit=False` is not a detail. `declare_tenant` sets app.tenant_id for
    # the CURRENT transaction, and append_audit_event's own commit ends it —
    # after which its refresh() opens a fresh transaction with no tenant
    # declared, the row-level policy matches nothing, and the insert that had
    # just succeeded cannot be read back. Flushing keeps the write inside the
    # transaction that is allowed to see it, and the commit here closes it.
    #
    # The whole thing is wrapped because of what it costs when it fails. By the
    # time we get here the code has been consumed and the session row is
    # committed: the person is signed in whether or not the trail records it.
    # Refusing the token would leave them locked out with a burned code and a
    # session they cannot use — which is exactly the bug this replaces. A lost
    # audit line is bad and says so loudly; a lost account is worse.
    try:
        declare_tenant(db, store.id)
        crud.append_audit_event(
            db, store_id=store.id, actor_id=user.id, action="auth.signed_in",
            resource_type="token_session", resource_id=str(row.id),
            after={"method": method, "expires_at": row.expires_at.isoformat()},
            commit=False)
        db.commit()
    except Exception:  # noqa: BLE001 - a sign-in must survive its own logging
        db.rollback()
        log.exception("Signed in but could not record it: session %s, user %s",
                      row.id, user.id)

    log.info("Signed in via %s: %s", METHOD_NAMES.get(method, method), user.email)
    return SessionResponse(
        token=token,
        email=user.email,
        role=SIGN_IN_ROLE,
        tenant_id=str(store.id),
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days),
    )


@router.get("/config", response_model=AuthConfigResponse)
def auth_config() -> AuthConfigResponse:
    """What the sign-in screen may offer.

    The browser asks rather than being built with the answer baked in, so one
    frontend build serves a deployment with Google configured and one without.
    """
    return AuthConfigResponse(
        google_client_id=google_client_id(),
        email_login=login_email_possible(),
        session_days=session_days(),
    )


@router.post("/google", response_model=SessionResponse)
def sign_in_with_google(payload: GoogleSignInRequest,
                        db: Session = Depends(get_session)) -> SessionResponse:
    try:
        email = verify_google_credential(payload.credential)
    except GoogleIdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    try:
        return _open_session(db, email=email, method="google")
    except InvalidEmailError as exc:  # pragma: no cover - Google would have to send junk
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/email/request", status_code=202)
def request_email_code(payload: EmailCodeRequest,
                       db: Session = Depends(get_session)) -> dict[str, bool]:
    """Send a code, and answer the same way whether or not the address is known."""
    if not login_email_possible():
        raise HTTPException(
            status_code=503,
            detail="Email sign-in is unavailable: set RESEND_API_KEY and LOGIN_EMAIL_FROM.")
    try:
        address, code = request_login_code(db, email=payload.email)
    except InvalidEmailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LoginCodeError as exc:
        raise HTTPException(status_code=getattr(exc, "status_code", 400),
                            detail=str(exc)) from exc
    try:
        send_login_code(address, code)
    except MailerNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"sent": True}


@router.post("/email/verify", response_model=SessionResponse)
def verify_email_code(payload: EmailCodeVerifyRequest,
                      db: Session = Depends(get_session)) -> SessionResponse:
    try:
        email = verify_login_code(db, email=payload.email, code=payload.code)
    except InvalidEmailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LoginCodeError as exc:
        raise HTTPException(status_code=getattr(exc, "status_code", 400),
                            detail=str(exc)) from exc
    return _open_session(db, email=email, method="email")


@router.get("/me", response_model=PrincipalResponse)
def whoami(db: Session = Depends(get_session)) -> PrincipalResponse:
    """Who this token says you are. The only authenticated route in this module."""
    principal = current_principal()
    if principal is None:
        if auth_enabled():  # pragma: no cover - the middleware answers 401 first
            raise HTTPException(status_code=401, detail="Bearer token required.")
        return PrincipalResponse(user_id="", tenant_id="", role="owner",
                                 email="authentication is disabled on this server")
    user = db.get(models.User, uuid.UUID(principal.user_id))
    return PrincipalResponse(user_id=principal.user_id, tenant_id=principal.tenant_id,
                             role=principal.role, email=user.email if user else "")


@router.post("/signout", response_model=SessionsEndedResponse)
def sign_out(db: Session = Depends(get_session)) -> SessionsEndedResponse:
    """End the session this request is holding.

    Clearing the browser was never signing out: the token stayed valid for the
    rest of its life, so a shared computer kept working long after somebody
    thought they had left. This ends it on the server, and the next request
    made with it is refused.
    """
    principal = current_principal()
    if principal is None:
        # Authentication is switched off on this deployment, so there is no
        # session to end. Saying "0" is truthful; erroring would not be.
        return SessionsEndedResponse(ended=0)

    row = token_sessions.revoke(db, principal.session_id)
    if row is None:
        return SessionsEndedResponse(ended=0)

    declare_tenant(db, row.store_id)
    crud.append_audit_event(
        db, store_id=row.store_id, actor_id=row.user_id, action="auth.signed_out",
        resource_type="token_session", resource_id=str(row.id))
    log.info("Signed out: session %s", row.id)
    return SessionsEndedResponse(ended=1)


@router.get("/sessions", response_model=list[TokenSessionResponse])
def list_sessions(db: Session = Depends(get_session)) -> list[TokenSessionResponse]:
    """Every way into this account that currently works.

    The point of showing them is recognising one you did not open, so the list
    carries dates and how each was opened — never a token or part of one.
    """
    principal = current_principal()
    if principal is None:
        return []
    rows = token_sessions.list_for_user(db, uuid.UUID(principal.user_id))
    return [
        TokenSessionResponse(
            id=str(row.id), method=row.method, issued_at=row.issued_at,
            last_seen_at=row.last_seen_at, expires_at=row.expires_at,
            current=str(row.id) == principal.session_id,
        )
        for row in rows
    ]


@router.post("/sessions/revoke-others", response_model=SessionsEndedResponse)
def revoke_other_sessions(db: Session = Depends(get_session)) -> SessionsEndedResponse:
    """End every session except this one.

    This is the answer to a laptop left on a train. It spares the session
    making the request on purpose: an action that signs you out while you are
    using it is one people hesitate to take, and hesitating is the failure mode
    that matters here.
    """
    principal = current_principal()
    if principal is None:
        return SessionsEndedResponse(ended=0)

    ended = token_sessions.revoke_all_for_user(
        db, uuid.UUID(principal.user_id), keep=principal.session_id)
    if ended:
        declare_tenant(db, uuid.UUID(principal.tenant_id))
        crud.append_audit_event(
            db, store_id=uuid.UUID(principal.tenant_id),
            actor_id=uuid.UUID(principal.user_id), action="auth.revoked_other_sessions",
            resource_type="token_session", after={"ended": ended})
        log.info("Ended %d other session(s) for user %s", ended, principal.user_id)
    return SessionsEndedResponse(ended=ended)
