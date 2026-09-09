"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createBillingCheckout, createBillingPortal, getEntitlement,
  type Entitlement, type EntitlementStatus,
} from "../../lib/api";
import { Icon, IconPlate } from "../icons";

/* ---------------------------------------------------------------------------
   What you are paying, and what happens if you stop.

   The status comes from /api/billing and is never inferred here. In particular
   it is not inferred from somebody arriving back on this page from Stripe:
   Checkout redirects on payment *submitted*, and the only thing that means a
   payment succeeded is the webhook Stripe sends afterwards. A screen that says
   "active" on the strength of a redirect will say it to somebody whose card was
   declined thirty seconds later.
   --------------------------------------------------------------------------- */

const TONE: Record<EntitlementStatus, { colour: string; word: string }> = {
  active: { colour: "var(--proven)", word: "ACTIVE" },
  trialing: { colour: "var(--proven)", word: "FREE TRIAL" },
  past_due: { colour: "var(--unproven)", word: "PAYMENT FAILED" },
  expired: { colour: "var(--waiting)", word: "TRIAL ENDED" },
  canceled: { colour: "var(--waiting)", word: "CANCELLED" },
  not_configured: { colour: "var(--ink-5)", word: "NO BILLING" },
};

function humanError(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error ?? "");
  if (/failed to fetch|networkerror|load failed/i.test(raw)) {
    return "We could not reach the service just now. Nothing has been charged "
      + "and nothing has changed — try again in a moment.";
  }
  return "That did not work. Nothing has been charged, and trying again is safe.";
}

function money(amount: number, currency: string): string {
  try {
    return amount.toLocaleString(undefined,
      { style: "currency", currency, maximumFractionDigits: 2 });
  } catch {
    return `${amount} ${currency}`;
  }
}

export default function BillingPage() {
  const [state, setState] = useState<Entitlement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const running = useRef(false);

  const refresh = useCallback(async () => {
    try {
      setState(await getEntitlement());
      setError(null);
    } catch (e) {
      setError(humanError(e));
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function go(action: "checkout" | "portal") {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError(null);
    try {
      const url = action === "checkout"
        ? await createBillingCheckout("operator")
        : await createBillingPortal();
      window.location.href = url;
    } catch (e) {
      setError(humanError(e));
      running.current = false;
      setBusy(false);
    }
  }

  const tone = state ? TONE[state.status] : null;
  const ends = state?.period_ends_at ? new Date(state.period_ends_at) : null;

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex items-center gap-3">
          <IconPlate name="profit" />
          <p className="lbl">Billing</p>
        </div>
        <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
          {state ? state.plan_name : "Billing"}
        </h1>
        <p aria-live="polite"
           style={{ margin: "16px 0 0", maxWidth: "68ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          {state ? state.explanation : "Reading your subscription…"}
        </p>
      </section>

      {error && (
        <div role="alert" className="card-unproven mb-6 flex items-start gap-3 px-5 py-4">
          <Icon name="alert" size={16} stroke="var(--unproven)" className="mt-0.5 shrink-0" />
          <p style={{ margin: 0, fontSize: "13.5px", lineHeight: 1.6 }}>{error}</p>
        </div>
      )}

      {state && tone && (
        <div className={state.action_required ? "card-unproven" : "card"}
             style={{ padding: "24px 26px",
                      ...(state.action_required ? { borderWidth: "1px", borderStyle: "solid",
                                                    borderRadius: "var(--r)" } : {}) }}>
          <div className="flex flex-wrap items-start justify-between gap-6">
            <div>
              {/* The word carries the state, not the colour alone. */}
              <p className="num" style={{ margin: 0, fontSize: "11px",
                                          letterSpacing: ".1em", color: tone.colour }}>
                {tone.word}
              </p>
              <p style={{ margin: "10px 0 0", fontSize: "26px", fontWeight: 600 }}>
                {money(state.price_per_month, state.currency)}
                <span style={{ fontSize: "14px", fontWeight: 400, color: "var(--ink-4)" }}>
                  {" "}per month
                </span>
              </p>
              {state.status === "trialing" && state.trial_days_remaining != null && (
                <p style={{ margin: "8px 0 0", fontSize: "13px", color: "var(--ink-2)" }}>
                  {state.trial_days_remaining} day(s) of trial left.
                </p>
              )}
              {ends && (
                <p className="num" style={{ margin: "8px 0 0", fontSize: "12px", color: "var(--ink-4)" }}>
                  {state.status === "active" ? "Renews " : "Ends "}
                  {ends.toISOString().slice(0, 10)}
                </p>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-3">
              {state.action === "checkout" && (
                state.checkout_available ? (
                  <button onClick={() => go("checkout")} disabled={busy}
                          className="btn-primary" style={{ padding: "11px 18px" }}>
                    {busy ? "OPENING…" : "SUBSCRIBE"}
                  </button>
                ) : (
                  <p style={{ margin: 0, maxWidth: "34ch", fontSize: "12.5px",
                              lineHeight: 1.6, color: "var(--ink-4)" }}>
                    Payment is not set up on this deployment yet, so there is
                    nothing to subscribe to.
                  </p>
                )
              )}
              {state.action === "portal" && (
                <button onClick={() => go("portal")} disabled={busy}
                        className={state.action_required ? "btn-primary" : "btn-quiet"}
                        style={{ padding: "11px 18px" }}>
                  {busy ? "OPENING…" : state.status === "past_due"
                    ? "UPDATE PAYMENT" : "MANAGE SUBSCRIPTION"}
                </button>
              )}
            </div>
          </div>

          {!state.access && (
            <p style={{ margin: "20px 0 0", maxWidth: "70ch", fontSize: "13px",
                        lineHeight: 1.7, color: "var(--ink-2)" }}>
              While this is unresolved the Operator stops importing orders and
              proposing changes. Everything it has already found stays here, and
              you can read it, export it or ask for it to be deleted at any time.
            </p>
          )}
        </div>
      )}

      {state && state.features.length > 0 && (
        <section className="mt-8">
          <p className="lbl">What it does</p>
          <ul className="mt-4 space-y-2" style={{ listStyle: "none", margin: "16px 0 0", padding: 0 }}>
            {state.features.map((feature) => (
              <li key={feature} className="flex items-start gap-3"
                  style={{ fontSize: "13.5px", lineHeight: 1.6, color: "var(--ink-2)" }}>
                <Icon name="check" size={14} stroke="var(--proven)" className="mt-1 shrink-0" />
                {feature}
              </li>
            ))}
          </ul>
        </section>
      )}

      {state?.support_email && (
        <p className="mt-8" style={{ fontSize: "12.5px", color: "var(--ink-4)" }}>
          Questions about a charge?{" "}
          <a href={`mailto:${state.support_email}`} style={{ color: "var(--ink-2)" }}>
            {state.support_email}
          </a>
          {state.support_response_time ? ` — we answer ${state.support_response_time}.` : "."}
        </p>
      )}
    </main>
  );
}
