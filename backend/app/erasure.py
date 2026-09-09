"""Carrying out a deletion request. Deliberately not something a customer can press.

The request and the execution are separate on purpose. `POST
/api/privacy/deletion-request` records that somebody asked and returns
`pending_review`; it destroys nothing, because erasure is irreversible and a
misdirected click should not perform it. This module is the other half, run by a
person who has read the request.

It is a command and not an endpoint for the same reason: there is no combination
of session, role and confirmation dialog that makes an irreversible cross-table
delete safe to expose to the internet. Running it requires shell access to the
deployment, which is a much smaller set of people than "anyone with a token".

    python -m app.erasure --store <uuid> --dry-run
    python -m app.erasure --store <uuid> --confirm "ERASE <uuid>"

`--dry-run` counts what would go and changes nothing; it is how you check you
have the right tenant before you cannot check any more. The confirmation has to
name the store, because typing the wrong id twice is harder than typing "yes".

What survives: a single audit event recording that the erasure happened, its
date and its request id. That is the minimum a deletion policy needs to be able
to demonstrate the deletion — a record of erasure holding no personal data is
not a copy of what was erased.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .db import models
from .db.session import SessionLocal, declare_tenant
from .runtime import log

#: Deleted in this order so a row never outlives what points at it. Everything
#: keyed on `store_id`; the store and its user go last.
TENANT_TABLES = (
    models.AmazonCostDaily,
    models.AmazonInventory,
    models.AmazonListing,
    models.AmazonReportRun,
    models.AmazonSalesDaily,
    models.AmazonSalesSkuPeriod,
    models.AmazonSkuCost,
    models.AmazonSyncRun,
    models.OAuthState,
    models.EmailReceipt,
    models.ProductDailyMetric,
    models.VariantDailyMetric,
    models.ProductCost,
    models.ChannelProduct,
    models.CommerceDailyMetric,
    models.WebhookDelivery,
    models.IdempotencyRecord,
    models.IntegrationCredential,
    models.OperatorAction,
    models.Recommendation,
    models.ProductEvaluation,
    models.Product,
    models.GuardrailPolicy,
    models.Subscription,
    models.ChannelConnection,
)


def plan(db: Session, store_id: uuid.UUID) -> dict[str, int]:
    """What would go, by table. Reads only this tenant's rows."""
    declare_tenant(db, store_id)
    counts: dict[str, int] = {}
    for model in TENANT_TABLES:
        rows = db.scalars(select(model).where(model.store_id == store_id)).all()
        if rows:
            counts[model.__tablename__] = len(rows)
    events = db.scalars(select(models.AuditEvent).where(
        models.AuditEvent.store_id == store_id)).all()
    if events:
        counts["audit_events"] = len(events)
    return counts


def erase(db: Session, store_id: uuid.UUID, *, request_id: str | None = None) -> dict[str, int]:
    """Remove every trace of one tenant, in one transaction.

    One transaction because a half-erased tenant is the worst of both: the
    customer's data is still here and the account no longer works, so nobody can
    tell what remains.
    """
    declare_tenant(db, store_id)
    removed = plan(db, store_id)

    for model in TENANT_TABLES:
        db.execute(delete(model).where(model.store_id == store_id))

    # The audit trail goes too — it names actions, targets and shops. What is
    # kept is one new event saying this happened, which is a record *of* the
    # erasure rather than a surviving copy of what was erased.
    db.execute(delete(models.AuditEvent).where(models.AuditEvent.store_id == store_id))

    store = db.get(models.Store, store_id)
    user_id = store.user_id if store is not None else None
    if store is not None:
        db.delete(store)
    # Sessions are keyed on the user and sit outside row-level security, so they
    # have to be named explicitly: a token that still resolves after erasure is
    # a door into an account that no longer exists.
    if user_id is not None:
        db.execute(delete(models.TokenSession).where(
            models.TokenSession.user_id == user_id))
        user = db.get(models.User, user_id)
        if user is not None:
            db.delete(user)

    db.add(models.AuditEvent(
        store_id=store_id, actor_id=user_id or store_id, action="privacy.erased",
        resource_type="workspace", resource_id=str(store_id),
        after={"request_id": request_id, "tables": removed,
               "erased_at": dt.datetime.now(dt.timezone.utc).isoformat()},
    ))
    db.commit()
    log.info("Erased tenant %s: %s", store_id, removed)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Erase one tenant's data.")
    parser.add_argument("--store", required=True, help="store id (tenant)")
    parser.add_argument("--confirm", default="",
                        help='must be exactly: ERASE <store id>')
    parser.add_argument("--dry-run", action="store_true",
                        help="count what would go and change nothing")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    try:
        store_id = uuid.UUID(args.store)
    except ValueError:
        print("Not a store id.", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        counts = plan(db, store_id)
        if not counts:
            print(f"Nothing found for {store_id}. Wrong id, or already erased.")
            return 1
        print(f"Tenant {store_id}:")
        for table, count in sorted(counts.items()):
            print(f"  {table:28} {count}")

        if args.dry_run:
            print("\nDry run. Nothing was changed.")
            return 0
        if args.confirm != f"ERASE {store_id}":
            print(f'\nRefused. Re-run with --confirm "ERASE {store_id}"',
                  file=sys.stderr)
            return 2

        removed = erase(db, store_id)
        print(f"\nErased. {sum(removed.values())} row(s) removed.")
        print("Now revoke the app in the Shopify Partner Dashboard so no token "
              "of theirs remains usable, and tell the customer, with the date.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
