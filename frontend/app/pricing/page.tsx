"use client";

import { useEffect, useState } from "react";
import { getEntitlement, getShopifyPilot, type Entitlement, type ShopifyPilot } from "../../lib/api";
import { Icon, IconPlate } from "../icons";

/* ---------------------------------------------------------------------------
   One plan, and what it actually does.

   There is no comparison table, because there is one plan: a table of tiers
   nobody can buy is a table of fiction, and it exists to make the middle column
   look reasonable rather than to inform anybody.

   Nothing here promises a return. The Operator finds things and proposes them;
   whether a proposal earns anything is answered by measuring it afterwards,
   from the seller's own numbers, and until one has been measured this product
   has proved nothing about money. Saying otherwise on a pricing page is the
   easiest lie in software and the one customers remember.
   --------------------------------------------------------------------------- */

const FALLBACK_PRICE = 49;
const FALLBACK_CURRENCY = "USD";

function money(amount: number, currency: string): string {
  try {
    return amount.toLocaleString(undefined,
      { style: "currency", currency, maximumFractionDigits: 0 });
  } catch {
    return `${amount} ${currency}`;
  }
}

export default function PricingPage() {
  // Read if we can — the price is configuration, not a constant — but this page
  // is public and must render for somebody with no account at all.
  const [plan, setPlan] = useState<Entitlement | null>(null);
  const [pilot, setPilot] = useState<ShopifyPilot | null>(null);
  useEffect(() => {
    getEntitlement().then(setPlan).catch(() => { /* signed out: use the defaults */ });
    getShopifyPilot().then(setPilot).catch(() => { /* render a stable fallback */ });
  }, []);

  const price = plan?.price_per_month ?? FALLBACK_PRICE;
  const currency = plan?.currency ?? FALLBACK_CURRENCY;
  const features = plan?.features ?? [
    "Connects to your Shopify store, read-only",
    "Imports your orders, products and stock every day",
    "Reads them and proposes changes worth making",
    "Never changes anything in your shop without you approving it",
    "Measures what a change was worth, from your own numbers",
    "Withholds profit figures until your costs are complete",
  ];

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex items-center gap-3">
          <IconPlate name="tag" />
          <p className="lbl">Pricing</p>
        </div>
        <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
          One plan
        </h1>
        <p style={{ margin: "16px 0 0", maxWidth: "68ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          An operator that reads your shop and tells you what is worth changing.
          It proposes; you decide. There is one plan because there is one
          product, and a free trial because you should see what it finds in your
          own data before paying for it.
        </p>
      </section>

      <div className="card-proven mb-6" style={{ padding: "20px 22px", borderWidth: "1px", borderStyle: "solid", maxWidth: "56ch" }}>
        <p className="lbl" style={{ color: "var(--proven)" }}>Feedback pilot</p>
        <p style={{ margin: "9px 0 0", fontSize: "16px", fontWeight: 600 }}>
          First {pilot?.maximum_stores ?? 10} Shopify stores: {pilot?.trial_days ?? 15} days free
        </p>
        <p style={{ margin: "8px 0 0", fontSize: "13px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          {pilot && !pilot.available
            ? "This cohort is full. Join the next one instead of starting Shopify approval for nothing."
            : "No card. Connect your store, use the Operator with your own data, and tell us what is useful or missing. Places are confirmed only after Shopify is connected."}
        </p>
      </div>

      <div className="card" style={{ padding: "28px 30px", maxWidth: "56ch" }}>
        <p className="lbl">{plan?.plan_name ?? "Operator"}</p>
        <p style={{ margin: "12px 0 0", fontSize: "34px", fontWeight: 600 }}>
          {money(price, currency)}
          <span style={{ fontSize: "15px", fontWeight: 400, color: "var(--ink-4)" }}>
            {" "}per month
          </span>
        </p>
        <p style={{ margin: "10px 0 0", fontSize: "13px", color: "var(--ink-4)" }}>
          {pilot?.trial_days ?? 15}-day feedback pilot first. No card until you choose to subscribe.
        </p>

        <ul className="mt-6 space-y-2" style={{ listStyle: "none", margin: "24px 0 0", padding: 0 }}>
          {features.map((feature) => (
            <li key={feature} className="flex items-start gap-3"
                style={{ fontSize: "13.5px", lineHeight: 1.6, color: "var(--ink-2)" }}>
              <Icon name="check" size={14} stroke="var(--proven)" className="mt-1 shrink-0" />
              {feature}
            </li>
          ))}
        </ul>

        <a href={plan ? "/billing" : "/signin"} className="btn-primary mt-8 inline-flex items-center gap-2"
           style={{ padding: "11px 18px" }}>
          <Icon name="spark" size={14} />
          {plan ? "GO TO BILLING" : "START THE FREE TRIAL"}
        </a>
      </div>

      <section className="mt-10" style={{ maxWidth: "68ch" }}>
        <p className="lbl">What it will not do</p>
        <p style={{ margin: "14px 0 0", fontSize: "13.5px", lineHeight: 1.75, color: "var(--ink-2)" }}>
          It holds <strong>read-only</strong> access to your Shopify store. It can
          see products, orders and stock, and it has no permission to change a
          price, a product or an order — not because it chooses not to, but
          because that permission was never asked for.
        </p>
        <p style={{ margin: "14px 0 0", fontSize: "13.5px", lineHeight: 1.75, color: "var(--ink-2)" }}>
          Everything it finds is a proposal that waits for you. A new store
          applies nothing automatically, and no amount of confidence on its part
          changes that.
        </p>
        <p style={{ margin: "14px 0 0", fontSize: "13.5px", lineHeight: 1.75, color: "var(--ink-2)" }}>
          It does not promise you a return, and you will not find a figure on
          this page claiming one. What a change was worth is measured afterwards,
          from your own numbers, over a fixed window — and a figure it has not
          measured is one it withholds rather than estimates.
        </p>
      </section>

      <p className="mt-10" style={{ fontSize: "12.5px", color: "var(--ink-4)" }}>
        <a href="/privacy" style={{ color: "var(--ink-2)" }}>Privacy</a>
        {" · "}
        <a href="/terms" style={{ color: "var(--ink-2)" }}>Terms</a>
        {plan?.support_email && (
          <>
            {" · "}
            <a href={`mailto:${plan.support_email}`} style={{ color: "var(--ink-2)" }}>
              {plan.support_email}
            </a>
          </>
        )}
      </p>
    </main>
  );
}
