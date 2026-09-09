"use client";

import { useEffect, useState } from "react";
import { getDailyReport, type DailyReport } from "../../lib/api";
import { Icon, IconPlate, type IconName } from "../icons";

/* ---------------------------------------------------------------------------
   The morning briefing: everything at once, ranked by what it costs.

   The severity of an item is a claim about money at risk, so it keeps its
   colour. The totals do not all mean the same thing and are not shown as
   though they did — wasted spend already left, while reorder cost is money you
   have not spent and may choose not to.
   --------------------------------------------------------------------------- */

const severityColour: Record<string, string> = {
  critical: "var(--unproven)",
  warning: "var(--waiting)",
  info: "var(--ink-4)",
};

const moduleLabel: Record<string, string> = {
  product_hunter: "PRODUCT",
  ppc: "PPC",
  inventory: "INVENTORY",
};

const moduleIcon: Record<string, IconName> = {
  product_hunter: "gem",
  ppc: "target",
  inventory: "box",
};

const money = (x: number) => `$${x.toLocaleString()}`;

export default function DailyReportPage() {
  const [report, setReport] = useState<DailyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setReport(await getDailyReport());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div>
            <div className="flex items-center gap-3">
              <IconPlate name="list" />
              <p className="lbl">Daily report</p>
            </div>
            <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
              What needs you this morning
            </h1>
            <p style={{ margin: "16px 0 0", maxWidth: "68ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
              Everything the Operator noticed across every module, ranked by the money at stake
              rather than by when it happened. The order is the point: the first line is the one
              that costs most to ignore.
            </p>
          </div>
          <button onClick={load} disabled={loading} className="btn-quiet shrink-0 inline-flex items-center gap-2">
            <Icon name="clock" size={13} /> {loading ? "REFRESHING…" : "REFRESH"}
          </button>
        </div>
      </section>

      {error && (
        <p className="card-unproven flex items-start gap-3 px-5 py-4"
           style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                    fontSize: "13.5px", color: "var(--unproven)" }}>
          <Icon name="alert" size={17} className="mt-0.5 shrink-0" /> {error}
        </p>
      )}

      {report && (
        <>
          <section className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))" }}>
            <Stat icon="alert" label="Money at stake" value={money(report.money_at_stake_usd)}
                  tone="unproven" note="what today costs if nothing is done" />
            <Stat icon="target" label="Wasted ad spend" value={money(report.wasted_spend_usd)}
                  note="measured — it already left" />
            <Stat icon="box" label="Reorder cost" value={money(report.reorder_cost_usd)}
                  tone="waiting" note="not spent yet — now and soon" />
          </section>

          {report.briefing && (
            <p className="card mt-4 px-6 py-5"
               style={{ fontSize: "14px", lineHeight: 1.75, color: "var(--ink-2)" }}>
              {report.briefing}
            </p>
          )}

          {report.items.length > 0 ? (
            <div className="card mt-4">
              {report.items.map((item, i) => (
                <div key={i} className="flex flex-wrap items-center gap-4 px-6 py-4"
                     style={{ borderTop: i === 0 ? "none" : "1px solid var(--line-faint)" }}>
                  <span className="num shrink-0 text-right"
                        style={{ width: "22px", fontSize: "12px", color: "var(--ink-6)" }}>
                    {i + 1}
                  </span>
                  <span className="dot shrink-0"
                        style={{ background: severityColour[item.severity] ?? "var(--ink-5)",
                                 boxShadow: item.severity === "info" ? "none"
                                   : `0 0 10px 1px ${severityColour[item.severity]}55` }} />
                  <span className="num flex shrink-0 items-center gap-2"
                        style={{ width: "112px", fontSize: "10.5px", letterSpacing: ".1em", color: "var(--ink-4)" }}>
                    <Icon name={moduleIcon[item.module] ?? "list"} size={13} />
                    {moduleLabel[item.module] ?? item.module.toUpperCase()}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate" style={{ margin: 0, fontSize: "14px" }}>{item.title}</p>
                    <p className="truncate" style={{ margin: "4px 0 0", fontSize: "12.5px", color: "var(--ink-4)" }}>
                      {item.detail}
                    </p>
                  </div>
                  {item.impact_usd > 0 && (
                    <span className="num shrink-0" style={{ fontSize: "14px", fontWeight: 500 }}>
                      {money(item.impact_usd)}
                    </span>
                  )}
                </div>
              ))}
            </div>
          ) : (
            !loading && (
              <div className="card mt-4 flex flex-col items-start gap-4 p-9">
                <IconPlate name="check" tone="proven" />
                <h2 style={{ margin: 0, fontSize: "18px", fontWeight: 600 }}>Nothing on record yet</h2>
                <p style={{ margin: 0, maxWidth: "62ch", fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-2)" }}>
                  An empty briefing means nothing has been analysed, not that nothing is wrong.
                  Run the PPC analyser, the inventory planner or Product Hunter and this fills in.
                </p>
                <div className="flex flex-wrap gap-2">
                  <a href="/ppc" className="btn-quiet inline-flex items-center gap-2">
                    <Icon name="target" size={13} /> PPC
                  </a>
                  <a href="/inventory" className="btn-quiet inline-flex items-center gap-2">
                    <Icon name="box" size={13} /> INVENTORY
                  </a>
                </div>
              </div>
            )
          )}
        </>
      )}
    </main>
  );
}

function Stat({ icon, label, value, note, tone }: {
  icon: IconName; label: string; value: string; note?: string; tone?: "unproven" | "waiting";
}) {
  return (
    <div className="card p-5">
      <div className="flex items-center gap-3">
        <IconPlate name={icon} tone={tone ?? "neutral"} size={34} />
        <p className="lbl">{label}</p>
      </div>
      <p className="num" style={{ margin: "16px 0 0", fontSize: "22px", fontWeight: 500,
           letterSpacing: "-.03em", color: tone ? `var(--${tone})` : "var(--ink)" }}>
        {value}
      </p>
      {note && <p style={{ margin: "7px 0 0", fontSize: "11px", lineHeight: 1.5, color: "var(--ink-5)" }}>{note}</p>}
    </div>
  );
}
