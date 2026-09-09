"""The action ledger, the guardrails around it, and the payroll it proves."""

from .. import actions_engine
from .. import commerce_measurement
from .. import billing
from .. import guardrails
from .. import llm
from ..db import crud, models
from ..db.session import get_session
from ..schemas import (
    ActionAppliedRequest, ActionCreateRequest, ActionDismissRequest, ActionListResponse,
    ActionMeasureRequest, ActionOut, ActionRevertRequest, AwaitingMeasurementOut,
    GuardrailPolicyOut, GuardrailPolicyUpdate, PayrollResponse)
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
import datetime as dt
import uuid

from ..deps import _actor_id

router = APIRouter()


def _action_out(row: models.OperatorAction) -> ActionOut:
    return ActionOut(
        id=str(row.id), module=row.module, action_type=row.action_type, target=row.target,
        evidence_mode=row.evidence_mode, source_type=row.source_type, source_id=row.source_id,
        status=row.status, applied_by=row.applied_by,
        projected_impact=float(row.projected_impact) if row.projected_impact is not None else None,
        baseline=row.baseline, outcome=row.outcome, impact=row.impact, revert_to=row.revert_to,
        measurement_days=row.measurement_days, currency=row.currency, note=row.note,
        proposed_at=row.proposed_at, applied_at=row.applied_at, measured_at=row.measured_at,
    )


def _owned_action(db: Session, action_id: str, store_id: uuid.UUID) -> models.OperatorAction:
    try:
        row = db.get(models.OperatorAction, uuid.UUID(action_id))
    except ValueError:
        row = None
    if row is None or row.store_id != store_id:
        raise HTTPException(status_code=404, detail="Action not found")
    return row


@router.post("/api/actions", response_model=ActionOut, status_code=201)
def create_action(req: ActionCreateRequest, db: Session = Depends(get_session)) -> ActionOut:
    """Record something the Operator proposes to do, with the money it predicts."""
    store = crud.get_or_create_dev_store(db)
    recommendation_id = None
    if req.recommendation_id:
        try:
            recommendation_id = uuid.UUID(req.recommendation_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="recommendation_id is not a valid id")
    row = models.OperatorAction(
        store_id=store.id, recommendation_id=recommendation_id, module=req.module,
        action_type=req.action_type, target=req.target,
        evidence_mode="unverified",
        status=actions_engine.ActionStatus.PROPOSED.value,
        projected_impact=req.projected_impact, measurement_days=req.measurement_days,
        currency=req.currency.upper(), note=req.note,
    )
    db.add(row); db.commit(); db.refresh(row)
    crud.append_audit_event(
        db, store_id=store.id, actor_id=_actor_id(store.user_id), action="action.proposed",
        resource_type="operator_action", resource_id=str(row.id),
        after={"module": row.module, "type": row.action_type, "target": row.target},
    )
    return _action_out(row)


def _load_policy(db: Session, store_id: uuid.UUID) -> tuple[models.GuardrailPolicy, guardrails.Policy]:
    row = db.scalar(select(models.GuardrailPolicy).where(
        models.GuardrailPolicy.store_id == store_id))
    if row is None:
        row = models.GuardrailPolicy(store_id=store_id, **guardrails.DEFAULT_POLICY)
        db.add(row); db.commit(); db.refresh(row)
    return row, guardrails.Policy(
        enabled=row.enabled, max_actions_per_day=row.max_actions_per_day,
        max_change_pct=row.max_change_pct, auto_apply_below=row.auto_apply_below,
        protected_spend_per_day=row.protected_spend_per_day,
    )


def _applied_today(db: Session, store_id: uuid.UUID) -> int:
    """Unattended changes already made this UTC day."""
    midnight = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.count()).select_from(models.OperatorAction).where(
        models.OperatorAction.store_id == store_id,
        models.OperatorAction.applied_by == "operator",
        models.OperatorAction.applied_at >= midnight,
    )) or 0


@router.get("/api/guardrails", response_model=GuardrailPolicyOut)
def get_guardrails(db: Session = Depends(get_session)) -> GuardrailPolicyOut:
    store = crud.get_or_create_dev_store(db)
    row, policy = _load_policy(db, store.id)
    used = _applied_today(db, store.id)
    return GuardrailPolicyOut(**policy.to_dict(), applied_today=used,
                              remaining_today=guardrails.remaining_today(policy, used))


@router.put("/api/guardrails", response_model=GuardrailPolicyOut)
def update_guardrails(req: GuardrailPolicyUpdate,
                      db: Session = Depends(get_session)) -> GuardrailPolicyOut:
    """Change the limits, including switching the Operator off entirely."""
    store = crud.get_or_create_dev_store(db)
    row, before = _load_policy(db, store.id)
    for name, value in req.model_dump(exclude_none=True).items():
        setattr(row, name, value)
    row.updated_at = dt.datetime.now(dt.timezone.utc)
    db.add(row); db.commit(); db.refresh(row)
    _, after = _load_policy(db, store.id)
    crud.append_audit_event(
        db, store_id=store.id, actor_id=_actor_id(store.user_id), action="guardrails.updated",
        resource_type="guardrail_policy", resource_id=str(row.id),
        before=before.to_dict(), after=after.to_dict(),
    )
    used = _applied_today(db, store.id)
    return GuardrailPolicyOut(**after.to_dict(), applied_today=used,
                              remaining_today=guardrails.remaining_today(after, used))


@router.post("/api/actions/{action_id}/applied", response_model=ActionOut)
def mark_action_applied(action_id: str, req: ActionAppliedRequest,
                        db: Session = Depends(get_session)) -> ActionOut:
    """Put an action into effect and freeze the 'before' picture it will be judged against.

    Everything that changes a live account passes the guardrails first, whether the
    Operator or a person is doing it.
    """
    store = crud.get_or_create_dev_store(db)
    row = _owned_action(db, action_id, store.id)
    if row.status not in (actions_engine.ActionStatus.PROPOSED.value,
                          actions_engine.ActionStatus.REVERTED.value):
        raise HTTPException(status_code=409, detail=f"Action is already {row.status}")

    _, policy = _load_policy(db, store.id)
    # Reaching this route means a person acted: it exists for the "I did this"
    # button. The unattended limits bound the Operator working alone, and the
    # worker applies its own actions directly rather than calling in here.
    actor = "human"
    decision = guardrails.evaluate(
        guardrails.Request(
            actor=actor,
            money_at_stake=abs(float(row.projected_impact or 0.0)),
            change_pct=req.change_pct,
            target_spend_per_day=req.baseline.spend / req.baseline.days,
            applied_today=_applied_today(db, store.id),
        ),
        policy,
    )
    if not decision.allowed:
        crud.append_audit_event(
            db, store_id=store.id, actor_id=_actor_id(store.user_id),
            action="guardrails.refused", resource_type="operator_action",
            resource_id=str(row.id), after=decision.to_dict(),
        )
        raise HTTPException(status_code=403, detail=decision.to_dict())
    row.baseline = req.baseline.model_dump()
    row.applied_by = actor
    row.revert_to = req.revert_to
    row.status = actions_engine.ActionStatus.APPLIED.value
    row.applied_at = dt.datetime.now(dt.timezone.utc)
    db.add(row); db.commit(); db.refresh(row)
    crud.append_audit_event(
        db, store_id=store.id, actor_id=_actor_id(store.user_id), action="action.applied",
        resource_type="operator_action", resource_id=str(row.id),
        before=row.revert_to, after={"baseline": row.baseline, "by": row.applied_by},
    )
    return _action_out(row)


@router.post("/api/actions/{action_id}/measure", response_model=ActionOut)
def measure_action(action_id: str, req: ActionMeasureRequest,
                   db: Session = Depends(get_session)) -> ActionOut:
    """Price an applied action against its baseline. The engine computes; nothing else does."""
    store = crud.get_or_create_dev_store(db)
    row = _owned_action(db, action_id, store.id)
    if row.status not in (actions_engine.ActionStatus.APPLIED.value,
                          actions_engine.ActionStatus.MEASURED.value):
        raise HTTPException(status_code=409, detail="Only an applied action can be measured")
    impact = actions_engine.measure(row.baseline, req.outcome.model_dump(),
                                    requested_days=row.measurement_days)
    if impact is None:
        raise HTTPException(
            status_code=422,
            detail=f"An outcome window shorter than {actions_engine.MIN_MEASURABLE_DAYS} days proves nothing",
        )
    row.outcome = req.outcome.model_dump()
    row.impact = impact.to_dict()
    row.status = actions_engine.ActionStatus.MEASURED.value
    row.measured_at = dt.datetime.now(dt.timezone.utc)
    db.add(row); db.commit(); db.refresh(row)
    crud.append_audit_event(
        db, store_id=store.id, actor_id=_actor_id(store.user_id), action="action.measured",
        resource_type="operator_action", resource_id=str(row.id), after=row.impact,
    )
    return _action_out(row)


@router.post("/api/actions/{action_id}/revert", response_model=ActionOut)
def revert_action(action_id: str, req: ActionRevertRequest,
                  db: Session = Depends(get_session)) -> ActionOut:
    """Undo an applied action, handing back the value it should be restored to.

    The Operator does not reach into the account here — the caller does, using
    `revert_to`. What this guarantees is that the value to restore was recorded
    before the change, and that the undo is on the record.
    """
    store = crud.get_or_create_dev_store(db)
    row = _owned_action(db, action_id, store.id)
    if row.status not in (actions_engine.ActionStatus.APPLIED.value,
                          actions_engine.ActionStatus.MEASURED.value):
        raise HTTPException(status_code=409,
                            detail="Only an action that is in effect can be undone")
    if row.revert_to is None:
        raise HTTPException(
            status_code=409,
            detail="No previous value was recorded for this action, so it cannot be undone automatically",
        )
    previous_status = row.status
    row.status = actions_engine.ActionStatus.REVERTED.value
    if req.note:
        row.note = req.note
    db.add(row); db.commit(); db.refresh(row)
    crud.append_audit_event(
        db, store_id=store.id, actor_id=_actor_id(store.user_id), action="action.reverted",
        resource_type="operator_action", resource_id=str(row.id),
        before={"status": previous_status, "impact": row.impact},
        after={"restore_to": row.revert_to},
    )
    return _action_out(row)


@router.post("/api/actions/{action_id}/dismiss", response_model=ActionOut)
def dismiss_action(action_id: str, req: ActionDismissRequest,
                   db: Session = Depends(get_session)) -> ActionOut:
    """Decline a proposal.

    Until this existed the queue had one exit. A proposal could be applied or
    it could sit there, so the only way to answer "no" was to ignore it — and a
    list that cannot be emptied stops being read, which costs more than any
    single bad suggestion. Saying no is a decision the store is entitled to
    record.

    A dismissed action never reaches the payroll: it was not applied, so there
    is nothing to measure and nothing to claim. It stays on the record because
    a pattern of declining the same suggestion is worth seeing.
    """
    store = crud.get_or_create_dev_store(db)
    row = _owned_action(db, action_id, store.id)
    if row.status != actions_engine.ActionStatus.PROPOSED.value:
        raise HTTPException(
            status_code=409,
            detail="Only a proposal that nobody has acted on can be declined",
        )
    row.status = actions_engine.ActionStatus.DISMISSED.value
    if req.note:
        row.note = req.note
    db.add(row); db.commit(); db.refresh(row)
    crud.append_audit_event(
        db, store_id=store.id, actor_id=_actor_id(store.user_id), action="action.dismissed",
        resource_type="operator_action", resource_id=str(row.id),
        before={"status": actions_engine.ActionStatus.PROPOSED.value},
        after={"status": row.status, "note": req.note},
    )
    return _action_out(row)


@router.get("/api/actions", response_model=ActionListResponse)
def list_actions(status: str | None = None, limit: int = Query(default=50, ge=1, le=200),
                 db: Session = Depends(get_session)) -> ActionListResponse:
    store = crud.get_or_create_dev_store(db)
    query = select(models.OperatorAction).where(models.OperatorAction.store_id == store.id)
    if status:
        query = query.where(models.OperatorAction.status == status)
    rows = db.scalars(query.order_by(models.OperatorAction.proposed_at.desc()).limit(limit))
    return ActionListResponse(actions=[_action_out(row) for row in rows])


def _awaiting(db: Session, rows: list[models.OperatorAction]
              ) -> list[AwaitingMeasurementOut]:
    """What is in effect and not yet priced, with the date it will be.

    "In effect, waiting to be priced" is not enough to act on. Somebody who
    confirmed a restock wants to know when the answer arrives, and — when it is
    late — whether that is the calendar or a sync that stopped.
    """
    now = dt.datetime.now(dt.timezone.utc)
    out: list[AwaitingMeasurementOut] = []
    for row in rows:
        if row.status != actions_engine.ActionStatus.APPLIED.value:
            continue
        if row.applied_at is None:
            continue
        state = commerce_measurement.readiness(db, row, now=now)
        window = state.window
        baseline = row.baseline or {}
        out.append(AwaitingMeasurementOut(
            action_id=str(row.id), action_type=row.action_type, target=row.target,
            applied_at=row.applied_at,
            stock_before=baseline.get("on_hand"),
            verified_on_hand=baseline.get("verified_on_hand"),
            window_first=window.first if window else None,
            window_last=window.last if window else None,
            measure_on=(window.last + dt.timedelta(days=1)) if window else None,
            days_remaining=window.days_remaining if window else None,
            state=state.state, data_state=state.data_state, reason=state.reason,
        ))
    out.sort(key=lambda item: item.applied_at)
    return out


@router.get("/api/dashboard/payroll", response_model=PayrollResponse)
def operator_payroll(days: int = Query(default=30, ge=7, le=365), explain: bool = False,
                     db: Session = Depends(get_session)) -> PayrollResponse:
    """Did the Operator earn more than it cost over this period?"""
    store = crud.get_or_create_dev_store(db)
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    all_rows = list(db.scalars(select(models.OperatorAction).where(
        models.OperatorAction.store_id == store.id,
        models.OperatorAction.proposed_at >= since,
    )))
    rows = [row for row in all_rows if row.evidence_mode == "real"]
    waiting = _awaiting(db, all_rows)
    subscription = billing.ensure_subscription(db, store_id=store.id)
    # A trial costs the seller nothing, so it must not be charged against impact.
    cost = 0.0 if subscription.status == "trialing" else billing.cost_for_period(
        subscription.plan, days)
    currency = rows[0].currency if rows else "USD"
    # Every action is counted; only proven money carries an impact. Counting
    # from the filtered list was the bug that hid an applied action from the
    # person who applied it — waiting to be measured and not yet proven are the
    # same state, so filtering by proof erased exactly what it should have shown.
    payroll = actions_engine.build_payroll(
        [{"status": row.status,
          "impact": row.impact if row.evidence_mode == "real" else None}
         for row in all_rows],
        operator_cost=cost, currency=currency,
    )
    explanation = None
    if explain:
        explanation = llm.explain_payroll(payroll.to_dict(), period_days=days)
    return PayrollResponse(
        period_days=days, explanation=explanation,
        excluded_unverified=sum(row.evidence_mode == "unverified" for row in all_rows),
        excluded_demo=sum(row.evidence_mode == "demo" for row in all_rows),
        awaiting=waiting,
        **payroll.to_dict(),
    )
