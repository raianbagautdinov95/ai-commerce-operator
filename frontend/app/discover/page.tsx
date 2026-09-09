"use client";

import { useEffect, useState } from "react";
import {
  discoverProducts,
  getDiscoveryHistory,
  getKeepaStatus,
  type DiscoverResult,
  type DiscoveryHistoryItem,
  type KeepaStatus,
} from "../../lib/api";
import { downloadCsv } from "../../lib/csv";
import { Icon, IconPlate } from "../icons";

const verdictTone: Record<string, "proven" | "waiting" | "unproven"> = {
  BUY: "proven", CAUTION: "waiting", AVOID: "unproven",
};

const NICHES = ["kitchen", "pet", "fitness", "office", "baby"];
const pct = (x: number) => `${(x * 100).toFixed(0)}%`;

export default function Discover() {
  const [niche, setNiche] = useState("kitchen");
  const [maxPrice, setMaxPrice] = useState("");
  const [minSales, setMinSales] = useState("");
  const [data, setData] = useState<DiscoverResult | null>(null);
  const [keepa, setKeepa] = useState<KeepaStatus | null>(null);
  const [history, setHistory] = useState<DiscoveryHistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadKeepa() {
    try {
      setKeepa(await getKeepaStatus());
    } catch {
      /* status is non-critical */
    }
  }

  async function loadHistory() {
    try {
      setHistory(await getDiscoveryHistory(10));
    } catch {
      /* history is non-critical */
    }
  }

  useEffect(() => {
    loadKeepa();
    loadHistory();
  }, []);

  async function onSearch(q?: string) {
    const term = q ?? niche;
    setNiche(term);
    setLoading(true);
    setError(null);
    try {
      setData(
        await discoverProducts(term, {
          limit: 15,
          max_price: maxPrice ? Number(maxPrice) : null,
          min_monthly_sales: minSales ? Number(minSales) : null,
        }),
      );
      loadKeepa();
      loadHistory();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div>
            <div className="flex items-center gap-3">
              <IconPlate name="search" />
              <p className="lbl">Discover</p>
            </div>
            <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
              What is worth a closer look
            </h1>
            <p style={{ margin: "16px 0 0", maxWidth: "70ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
              Name a niche and the engine pulls candidates, then ranks them by economics,
              competition and risk. This is a shortlist, not a verdict: the numbers behind each
              one are estimates until you take it to the hunter.
            </p>
          </div>
          {keepa?.enabled && (
            <span className="num shrink-0"
                  style={{ border: "1px solid var(--line)", borderRadius: "var(--r-sm)",
                           padding: "10px 14px", fontSize: "11px", letterSpacing: ".08em",
                           color: "var(--ink-4)" }}>
              KEEPA · {keepa.tokens_left?.toLocaleString()} TOKENS · +{keepa.refill_rate}/MIN
            </span>
          )}
        </div>
      </section>

      <section className="card p-6">
        <div className="flex flex-wrap items-end gap-3">
          <label style={{ display: "block" }}>
            <span className="lbl">Niche</span>
            <input className="field" style={{ width: "260px", marginTop: "7px" }}
                   value={niche}
                   onChange={(e) => setNiche(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && onSearch()}
                   placeholder="kitchen, pet, fitness…" />
          </label>
          <label style={{ display: "block" }}>
            <span className="lbl">Max price $</span>
            <input className="field num" style={{ width: "120px", marginTop: "7px" }} inputMode="decimal"
                   value={maxPrice}
                   onChange={(e) => setMaxPrice(e.target.value.replace(/[^0-9.]/g, ""))}
                   placeholder="any" />
          </label>
          <label style={{ display: "block" }}>
            <span className="lbl">Min sales / mo</span>
            <input className="field num" style={{ width: "130px", marginTop: "7px" }} inputMode="numeric"
                   value={minSales}
                   onChange={(e) => setMinSales(e.target.value.replace(/[^0-9]/g, ""))}
                   placeholder="any" />
          </label>
          <button onClick={() => onSearch()} disabled={loading}
                  className="btn-primary inline-flex items-center gap-2">
            <Icon name="search" size={13} /> {loading ? "SEARCHING…" : "FIND OPPORTUNITIES"}
          </button>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-2"
             style={{ paddingTop: "18px", borderTop: "1px solid var(--line-faint)" }}>
          <span className="lbl" style={{ marginRight: "6px" }}>Or try</span>
          {NICHES.map((n) => (
            <button key={n} onClick={() => onSearch(n)} className="num"
                    style={{ border: "1px solid var(--line)", borderRadius: "999px",
                             padding: "6px 14px", fontSize: "11px", letterSpacing: ".08em",
                             color: "var(--ink-3)" }}>
              {n.toUpperCase()}
            </button>
          ))}
        </div>
      </section>

      {error && (
        <p className="card-unproven mt-4 flex items-start gap-3 px-5 py-4"
           style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                    fontSize: "13.5px", color: "var(--unproven)" }}>
          <Icon name="alert" size={17} className="mt-0.5 shrink-0" /> {error}
        </p>
      )}

      {data && (
        <>
          <div className="mt-5 flex flex-wrap items-center justify-between gap-4">
            <p className="card-waiting flex items-start gap-3 px-4 py-3"
               style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                        fontSize: "12.5px", lineHeight: 1.6, color: "var(--ink-2)" }}>
              <Icon name="alert" size={15} stroke="var(--waiting)" className="mt-0.5 shrink-0" />
              <span>
                {data.note}
                {data.cached && " · served from cache, no API tokens spent"}
              </span>
            </p>
            <div className="flex shrink-0 flex-wrap gap-2">
              <button
                onClick={() =>
                  downloadCsv(
                    `discover-${niche}.csv`,
                    data.results.map((r, i) => ({
                      rank: i + 1,
                      product: r.name,
                      verdict: r.verdict,
                      score: r.score,
                      margin_pct: Math.round(r.economics.margin * 100),
                      roi_pct: Math.round(r.economics.roi * 100),
                      monthly_profit: r.economics.monthly_profit,
                      price: r.inputs?.price ?? "",
                      est_cogs: r.inputs?.cogs ?? "",
                    })),
                  )
                }
                className="btn-quiet inline-flex items-center gap-2">
                <Icon name="list" size={13} /> EXPORT CSV
              </button>
              {data.results.some((r) => r.inputs) && (
                <button
                  onClick={() => {
                    const all = data.results.map((r) => r.inputs).filter(Boolean);
                    localStorage.setItem("ph_prefill_multi", JSON.stringify(all));
                    window.location.href = "/";
                  }}
                  className="btn-quiet inline-flex items-center gap-2">
                  <Icon name="gem" size={13} /> COMPARE ALL
                </button>
              )}
            </div>
          </div>

          <div className="mt-4 space-y-3">
            {data.results.map((r, i) => {
              const tone = verdictTone[r.verdict] ?? "waiting";
              const top = i < 3;
              return (
                <article key={r.name} className="card p-5"
                         style={top ? { borderColor: "rgba(74,222,128,.22)",
                                        background: "rgba(74,222,128,.03)" } : undefined}>
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="flex min-w-0 items-start gap-4">
                      <span className="num shrink-0"
                            style={{ fontSize: "12px", color: "var(--ink-5)", paddingTop: "3px" }}>
                        {i + 1}
                      </span>
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-3">
                          <h2 style={{ margin: 0, fontSize: "16px", fontWeight: 600 }}>{r.name}</h2>
                          {top && (
                            <span className="num" style={{ fontSize: "10px", letterSpacing: ".14em",
                                 color: "var(--proven)" }}>
                              {i === 0 ? "BEST OF THIS SEARCH" : `TOP ${i + 1}`}
                            </span>
                          )}
                        </div>
                        <div className="mt-3 flex flex-wrap gap-x-7 gap-y-2">
                          <Inline label="Margin" value={pct(r.economics.margin)} />
                          <Inline label="ROI" value={pct(r.economics.roi)} />
                          <Inline label="Profit / unit" value={`$${r.economics.profit_per_unit}`} />
                          <Inline label="Per month" value={`~$${r.economics.monthly_profit.toLocaleString()}`} dim />
                        </div>
                        {r.risks.length > 0 && (
                          <p className="mt-3 flex items-start gap-2"
                             style={{ fontSize: "12.5px", lineHeight: 1.6, color: "var(--unproven)" }}>
                            <Icon name="alert" size={14} className="mt-0.5 shrink-0" />
                            {r.risks[0]}
                          </p>
                        )}
                      </div>
                    </div>

                    <div className="flex shrink-0 flex-col items-end gap-3">
                      <span className={`num card-${tone}`}
                            style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r-sm)",
                                     padding: "7px 13px", fontSize: "11px", letterSpacing: ".1em",
                                     color: `var(--${tone})` }}>
                        {r.verdict} · {r.score}/100
                      </span>
                      {r.inputs && (
                        <button
                          onClick={() => {
                            localStorage.setItem("ph_prefill", JSON.stringify(r.inputs));
                            window.location.href = "/";
                          }}
                          className="num inline-flex items-center gap-2"
                          style={{ fontSize: "11px", letterSpacing: ".1em", color: "var(--proven)" }}>
                          SCORE IT PROPERLY <Icon name="chevron" size={13} className="-rotate-90" />
                        </button>
                      )}
                    </div>
                  </div>
                </article>
              );
            })}
          </div>

          <p className="num" style={{ margin: "16px 0 0", fontSize: "10.5px", letterSpacing: ".1em", color: "var(--ink-5)" }}>
            EVERY FIGURE HERE RESTS ON AN ESTIMATED COST AND AN ESTIMATED DEMAND · NONE IS MEASURED
          </p>
        </>
      )}

      {history.length > 0 && (
        <section className="mt-12">
          <div className="flex items-center gap-3">
            <IconPlate name="clock" size={32} />
            <h2 className="lbl" style={{ margin: 0 }}>Recent searches</h2>
          </div>
          <div className="card mt-4">
            {history.map((h, i) => (
              <button key={h.id} onClick={() => onSearch(h.niche)}
                      className="flex w-full items-center justify-between gap-4 px-6 py-4 text-left"
                      style={{ borderTop: i === 0 ? "none" : "1px solid var(--line-faint)" }}>
                <div className="min-w-0">
                  <p className="truncate" style={{ margin: 0, fontSize: "14px" }}>{h.niche}</p>
                  <p className="num truncate" style={{ margin: "5px 0 0", fontSize: "11.5px", color: "var(--ink-4)" }}>
                    {h.results_count} CANDIDATES
                    {h.top_name ? ` · TOP: ${h.top_name} (${h.top_score})` : ""} ·
                    {" "}{new Date(h.created_at).toLocaleDateString()}
                  </p>
                </div>
                <Icon name="chevron" size={15} className="-rotate-90 shrink-0" stroke="var(--ink-6)" />
              </button>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}

function Inline({ label, value, dim }: { label: string; value: string; dim?: boolean }) {
  return (
    <span className="flex items-baseline gap-2">
      <span className="lbl">{label}</span>
      <span className="num" style={{ fontSize: "13px", color: dim ? "var(--ink-3)" : "var(--ink)" }}>
        {value}
      </span>
    </span>
  );
}
