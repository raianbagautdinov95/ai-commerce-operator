"use client";

import { useEffect, useState } from "react";
import {
  autopilotScan,
  clearQueue,
  decideQueueItem,
  getPortfolio,
  getQueue,
  waitForBackgroundJob,
  type Portfolio,
  type QueueItem,
} from "../../lib/api";
import { downloadCsv } from "../../lib/csv";
import { Icon, IconPlate } from "../icons";

/* ---------------------------------------------------------------------------
   The one screen where the Operator works without being asked.

   Everything in the queue is a forecast. Margin, monthly profit, payback — the
   decision engine computed them from a price and a cost nobody has paid yet,
   and not one of them has survived contact with a real order. So none of it is
   allowed to be green: in this app green means measured, and a launch plan has
   measured nothing. Amber is for the money you would have to put in first,
   because that is the only figure here that is certain.
   --------------------------------------------------------------------------- */

const verdictTone: Record<string, "proven" | "waiting" | "unproven"> = {
  BUY: "proven", CAUTION: "waiting", AVOID: "unproven",
};

const pct = (x: number) => `${(x * 100).toFixed(0)}%`;
const dollars = (x: number) => `$${x.toLocaleString()}`;

export default function Autopilot() {
  const [niches, setNiches] = useState("kitchen, pet, fitness");
  const [onlyBuy, setOnlyBuy] = useState(true);
  const [minMargin, setMinMargin] = useState("25");
  const [minProfit, setMinProfit] = useState("1000");
  const [minRoi, setMinRoi] = useState("");
  const [maxPayback, setMaxPayback] = useState("");
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [total, setTotal] = useState(0);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadQueue() {
    try {
      const { results, total_monthly_profit } = await getQueue("pending");
      setQueue(results);
      setTotal(total_monthly_profit);
      setPortfolio(await getPortfolio());
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => { loadQueue(); }, []);

  async function onScan() {
    setRunning(true);
    setError(null);
    setInfo(null);
    try {
      const list = niches.split(",").map((n) => n.trim()).filter(Boolean);
      const scan = await autopilotScan(list, {
        only_buy: onlyBuy,
        min_margin: minMargin ? Number(minMargin) / 100 : null,
        min_monthly_profit: minProfit ? Number(minProfit) : null,
        min_roi: minRoi ? Number(minRoi) / 100 : null,
        max_payback_months: maxPayback ? Number(maxPayback) : null,
      });
      let queued = scan.queued;
      let skipped = scan.skipped;
      if (scan.job_id) {
        setInfo("Scan queued — the worker is processing it with automatic retries…");
        const job = await waitForBackgroundJob(scan.job_id);
        queued = job.result?.queued ?? 0;
        skipped = job.result?.skipped ?? 0;
      }
      setInfo(`Queued ${queued} candidate${queued === 1 ? "" : "s"}; skipped ${skipped} that missed the bar.`);
      await loadQueue();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  async function decide(id: string, action: "approve" | "dismiss") {
    await decideQueueItem(id, action);
    setQueue((q) => q.filter((x) => x.id !== id));
    setPortfolio(await getPortfolio());   // approved plans roll into the portfolio
  }

  async function clearAll() {
    if (!confirm("Clear every queued and approved plan? This cannot be undone.")) return;
    await clearQueue();
    await loadQueue();
  }

  function exportPortfolio() {
    if (!portfolio) return;
    downloadCsv(
      "portfolio.csv",
      portfolio.items.map((i) => ({
        product: i.product_name,
        niche: i.niche,
        verdict: i.verdict,
        score: i.score,
        margin_pct: Math.round(i.margin * 100),
        roi_pct: Math.round(i.roi * 100),
        monthly_profit: i.monthly_profit,
        cogs: i.suggested_cogs,
        upfront_investment: i.upfront_investment,
        break_even_units: i.break_even_units,
        payback_months: i.payback_months,
        supplier: i.supplier,
      })),
    );
  }

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div>
            <div className="flex items-center gap-3">
              <IconPlate name="bolt" />
              <p className="lbl">Autopilot</p>
            </div>
            <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
              It looks while you are not
            </h1>
            <p style={{ margin: "16px 0 0", maxWidth: "68ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
              The agents scan your niches on their own and queue ready-made launch plans. Every
              figure below is a forecast from a cost nobody has paid yet — nothing here has been
              measured, and nothing is bought or published without you.
            </p>
          </div>
          <button onClick={clearAll} className="btn-quiet shrink-0">CLEAR ALL</button>
        </div>

        <div className="mt-8 flex flex-wrap items-center gap-3">
          <input className="field" style={{ width: "360px" }} value={niches}
                 onChange={(e) => setNiches(e.target.value)}
                 placeholder="niches, comma separated" />
          <button onClick={onScan} disabled={running}
                  className="btn-primary inline-flex items-center gap-2">
            <Icon name="bolt" size={13} />
            {running ? "AGENTS WORKING…" : "RUN AGENTS NOW"}
          </button>
        </div>
      </section>

      <section className="card p-6">
        <div className="flex items-center gap-3">
          <Icon name="target" size={17} stroke="var(--ink-3)" />
          <p className="lbl">The bar a candidate has to clear</p>
        </div>
        <div className="mt-5 flex flex-wrap items-end gap-5">
          <label className="flex cursor-pointer items-center gap-2.5 pb-2.5">
            <input type="checkbox" checked={onlyBuy} onChange={(e) => setOnlyBuy(e.target.checked)}
                   style={{ accentColor: "#4ADE80", width: "15px", height: "15px" }} />
            <span style={{ fontSize: "13px", color: "var(--ink-2)" }}>Only BUY verdicts</span>
          </label>
          <Limit label="Min margin %" value={minMargin} onChange={(v) => setMinMargin(v.replace(/[^0-9]/g, ""))} width="90px" />
          <Limit label="Min $ / month" value={minProfit} onChange={(v) => setMinProfit(v.replace(/[^0-9]/g, ""))} width="110px" />
          <Limit label="Min ROI %" value={minRoi} onChange={(v) => setMinRoi(v.replace(/[^0-9]/g, ""))} width="90px" placeholder="any" />
          <Limit label="Max payback (mo)" value={maxPayback} onChange={(v) => setMaxPayback(v.replace(/[^0-9.]/g, ""))} width="120px" placeholder="any" />
        </div>
        <p style={{ margin: "20px 0 0", paddingTop: "18px", borderTop: "1px solid var(--line-faint)",
                    maxWidth: "80ch", fontSize: "12.5px", lineHeight: 1.7, color: "var(--ink-4)" }}>
          Only candidates clearing this bar are queued, ranked by money. To let it run on a
          schedule with nobody watching, set <span className="num">AUTOPILOT_ENABLED=true</span> and{" "}
          <span className="num">AUTOPILOT_NICHES</span> in the environment. Scanning spends
          discovery tokens, which is why it is off until you say otherwise.
        </p>
      </section>

      {info && (
        <p className="card mt-4 flex items-start gap-3 px-5 py-4"
           style={{ fontSize: "13.5px", lineHeight: 1.6, color: "var(--ink-2)" }}>
          <Icon name="clock" size={17} stroke="var(--ink-4)" className="mt-0.5 shrink-0" /> {info}
        </p>
      )}
      {error && (
        <p className="card-unproven mt-4 flex items-start gap-3 px-5 py-4"
           style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                    fontSize: "13.5px", color: "var(--unproven)" }}>
          <Icon name="alert" size={17} className="mt-0.5 shrink-0" /> {error}
        </p>
      )}

      {portfolio && portfolio.count > 0 && (
        <section className="card mt-6 p-6 sm:p-8">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <IconPlate name="gem" size={34} />
              <div>
                <p className="lbl">Approved portfolio</p>
                <h2 style={{ margin: "4px 0 0", fontSize: "18px", fontWeight: 600 }}>
                  {portfolio.count} product{portfolio.count === 1 ? "" : "s"} you said yes to
                </h2>
              </div>
            </div>
            <button onClick={exportPortfolio} className="btn-quiet inline-flex items-center gap-2">
              <Icon name="list" size={13} /> EXPORT CSV
            </button>
          </div>

          <div className="mt-7 grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))" }}>
            <Stat label="Capital to invest" value={dollars(portfolio.total_upfront_investment)}
                  tone="waiting" note="the one certain figure here" />
            <Stat label="Monthly profit" value={`~${dollars(portfolio.total_monthly_profit)}`}
                  note="projected, never measured" />
            <Stat label="Blended payback"
                  value={portfolio.blended_payback_months != null ? `~${portfolio.blended_payback_months} mo` : "—"}
                  note="projected" />
            <Stat label="Products" value={String(portfolio.count)} />
          </div>

          <div className="mt-6" style={{ paddingTop: "6px" }}>
            {portfolio.items.map((item, i) => (
              <div key={item.id} className="flex items-center justify-between gap-4 py-3"
                   style={{ borderTop: i === 0 ? "none" : "1px solid var(--line-faint)" }}>
                <span className="min-w-0 truncate" style={{ fontSize: "13.5px", color: "var(--ink-2)" }}>
                  {item.product_name}
                </span>
                <span className="num shrink-0" style={{ fontSize: "12.5px", color: "var(--ink-4)" }}>
                  {item.upfront_investment != null && `${dollars(item.upfront_investment)} → `}
                  ~{dollars(item.monthly_profit)}/MO
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="mt-10">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex items-center gap-3">
            <IconPlate name="list" size={32} />
            <h2 className="lbl" style={{ margin: 0 }}>Waiting for your approval ({queue.length})</h2>
          </div>
          {queue.length > 0 && (
            <div className="text-right">
              <p className="num" style={{ margin: 0, fontSize: "20px", fontWeight: 500, letterSpacing: "-.03em" }}>
                ~{dollars(total)}<span style={{ fontSize: "13px", color: "var(--ink-4)" }}> / month</span>
              </p>
              <p className="num" style={{ margin: "5px 0 0", fontSize: "10.5px", letterSpacing: ".1em", color: "var(--ink-5)" }}>
                PROJECTED ACROSS {queue.length} PLAN{queue.length === 1 ? "" : "S"} · NOT PROVEN
              </p>
            </div>
          )}
        </div>

        {queue.length === 0 ? (
          <p className="mt-4 p-9 text-center"
             style={{ border: "1px dashed var(--line)", borderRadius: "var(--r)",
                      fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-5)" }}>
            Nothing queued. Run the agents to generate launch plans.
          </p>
        ) : (
          <div className="mt-4 space-y-4">
            {queue.map((item) => {
              const tone = verdictTone[item.verdict] ?? "waiting";
              return (
                <article key={item.id} className="card">
                  <div className="flex flex-wrap items-start justify-between gap-6 p-6">
                    <div className="min-w-0">
                      <p className="num" style={{ margin: 0, fontSize: "11px", letterSpacing: ".12em", color: "var(--ink-5)" }}>
                        {item.niche.toUpperCase()}
                      </p>
                      <h3 className="truncate" style={{ margin: "9px 0 0", fontSize: "17px", fontWeight: 600 }}>
                        {item.product_name}
                      </h3>
                      <div className="mt-4 flex flex-wrap gap-x-7 gap-y-2">
                        <Inline label="Margin" value={pct(item.margin)} />
                        <Inline label="ROI" value={pct(item.roi)} />
                        <Inline label="Per month" value={`~${dollars(item.monthly_profit)}`} />
                        {item.suggested_cogs != null && (
                          <Inline label="COGS" value={`$${item.suggested_cogs.toFixed(2)}`} />
                        )}
                      </div>
                      <div className="mt-2.5 flex flex-wrap gap-x-7 gap-y-2">
                        {item.upfront_investment != null && (
                          <Inline label="Up front" value={dollars(item.upfront_investment)} tone="waiting" />
                        )}
                        {item.break_even_units != null && (
                          <Inline label="Break even" value={`${item.break_even_units.toLocaleString()} units`} />
                        )}
                        {item.payback_months != null && (
                          <Inline label="Pays back" value={`~${item.payback_months} mo`} />
                        )}
                        {item.supplier && <Inline label="Supplier" value={item.supplier} />}
                      </div>
                    </div>

                    <div className="flex shrink-0 flex-col items-end gap-3">
                      <span className={`num card-${tone}`}
                            style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r-sm)",
                                     padding: "7px 13px", fontSize: "11.5px", letterSpacing: ".1em",
                                     color: `var(--${tone})` }}>
                        {item.verdict} · {item.score}
                      </span>
                      <div className="flex gap-2">
                        <button onClick={() => decide(item.id, "approve")}
                                className="btn-primary inline-flex items-center gap-1.5" style={{ padding: "9px 14px" }}>
                          <Icon name="check" size={13} /> APPROVE
                        </button>
                        <button onClick={() => decide(item.id, "dismiss")}
                                className="btn-quiet" style={{ padding: "9px 14px" }}>
                          DISMISS
                        </button>
                      </div>
                    </div>
                  </div>

                  <div className="px-6 py-3.5" style={{ borderTop: "1px solid var(--line-faint)" }}>
                    <button onClick={() => setOpen(open === item.id ? null : item.id)}
                            className="num inline-flex items-center gap-2"
                            style={{ fontSize: "11px", letterSpacing: ".1em", color: "var(--proven)" }}>
                      {open === item.id ? "HIDE THE PLAN" : "SEE THE PLAN & LISTING"}
                      <Icon name="chevron" size={13}
                            className={open === item.id ? "rotate-180" : ""} />
                    </button>
                  </div>

                  {open === item.id && (
                    <div className="px-6 py-5" style={{ borderTop: "1px solid var(--line-faint)", background: "#05070A" }}>
                      <ol className="space-y-2.5">
                        {item.steps.map((step, i) => (
                          <li key={i} className="flex items-start gap-3"
                              style={{ fontSize: "13px", color: "var(--ink-2)" }}>
                            <Icon name="check" size={14} stroke="var(--proven)" className="mt-0.5 shrink-0" />
                            <span>{step}</span>
                          </li>
                        ))}
                      </ol>
                      <pre className="mt-5 overflow-x-auto p-4"
                           style={{ background: "var(--panel)", border: "1px solid var(--line-faint)",
                                    borderRadius: "var(--r)", fontSize: "12.5px", lineHeight: 1.7,
                                    whiteSpace: "pre-wrap", color: "var(--ink-2)",
                                    fontFamily: "IBM Plex Mono, ui-monospace, monospace" }}>
                        {item.listing_draft}
                      </pre>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </section>
    </main>
  );
}

function Limit({ label, value, onChange, width, placeholder }: {
  label: string; value: string; onChange: (v: string) => void; width: string; placeholder?: string;
}) {
  return (
    <label style={{ display: "block" }}>
      <span className="lbl">{label}</span>
      <input className="field num" style={{ width, marginTop: "7px" }} inputMode="decimal"
             value={value} placeholder={placeholder}
             onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function Stat({ label, value, tone, note }: {
  label: string; value: string; tone?: "waiting"; note?: string;
}) {
  return (
    <div className="card p-5">
      <p className="lbl">{label}</p>
      <p className="num" style={{ margin: "12px 0 0", fontSize: "21px", fontWeight: 500,
           letterSpacing: "-.03em", color: tone ? `var(--${tone})` : "var(--ink)" }}>
        {value}
      </p>
      {note && <p style={{ margin: "7px 0 0", fontSize: "11px", color: "var(--ink-5)" }}>{note}</p>}
    </div>
  );
}

function Inline({ label, value, tone }: { label: string; value: string; tone?: "waiting" }) {
  return (
    <span className="flex items-baseline gap-2">
      <span className="lbl">{label}</span>
      <span className="num" style={{ fontSize: "13px", color: tone ? `var(--${tone})` : "var(--ink)" }}>
        {value}
      </span>
    </span>
  );
}
