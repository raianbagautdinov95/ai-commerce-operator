"use client";

import { useEffect, useState } from "react";
import {
  analyzePpc,
  getPpcHistory,
  type KeywordRequest,
  type PpcHistoryItem,
  type PpcResult,
} from "../../lib/api";
import CsvImport from "../../components/CsvImport";
import { num, str } from "../../lib/csv";
import { Icon, IconPlate } from "../icons";

/* ---------------------------------------------------------------------------
   Where the negative keywords come from.

   Two figures on this screen look alike and are not: wasted spend is money
   that already left, measured, and earned nothing — red, because it did not
   work. Potential savings is what the engine thinks stopping it would return,
   which has not happened to anybody yet. It used to be green. Green here means
   measured, so it isn't any more.

   The table starts with an example so the shape of the data is obvious. It is
   labelled as one, because a screen pre-filled with numbers that look like
   yours is how somebody ends up trusting a verdict about a product they never
   entered.
   --------------------------------------------------------------------------- */

const EXAMPLE: KeywordRequest[] = [
  { keyword: "silicone mold", clicks: 50, spend: 40, sales: 200, orders: 8 },
  { keyword: "bakeware set", clicks: 30, spend: 25, sales: 0, orders: 0 },
  { keyword: "cake", clicks: 5, spend: 3, sales: 0, orders: 0 },
  { keyword: "silicone tray", clicks: 40, spend: 60, sales: 100, orders: 4 },
  { keyword: "molds cheap", clicks: 60, spend: 20, sales: 300, orders: 15 },
];

// An action is advice, not a claim about money that moved. Only the one tied to
// spend that measurably earned nothing is allowed the loud colour.
const actionTone: Record<string, string> = {
  NEGATE: "var(--unproven)",
  LOWER_BID: "var(--waiting)",
  RAISE_BID: "var(--ink)",
  KEEP: "var(--ink-4)",
  GATHER_DATA: "var(--ink-4)",
};

const cols: [keyof KeywordRequest, string][] = [
  ["keyword", "Keyword"],
  ["clicks", "Clicks"],
  ["spend", "Spend $"],
  ["sales", "Sales $"],
  ["orders", "Orders"],
];

const acosPct = (x: number | null) => (x == null ? "n/a" : `${(x * 100).toFixed(0)}%`);
const money = (x: number | null) => (x == null ? "—" : `$${x.toFixed(2)}`);

export default function PpcAnalyzer() {
  const [name, setName] = useState("Silicone molds — example");
  const [marginPct, setMarginPct] = useState(35); // break-even ACOS = product margin
  const [rows, setRows] = useState<KeywordRequest[]>(EXAMPLE);
  const [untouched, setUntouched] = useState(true);
  const [result, setResult] = useState<PpcResult | null>(null);
  const [history, setHistory] = useState<PpcHistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadHistory() {
    try {
      setHistory(await getPpcHistory(10));
    } catch {
      /* history is non-critical */
    }
  }

  useEffect(() => { loadHistory(); }, []);

  const setCell = (i: number, k: keyof KeywordRequest, v: string) => {
    setUntouched(false);
    setRows((rs) =>
      rs.map((r, j) =>
        j === i ? { ...r, [k]: k === "keyword" ? v : Number.isNaN(+v) ? r[k] : +v } : r,
      ),
    );
  };

  const addRow = () => {
    setUntouched(false);
    setRows((rs) => [...rs, { keyword: "", clicks: 0, spend: 0, sales: 0, orders: 0 }]);
  };

  const removeRow = (i: number) => {
    setUntouched(false);
    setRows((rs) => (rs.length > 1 ? rs.filter((_, j) => j !== i) : rs));
  };

  const clearExample = () => {
    setUntouched(false);
    setRows([{ keyword: "", clicks: 0, spend: 0, sales: 0, orders: 0 }]);
  };

  function importRows(raw: Record<string, string>[]): number {
    const mapped: KeywordRequest[] = raw
      .map((r) => ({
        keyword: str(r, ["keyword", "customer search term", "search term", "targeting"]),
        clicks: num(r, ["clicks"]),
        spend: num(r, ["spend", "cost"]),
        sales: num(r, ["sales", "7 day total sales", "total sales", "total advertising sales"]),
        orders: num(r, ["orders", "7 day total orders (#)", "total orders", "orders (#)"]),
        impressions: num(r, ["impressions"]),
      }))
      .filter((k) => k.keyword);
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
      setResult(await analyzePpc({ name, break_even_acos: marginPct / 100, keywords: rows }));
      loadHistory();
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
          <IconPlate name="target" />
          <p className="lbl">PPC</p>
        </div>
        <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
          Which words are spending nothing back
        </h1>
        <p style={{ margin: "16px 0 0", maxWidth: "70ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          Paste a keyword-level Amazon Ads report. The engine computes every ACOS, flags the
          spend that earned nothing and works out the bid moves; the model only writes the
          explanation. Connect the Advertising API and this arrives on its own.
        </p>
      </section>

      <section className="card p-6 sm:p-8">
        <div className="flex flex-wrap items-end gap-5">
          <label style={{ display: "block" }}>
            <span className="lbl">Campaign name</span>
            <input className="field" style={{ width: "280px", marginTop: "7px" }}
                   value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label style={{ display: "block" }}>
            <span className="lbl">Product margin % — the break-even ACOS</span>
            <input className="field num" style={{ width: "150px", marginTop: "7px" }} inputMode="decimal"
                   value={String(marginPct)}
                   onChange={(e) => setMarginPct(Number.isNaN(+e.target.value) ? marginPct : +e.target.value)} />
          </label>
        </div>

        {untouched && (
          <div className="card-waiting mt-6 flex flex-wrap items-center justify-between gap-4 px-5 py-4"
               style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }}>
            <span className="flex items-start gap-3" style={{ fontSize: "13px", lineHeight: 1.6, color: "var(--ink-2)" }}>
              <Icon name="alert" size={17} stroke="var(--waiting)" className="mt-0.5 shrink-0" />
              These five keywords are an example, not your campaign. Any verdict below is about
              them until you paste your own report.
            </span>
            <button onClick={clearExample} className="btn-quiet shrink-0">CLEAR IT</button>
          </div>
        )}

        <div className="mt-6 overflow-x-auto" style={{ border: "1px solid var(--line)", borderRadius: "var(--r)" }}>
          <table className="w-full" style={{ borderCollapse: "collapse", minWidth: "660px" }}>
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
                      <input className={k === "keyword" ? "field" : "field num"}
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
          hint="Paste an Amazon Ads search-term report, or any table with keyword, clicks, spend, sales and orders."
          onRows={importRows}
        />

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button onClick={onAnalyze} disabled={loading}
                  className="btn-primary inline-flex items-center gap-2">
            <Icon name="target" size={13} /> {loading ? "ANALYSING…" : "ANALYSE"}
          </button>
          <button onClick={addRow} className="btn-quiet">+ ADD KEYWORD</button>
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
            <Stat label="Overall ACOS" value={acosPct(s.overall_acos)}
                  note={`break-even is ${acosPct(s.break_even_acos)}`} />
            <Stat label="Total spend" value={money(s.total_spend)}
                  note={`against ${money(s.total_sales)} of sales`} />
            <Stat label="Wasted spend" value={money(s.wasted_spend)} tone="unproven"
                  note="measured — it left and earned nothing" />
            <Stat label="Potential savings" value={money(s.potential_savings)}
                  note="projected — nothing has been changed yet" />
          </section>

          {result?.explanation && (
            <p className="card mt-4 px-6 py-5"
               style={{ fontSize: "13.5px", lineHeight: 1.75, color: "var(--ink-2)" }}>
              {result.explanation}
            </p>
          )}

          <section className="card mt-4 overflow-x-auto">
            <table className="w-full" style={{ borderCollapse: "collapse", minWidth: "820px" }}>
              <thead>
                <tr style={{ background: "var(--raised)", borderBottom: "1px solid var(--line)" }}>
                  <th className="lbl px-4 py-3.5 text-left">Keyword</th>
                  <th className="lbl px-4 py-3.5 text-left">Action</th>
                  <th className="lbl px-4 py-3.5 text-right">ACOS</th>
                  <th className="lbl px-4 py-3.5 text-right">Spend</th>
                  <th className="lbl px-4 py-3.5 text-right">Bid →</th>
                  <th className="lbl px-4 py-3.5 text-right">Saves</th>
                  <th className="lbl px-4 py-3.5 text-left">Why</th>
                </tr>
              </thead>
              <tbody>
                {result.findings.map((f) => (
                  <tr key={f.keyword} style={{ borderTop: "1px solid var(--line-faint)" }}>
                    <td className="px-4 py-3.5" style={{ fontSize: "13.5px" }}>{f.keyword}</td>
                    <td className="num px-4 py-3.5"
                        style={{ fontSize: "11px", letterSpacing: ".1em",
                                 color: actionTone[f.action] ?? "var(--ink-3)" }}>
                      {f.action}
                    </td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px" }}>{acosPct(f.acos)}</td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px" }}>{money(f.spend)}</td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px", color: "var(--ink-3)" }}>
                      {money(f.suggested_bid)}
                    </td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px", color: "var(--ink-3)" }}>
                      {f.savings > 0 ? money(f.savings) : "—"}
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
            EVERY FIGURE IN THIS TABLE IS COMPUTED BY THE ENGINE · THE MODEL ONLY WROTE THE WHY
          </p>
        </>
      )}

      {history.length > 0 && (
        <section className="mt-12">
          <div className="flex items-center gap-3">
            <IconPlate name="list" size={32} />
            <h2 className="lbl" style={{ margin: 0 }}>Recent analyses</h2>
          </div>
          <div className="card mt-4">
            {history.map((h, i) => (
              <div key={h.id} className="flex flex-wrap items-center justify-between gap-4 px-6 py-4"
                   style={{ borderTop: i === 0 ? "none" : "1px solid var(--line-faint)" }}>
                <div className="min-w-0">
                  <p className="truncate" style={{ margin: 0, fontSize: "14px" }}>{h.campaign}</p>
                  <p className="num" style={{ margin: "5px 0 0", fontSize: "11.5px", color: "var(--ink-4)" }}>
                    ACOS {acosPct(h.overall_acos)} · WASTED {money(h.wasted_spend)} ·
                    {" "}COULD SAVE {money(h.potential_savings)} · {new Date(h.created_at).toLocaleDateString()}
                  </p>
                </div>
                <span className="num shrink-0" style={{ fontSize: "11px", letterSpacing: ".12em",
                    color: h.severity === "critical" ? "var(--unproven)"
                         : h.severity === "warning" ? "var(--waiting)" : "var(--ink-5)" }}>
                  {h.severity.toUpperCase()}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}

function Stat({ label, value, note, tone }: {
  label: string; value: string; note?: string; tone?: "unproven" | "waiting";
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
