"use client";

import { useState } from "react";
import { analyzeInventory, type InventoryResult, type StockRequest } from "../../lib/api";
import CsvImport from "../../components/CsvImport";
import { num, str } from "../../lib/csv";
import { Icon, IconPlate } from "../icons";

/* ---------------------------------------------------------------------------
   Running out is the expensive failure, so this screen ranks by time, not by
   money: a SKU with nine days of cover and a thirty-day lead time is already
   too late, however small its stock value.

   Every status is a claim about a date the engine computed from a velocity you
   entered. The three saturated colours mean what they always mean, and the
   example rows say they are an example.
   --------------------------------------------------------------------------- */

const EXAMPLE: StockRequest[] = [
  { name: "Silicone molds", on_hand: 40, avg_daily_sales: 5, lead_time_days: 30, inbound: 0, unit_cost: 6.5 },
  { name: "Dog bowl", on_hand: 300, avg_daily_sales: 5, lead_time_days: 30, inbound: 0, unit_cost: 4.5 },
  { name: "Yoga mat", on_hand: 1000, avg_daily_sales: 5, lead_time_days: 30, inbound: 0, unit_cost: 9 },
];

const statusColour: Record<string, string> = {
  REORDER_NOW: "var(--unproven)",
  REORDER_SOON: "var(--waiting)",
  HEALTHY: "var(--proven)",
  OVERSTOCK: "var(--ink-3)",
  NO_SALES: "var(--ink-5)",
};

const cols: [keyof StockRequest, string][] = [
  ["name", "SKU"],
  ["on_hand", "On hand"],
  ["avg_daily_sales", "Daily sales"],
  ["lead_time_days", "Lead time (d)"],
  ["inbound", "Inbound"],
  ["unit_cost", "Unit cost $"],
];

const days = (x: number | null) => (x == null ? "—" : `${x.toFixed(0)}d`);
const money = (x: number) => `$${x.toLocaleString()}`;

export default function InventoryPlanner() {
  const [rows, setRows] = useState<StockRequest[]>(EXAMPLE);
  const [untouched, setUntouched] = useState(true);
  const [result, setResult] = useState<InventoryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const setCell = (i: number, k: keyof StockRequest, v: string) => {
    setUntouched(false);
    setRows((rs) =>
      rs.map((r, j) =>
        j === i ? { ...r, [k]: k === "name" ? v : Number.isNaN(+v) ? r[k] : +v } : r,
      ),
    );
  };

  const addRow = () => {
    setUntouched(false);
    setRows((rs) => [...rs, { name: "", on_hand: 0, avg_daily_sales: 0, lead_time_days: 30, inbound: 0, unit_cost: 0 }]);
  };

  const removeRow = (i: number) => {
    setUntouched(false);
    setRows((rs) => (rs.length > 1 ? rs.filter((_, j) => j !== i) : rs));
  };

  const clearExample = () => {
    setUntouched(false);
    setRows([{ name: "", on_hand: 0, avg_daily_sales: 0, lead_time_days: 30, inbound: 0, unit_cost: 0 }]);
  };

  function importRows(raw: Record<string, string>[]): number {
    const mapped: StockRequest[] = raw
      .map((r) => ({
        name: str(r, ["name", "sku", "product", "title"]),
        on_hand: num(r, ["on_hand", "on hand", "available", "afn fulfillable quantity", "stock"]),
        avg_daily_sales: num(r, ["avg_daily_sales", "daily sales", "units/day", "velocity"]),
        lead_time_days: num(r, ["lead_time_days", "lead time", "lead time (days)"]) || 30,
        inbound: num(r, ["inbound", "in transit", "afn inbound shipped quantity"]),
        unit_cost: num(r, ["unit_cost", "cost", "landed cost", "cogs"]),
      }))
      .filter((s) => s.name);
    if (mapped.length) {
      setRows(mapped);
      setUntouched(false);
    }
    return mapped.length;
  }

  async function onAnalyze() {
    setLoading(true);
    setError(null);
    try {
      setResult(await analyzeInventory(rows));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  const s = result?.summary;

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex items-center gap-3">
          <IconPlate name="box" />
          <p className="lbl">Inventory</p>
        </div>
        <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
          What runs out before it can be replaced
        </h1>
        <p style={{ margin: "16px 0 0", maxWidth: "70ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          Enter stock and how fast each SKU sells. The engine computes days of cover, the date
          you have to order by, and how much. Lead time is what makes this urgent: nine days of
          stock and a thirty-day lead time is already late.
        </p>
      </section>

      <section className="card p-6 sm:p-8">
        {untouched && (
          <div className="card-waiting mb-6 flex flex-wrap items-center justify-between gap-4 px-5 py-4"
               style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }}>
            <span className="flex items-start gap-3" style={{ fontSize: "13px", lineHeight: 1.6, color: "var(--ink-2)" }}>
              <Icon name="alert" size={17} stroke="var(--waiting)" className="mt-0.5 shrink-0" />
              These three SKUs are an example, not your stock. Any verdict below is about them
              until you paste your own report.
            </span>
            <button onClick={clearExample} className="btn-quiet shrink-0">CLEAR IT</button>
          </div>
        )}

        <div className="overflow-x-auto" style={{ border: "1px solid var(--line)", borderRadius: "var(--r)" }}>
          <table className="w-full" style={{ borderCollapse: "collapse", minWidth: "760px" }}>
            <thead>
              <tr style={{ background: "var(--raised)", borderBottom: "1px solid var(--line)" }}>
                {cols.map(([, label]) => <th key={label} className="lbl px-3 py-3 text-left">{label}</th>)}
                <th className="px-3 py-3" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} style={{ borderTop: i === 0 ? "none" : "1px solid var(--line-faint)" }}>
                  {cols.map(([k]) => (
                    <td key={k} className="px-2 py-2">
                      <input className={k === "name" ? "field" : "field num"}
                             style={{ padding: "8px 10px" }}
                             value={String(r[k] ?? "")}
                             onChange={(e) => setCell(i, k, e.target.value)} />
                    </td>
                  ))}
                  <td className="px-3 py-2 text-right">
                    {rows.length > 1 && (
                      <button onClick={() => removeRow(i)} className="num"
                              style={{ fontSize: "11px", letterSpacing: ".08em", color: "var(--ink-5)" }}>
                        REMOVE
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <CsvImport
          hint="Paste a stock report with name/sku, on_hand, avg_daily_sales, lead_time_days, inbound and unit_cost. Missing columns fall back to defaults."
          onRows={importRows}
        />

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button onClick={onAnalyze} disabled={loading}
                  className="btn-primary inline-flex items-center gap-2">
            <Icon name="box" size={13} /> {loading ? "ANALYSING…" : "ANALYSE"}
          </button>
          <button onClick={addRow} className="btn-quiet">+ ADD SKU</button>
        </div>
      </section>

      {error && (
        <p className="card-unproven mt-4 flex items-start gap-3 px-5 py-4"
           style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                    fontSize: "13.5px", color: "var(--unproven)" }}>
          <Icon name="alert" size={17} className="mt-0.5 shrink-0" /> {error}
        </p>
      )}

      {s && (
        <>
          <section className="mt-6 grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))" }}>
            <Stat label="SKUs at risk" value={String(s.skus_at_risk)}
                  tone={s.skus_at_risk > 0 ? "unproven" : "proven"}
                  note={`out of ${s.total_skus}`} />
            <Stat label="Stock value" value={money(s.total_stock_value)}
                  note="money already sitting on a shelf" />
            <Stat label="Reorder cost" value={money(s.total_reorder_cost)} tone="waiting"
                  note="not spent yet — now and soon" />
            <Stat label="SKUs total" value={String(s.total_skus)} />
          </section>

          {result?.explanation && (
            <p className="card mt-4 px-6 py-5"
               style={{ fontSize: "13.5px", lineHeight: 1.75, color: "var(--ink-2)" }}>
              {result.explanation}
            </p>
          )}

          <section className="card mt-4 overflow-x-auto">
            <table className="w-full" style={{ borderCollapse: "collapse", minWidth: "860px" }}>
              <thead>
                <tr style={{ background: "var(--raised)", borderBottom: "1px solid var(--line)" }}>
                  <th className="lbl px-4 py-3.5 text-left">SKU</th>
                  <th className="lbl px-4 py-3.5 text-left">Status</th>
                  <th className="lbl px-4 py-3.5 text-right">Cover</th>
                  <th className="lbl px-4 py-3.5 text-right">Stockout in</th>
                  <th className="lbl px-4 py-3.5 text-right">Order by</th>
                  <th className="lbl px-4 py-3.5 text-right">Order qty</th>
                  <th className="lbl px-4 py-3.5 text-left">Why</th>
                </tr>
              </thead>
              <tbody>
                {result.findings.map((f) => (
                  <tr key={f.name} style={{ borderTop: "1px solid var(--line-faint)" }}>
                    <td className="px-4 py-3.5" style={{ fontSize: "13.5px" }}>{f.name}</td>
                    <td className="num px-4 py-3.5"
                        style={{ fontSize: "11px", letterSpacing: ".1em",
                                 color: statusColour[f.status] ?? "var(--ink-3)" }}>
                      {f.status}
                    </td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px" }}>{days(f.days_of_cover)}</td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px" }}>{days(f.stockout_in_days)}</td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px" }}>{days(f.days_to_reorder)}</td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px", color: "var(--ink-3)" }}>
                      {f.suggested_order_qty > 0 ? f.suggested_order_qty.toLocaleString() : "—"}
                    </td>
                    <td className="px-4 py-3.5" style={{ fontSize: "12.5px", lineHeight: 1.6, color: "var(--ink-3)" }}>
                      {f.reason}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <p className="num" style={{ margin: "14px 0 0", fontSize: "10.5px", letterSpacing: ".1em", color: "var(--ink-5)" }}>
            EVERY DATE HERE IS COMPUTED FROM THE VELOCITY YOU ENTERED · THE MODEL ONLY WROTE THE WHY
          </p>
        </>
      )}
    </main>
  );
}

function Stat({ label, value, note, tone }: {
  label: string; value: string; note?: string; tone?: "proven" | "waiting" | "unproven";
}) {
  return (
    <div className="card p-5">
      <p className="lbl">{label}</p>
      <p className="num" style={{ margin: "12px 0 0", fontSize: "21px", fontWeight: 500,
           letterSpacing: "-.03em", color: tone ? `var(--${tone})` : "var(--ink)" }}>
        {value}
      </p>
      {note && <p style={{ margin: "7px 0 0", fontSize: "11px", lineHeight: 1.5, color: "var(--ink-5)" }}>{note}</p>}
    </div>
  );
}
