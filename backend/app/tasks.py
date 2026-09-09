"""RQ worker entrypoints. Functions here must be retry-safe and tenant-bound."""
from __future__ import annotations

import os
import uuid
import datetime as dt
import time
from decimal import Decimal

from sqlalchemy import delete, func, select

from .db import crud, models
from .db.session import SessionLocal
from .autopilot_service import meets_money_criteria, operator_to_detail
from .schemas import AutopilotScanRequest
from .runtime import log
from .security import Principal, bind_principal, reset_principal
from . import amazon_sp_api, credentials, product_costs, shopify, woocommerce


def run_autopilot_scan(*, payload: dict, tenant_id: str, actor_id: str,
                       idempotency_record_id: str) -> dict:
    principal_token = bind_principal(
        Principal(user_id=actor_id, tenant_id=tenant_id, role="operator")
    )
    db = SessionLocal()
    record = None
    try:
        req = AutopilotScanRequest(**payload)
        store = crud.get_or_create_dev_store(db)
        record = db.get(models.IdempotencyRecord, uuid.UUID(idempotency_record_id))
        if record is None or record.store_id != store.id:
            raise RuntimeError("Idempotency record not found for tenant.")
        if record.status == "completed" and record.response:
            return record.response

        queued = skipped = 0
        for niche in req.niches:
            detail = operator_to_detail(niche, req.limit)
            if not meets_money_criteria(detail, req):
                skipped += 1
                continue
            crud.save_recommendation(
                db, store_id=store.id, module="autopilot", severity=detail["severity"],
                title=niche, detail=detail, commit=False,
            )
            queued += 1
        result = {"queued": queued, "skipped": skipped}
        crud.append_audit_event(
            db, store_id=store.id, actor_id=uuid.UUID(actor_id), action="autopilot.scan",
            resource_type="autopilot_queue", idempotency_key=record.key,
            after={**result, "niches": req.niches}, commit=False,
        )
        crud.complete_idempotency(db, record, result)
        return result
    except Exception:
        db.rollback()
        if record is not None:
            crud.fail_idempotency(db, record)
        raise
    finally:
        db.close()
        reset_principal(principal_token)


def _listing_identity(item: dict) -> tuple[str | None, str | None]:
    asin = None
    for group in item.get("identifiers", []):
        for identifier in group.get("identifiers", []):
            if identifier.get("identifierType") == "ASIN":
                asin = identifier.get("identifier")
                break
    summaries = item.get("summaries") or []
    title = summaries[0].get("itemName") if summaries else None
    return asin, title


def run_amazon_sync(*, tenant_id: str, actor_id: str, region: str,
                    idempotency_record_id: str) -> dict:
    principal_token = bind_principal(
        Principal(user_id=actor_id, tenant_id=tenant_id, role="operator")
    )
    db = SessionLocal()
    record = None
    run = None
    try:
        store = crud.get_or_create_dev_store(db)
        record_id = uuid.UUID(idempotency_record_id)
        record = db.get(models.IdempotencyRecord, record_id)
        if record is None or record.store_id != store.id:
            raise RuntimeError("Idempotency record not found for tenant.")
        if record.status == "completed" and record.response:
            return record.response
        run = db.get(models.AmazonSyncRun, record_id)
        if run is None:
            run = models.AmazonSyncRun(id=record_id, store_id=store.id, status="running")
        else:
            run.status = "running"
            run.error_code = None
        db.add(run)
        db.commit()

        client = amazon_sp_api.client_for_store(db, store_id=store.id, region=region)
        account = client.marketplace_participations().get("payload", {})
        participations = account.get("marketplaceParticipations", [])
        marketplace_ids = [
            entry.get("marketplace", {}).get("id") for entry in participations
            if entry.get("marketplace", {}).get("id")
        ]
        now = dt.datetime.now(dt.timezone.utc)
        listing_count = inventory_count = 0
        for marketplace_id in marketplace_ids:
            for page in client.listing_pages(
                seller_id=store.seller_id, marketplace_id=marketplace_id
            ):
                for item in page.get("items", []):
                    sku = item.get("sku")
                    if not sku:
                        continue
                    row = db.scalar(select(models.AmazonListing).where(
                        models.AmazonListing.store_id == store.id,
                        models.AmazonListing.marketplace_id == marketplace_id,
                        models.AmazonListing.sku == sku,
                    )) or models.AmazonListing(
                        store_id=store.id, marketplace_id=marketplace_id, sku=sku
                    )
                    row.asin, row.title = _listing_identity(item)
                    row.status = {"statuses": item.get("status", [])}
                    row.issues = {"issues": item.get("issues", [])}
                    row.synced_at = now
                    db.add(row)
                    listing_count += 1
                # Commit per page: a large catalogue must not ride on one giant
                # transaction, and an interrupted run keeps the pages it finished.
                # Rows are upserts keyed by (store, marketplace, sku), so a retry
                # re-syncs them harmlessly.
                run.listings_count = listing_count
                db.add(run)
                db.commit()
            for page in client.inventory_pages(marketplace_id=marketplace_id):
                for item in page.get("payload", {}).get("inventorySummaries", []):
                    sku = item.get("sellerSku")
                    if not sku:
                        continue
                    details = item.get("inventoryDetails") or {}
                    row = db.scalar(select(models.AmazonInventory).where(
                        models.AmazonInventory.store_id == store.id,
                        models.AmazonInventory.marketplace_id == marketplace_id,
                        models.AmazonInventory.seller_sku == sku,
                    )) or models.AmazonInventory(
                        store_id=store.id, marketplace_id=marketplace_id, seller_sku=sku
                    )
                    row.asin = item.get("asin")
                    row.fn_sku = item.get("fnSku")
                    row.fulfillable_quantity = int(details.get("fulfillableQuantity") or 0)
                    row.total_quantity = int(item.get("totalQuantity") or 0)
                    row.quantities = details
                    row.synced_at = now
                    db.add(row)
                    inventory_count += 1
                run.inventory_count = inventory_count
                db.add(run)
                db.commit()
        run.status = "completed"
        run.listings_count = listing_count
        run.inventory_count = inventory_count
        run.completed_at = now
        result = {"listings": listing_count, "inventory": inventory_count,
                  "marketplaces": len(marketplace_ids)}
        db.add(run)
        crud.append_audit_event(
            db, store_id=store.id, actor_id=uuid.UUID(actor_id), action="amazon.sync.completed",
            resource_type="amazon_sync", resource_id=str(run.id),
            idempotency_key=record.key, after=result, commit=False,
        )
        crud.complete_idempotency(db, record, result)
        return result
    except Exception as exc:
        db.rollback()
        if run is not None:
            run.status = "failed"
            run.error_code = type(exc).__name__[:64]
            run.completed_at = dt.datetime.now(dt.timezone.utc)
            db.add(run)
            db.commit()
        if record is not None:
            crud.fail_idempotency(db, record)
        raise
    finally:
        db.close()
        reset_principal(principal_token)


def _mark_channel_revoked(db, store, channel, reason: str, actor_id: str) -> None:
    """Record that a channel can no longer be reached with the token we hold.

    Left connected, the UI keeps promising a live store and every scheduled sync
    burns its retries against a door that is locked. The credential goes too: a
    token Shopify has revoked is not a secret worth keeping, only a liability.
    """
    channel.status = "disconnected"
    channel.settings = {**(channel.settings or {}),
                        "revoked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "revoked_reason": reason[:300]}
    db.add(channel)
    db.execute(delete(models.IntegrationCredential).where(
        models.IntegrationCredential.store_id == store.id,
        models.IntegrationCredential.provider == shopify.credential_provider(
            channel.external_account_id),
    ))
    crud.append_audit_event(
        db, store_id=store.id, actor_id=uuid.UUID(actor_id), action="shopify.access_revoked",
        resource_type="channel_connection", resource_id=str(channel.id),
        after={"reason": reason[:300]}, commit=False,
    )
    db.commit()


def _money(node, key: str = "shopMoney") -> Decimal:
    """One MoneyV2 as Decimal. Absent is zero here on purpose: these are sums of
    line components, and a missing tax line means no tax, not unknown tax."""
    amount = ((node or {}).get(key) or {}).get("amount")
    return product_costs.to_decimal(amount) or Decimal("0")


def _line_revenue(item: dict, *, taxes_included: bool | None) -> Decimal:
    """What the shop kept for one line.

    `discountedTotalSet` is the line after its discounts. `originalTotalSet` is
    before them, and using it would credit a restock with money nobody paid — so
    it is only the fallback for a shop whose API would not give us the other.

    Tax is removed when the shop's prices include it. Where they do not, the tax
    was never inside this figure and subtracting it would understate the sale.
    """
    discounted = item.get("discountedTotalSet")
    gross = (_money(discounted) if discounted is not None
             else _money(item.get("originalTotalSet")))
    if not taxes_included:
        return gross
    tax = sum((_money(line.get("priceSet")) for line in item.get("taxLines") or []),
              Decimal("0"))
    return gross - tax


def _refunds_by_line(order: dict) -> dict[str, dict]:
    """Units and money given back, per line item.

    Attributed to the day of the sale rather than the day of the refund: the
    question a window answers is how many units this restock actually sold, and
    a unit that came back was not one of them. A refund that arrives after a
    result has been priced does not restate it — measured results are not
    rewritten, and that limit is named rather than hidden.
    """
    out: dict[str, dict] = {}
    for refund in order.get("refunds") or []:
        for node in ((refund.get("refundLineItems") or {}).get("nodes") or []):
            line_id = str(((node.get("lineItem") or {}).get("id")) or "")
            if not line_id:
                continue
            entry = out.setdefault(line_id, {"units": 0, "amount": Decimal("0")})
            entry["units"] += int(node.get("quantity") or 0)
            entry["amount"] += _money(node.get("subtotalSet"))
    return out


def run_shopify_sync(*, tenant_id: str, actor_id: str, channel_id: str, days: int,
                     idempotency_record_id: str) -> dict:
    """Rebuild daily aggregate facts; never persist Shopify order/customer payloads."""
    principal_token = bind_principal(
        Principal(user_id=actor_id, tenant_id=tenant_id, role="operator")
    )
    db = SessionLocal(); record = None; client = None
    try:
        if not 1 <= days <= 60:
            raise ValueError("Shopify sync range must be between 1 and 60 days.")
        store = crud.get_or_create_dev_store(db)
        record = db.get(models.IdempotencyRecord, uuid.UUID(idempotency_record_id))
        channel = db.get(models.ChannelConnection, uuid.UUID(channel_id))
        if record is None or record.store_id != store.id or channel is None or \
                channel.store_id != store.id or channel.provider != "shopify":
            raise RuntimeError("Shopify sync context not found for tenant.")
        if record.status == "completed" and record.response:
            return record.response
        token = credentials.load_credential(
            db, store_id=store.id, provider=shopify.credential_provider(channel.external_account_id)
        )
        if not token:
            raise shopify.ShopifyAuthorizationError("Shopify credential is missing.")
        client = shopify.AdminGraphQLClient(channel.external_account_id, token)
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        totals: dict[tuple[dt.date, str], dict[str, float | int]] = {}
        per_product: dict[tuple[dt.date, str], dict] = {}
        per_variant: dict[tuple[dt.date, str], dict] = {}
        for orders in client.order_pages(created_at_min=cutoff):
            for order in orders:
                if order.get("cancelledAt"):
                    continue
                created = dt.datetime.fromisoformat(str(order["createdAt"]).replace("Z", "+00:00")).date()
                money = ((order.get("currentTotalPriceSet") or {}).get("shopMoney") or {})
                currency = money.get("currencyCode") or order.get("currencyCode") or channel.currency
                key = (created, currency)
                total = totals.setdefault(key, {"revenue": 0.0, "orders": 0, "units": 0})
                total["revenue"] = float(total["revenue"]) + float(money.get("amount") or 0)
                total["orders"] = int(total["orders"]) + 1
                items = (order.get("lineItems") or {}).get("nodes") or []
                total["units"] = int(total["units"]) + sum(
                    int(item.get("quantity") or 0) for item in items
                )
                # The same orders, broken out per product and per variant.
                # Store-wide totals cannot answer a question about one product,
                # and margin cannot be answered per product either: two variants
                # of one snowboard can cost different amounts to buy.
                taxes_included = order.get("taxesIncluded")
                refunded = _refunds_by_line(order)
                for item in items:
                    product = item.get("product") or {}
                    product_id = str(product.get("id") or "")
                    if not product_id:
                        continue   # a deleted product cannot be acted on
                    quantity = int(item.get("quantity") or 0)
                    revenue = _line_revenue(item, taxes_included=taxes_included)
                    back = refunded.get(str(item.get("id") or ""), None) or {}
                    refunded_units = min(int(back.get("units") or 0), quantity)
                    refunded_revenue = Decimal(str(back.get("amount") or 0))

                    variant = item.get("variant") or {}
                    variant_id = str(variant.get("id") or "")
                    if not variant_id:
                        # Known to have sold, not known as what. No cost will
                        # ever be found for this key, which is the honest
                        # outcome: units we cannot attribute are units we
                        # cannot price.
                        variant_id = product_costs.unknown_variant_key(product_id)
                    variant_key = (created, variant_id)
                    row = per_variant.setdefault(variant_key, {
                        "product_id": product_id, "units": 0,
                        "revenue": Decimal("0"), "refunded_units": 0,
                        "refunded_revenue": Decimal("0"), "currency": currency,
                        "title": variant.get("title") or "",
                    })
                    row["units"] += quantity
                    row["revenue"] += revenue
                    row["refunded_units"] += refunded_units
                    row["refunded_revenue"] += refunded_revenue

                    per_product_key = (created, product_id)
                    line = per_product.setdefault(per_product_key, {
                        "units": 0, "revenue": Decimal("0"), "currency": currency,
                        "title": product.get("title") or "",
                    })
                    line["units"] += quantity - refunded_units
                    line["revenue"] += revenue - refunded_revenue
        now = dt.datetime.now(dt.timezone.utc)
        for (metric_date, currency), total in totals.items():
            row = db.scalar(select(models.CommerceDailyMetric).where(
                models.CommerceDailyMetric.store_id == store.id,
                models.CommerceDailyMetric.channel_id == channel.id,
                models.CommerceDailyMetric.metric_date == metric_date,
            )) or models.CommerceDailyMetric(
                store_id=store.id, channel_id=channel.id, metric_date=metric_date,
                refunds=0, fees=0, landed_cogs=0, advertising_spend=0, sessions=0,
            )
            row.revenue = total["revenue"]; row.orders = total["orders"]
            row.units = total["units"]; row.currency = currency
            row.source = "shopify_graphql"; row.costs_complete = False; row.synced_at = now
            db.add(row)

        for (metric_date, variant_id), line in per_variant.items():
            row = db.scalar(select(models.VariantDailyMetric).where(
                models.VariantDailyMetric.store_id == store.id,
                models.VariantDailyMetric.channel_id == channel.id,
                models.VariantDailyMetric.external_variant_id == variant_id,
                models.VariantDailyMetric.metric_date == metric_date,
            )) or models.VariantDailyMetric(
                store_id=store.id, channel_id=channel.id,
                external_product_id=str(line["product_id"]),
                external_variant_id=variant_id, metric_date=metric_date,
            )
            row.external_product_id = str(line["product_id"])
            row.variant_title = str(line["title"])[:255] or None
            row.units = int(line["units"])
            row.revenue = Decimal(line["revenue"]).quantize(Decimal("0.01"))
            row.refunded_units = int(line["refunded_units"])
            row.refunded_revenue = Decimal(line["refunded_revenue"]).quantize(Decimal("0.01"))
            row.currency = str(line["currency"]); row.source = "shopify_graphql"
            row.synced_at = now
            db.add(row)

        for (metric_date, product_id), line in per_product.items():
            row = db.scalar(select(models.ProductDailyMetric).where(
                models.ProductDailyMetric.store_id == store.id,
                models.ProductDailyMetric.channel_id == channel.id,
                models.ProductDailyMetric.external_product_id == product_id,
                models.ProductDailyMetric.metric_date == metric_date,
            )) or models.ProductDailyMetric(
                store_id=store.id, channel_id=channel.id,
                external_product_id=product_id, metric_date=metric_date,
            )
            row.units = int(line["units"])
            row.revenue = Decimal(line["revenue"]).quantize(Decimal("0.01"))
            row.currency = str(line["currency"]); row.source = "shopify_graphql"
            row.synced_at = now
            db.add(row)

        # What is on the shelf right now. Read every time, because the whole
        # restock proposal turns on a number that is only true at a moment.
        products_seen = 0
        costs_seen = 0
        for products in client.product_pages():
            for product in products:
                product_id = str(product.get("id") or "")
                if not product_id:
                    continue
                row = db.scalar(select(models.ChannelProduct).where(
                    models.ChannelProduct.store_id == store.id,
                    models.ChannelProduct.channel_id == channel.id,
                    models.ChannelProduct.external_product_id == product_id,
                )) or models.ChannelProduct(
                    store_id=store.id, channel_id=channel.id,
                    external_product_id=product_id,
                )
                row.title = product.get("title")
                row.status = product.get("status")
                # An untracked product reports 0 the same as an empty one, so
                # the count is only kept when Shopify says it is counting. None
                # means unknown, and the engine skips what it does not know.
                inventory = product.get("totalInventory")
                row.on_hand = (int(inventory)
                               if product.get("tracksInventory") and inventory is not None
                               else None)
                row.is_gift_card = bool(product.get("isGiftCard"))
                # Physical only if every variant we looked at ships. A product
                # with no variants tells us nothing, and nothing is not a yes.
                variants = (product.get("variants") or {}).get("nodes") or []
                shipping = [bool((v.get("inventoryItem") or {}).get("requiresShipping"))
                            for v in variants]
                row.requires_shipping = all(shipping) if shipping else None
                row.synced_at = now
                db.add(row)
                products_seen += 1

                # What the shop paid for a unit, straight out of its own record.
                # `read_inventory` already permits this, so nobody is asked to
                # reconnect. Shopify has no history for it — the value is simply
                # what it is today — so today is when this one starts applying,
                # and a change tomorrow becomes a second row rather than an edit
                # to this one.
                for variant in variants:
                    variant_id = str(variant.get("id") or "")
                    if not variant_id:
                        continue
                    unit_cost = (variant.get("inventoryItem") or {}).get("unitCost")
                    if not unit_cost:
                        # Not filled in. Unknown is not zero, and writing a zero
                        # here would turn "we do not know" into a proven margin
                        # equal to the whole sale price.
                        continue
                    amount = product_costs.to_decimal(unit_cost.get("amount"))
                    if amount is None:
                        continue
                    costs_seen += product_costs.record(
                        db, store_id=store.id, channel_id=channel.id,
                        product_id=product_id, variant_id=variant_id,
                        variant_title=variant.get("title"),
                        amount=amount,
                        currency=str(unit_cost.get("currencyCode")
                                     or channel.currency or "USD"),
                        effective_from=now.date(), source=product_costs.SHOPIFY,
                        verification=product_costs.CONFIRMED,
                        entered_by="shopify",
                    ) is not None

        channel.synced_at = now
        # Which days this read, not merely that it read. A measurement window is
        # only observed if something actually looked at the days inside it, and
        # "the shop was synced afterwards" does not say how far back the sync
        # reached. Without this, a 7-day sync would silently certify a 14-day
        # window as covered.
        channel.settings = {**(channel.settings or {}),
                            "synced_from": cutoff.date().isoformat(),
                            "synced_through": now.date().isoformat()}
        db.add(channel)
        result = {"days": days, "daily_rows": len(totals),
                  "product_rows": len(per_product), "variant_rows": len(per_variant),
                  "products": products_seen, "costs_recorded": costs_seen,
                  "orders": sum(int(v["orders"]) for v in totals.values())}
        # What this shop would not answer, so the measurement can say why it is
        # waiting instead of reporting an absence it cannot explain.
        refused = sorted(getattr(client, "unavailable", set()) or ())
        if refused:
            result["unavailable"] = refused
            channel.settings = {**(channel.settings or {}), "shopify_unavailable": refused}
        elif (channel.settings or {}).get("shopify_unavailable"):
            settings = dict(channel.settings or {})
            settings.pop("shopify_unavailable", None)
            channel.settings = settings
        crud.append_audit_event(
            db, store_id=store.id, actor_id=uuid.UUID(actor_id),
            action="shopify.sync.completed", resource_type="channel_connection",
            resource_id=str(channel.id), idempotency_key=record.key, after=result, commit=False,
        )
        crud.complete_idempotency(db, record, result)

        # New sales have just landed, so re-read them. Waiting for a person to
        # press a button was the reason a seller could sell out of something and
        # hear about it only when they thought to ask.
        #
        # Deliberately after the sync has been recorded as complete, and inside
        # its own guard: a scan that fails must not undo a sync that worked, nor
        # make the job retry and import everything again.
        try:
            from . import commerce_service
            scanned = commerce_service.scan_store(db, store)
            result = {**result, "proposals_opened": scanned["opened"]}
        except Exception:  # noqa: BLE001 - a sync must survive its own follow-up
            db.rollback()
            log.exception("Sync finished but the follow-up scan failed for store %s",
                          store.id)
        return result
    except shopify.ShopifyAuthorizationError as exc:
        # Not a transient failure: retrying cannot make a revoked token valid.
        db.rollback()
        try:
            store = crud.get_or_create_dev_store(db)
            channel = db.get(models.ChannelConnection, uuid.UUID(channel_id))
            if channel is not None:
                _mark_channel_revoked(db, store, channel, str(exc), actor_id)
        except Exception:                       # pragma: no cover - best effort
            db.rollback()
        if record is not None:
            crud.complete_idempotency(db, record, {"status": "revoked", "reason": str(exc)[:300]})
        return {"status": "revoked", "orders": 0, "reason": str(exc)[:300]}
    except Exception:
        db.rollback()
        if record is not None:
            crud.fail_idempotency(db, record)
        raise
    finally:
        if client is not None: client.close()
        db.close(); reset_principal(principal_token)


def run_woocommerce_sync(*, tenant_id: str, actor_id: str, channel_id: str, days: int,
                         idempotency_record_id: str) -> dict:
    principal_token = bind_principal(
        Principal(user_id=actor_id, tenant_id=tenant_id, role="operator")
    )
    db = SessionLocal(); record = None; client = None
    try:
        if not 1 <= days <= 90:
            raise ValueError("WooCommerce sync range must be between 1 and 90 days.")
        store = crud.get_or_create_dev_store(db)
        record = db.get(models.IdempotencyRecord, uuid.UUID(idempotency_record_id))
        channel = db.get(models.ChannelConnection, uuid.UUID(channel_id))
        if record is None or record.store_id != store.id or channel is None or \
                channel.store_id != store.id or channel.provider != "woocommerce":
            raise RuntimeError("WooCommerce sync context not found for tenant.")
        if record.status == "completed" and record.response:
            return record.response
        raw = credentials.load_credential(
            db, store_id=store.id,
            provider=woocommerce.credential_provider(channel.external_account_id),
        )
        parsed = __import__("json").loads(raw or "{}")
        if not parsed.get("consumer_key") or not parsed.get("consumer_secret"):
            raise woocommerce.WooCommerceAuthorizationError("WooCommerce credential is missing.")
        client = woocommerce.ReadOnlyClient(
            channel.external_account_id, parsed["consumer_key"], parsed["consumer_secret"]
        )
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        totals: dict[tuple[dt.date, str], dict[str, float | int]] = {}
        accepted_statuses = {"processing", "completed", "on-hold"}
        for orders in client.order_pages(after=cutoff):
            for order in orders:
                if order.get("status") not in accepted_statuses:
                    continue
                created = dt.datetime.fromisoformat(
                    str(order["date_created_gmt"]).replace("Z", "+00:00")
                ).date()
                currency = str(order.get("currency") or channel.currency)
                total = totals.setdefault((created, currency), {
                    "revenue": 0.0, "refunds": 0.0, "orders": 0, "units": 0,
                })
                total["revenue"] = float(total["revenue"]) + float(order.get("total") or 0)
                total["refunds"] = float(total["refunds"]) + sum(
                    abs(float(item.get("total") or item.get("refund") or 0))
                    for item in order.get("refunds") or []
                )
                total["orders"] = int(total["orders"]) + 1
                total["units"] = int(total["units"]) + sum(
                    int(item.get("quantity") or 0) for item in order.get("line_items") or []
                )
        now = dt.datetime.now(dt.timezone.utc)
        for (metric_date, currency), total in totals.items():
            row = db.scalar(select(models.CommerceDailyMetric).where(
                models.CommerceDailyMetric.store_id == store.id,
                models.CommerceDailyMetric.channel_id == channel.id,
                models.CommerceDailyMetric.metric_date == metric_date,
            )) or models.CommerceDailyMetric(
                store_id=store.id, channel_id=channel.id, metric_date=metric_date,
                fees=0, landed_cogs=0, advertising_spend=0, sessions=0,
            )
            row.revenue = total["revenue"]; row.refunds = total["refunds"]
            row.orders = total["orders"]; row.units = total["units"]
            row.currency = currency; row.source = "woocommerce_rest"
            row.costs_complete = False; row.synced_at = now; db.add(row)
        channel.synced_at = now; db.add(channel)
        result = {"days": days, "daily_rows": len(totals),
                  "orders": sum(int(value["orders"]) for value in totals.values())}
        crud.append_audit_event(
            db, store_id=store.id, actor_id=uuid.UUID(actor_id),
            action="woocommerce.sync.completed", resource_type="channel_connection",
            resource_id=str(channel.id), idempotency_key=record.key, after=result, commit=False,
        )
        crud.complete_idempotency(db, record, result)
        return result
    except Exception:
        db.rollback()
        if record is not None: crud.fail_idempotency(db, record)
        raise
    finally:
        if client is not None: client.close()
        db.close(); reset_principal(principal_token)


def run_amazon_sales_report(*, tenant_id: str, actor_id: str, region: str,
                            marketplace_id: str, days: int,
                            idempotency_record_id: str) -> dict:
    principal_token = bind_principal(
        Principal(user_id=actor_id, tenant_id=tenant_id, role="operator")
    )
    db = SessionLocal()
    record = run = None
    try:
        if not 7 <= days <= 30:
            raise ValueError("Report range must be between 7 and 30 days.")
        store = crud.get_or_create_dev_store(db)
        record_id = uuid.UUID(idempotency_record_id)
        record = db.get(models.IdempotencyRecord, record_id)
        if record is None or record.store_id != store.id:
            raise RuntimeError("Idempotency record not found for tenant.")
        if record.status == "completed" and record.response:
            return record.response

        today = dt.datetime.now(dt.timezone.utc).date()
        date_start, date_end = today - dt.timedelta(days=days), today - dt.timedelta(days=1)
        run = db.get(models.AmazonReportRun, record_id)
        if run is None:
            run = models.AmazonReportRun(
                id=record_id, store_id=store.id, marketplace_id=marketplace_id,
                report_type="GET_SALES_AND_TRAFFIC_REPORT", status="creating",
                date_start=date_start, date_end=date_end,
            )
        run.error_code = None
        db.add(run)
        db.commit()

        client = amazon_sp_api.client_for_store(db, store_id=store.id, region=region)
        if not run.amazon_report_id:
            start = dt.datetime.combine(date_start, dt.time.min, tzinfo=dt.timezone.utc)
            end = dt.datetime.combine(date_end + dt.timedelta(days=1), dt.time.min,
                                      tzinfo=dt.timezone.utc)
            created = client.create_sales_traffic_report(
                marketplace_id=marketplace_id, start=start, end=end
            )
            report_id = created.get("reportId")
            if not report_id:
                raise amazon_sp_api.AmazonAPIError("Amazon did not return a report ID.")
            run.amazon_report_id = str(report_id)
            run.status = "processing"
            db.add(run)
            db.commit()  # persist external ID before polling so an RQ retry reuses it

        report = None
        for attempt in range(40):
            report = client.get_report(run.amazon_report_id)
            status = report.get("processingStatus")
            run.status = str(status or "processing").lower()
            db.add(run)
            db.commit()
            if status in {"DONE", "CANCELLED", "FATAL"}:
                break
            if attempt < 39:
                time.sleep(15)
        if not report or report.get("processingStatus") != "DONE":
            status = report.get("processingStatus") if report else "TIMEOUT"
            raise amazon_sp_api.AmazonAPIError(f"Amazon report did not complete ({status}).")
        document_id = report.get("reportDocumentId")
        if not document_id:
            raise amazon_sp_api.AmazonAPIError("Amazon did not return a report document ID.")
        run.report_document_id = str(document_id)
        metadata = client.get_report_document(run.report_document_id)
        payload = client.download_report_document(metadata)

        now = dt.datetime.now(dt.timezone.utc)
        rows_count = 0
        for item in payload.get("salesAndTrafficByDate", []):
            try:
                sales_date = dt.date.fromisoformat(item["date"])
            except (KeyError, TypeError, ValueError):
                continue
            if not date_start <= sales_date <= date_end:
                continue
            sales = item.get("salesByDate") or {}
            traffic = item.get("trafficByDate") or {}
            money = sales.get("orderedProductSales") or {}
            row = db.scalar(select(models.AmazonSalesDaily).where(
                models.AmazonSalesDaily.store_id == store.id,
                models.AmazonSalesDaily.marketplace_id == marketplace_id,
                models.AmazonSalesDaily.sales_date == sales_date,
            )) or models.AmazonSalesDaily(
                store_id=store.id, marketplace_id=marketplace_id, sales_date=sales_date
            )
            row.ordered_sales = Decimal(str(money.get("amount") or 0)).quantize(Decimal("0.01"))
            row.currency = (money.get("currencyCode") or None)
            row.units_ordered = int(sales.get("unitsOrdered") or 0)
            row.order_items = int(sales.get("totalOrderItems") or 0)
            row.page_views = int(traffic.get("pageViews") or 0)
            row.sessions = int(traffic.get("sessions") or 0)
            buy_box = traffic.get("buyBoxPercentage")
            row.buy_box_percentage = float(buy_box) if buy_box is not None else None
            row.synced_at = now
            db.add(row)
            rows_count += 1

        sku_rows_count = 0
        for item in payload.get("salesAndTrafficByAsin", []):
            sku = item.get("sku")
            if not sku:
                continue
            sales = item.get("salesByAsin") or {}
            money = sales.get("orderedProductSales") or {}
            row = db.scalar(select(models.AmazonSalesSkuPeriod).where(
                models.AmazonSalesSkuPeriod.report_run_id == run.id,
                models.AmazonSalesSkuPeriod.sku == sku,
            )) or models.AmazonSalesSkuPeriod(
                report_run_id=run.id, store_id=store.id,
                marketplace_id=marketplace_id, sku=sku,
            )
            row.units_ordered = int(sales.get("unitsOrdered") or 0)
            row.ordered_sales = Decimal(str(money.get("amount") or 0)).quantize(
                Decimal("0.01")
            )
            row.currency = money.get("currencyCode") or None
            db.add(row)
            sku_rows_count += 1

        run.status = "completed"
        run.rows_count = rows_count
        run.completed_at = now
        result = {"marketplace_id": marketplace_id, "days": days, "rows": rows_count,
                  "sku_rows": sku_rows_count,
                  "date_start": date_start.isoformat(), "date_end": date_end.isoformat()}
        db.add(run)
        crud.append_audit_event(
            db, store_id=store.id, actor_id=uuid.UUID(actor_id),
            action="amazon.sales_report.completed", resource_type="amazon_report",
            resource_id=str(run.id), idempotency_key=record.key, after=result, commit=False,
        )
        crud.complete_idempotency(db, record, result)
        return result
    except Exception as exc:
        db.rollback()
        if run is not None:
            run.status = "failed"
            run.error_code = type(exc).__name__[:64]
            run.completed_at = dt.datetime.now(dt.timezone.utc)
            db.add(run)
            db.commit()
        if record is not None:
            crud.fail_idempotency(db, record)
        raise
    finally:
        db.close()
        reset_principal(principal_token)


def _expense_breakdowns(nodes: list[dict] | None):
    for node in nodes or []:
        if node.get("breakdownType") == "Expenses":
            amount = node.get("breakdownAmount") or {}
            yield amount
            continue
        yield from _expense_breakdowns(node.get("breakdowns"))


def run_amazon_finances_sync(*, tenant_id: str, actor_id: str, region: str,
                             marketplace_id: str, days: int,
                             idempotency_record_id: str) -> dict:
    principal_token = bind_principal(
        Principal(user_id=actor_id, tenant_id=tenant_id, role="operator")
    )
    db = SessionLocal()
    record = None
    try:
        if not 7 <= days <= 90:
            raise ValueError("Finance range must be between 7 and 90 days.")
        store = crud.get_or_create_dev_store(db)
        record = db.get(models.IdempotencyRecord, uuid.UUID(idempotency_record_id))
        if record is None or record.store_id != store.id:
            raise RuntimeError("Idempotency record not found for tenant.")
        if record.status == "completed" and record.response:
            return record.response
        client = amazon_sp_api.client_for_store(db, store_id=store.id, region=region)
        before = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=3)
        after = before - dt.timedelta(days=days)
        totals: dict[tuple[dt.date, str | None], tuple[Decimal, int]] = {}
        for page in client.transaction_pages(
                marketplace_id=marketplace_id, posted_after=after, posted_before=before):
            for transaction in page.get("transactions", []):
                raw_date = transaction.get("postedDate")
                try:
                    posted_date = dt.datetime.fromisoformat(
                        str(raw_date).replace("Z", "+00:00")
                    ).date()
                except (TypeError, ValueError):
                    continue
                nodes = list(transaction.get("breakdowns") or [])
                for item in transaction.get("items") or []:
                    nodes.extend(item.get("breakdowns") or [])
                for money in _expense_breakdowns(nodes):
                    currency = money.get("currencyCode")
                    raw_amount = Decimal(str(money.get("currencyAmount") or 0))
                    # Amazon expenses are normally negative; positive reversals reduce cost.
                    cost = -raw_amount
                    key = (posted_date, currency)
                    old_amount, old_count = totals.get(key, (Decimal("0"), 0))
                    totals[key] = (old_amount + cost, old_count + 1)
        now = dt.datetime.now(dt.timezone.utc)
        for (cost_date, currency), (amount, count) in totals.items():
            row = db.scalar(select(models.AmazonCostDaily).where(
                models.AmazonCostDaily.store_id == store.id,
                models.AmazonCostDaily.marketplace_id == marketplace_id,
                models.AmazonCostDaily.cost_date == cost_date,
                models.AmazonCostDaily.category == "amazon_fees",
            )) or models.AmazonCostDaily(
                store_id=store.id, marketplace_id=marketplace_id,
                cost_date=cost_date, category="amazon_fees",
            )
            row.amount = amount.quantize(Decimal("0.01"))
            row.currency = currency
            row.source = "sp_api_finances"
            row.source_count = count
            row.synced_at = now
            db.add(row)
        result = {"marketplace_id": marketplace_id, "days": days,
                  "daily_rows": len(totals),
                  "amazon_fees": float(sum((value[0] for value in totals.values()), Decimal("0")))}
        crud.append_audit_event(
            db, store_id=store.id, actor_id=uuid.UUID(actor_id),
            action="amazon.finances_sync.completed", resource_type="amazon_cost_ledger",
            idempotency_key=record.key, after=result, commit=False,
        )
        crud.complete_idempotency(db, record, result)
        return result
    except Exception:
        db.rollback()
        if record is not None:
            crud.fail_idempotency(db, record)
        raise
    finally:
        db.close()
        reset_principal(principal_token)


def _ppc_action_rows(db, store, proposals: list, policy, applied_today: int,
                     currency: str, client=None) -> dict:
    """Open one action per proposal, applying only what the guardrails allow alone.

    The Operator never talks itself past a limit: each proposal is judged
    separately, and anything blocked or escalated is left `proposed` for a human.
    """
    from . import actions_engine, guardrails

    opened = auto_applied = needs_approval = unproven = 0
    for proposal in proposals:
        row = models.OperatorAction(
            store_id=store.id, module=proposal.module, action_type=proposal.action_type,
            target=proposal.target, status=actions_engine.ActionStatus.PROPOSED.value,
            # The REPORT is real; the CHANGE is not, and will not be until it has
            # been read back out of the account. Provenance of the input is not
            # provenance of the outcome, and only the outcome may be counted.
            evidence_mode="unverified", source_type="amazon_ads_report",
            source_id=proposal.target,
            projected_impact=proposal.projected_impact, baseline=proposal.baseline,
            revert_to=proposal.revert_to, note=proposal.note, currency=currency,
        )
        db.add(row)
        db.flush()
        opened += 1

        window = proposal.baseline
        decision = guardrails.evaluate(
            guardrails.Request(
                actor="operator",
                money_at_stake=abs(proposal.projected_impact),
                target_spend_per_day=window["spend"] / max(window["days"], 1),
                applied_today=applied_today + auto_applied,
            ),
            policy,
        )
        if not decision.allowed:
            needs_approval += 1
            crud.append_audit_event(
                db, store_id=store.id, actor_id=uuid.UUID(str(store.user_id)),
                action="guardrails.refused", resource_type="operator_action",
                resource_id=str(row.id), after=decision.to_dict(), commit=False,
            )
            continue

        # Allowed unattended. Make the change and prove it landed; a request that
        # was accepted is not evidence that the account moved.
        revert = proposal.revert_to or {}
        if client is None:
            # No write credential: stage it for the seller rather than pretend.
            unproven += 1
            row.note = ((row.note or "") +
                        " | Staged: no advertising write access, so nothing was changed.").strip()
            continue

        outcome = client.apply_negative_keyword(
            campaign_id=revert.get("campaign_id", ""),
            ad_group_id=revert.get("ad_group_id", ""),
            keyword_text=proposal.target,
        )
        row.evidence_mode = outcome.evidence_mode
        row.source_id = outcome.external_id or row.source_id
        row.outcome = {"verification": outcome.to_dict()}

        if not outcome.countable:
            # Sent but unproven, or already in place. Either way it is not ours to
            # count, and the reason travels with the row.
            unproven += 1
            row.note = ((row.note or "") + " | " + outcome.reason).strip(" |")
            crud.append_audit_event(
                db, store_id=store.id, actor_id=uuid.UUID(str(store.user_id)),
                action="action.unproven", resource_type="operator_action",
                resource_id=str(row.id), after=outcome.to_dict(), commit=False,
            )
            continue

        row.status = actions_engine.ActionStatus.APPLIED.value
        row.applied_by = "operator"
        row.applied_at = dt.datetime.now(dt.timezone.utc)
        auto_applied += 1
        crud.append_audit_event(
            db, store_id=store.id, actor_id=uuid.UUID(str(store.user_id)),
            action="action.applied", resource_type="operator_action",
            resource_id=str(row.id),
            after={"by": "operator", "baseline": row.baseline,
                   "verification": outcome.to_dict()},
            commit=False,
        )
    return {"opened": opened, "auto_applied": auto_applied,
            "needs_approval": needs_approval, "unproven": unproven}


def run_ads_scan(*, tenant_id: str, actor_id: str, region: str, days: int,
                 profile_id: str | None, idempotency_record_id: str) -> dict:
    """Read yesterday's search terms, price the waste, and open actions for it.

    This is the loop that makes the Operator an operator: nobody presses a button
    per keyword. It reads, the deterministic engine judges, the guardrails decide
    what may happen unattended, and the ledger records all of it.
    """
    from . import actions_engine, amazon_ads_api, guardrails, ppc_engine

    principal_token = bind_principal(
        Principal(user_id=actor_id, tenant_id=tenant_id, role="operator")
    )
    db = SessionLocal()
    record = None
    try:
        store = crud.get_or_create_dev_store(db)
        record = db.get(models.IdempotencyRecord, uuid.UUID(idempotency_record_id))
        if record is None or record.store_id != store.id:
            raise RuntimeError("Idempotency record not found for tenant.")
        if record.status == "completed" and record.response:
            return record.response

        client = amazon_ads_api.client_for_store(
            db, store_id=store.id, region=region, profile_id=profile_id
        )
        # Writing to the account is opt-in and separate from reading it.
        write_enabled = os.getenv("ADS_WRITE_ENABLED", "false").lower() == "true"
        rows = client.search_terms(days=days)
        if not rows:
            result = {"terms": 0, "opened": 0, "auto_applied": 0,
                      "needs_approval": 0, "unproven": 0}
            crud.complete_idempotency(db, record, result)
            return result

        summary, findings = ppc_engine.analyze(ppc_engine.CampaignInput(
            name=f"Sponsored Products ({days}d)",
            break_even_acos=float(os.getenv("PPC_BREAK_EVEN_ACOS", "0.30")),
            keywords=[ppc_engine.KeywordInput(**row.to_keyword_input()) for row in rows],
        ))
        proposals = actions_engine.proposals_from_ppc(
            [f.to_dict() for f in findings],
            [{"search_term": r.search_term, "campaign_id": r.campaign_id,
              "ad_group_id": r.ad_group_id} for r in rows],
            window_days=days,
        )

        policy_row = db.scalar(select(models.GuardrailPolicy).where(
            models.GuardrailPolicy.store_id == store.id))
        policy = guardrails.Policy.from_dict(
            None if policy_row is None else {
                "enabled": policy_row.enabled,
                "max_actions_per_day": policy_row.max_actions_per_day,
                "max_change_pct": policy_row.max_change_pct,
                "auto_apply_below": policy_row.auto_apply_below,
                "protected_spend_per_day": policy_row.protected_spend_per_day,
            }
        )
        midnight = dt.datetime.now(dt.timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0)
        applied_today = db.scalar(select(func.count()).select_from(models.OperatorAction).where(
            models.OperatorAction.store_id == store.id,
            models.OperatorAction.applied_by == "operator",
            models.OperatorAction.applied_at >= midnight,
        )) or 0

        counts = _ppc_action_rows(db, store, proposals, policy, applied_today,
                                  rows[0].__dict__.get("currency", "USD") or "USD",
                                  client=client if write_enabled else None)
        result = {"terms": len(rows), "wasted_spend": summary.wasted_spend, **counts}
        crud.append_audit_event(
            db, store_id=store.id, actor_id=uuid.UUID(actor_id), action="ads.scan",
            resource_type="ppc", idempotency_key=record.key, after=result, commit=False,
        )
        crud.complete_idempotency(db, record, result)
        return result
    except Exception:
        db.rollback()
        if record is not None:
            crud.fail_idempotency(db, record)
        raise
    finally:
        db.close()
        reset_principal(principal_token)


def send_subscription_email(*, receipt_id: str, tenant_id: str, kind: str,
                            context: dict) -> dict:
    """Deliver one reserved subscription email.

    Runs in the worker rather than in the Stripe webhook: Stripe wants an answer
    within seconds and an SMTP round trip is not something to make it wait for.
    The receipt was already reserved by whoever enqueued this, so a redelivered
    event never reaches here twice.
    """
    from . import notifications

    principal_token = bind_principal(
        Principal(user_id=tenant_id, tenant_id=tenant_id, role="operator")
    )
    db = SessionLocal()
    try:
        store = db.get(models.Store, uuid.UUID(tenant_id))
        if store is None:
            return {"outcome": "no_store"}
        outcome = notifications.deliver(
            db, receipt_id=uuid.UUID(receipt_id), store=store, kind=kind,
            context=context)
        # A transient failure is raised so RQ retries it on its own schedule;
        # anything final is returned, because retrying it would only spend the
        # queue on an answer that will not change.
        if outcome == "retry":
            raise RuntimeError(f"{kind} delivery failed; will retry")
        return {"outcome": outcome}
    finally:
        db.close()
        reset_principal(principal_token)


def warn_about_ending_trials(*, days: int | None = None, limit: int = 200,
                            dry_run: bool = False) -> dict:
    """Find trials about to end and queue one warning each.

    Runs daily from the scheduler, so it must be safe to run twice: the receipt
    is keyed on the trial's end date, which does not move, so a second run the
    same day finds the row and queues nothing.

    Deliberately does not change any entitlement. Warning somebody and expiring
    them are different jobs, and mixing them would mean a scheduler outage
    silently extended everybody's trial.
    """
    from . import notifications
    from .db.session import declare_tenant
    from .queueing import enqueue_notification

    window = notifications.TRIAL_WARNING_DAYS if days is None else days
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now + dt.timedelta(days=window)

    db = SessionLocal()
    queued = skipped = 0
    try:
        rows = list(db.scalars(select(models.Subscription).where(
            models.Subscription.status == "trialing",
            models.Subscription.trial_ends_at <= cutoff,
            models.Subscription.trial_ends_at > now,
        ).limit(limit)))
        for subscription in rows:
            # One tenant at a time, declared before anything of theirs is
            # touched: a batch job is exactly where a missing declaration would
            # otherwise read across everybody.
            token = bind_principal(Principal(
                user_id=str(subscription.store_id),
                tenant_id=str(subscription.store_id), role="operator"))
            try:
                declare_tenant(db, subscription.store_id)
                ends = subscription.trial_ends_at
                if ends.tzinfo is None:
                    ends = ends.replace(tzinfo=dt.timezone.utc)
                receipt = notifications.reserve(
                    db, store_id=subscription.store_id,
                    kind=notifications.TRIAL_ENDING,
                    dedupe_key=ends.date().isoformat())
                if receipt is None:
                    skipped += 1
                    continue
                if dry_run:
                    # Claimed nothing and queued nothing: roll the reservation
                    # back so a dry run leaves the next real one free to send.
                    db.rollback()
                    queued += 1
                    continue
                db.commit()
                enqueue_notification(
                    receipt_id=str(receipt.id), tenant_id=str(subscription.store_id),
                    kind=notifications.TRIAL_ENDING,
                    context={"ends_on": ends.date().isoformat()})
                queued += 1
            except Exception:  # noqa: BLE001 - one tenant must not stop the rest
                db.rollback()
                log.exception("Could not queue a trial warning for store %s",
                              subscription.store_id)
            finally:
                reset_principal(token)
        return {"queued": queued, "already_warned": skipped, "considered": len(rows)}
    finally:
        db.close()


# --- what the daily run does --------------------------------------------------

#: How far back a routine sync reads. A day would be enough if nothing ever
#: went wrong; a week absorbs a missed run, a redeploy and a webhook that never
#: arrived, and re-reading a day that is already correct costs one upsert.
DAILY_SYNC_DAYS = int(os.getenv("DAILY_SYNC_DAYS", "7"))

#: Shopify sync range ceiling, matching the guard inside run_shopify_sync. A gap
#: wider than this cannot be closed in one run, and pretending otherwise would
#: certify a window that was never read.
MAX_SYNC_DAYS = 60


def _sync_days_for(db, *, store_id, channel, now: dt.datetime) -> int:
    """How far back this store needs reading today.

    Two claims on the range. The routine one is DAILY_SYNC_DAYS. The other comes
    from measurement: an action whose window is still open needs every day of
    that window read before it can be priced, so the sync reaches back to the
    oldest open window rather than leaving a result stranded for want of days
    nobody fetched.
    """
    from . import commerce_measurement

    today = now.date()
    days = DAILY_SYNC_DAYS
    settings = channel.settings or {}
    through = settings.get("synced_through")
    if through:
        try:
            gap = (today - dt.date.fromisoformat(str(through))).days
            days = max(days, gap + 2)   # +2: the boundary day and today
        except ValueError:
            pass

    rows = db.scalars(select(models.OperatorAction).where(
        models.OperatorAction.store_id == store_id,
        models.OperatorAction.status == "applied",
        models.OperatorAction.measured_at.is_(None),
        models.OperatorAction.applied_at.is_not(None),
    ).limit(200))
    for row in rows:
        window = commerce_measurement.window_for(
            row.applied_at, measurement_days=row.measurement_days, now=now)
        days = max(days, (today - window.first).days + 1)
    return max(1, min(days, MAX_SYNC_DAYS))


def _sync_already_running(db, *, store_id) -> bool:
    """Is another sync for this store in flight?

    Two syncs of the same shop at once are not a correctness problem — every
    write is an upsert — but they are two full reads of somebody's store for one
    set of numbers, and the second one teaches the first's rate limit a lesson.
    """
    rows = db.scalars(select(models.IdempotencyRecord).where(
        models.IdempotencyRecord.store_id == store_id,
        models.IdempotencyRecord.operation.like("shopify.sync%"),
        models.IdempotencyRecord.status == "processing",
    ).limit(20))
    return any(crud.claim_is_in_flight(row) for row in rows)


def sync_connected_shopify_stores(*, limit: int = 200, dry_run: bool = False,
                                  now: dt.datetime | None = None) -> dict:
    """Queue one read-only Shopify sync per connected store.

    Queued rather than performed: this runs in a cron container that should
    finish in seconds, and the worker already owns syncing, its retries and its
    revoked-token handling. Adding a second implementation here is how two
    versions of the same numbers get written.

    One tenant at a time, declared before anything of theirs is read. A batch
    job is exactly where a missing declaration reads across everybody.
    """
    from .db.session import declare_tenant
    from .queueing import enqueue_shopify_sync

    # The clock is an argument so the tests can stand at a chosen moment. A test
    # that has to wait fourteen days is a test nobody runs.
    now = now or dt.datetime.now(dt.timezone.utc)
    db = SessionLocal()
    queued = skipped = failed = 0
    reasons: dict[str, int] = {}

    def note(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    try:
        channels = list(db.scalars(select(models.ChannelConnection).where(
            models.ChannelConnection.provider == "shopify",
            models.ChannelConnection.status == "connected",
        ).limit(limit)))
        for channel in channels:
            token_binding = bind_principal(Principal(
                user_id=str(channel.store_id), tenant_id=str(channel.store_id),
                role="operator"))
            try:
                declare_tenant(db, channel.store_id)
                store = db.get(models.Store, channel.store_id)
                if store is None:
                    skipped += 1; note("store missing"); continue
                # A credential that cannot be decrypted is a rotated keyring, not
                # a transient fault. Queueing the job would burn a worker slot to
                # reach the same conclusion with less context.
                token = credentials.load_credential(
                    db, store_id=channel.store_id,
                    provider=shopify.credential_provider(channel.external_account_id))
                if not token:
                    skipped += 1; note("no readable credential"); continue
                if _sync_already_running(db, store_id=channel.store_id):
                    skipped += 1; note("a sync is already running"); continue

                days = _sync_days_for(db, store_id=channel.store_id, channel=channel,
                                      now=now)
                if dry_run:
                    queued += 1; continue
                record, is_new = crud.claim_idempotency(
                    db, store_id=channel.store_id,
                    operation=f"shopify.sync:{channel.id}:{days}",
                    key=f"scheduled:{scheduler_run_key(now)}:{channel.id}")
                if not is_new:
                    skipped += 1; note("already queued today"); continue
                enqueue_shopify_sync(
                    job_id=str(record.id), tenant_id=str(channel.store_id),
                    actor_id=str(store.user_id), channel_id=str(channel.id),
                    days=days)
                queued += 1
            except Exception:  # noqa: BLE001 - one shop must not stop the rest
                db.rollback()
                failed += 1
                note("failed to queue")
                log.exception("Could not queue the daily Shopify sync for store %s",
                              channel.store_id)
            finally:
                reset_principal(token_binding)
        return {"considered": len(channels), "queued": queued, "skipped": skipped,
                "failed": failed, "reasons": reasons}
    finally:
        db.close()


def scheduler_run_key(now: dt.datetime | None = None) -> str:
    from . import scheduler

    return scheduler.run_key_for(now)


def measure_due_actions(*, limit: int = 200, dry_run: bool = False,
                        now: dt.datetime | None = None) -> dict:
    """Price every applied action whose window has closed.

    Finding them is the part that did not exist: an action could sit `applied`
    for ever, because the only thing that ever measured one was a person
    pressing a button on a screen they had no reason to revisit.

    Nothing here decides anything about money. The window, the arithmetic and
    the evidence rule all live in `commerce_measurement`, which the route calls
    too — this walks the tenants and hands each row over.
    """
    from . import commerce_measurement
    from .db.session import declare_tenant

    now = now or dt.datetime.now(dt.timezone.utc)
    db = SessionLocal()
    measured = waiting = failed = 0
    reasons: dict[str, int] = {}
    considered = 0
    try:
        store_ids = list(db.scalars(select(models.OperatorAction.store_id).where(
            models.OperatorAction.status == "applied",
            models.OperatorAction.measured_at.is_(None),
            models.OperatorAction.applied_at.is_not(None),
        ).distinct().limit(limit)))
        for store_id in store_ids:
            token_binding = bind_principal(Principal(
                user_id=str(store_id), tenant_id=str(store_id), role="operator"))
            try:
                declare_tenant(db, store_id)
                store = db.get(models.Store, store_id)
                actor = store.user_id if store is not None else store_id
                due = commerce_measurement.due_actions(db, store_id=store_id, now=now)
                considered += len(due)
                for row in due:
                    if dry_run:
                        state = commerce_measurement.readiness(db, row, now=now)
                        if state.state == commerce_measurement.READY:
                            measured += 1
                        else:
                            waiting += 1
                            reasons[state.state] = reasons.get(state.state, 0) + 1
                        continue
                    result = commerce_measurement.measure(db, row, now=now, commit=False)
                    if not result.measured:
                        db.rollback()
                        waiting += 1
                        reasons[result.state] = reasons.get(result.state, 0) + 1
                        continue
                    crud.append_audit_event(
                        db, store_id=store_id, actor_id=actor,
                        action="commerce.measured", resource_type="operator_action",
                        resource_id=str(row.id),
                        after={"impact": row.impact,
                               "evidence_mode": row.evidence_mode},
                        commit=False)
                    db.commit()
                    measured += 1
            except Exception:  # noqa: BLE001 - one tenant must not stop the rest
                db.rollback()
                failed += 1
                log.exception("Could not measure due actions for store %s", store_id)
            finally:
                reset_principal(token_binding)
        return {"considered": considered, "measured": measured, "waiting": waiting,
                "failed": failed, "reasons": reasons}
    finally:
        db.close()
