"use client";

import { useState } from "react";
import { buildLaunchPlan, type LaunchPlan } from "../../lib/api";
import UnitEconomics from "../../components/UnitEconomics";
import { Icon, IconPlate } from "../icons";

const verdictTone: Record<string, "proven" | "waiting" | "unproven"> = {
  BUY: "proven",
  CAUTION: "waiting",
  AVOID: "unproven",
};

const pct = (x: number) => `${(x * 100).toFixed(0)}%`;
const money = (x: number) => `$${x.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

export default function Operator() {
  const [niche, setNiche] = useState("kitchen");
  const [plan, setPlan] = useState<LaunchPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onRun() {
    setLoading(true);
    setError(null);
    try {
      setPlan(await buildLaunchPlan(niche, 10));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  const p = plan?.product;
  const tone = p ? verdictTone[p.verdict] ?? "waiting" : "waiting";

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex items-center gap-3">
          <IconPlate name="spark" />
          <p className="lbl">Operator</p>
        </div>
        <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
          Find something worth selling
        </h1>
        <p style={{ margin: "16px 0 0", maxWidth: "68ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          Three agents in one run: find a product, find a supplier, re-score it with the real
          landed cost, then draft the listing. Every number below is computed by the decision
          engine — the model only writes the words. Nothing is published until you say so.
        </p>

        <div className="mt-8 flex flex-wrap items-center gap-3">
          <input
            className="field"
            style={{ width: "320px" }}
            value={niche}
            onChange={(e) => setNiche(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onRun()}
            placeholder="niche, e.g. kitchen / pet / fitness"
          />
          <button onClick={onRun} disabled={loading}
                  className="btn-primary inline-flex items-center gap-2">
            <Icon name="bolt" size={13} />
            {loading ? "RUNNING AGENTS…" : "BUILD LAUNCH PLAN"}
          </button>
        </div>

        {error && (
          <p className="card-unproven mt-5 inline-flex items-center gap-3 px-4 py-3"
             style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                      fontSize: "13.5px", color: "var(--unproven)" }}>
            <Icon name="alert" size={16} /> {error}
          </p>
        )}
      </section>

      {plan && p && (
        <div className="space-y-6">
          <section className="card p-6 sm:p-8">
            <p className="lbl">Agent pipeline</p>
            <ol className="mt-5 space-y-3">
              {plan.steps.map((step, i) => (
                <li key={i} className="flex items-start gap-3" style={{ fontSize: "13.5px", color: "var(--ink-2)" }}>
                  <Icon name="check" size={15} stroke="var(--proven)" className="mt-0.5 shrink-0" />
                  <span>{step}</span>
                </li>
              ))}
            </ol>
          </section>

          <div className="grid gap-6 lg:grid-cols-2">
            <section className="card p-6 sm:p-8">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="lbl">Chosen product</p>
                  <h2 style={{ margin: "8px 0 0", fontSize: "18px", fontWeight: 600 }}>{p.name}</h2>
                </div>
                <span className={`num card-${tone}`}
                      style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r-sm)",
                               padding: "8px 14px", fontSize: "11.5px", letterSpacing: ".12em",
                               color: `var(--${tone})` }}>
                  {p.verdict} · {p.score}/100
                </span>
              </div>

              <div className="mt-7 grid gap-px"
                   style={{ gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))" }}>
                <Figure label="Margin" value={pct(p.economics.margin)} />
                <Figure label="ROI" value={pct(p.economics.roi)} />
                <Figure label="Profit / unit" value={money(p.economics.profit_per_unit)} />
                <Figure label="Per month" value={`~${money(p.economics.monthly_profit)}`}
                        note="projected, not proven" />
              </div>

              <p style={{ margin: "22px 0 0", paddingTop: "20px", borderTop: "1px solid var(--line-faint)",
                          fontSize: "13px", lineHeight: 1.7, color: "var(--ink-2)" }}>
                {p.reason}
              </p>

              {p.inputs && (
                <div className="mt-6">
                  <UnitEconomics
                    price={p.inputs.price}
                    cogs={p.inputs.cogs}
                    referral={p.economics.referral_fee}
                    fba={p.economics.fba_fee_total}
                    ads={p.inputs.ppc_per_unit ?? 0}
                    profit={p.economics.profit_per_unit}
                  />
                </div>
              )}
            </section>

            <section className="card p-6 sm:p-8">
              <div className="flex items-center gap-3">
                <IconPlate name="truck" size={34} />
                <div>
                  <p className="lbl">Supplier &amp; cost</p>
                  <h2 style={{ margin: "4px 0 0", fontSize: "18px", fontWeight: 600 }}>
                    {plan.supplier ? plan.supplier.supplier : "Nothing found"}
                  </h2>
                </div>
              </div>

              {plan.supplier ? (
                <>
                  <div className="mt-7 grid gap-px"
                       style={{ gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))" }}>
                    <Figure label="Country" value={plan.supplier.country} />
                    <Figure label="Unit price" value={money(plan.supplier.unit_price)} />
                    <Figure label="MOQ" value={plan.supplier.moq.toLocaleString()} />
                    <Figure label="Lead time" value={`${plan.supplier.lead_time_days} d`} />
                  </div>

                  <div className="card-proven mt-7 flex items-baseline justify-between gap-4 p-4"
                       style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }}>
                    <span style={{ fontSize: "13.5px", color: "var(--ink-2)" }}>Landed COGS</span>
                    <span className="num" style={{ fontSize: "18px", fontWeight: 500, color: "var(--proven)" }}>
                      {plan.suggested_cogs == null ? "—" : money(plan.suggested_cogs)}
                    </span>
                  </div>

                  {plan.payback && (
                    <div className="mt-6 space-y-3" style={{ paddingTop: "20px", borderTop: "1px solid var(--line-faint)" }}>
                      <Row label="First order"
                           value={`${plan.payback.first_batch_units.toLocaleString()} units`} />
                      <Row label="Money up front"
                           value={`~${money(plan.payback.upfront_investment)}`} />
                      {plan.payback.break_even_units != null && (
                        <Row label="Break even at"
                             value={`${plan.payback.break_even_units.toLocaleString()} units`} />
                      )}
                      {plan.payback.payback_months != null && (
                        <Row label="Pays back in" value={`~${plan.payback.payback_months} mo`} />
                      )}
                    </div>
                  )}
                </>
              ) : (
                <p style={{ margin: "20px 0 0", fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-5)" }}>
                  No supplier was found for this product, so the landed cost is unknown and the
                  economics above rest on an assumed cost rather than a quoted one.
                </p>
              )}
            </section>
          </div>

          <section className="card p-6 sm:p-8">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <IconPlate name="tag" size={34} />
                <div>
                  <p className="lbl">Listing draft</p>
                  <h2 style={{ margin: "4px 0 0", fontSize: "18px", fontWeight: 600 }}>Written by the model</h2>
                </div>
              </div>
              <button
                onClick={() => {
                  localStorage.setItem("creative_prefill", p.name);
                  window.location.href = "/creative";
                }}
                className="btn-quiet inline-flex items-center gap-2">
                <Icon name="image" size={13} /> MAKE IT SELL
              </button>
            </div>
            <pre className="mt-6 overflow-x-auto p-5"
                 style={{ background: "#05070A", border: "1px solid var(--line-faint)",
                          borderRadius: "var(--r)", fontSize: "13px", lineHeight: 1.7,
                          whiteSpace: "pre-wrap", color: "var(--ink-2)",
                          fontFamily: "IBM Plex Mono, ui-monospace, monospace" }}>
              {plan.listing_draft}
            </pre>
          </section>

          <section className="card-waiting flex flex-wrap items-center justify-between gap-6 p-6"
                   style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }}>
            <div className="flex gap-4">
              <Icon name="clock" size={18} stroke="var(--waiting)" className="mt-0.5 shrink-0" />
              <p style={{ margin: 0, maxWidth: "72ch", fontSize: "13.5px", lineHeight: 1.65, color: "var(--ink-2)" }}>
                {plan.note}
              </p>
            </div>
            <button disabled title="Requires an Amazon Seller account with SP-API access"
                    className="btn-quiet shrink-0">
              PUBLISH — NEEDS SP-API
            </button>
          </section>
        </div>
      )}
    </main>
  );
}

function Figure({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div>
      <p className="lbl">{label}</p>
      <p className="num" style={{ margin: "8px 0 0", fontSize: "18px", fontWeight: 500, letterSpacing: "-.02em" }}>
        {value}
      </p>
      {note && <p style={{ margin: "5px 0 0", fontSize: "11px", color: "var(--ink-5)" }}>{note}</p>}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span style={{ fontSize: "13px", color: "var(--ink-2)" }}>{label}</span>
      <span className="num" style={{ fontSize: "13px" }}>{value}</span>
    </div>
  );
}
