"""
SQLAlchemy ORM models — the core data layer.

Kept intentionally small for the MVP. The schema is designed to grow into the
full architecture (campaigns, keywords, inventory, reports) without rework.
pgvector is used later for semantic search over keywords/competitors.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, Uuid, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Portable column types: native JSONB/UUID on Postgres (production), generic
# JSON/CHAR on SQLite so dev and tests run with zero infrastructure.
_JSON = JSON().with_variant(JSONB, "postgresql")
_UUID = Uuid(as_uuid=True)


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    stores: Mapped[list["Store"]] = relationship(back_populates="user")


class Store(Base):
    __tablename__ = "stores"
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    marketplace: Mapped[str] = mapped_column(String(16), default="US")  # US, DE, UK...
    seller_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    user: Mapped["User"] = relationship(back_populates="stores")


class LoginCode(Base):
    """One six-digit code sent to one address, and the evidence it was used.

    Deliberately *not* a tenant table: it is read before anyone knows which
    tenant the request belongs to, exactly like `oauth_states`, so it stays out
    of the row-level-security policies for the same reason. What guards it is
    that a row is only ever found by an address the caller had to name, and only
    ever accepted with a secret we sent to that address.

    The code itself is never stored — only an HMAC of it — so a leaked database
    does not hand over the ability to sign in as anyone with a pending code.
    """
    __tablename__ = "login_codes"
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class TokenSession(Base):
    """One issued token, and the ability to take it back.

    Before this table a token was a promise nobody could break: signing out
    cleared the browser and the token stayed valid for its whole life, so a
    laptop left on a train was an open door until the week ran out. The only
    lever was rotating `JWT_SECRET`, which signs out every user on every device
    to deal with one lost machine.

    The token now carries a `jti` and this row is what it names. Verifying the
    signature still proves the token was minted here; this proves it is *still*
    meant to work. Both have to hold, so revoking is a single UPDATE and takes
    effect on the next request.

    Not a tenant table, for the same reason as `login_codes` and `oauth_states`:
    it is read by the middleware before a tenant context exists. It is keyed on
    the user, and the only way to name a row is to hold the token that carries
    its id.
    """
    __tablename__ = "token_sessions"
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(_UUID, index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(_UUID, index=True)
    role: Mapped[str] = mapped_column(String(32))
    # How the person proved who they were: "google", "email" or "cli".
    method: Mapped[str] = mapped_column(String(32))
    issued_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Written at most once a minute, so the list of sessions can say "last used
    # an hour ago" without costing a write on every request.
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Subscription(Base):
    """Tenant-owned commercial access state; Stripe identifiers are optional."""
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("store_id"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    plan: Mapped[str] = mapped_column(String(24), default="operator")
    status: Mapped[str] = mapped_column(String(24), index=True, default="trialing")
    trial_ends_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ChannelConnection(Base):
    """Provider-neutral sales channel connected to one tenant store."""
    __tablename__ = "channel_connections"
    __table_args__ = (UniqueConstraint("store_id", "provider", "external_account_id"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    external_account_id: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    settings: Mapped[dict] = mapped_column(_JSON, default=dict)
    connected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommerceDailyMetric(Base):
    """Canonical daily commerce facts produced by any channel adapter."""
    __tablename__ = "commerce_daily_metrics"
    __table_args__ = (UniqueConstraint("store_id", "channel_id", "metric_date"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channel_connections.id"), index=True)
    metric_date: Mapped[dt.date] = mapped_column(Date, index=True)
    revenue: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    refunds: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    fees: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    landed_cogs: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    advertising_spend: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    orders: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[int] = mapped_column(Integer, default=0)
    sessions: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3))
    source: Mapped[str] = mapped_column(String(32))
    costs_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ChannelProduct(Base):
    """What a channel says is on the shelf right now.

    `on_hand` is nullable and the distinction matters: None means the channel
    does not count this product's stock, which is not the same as none left.
    Reading it as zero would report every untracked product as about to run out.
    """
    __tablename__ = "channel_products"
    __table_args__ = (UniqueConstraint("store_id", "channel_id", "external_product_id"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channel_connections.id"), index=True)
    external_product_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    on_hand: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # What tells a snowboard from a gift card. Nullable because a product synced
    # before these existed has neither, and unknown must not read as eligible.
    is_gift_card: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    requires_shipping: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ProductDailyMetric(Base):
    """Units and money per product per day.

    The store-wide daily totals cannot answer a product question, and a restock
    proposal is judged on what that one product did before and after — not on
    what the shop did around it.
    """
    __tablename__ = "product_daily_metrics"
    __table_args__ = (
        UniqueConstraint("store_id", "channel_id", "external_product_id", "metric_date"),
    )
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channel_connections.id"), index=True)
    external_product_id: Mapped[str] = mapped_column(String(128), index=True)
    metric_date: Mapped[dt.date] = mapped_column(Date, index=True)
    units: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3))
    source: Mapped[str] = mapped_column(String(32))
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class VariantDailyMetric(Base):
    """Units and money per *variant* per day, net of what was given back.

    `product_daily_metrics` answers "is this product running out", which is a
    product-level question. Margin is not: two variants of one snowboard can
    cost different amounts to buy, and pricing a restock from a product-level
    average would be an invented number wearing a measured one's clothes.

    `revenue` is what the shop actually kept for these units: after line
    discounts, after tax where the shop's prices include it, and after refunds.
    Cancelled orders never appear at all. Nothing here identifies a customer —
    the same rule as everywhere else — so there is nothing to redact.
    """
    __tablename__ = "variant_daily_metrics"
    __table_args__ = (
        UniqueConstraint("store_id", "channel_id", "external_variant_id", "metric_date"),
    )
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channel_connections.id"),
                                                  index=True)
    external_product_id: Mapped[str] = mapped_column(String(128), index=True)
    external_variant_id: Mapped[str] = mapped_column(String(128), index=True)
    variant_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metric_date: Mapped[dt.date] = mapped_column(Date, index=True)
    units: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    refunded_units: Mapped[int] = mapped_column(Integer, default=0)
    refunded_revenue: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3))
    source: Mapped[str] = mapped_column(String(32))
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ProductCost(Base):
    """What one unit cost to buy, and how we know.

    Kept as history rather than a current value. A window is measured from the
    days inside it, so the cost that matters is the one that was true on the day
    the unit sold — overwriting last month's figure with this month's would
    quietly restate every result that had already been priced.

    `external_variant_id` is the empty string for a value that applies to every
    variant of a product. An exact variant always wins over that fallback; among
    equals, the latest `effective_from`, then the latest `observed_at`, then a
    seller's own value over Shopify's, because a person overriding is a
    deliberate act.

    `source` says where the number came from and `verification` how far that
    goes: `confirmed` for a value read out of the shop's own record, `reported`
    for one a person typed. Neither is audited by us, and the screens say which
    is which rather than presenting both as fact.
    """
    __tablename__ = "product_costs"
    __table_args__ = (
        UniqueConstraint("store_id", "external_product_id", "external_variant_id",
                         "effective_from", "source", name="uq_product_cost_per_day"),
    )
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("channel_connections.id"), nullable=True, index=True)
    external_product_id: Mapped[str] = mapped_column(String(128), index=True)
    #: "" means every variant of this product.
    external_variant_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    variant_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Four places, not two: a landed cost of 12.3456 a unit is ordinary, and
    #: rounding it at rest would make the margin depend on when we stored it.
    amount: Mapped[float] = mapped_column(Numeric(18, 4))
    #: The same figure split the way a seller thinks about it. `amount` is what
    #: the measurement uses and is always the total; these are null for a value
    #: read out of Shopify, which reports one number and no breakdown.
    purchase_amount: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    extra_amount: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    effective_from: Mapped[dt.date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(16))          # shopify | manual
    verification: Mapped[str] = mapped_column(String(16))    # confirmed | reported
    entered_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ProductEvaluation(Base):
    """Persisted output of the AI Product Hunter — feeds the future data moat."""
    __tablename__ = "product_evaluations"
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    inputs: Mapped[dict] = mapped_column(_JSON)        # raw ProductInput
    economics: Mapped[dict] = mapped_column(_JSON)     # computed economics
    subscores: Mapped[dict] = mapped_column(_JSON)
    score: Mapped[int] = mapped_column(Integer)
    verdict: Mapped[str] = mapped_column(String(16))
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


# --- Stubs for later modules (Phase 1+). Defined now so migrations stay additive. ---

class Product(Base):
    __tablename__ = "products"
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    asin: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    cogs: Mapped[float | None] = mapped_column(Float, nullable=True)


class SchedulerRun(Base):
    """One row per scheduled run — the lock and the record at once.

    A cron that fires twice must not do the work twice, and the unique key is
    what stops it: the second firing loses the insert. A scheduler that silently
    stops firing produces no errors at all, and the same row answers "when did
    this last finish".

    Not a tenant table: a run is about the deployment, and the per-store work
    inside it declares its own tenant.
    """
    __tablename__ = "scheduler_runs"
    __table_args__ = (UniqueConstraint("job", "run_key", name="uq_scheduler_run_once"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    job: Mapped[str] = mapped_column(String(64), index=True)
    run_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Counts, never identities.
    found: Mapped[int] = mapped_column(Integer, default=0)
    queued: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class EmailReceipt(Base):
    """One row per email we decided to send, so we decide once.

    Stripe redelivers, the trial scheduler runs daily, and a worker can die
    between reserving a send and performing it. Without this, each becomes a
    second copy of a message about somebody's money.

    `dedupe_key` is what makes two sends the same send — a Stripe event id, a
    trial end date. The recipient is deliberately not part of it: an address
    that changed does not make it a different notification.
    """
    __tablename__ = "email_receipts"
    __table_args__ = (UniqueConstraint("store_id", "kind", "dedupe_key",
                                       name="uq_email_receipt_once"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    kind: Mapped[str] = mapped_column(String(48))
    dedupe_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="reserved", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    module: Mapped[str] = mapped_column(String(32))     # product_hunter | ppc | inventory | listing
    severity: Mapped[str] = mapped_column(String(16), default="info")  # info | warning | critical
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[dict] = mapped_column(_JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("store_id", "operation", "key"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    operation: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="processing")
    response: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    before: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class IntegrationCredential(Base):
    __tablename__ = "integration_credentials"
    __table_args__ = (UniqueConstraint("store_id", "provider"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    encrypted_secret: Mapped[str] = mapped_column(Text)
    nonce: Mapped[str] = mapped_column(String(64))
    key_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PublicFunnelEvent(Base):
    """One event on the public /try page — a visit or an evaluation — with the
    campaign source the link carried. No tenant, no person: `visitor` is a
    keyed daily hash, never an address. See migration 0025."""
    __tablename__ = "public_funnel_events"
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(16))
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    medium: Mapped[str | None] = mapped_column(String(64), nullable=True)
    campaign: Mapped[str | None] = mapped_column(String(64), nullable=True)
    visitor: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OAuthState(Base):
    __tablename__ = "oauth_states"
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    context: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("provider", "delivery_id"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    delivery_id: Mapped[str] = mapped_column(String(128))
    topic: Mapped[str] = mapped_column(String(64), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AmazonListing(Base):
    __tablename__ = "amazon_listings"
    __table_args__ = (UniqueConstraint("store_id", "marketplace_id", "sku"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(32), index=True)
    sku: Mapped[str] = mapped_column(String(128))
    asin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[dict] = mapped_column(_JSON)
    issues: Mapped[dict] = mapped_column(_JSON)
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AmazonInventory(Base):
    __tablename__ = "amazon_inventory"
    __table_args__ = (UniqueConstraint("store_id", "marketplace_id", "seller_sku"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(32), index=True)
    seller_sku: Mapped[str] = mapped_column(String(128))
    asin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    fn_sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fulfillable_quantity: Mapped[int] = mapped_column(Integer, default=0)
    total_quantity: Mapped[int] = mapped_column(Integer, default=0)
    quantities: Mapped[dict] = mapped_column(_JSON)
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AmazonSyncRun(Base):
    __tablename__ = "amazon_sync_runs"
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    listings_count: Mapped[int] = mapped_column(Integer, default=0)
    inventory_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AmazonReportRun(Base):
    __tablename__ = "amazon_report_runs"
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(32), index=True)
    report_type: Mapped[str] = mapped_column(String(64))
    amazon_report_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    report_document_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    date_start: Mapped[dt.date] = mapped_column(Date)
    date_end: Mapped[dt.date] = mapped_column(Date)
    rows_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AmazonSalesDaily(Base):
    __tablename__ = "amazon_sales_daily"
    __table_args__ = (UniqueConstraint("store_id", "marketplace_id", "sales_date"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(32), index=True)
    sales_date: Mapped[dt.date] = mapped_column(Date, index=True)
    ordered_sales: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    units_ordered: Mapped[int] = mapped_column(Integer, default=0)
    order_items: Mapped[int] = mapped_column(Integer, default=0)
    page_views: Mapped[int] = mapped_column(Integer, default=0)
    sessions: Mapped[int] = mapped_column(Integer, default=0)
    buy_box_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AmazonCostDaily(Base):
    __tablename__ = "amazon_cost_daily"
    __table_args__ = (UniqueConstraint("store_id", "marketplace_id", "cost_date", "category"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(32), index=True)
    cost_date: Mapped[dt.date] = mapped_column(Date, index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    source: Mapped[str] = mapped_column(String(32))
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AmazonSalesSkuPeriod(Base):
    __tablename__ = "amazon_sales_sku_periods"
    __table_args__ = (UniqueConstraint("report_run_id", "sku"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    report_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("amazon_report_runs.id"), index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(32), index=True)
    sku: Mapped[str] = mapped_column(String(128))
    units_ordered: Mapped[int] = mapped_column(Integer, default=0)
    ordered_sales: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)


class AmazonSkuCost(Base):
    __tablename__ = "amazon_sku_costs"
    __table_args__ = (UniqueConstraint("store_id", "marketplace_id", "sku"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(32), index=True)
    sku: Mapped[str] = mapped_column(String(128))
    landed_cost: Mapped[float] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(3))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OperatorAction(Base):
    """One thing the Operator proposed, and what it was actually worth.

    This is the ledger behind the payroll: every row carries the money the engine
    predicted, the money that was observed, and enough of a trail (baseline,
    outcome, who applied it, what it can be reverted to) to defend the number.
    """
    __tablename__ = "operator_actions"
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendations.id"), nullable=True, index=True)
    module: Mapped[str] = mapped_column(String(32), index=True)      # ppc | inventory | ...
    action_type: Mapped[str] = mapped_column(String(48))             # NEGATE_KEYWORD | ...
    target: Mapped[str] = mapped_column(String(255))                 # keyword / sku / asin
    evidence_mode: Mapped[str] = mapped_column(String(16), default="unverified", index=True)
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="proposed", index=True)
    applied_by: Mapped[str | None] = mapped_column(String(16), nullable=True)  # human | operator
    projected_impact: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    baseline: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    outcome: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    impact: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    revert_to: Mapped[dict | None] = mapped_column(_JSON, nullable=True)  # previous value
    measurement_days: Mapped[int] = mapped_column(Integer, default=14)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    applied_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    measured_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GuardrailPolicy(Base):
    """The limits one store places on what the Operator may do unattended."""
    __tablename__ = "guardrail_policies"
    __table_args__ = (UniqueConstraint("store_id"),)
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)   # the kill switch
    max_actions_per_day: Mapped[int] = mapped_column(Integer, default=20)
    max_change_pct: Mapped[float] = mapped_column(Float, default=0.20)
    auto_apply_below: Mapped[float] = mapped_column(Float, default=25.0)
    protected_spend_per_day: Mapped[float] = mapped_column(Float, default=50.0)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
