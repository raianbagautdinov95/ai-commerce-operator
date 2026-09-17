"use client";

import { useEffect, useState } from "react";
import {
  createBillingCheckout, createBillingPortal, getAccountStatus, getEntitlement,
  type AccountStatus, type Entitlement,
} from "../../lib/api";
import { Icon, IconPlate, type IconName } from "../icons";

/* Getting from an empty workspace to a decision the store can act on. The
   order is the dependency order, not a wishlist: nothing downstream means
   anything until a channel is connected and has actually been read. */

const steps: readonly (readonly [keyof AccountStatus["onboarding"], string, string, string, IconName])[] = [
  ["account_created", "Create workspace", "Your isolated workspace is ready.", "/dashboard", "check"],
  ["channel_connected", "Connect a channel", "Shopify, Amazon or WooCommerce.", "/integrations", "plug"],
  ["data_imported", "Import performance", "A read-only sync from the channel page.", "/integrations", "revenue"],
  ["first_ai_action", "Make the first decision", "Score a product, or answer a proposal.", "/proposals", "spark"],
] as const;

function money(amount: number, currency: string): string {
  try {
    return amount.toLocaleString(undefined, {
      style: "currency", currency, maximumFractionDigits: 2,
    });
  } catch {
    return `${amount} ${currency}`;
  }
}

export default function SetupPage() {
  const [status, setStatus] = useState<AccountStatus | null>(null);
  const [entitlement, setEntitlement] = useState<Entitlement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [billingBusy, setBillingBusy] = useState(false);

  async function choosePlan() {
    setBillingBusy(true); setError(null);
    try { window.location.assign(await createBillingCheckout("operator")); }
    catch (e) { setError((e as Error).message); setBillingBusy(false); }
  }

  async function manageBilling() {
    setBillingBusy(true); setError(null);
    try { window.location.assign(await createBillingPortal()); }
    catch (e) { setError((e as Error).message); setBillingBusy(false); }
  }

  useEffect(() => {
    getAccountStatus().then(setStatus).catch((e) => setError((e as Error).message));
    getEntitlement().then(setEntitlement).catch((e) => setError((e as Error).message));
  }, []);

  const done = status?.completion_percentage ?? 0;

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div>
            <div className="flex items-center gap-3">
              <IconPlate name="check" />
              <p className="lbl">Quick start</p>
            </div>
            <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
              From an empty workspace to a decision
            </h1>
            <p style={{ margin: "16px 0 0", maxWidth: "66ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
              Four steps, in the order they depend on each other. Nothing after the second one
              means anything until a channel is connected and has actually been read.
            </p>
          </div>
          {status && (
            <div className="card shrink-0 p-5">
              <p className="lbl">Operator trial</p>
              <p className="num" style={{ margin: "12px 0 0", fontSize: "24px", fontWeight: 500, letterSpacing: "-.03em" }}>
                {status.trial_days_remaining} days left
              </p>
              <p style={{ margin: "7px 0 0", fontSize: "11.5px", color: "var(--ink-5)" }}>
                No payment method required
              </p>
            </div>
          )}
        </div>
      </section>

      {error && (
        <p className="card-unproven mb-5 flex items-start gap-3 px-5 py-4"
           style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                    fontSize: "13.5px", color: "var(--unproven)" }}>
          <Icon name="alert" size={17} className="mt-0.5 shrink-0" /> {error}
        </p>
      )}

      <section className="card p-6 sm:p-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="lbl">Onboarding</p>
            <h2 style={{ margin: "8px 0 0", fontSize: "20px", fontWeight: 600, letterSpacing: "-.015em" }}>
              Workspace readiness
            </h2>
          </div>
          <span className="num" style={{ fontSize: "26px", fontWeight: 500, letterSpacing: "-.03em",
               color: done === 100 ? "var(--proven)" : "var(--ink)" }}>
            {done}%
          </span>
        </div>

        <div className="mt-5 overflow-hidden" style={{ height: "6px", borderRadius: "999px", background: "var(--raised)" }}>
          <div style={{ height: "100%", width: `${done}%`, borderRadius: "999px", background: "var(--proven)" }} />
        </div>

        <div className="mt-7 grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
          {steps.map(([key, title, text, href, icon], index) => {
            const complete = status?.onboarding[key] ?? false;
            return (
              <a key={key} href={href}
                 className={`p-5 transition-colors ${complete ? "card-proven" : "card"}`}
                 style={complete
                   ? { borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }
                   : undefined}>
                <div className="flex items-start gap-4">
                  <IconPlate name={complete ? "check" : icon} tone={complete ? "proven" : "neutral"} size={34} />
                  <div>
                    <p className="num" style={{ margin: 0, fontSize: "10.5px", letterSpacing: ".14em",
                         color: complete ? "var(--proven)" : "var(--ink-5)" }}>
                      {complete ? "DONE" : `STEP ${index + 1}`}
                    </p>
                    <p style={{ margin: "8px 0 0", fontSize: "14.5px", fontWeight: 600 }}>{title}</p>
                    <p style={{ margin: "6px 0 0", fontSize: "12.5px", lineHeight: 1.6, color: "var(--ink-3)" }}>{text}</p>
                  </div>
                </div>
              </a>
            );
          })}
        </div>
      </section>

      <section className="mt-10">
        <div className="text-center">
          <p className="lbl">Pricing</p>
          <h2 style={{ margin: "10px 0 0", fontSize: "22px", fontWeight: 600, letterSpacing: "-.02em" }}>
            Grow without changing your operating system
          </h2>
          <p style={{ margin: "10px 0 0", fontSize: "13.5px", color: "var(--ink-3)" }}>
            One plan for this pilot. The price below is read from the billing service,
            so it cannot drift from checkout.
          </p>
          {status?.subscription_status === "active" && (
            <button disabled={billingBusy} onClick={manageBilling} className="btn-quiet mt-5">
              MANAGE BILLING
            </button>
          )}
        </div>

        {entitlement && (
          <div className="card mx-auto mt-7 max-w-md p-6"
               style={{ borderColor: "rgba(74,222,128,.3)" }}>
            <div className="flex items-center justify-between gap-3">
              <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 600 }}>
                {entitlement.plan_name}
              </h3>
              <span className="num" style={{ fontSize: "10px", letterSpacing: ".12em", color: "var(--proven)" }}>
                {entitlement.status === "trialing" ? "CURRENT TRIAL" : "CURRENT PLAN"}
              </span>
            </div>
            <p className="num" style={{ margin: "20px 0 0", fontSize: "30px", fontWeight: 500, letterSpacing: "-.03em" }}>
              {money(entitlement.price_per_month, entitlement.currency)}
              <span style={{ fontSize: "13px", color: "var(--ink-5)" }}> / month</span>
            </p>
            <div className="mt-6 space-y-2.5">
              {entitlement.features.map((line) => (
                <p key={line} className="flex items-start gap-2.5"
                   style={{ margin: 0, fontSize: "13px", color: "var(--ink-2)" }}>
                  <Icon name="check" size={14} stroke="var(--proven)" className="mt-0.5 shrink-0" />
                  {line}
                </p>
              ))}
            </div>
            {entitlement.action === "checkout" && entitlement.checkout_available && (
              <button disabled={billingBusy} onClick={choosePlan} className="btn-primary mt-7 w-full">
                CHOOSE OPERATOR
              </button>
            )}
          </div>
        )}
      </section>
    </main>
  );
}
