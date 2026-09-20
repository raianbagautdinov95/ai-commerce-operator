"use client";

import { useEffect, useState } from "react";
import {
  evaluateProducts,
  evaluateProductsPublic,
  getHistory,
  recordPublicVisit,
  rememberAttribution,
  type Attribution,
  type Evaluation,
  type HistoryItem,
  type ProductRequest,
} from "../lib/api";
import CsvImport from "./CsvImport";
import UnitEconomics from "./UnitEconomics";
import { num, str } from "../lib/csv";
import { Icon, IconPlate } from "../app/icons";

/* ---------------------------------------------------------------------------
   Scoring a product before any of it is real.

   Every figure on this screen descends from numbers somebody typed in — an
   estimated monthly sales volume above all. The engine's arithmetic is exact
   and the verdict is deterministic, but "$4,200 a month" here is a consequence
   of a guess about demand, not a measurement of anything. It is labelled that
   way, and it never travels to the payroll.

   The verdict keeps the three colours because it is the engine's judgment on a
   fixed scale, computed the same way every time. The money next to it does not.
   --------------------------------------------------------------------------- */

const EMPTY: ProductRequest = {
  name: "",
  price: 27,
  cogs: 6.5,
  fba_fee: 3.3,
  monthly_sales: 600,
  referral_rate: 0.15,
  ppc_per_unit: 2.5,
  size_score: 1,
  dominant_brands: 2,
  median_reviews: 180,
  demand_score: 1,
  patent_ok: true,
  cert_score: 1,
};

// Two distinct candidates so the ranked comparison is meaningful out of the box.
const EXAMPLE: ProductRequest[] = [
  { ...EMPTY, name: "Silicone baking molds" },
  {
    ...EMPTY,
    name: "Garden hose 50ft",
    price: 38,
    cogs: 15,
    fba_fee: 8.74,
    monthly_sales: 350,
    ppc_per_unit: 4,
    size_score: 0,
    dominant_brands: 5,
    median_reviews: 900,
    demand_score: 0.5,
  },
];

const verdictTone: Record<string, "proven" | "waiting" | "unproven"> = {
  BUY: "proven", CAUTION: "waiting", AVOID: "unproven",
};

const fields: [keyof ProductRequest, string][] = [
  ["name", "Product name"],
  ["price", "Price $"],
  ["cogs", "Landed cost $"],
  ["fba_fee", "FBA fee $"],
  ["ppc_per_unit", "Ad cost / unit $"],
  ["monthly_sales", "Est. sales / mo"],
  ["referral_rate", "Referral rate"],
  ["size_score", "Size 0-1"],
  ["dominant_brands", "Dominant brands"],
  ["median_reviews", "Median reviews"],
  ["demand_score", "Demand 0-1"],
  ["cert_score", "Cert 0-1"],
];

const pct = (x: number) => `${(x * 100).toFixed(0)}%`;
const money = (x: number | null) => (x == null ? "—" : `$${x.toFixed(2)}`);
// Whole dollars, always "6,320": the fee schedule is Amazon US, and a browser
// set to another locale would otherwise print "6 320,7" under a $ sign.
const dollars = (x: number) => Math.round(x).toLocaleString("en-US");

const criterionLabels: Record<string, string> = {
  margin: "Margin",
  roi: "ROI",
  price: "Price",
  size: "Size",
  competition: "Competition",
  demand: "Demand",
  patent: "Patent",
  certification: "Certification",
};

// How much of a criterion's points were earned, on the same three-colour scale
// the verdict uses — this is the engine grading itself, not a money claim.
const barColour = (s: number) =>
  s >= 0.8 ? "var(--proven)" : s >= 0.5 ? "var(--waiting)" : "var(--unproven)";

/* The same screen answers two audiences. Signed in, it is the Hunter: history,
   CSV import, prefills from Discover, a whole catalogue at once. Public, it is
   the front door: up to five candidates, nothing remembered, and at the end an
   invitation — the verdict a stranger just saw is the product's best argument. */
export const PUBLIC_MAX_CANDIDATES = 5;

export default function ProductFinder({ mode = "app" }: { mode?: "app" | "public" }) {
  const isPublic = mode === "public";
  const [attribution, setAttribution] = useState<Attribution>({});
  const [forms, setForms] = useState<ProductRequest[]>(EXAMPLE);

  // The public page counts its visitors — once, with the source the link
  // carried, and nothing else about them (see the API's visitor_key).
  useEffect(() => {
    if (!isPublic) return;
    const a = rememberAttribution(window.location.search);
    setAttribution(a);
    recordPublicVisit(a);
  }, [isPublic]);
  const [untouched, setUntouched] = useState(true);
  const [results, setResults] = useState<Evaluation[]>([]);
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isPublic) return;
    // Shopify opens the app with a signed launch URL that names the shop. We
    // do not grant access from that value; OAuth still does that. It merely
    // spares the owner from finding and typing their permanent shop address.
    const query = new URLSearchParams(window.location.search);
    const shop = query.get("shop")?.trim().toLowerCase();
    if (!query.get("hmac") || !shop || !/^[a-z0-9][a-z0-9-]{0,58}\.myshopify\.com$/.test(shop)) return;
    const key = `shopify-launch-seen:${shop}`;
    if (window.sessionStorage.getItem(key)) return;
    window.sessionStorage.setItem(key, "1");
    window.location.replace(`/integrations/shopify?from=shopify&shop=${encodeURIComponent(shop)}`);
  }, [isPublic]);

  const setField = (i: number, k: keyof ProductRequest, v: string | boolean) => {
    setUntouched(false);
    setForms((fs) =>
      fs.map((f, j) =>
        j === i ? { ...f, [k]: typeof v === "boolean" ? v : Number.isNaN(+v) ? v : +v } : f,
      ),
    );
  };

  const atPublicCap = isPublic && forms.length >= PUBLIC_MAX_CANDIDATES;

  const addProduct = () => {
    if (atPublicCap) return;
    setUntouched(false);
    setForms((fs) => [...fs, { ...EMPTY, name: `Candidate ${fs.length + 1}` }]);
  };

  const removeProduct = (i: number) => {
    setUntouched(false);
    setForms((fs) => (fs.length > 1 ? fs.filter((_, j) => j !== i) : fs));
  };

  const clearExample = () => {
    setUntouched(false);
    setForms([{ ...EMPTY, name: "" }]);
  };

  function importForms(raw: Record<string, string>[]): number {
    const has = (r: Record<string, string>, a: string[]) =>
      a.some((k) => r[k] != null && r[k] !== "");
    const pick = (r: Record<string, string>, a: string[], fallback: number) =>
      has(r, a) ? num(r, a) : fallback;
    const mapped: ProductRequest[] = raw
      .map((r) => {
        const name = str(r, ["name", "product name", "product", "title"]);
        if (!name) return null;
        const patentCol = str(r, ["patent_ok", "patent ok", "no patent risk"]).toLowerCase();
        return {
          ...EMPTY,
          name,
          price: pick(r, ["price", "selling price"], EMPTY.price),
          cogs: pick(r, ["cogs", "landed cost", "cost", "unit cost"], EMPTY.cogs),
          fba_fee: pick(r, ["fba_fee", "fba fee", "fulfillment fee"], EMPTY.fba_fee),
          monthly_sales: pick(r, ["monthly_sales", "monthly sales", "sales/mo", "units/mo"], EMPTY.monthly_sales),
          referral_rate: pick(r, ["referral_rate", "referral rate"], EMPTY.referral_rate!),
          ppc_per_unit: pick(r, ["ppc_per_unit", "ad cost/unit", "ppc", "ad cost"], EMPTY.ppc_per_unit!),
          size_score: pick(r, ["size_score", "size"], EMPTY.size_score!),
          dominant_brands: pick(r, ["dominant_brands", "dominant brands", "brands"], EMPTY.dominant_brands!),
          median_reviews: pick(r, ["median_reviews", "median reviews", "reviews"], EMPTY.median_reviews!),
          demand_score: pick(r, ["demand_score", "demand"], EMPTY.demand_score!),
          cert_score: pick(r, ["cert_score", "cert", "certification"], EMPTY.cert_score!),
          patent_ok: patentCol ? ["true", "yes", "1", "ok"].includes(patentCol) : EMPTY.patent_ok,
        } as ProductRequest;
      })
      .filter((p): p is ProductRequest => p !== null);
    if (mapped.length) {
      setForms(mapped);
      setUntouched(false);
    }
    return mapped.length;
  }

  async function loadHistory() {
    try {
      setHistory(await getHistory(10));
    } catch {
      /* history is non-critical; ignore load errors */
    }
  }

  useEffect(() => {
    if (isPublic) return;
    loadHistory();
    // Prefill from Discover: a whole list ("Compare all") or a single product.
    try {
      const multi = localStorage.getItem("ph_prefill_multi");
      const single = localStorage.getItem("ph_prefill");
      if (multi) {
        localStorage.removeItem("ph_prefill_multi");
        const list = JSON.parse(multi) as Partial<ProductRequest>[];
        if (list.length) { setForms(list.map((p) => ({ ...EMPTY, ...p }))); setUntouched(false); }
      } else if (single) {
        localStorage.removeItem("ph_prefill");
        const p = JSON.parse(single) as Partial<ProductRequest>;
        setForms([{ ...EMPTY, ...p }]);
        setUntouched(false);
      }
    } catch {
      /* ignore malformed prefill */
    }
  }, [isPublic]);

  async function onEvaluate() {
    setLoading(true);
    setError(null);
    try {
      const { results: r, weights: w } = isPublic
        ? await evaluateProductsPublic(forms, true, attribution)
        : await evaluateProducts(forms, true);
      setResults(r);
      setWeights(w);
      if (!isPublic) loadHistory();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex items-center gap-3">
          <IconPlate name="gem" />
          <p className="lbl">{isPublic ? "Free product check · no account" : "Product hunter"}</p>
        </div>
        <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
          Is it worth selling, before you buy any
        </h1>
        <p style={{ margin: "16px 0 0", maxWidth: "70ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          {isPublic
            ? `Type in a product you are thinking of buying — price, landed cost, FBA fee, how many
              you expect to sell. The engine computes margin, ROI and profit per unit, gives a
              BUY / CAUTION / AVOID verdict on a fixed scale, and explains it. Up to
              ${PUBLIC_MAX_CANDIDATES} products at a time; nothing you type is stored.`
            : `Add one candidate or twenty. The engine computes the economics and the verdict on a
              fixed scale, ranks them best first, and the model writes the explanation. Every figure
              descends from the sales estimate you enter — the arithmetic is exact, the demand is
              still a guess.`}
        </p>
      </section>

      {untouched && (
        <div className="card-waiting mb-5 flex flex-wrap items-center justify-between gap-4 px-5 py-4"
             style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }}>
          <span className="flex items-start gap-3" style={{ fontSize: "13px", lineHeight: 1.6, color: "var(--ink-2)" }}>
            <Icon name="alert" size={17} stroke="var(--waiting)" className="mt-0.5 shrink-0" />
            These two candidates are an example, not products you are looking at. Any verdict
            below is about them until you enter your own.
          </span>
          <button onClick={clearExample} className="btn-quiet shrink-0">CLEAR IT</button>
        </div>
      )}

      <div className="space-y-4">
        {forms.map((form, i) => (
          <div key={i} className="card p-6">
            <div className="mb-5 flex items-center justify-between">
              <span className="lbl">Candidate {i + 1}</span>
              {forms.length > 1 && (
                <button onClick={() => removeProduct(i)} className="num"
                        style={{ fontSize: "11px", letterSpacing: ".08em", color: "var(--ink-5)" }}>
                  REMOVE
                </button>
              )}
            </div>
            <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))" }}>
              {fields.map(([k, label]) => (
                <label key={k} style={{ display: "block" }}>
                  <span className="lbl">{label}</span>
                  <input className={k === "name" ? "field" : "field num"}
                         style={{ marginTop: "7px" }}
                         value={String(form[k] ?? "")}
                         onChange={(e) => setField(i, k, e.target.value)} />
                </label>
              ))}
              <label className="flex cursor-pointer items-center gap-2.5 self-end pb-3">
                <input type="checkbox" checked={!!form.patent_ok}
                       onChange={(e) => setField(i, "patent_ok", e.target.checked)}
                       style={{ accentColor: "#4ADE80", width: "15px", height: "15px" }} />
                <span style={{ fontSize: "13px", color: "var(--ink-2)" }}>No patent risk</span>
              </label>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <button onClick={onEvaluate} disabled={loading}
                className="btn-primary inline-flex items-center gap-2">
          <Icon name="gem" size={13} />
          {loading ? "EVALUATING…" : forms.length > 1 ? "EVALUATE ALL" : "EVALUATE"}
        </button>
        <button onClick={addProduct} className="btn-quiet" disabled={atPublicCap}
                title={atPublicCap ? `Up to ${PUBLIC_MAX_CANDIDATES} here — sign in for a whole catalogue` : undefined}>
          + ADD PRODUCT
        </button>
        {atPublicCap && (
          <span className="num" style={{ fontSize: "10.5px", letterSpacing: ".1em", color: "var(--ink-5)" }}>
            {PUBLIC_MAX_CANDIDATES} IS THE LIMIT HERE · <a href="/signin" style={{ color: "var(--ink-3)" }}>SIGN IN</a> FOR A WHOLE CATALOGUE
          </span>
        )}
      </div>

      {!isPublic && (
        <CsvImport
          hint="Paste a table with name, price, cogs, fba_fee, monthly_sales, ppc_per_unit, dominant_brands and median_reviews. Anything missing falls back to a default."
          onRows={importForms}
        />
      )}

      {error && (
        <p className="card-unproven mt-4 flex items-start gap-3 px-5 py-4"
           style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                    fontSize: "13.5px", color: "var(--unproven)" }}>
          <Icon name="alert" size={17} className="mt-0.5 shrink-0" /> {error}
        </p>
      )}

      {results.length > 1 && (
        <section className="mt-10">
          <div className="flex items-center gap-3">
            <IconPlate name="list" size={32} />
            <h2 className="lbl" style={{ margin: 0 }}>Ranked best first</h2>
          </div>
          <div className="card mt-4 overflow-x-auto">
            <table className="w-full" style={{ borderCollapse: "collapse", minWidth: "720px" }}>
              <thead>
                <tr style={{ background: "var(--raised)", borderBottom: "1px solid var(--line)" }}>
                  <th className="lbl px-4 py-3.5 text-left">#</th>
                  <th className="lbl px-4 py-3.5 text-left">Product</th>
                  <th className="lbl px-4 py-3.5 text-left">Verdict</th>
                  <th className="lbl px-4 py-3.5 text-right">Score</th>
                  <th className="lbl px-4 py-3.5 text-right">Margin</th>
                  <th className="lbl px-4 py-3.5 text-right">ROI</th>
                  <th className="lbl px-4 py-3.5 text-right">Profit / mo</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={r.name}
                      style={{ borderTop: "1px solid var(--line-faint)",
                               background: i === 0 ? "rgba(74,222,128,.035)" : undefined }}>
                    <td className="num px-4 py-3.5" style={{ fontSize: "12px", color: "var(--ink-5)" }}>{i + 1}</td>
                    <td className="px-4 py-3.5" style={{ fontSize: "13.5px" }}>{r.name}</td>
                    <td className="num px-4 py-3.5"
                        style={{ fontSize: "11px", letterSpacing: ".1em",
                                 color: `var(--${verdictTone[r.verdict] ?? "waiting"})` }}>
                      {r.verdict}
                    </td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px" }}>{r.score}</td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px" }}>{pct(r.economics.margin)}</td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px" }}>{pct(r.economics.roi)}</td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px", color: "var(--ink-3)" }}>
                      ~${dollars(r.economics.monthly_profit)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="num" style={{ margin: "12px 0 0", fontSize: "10.5px", letterSpacing: ".1em", color: "var(--ink-5)" }}>
            PROFIT PER MONTH IS PROJECTED FROM THE SALES ESTIMATE YOU ENTERED · NOT MEASURED
          </p>
        </section>
      )}

      {results.map((r) => (
        <section key={r.name} className="card mt-5 p-6 sm:p-8">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <h2 style={{ margin: 0, fontSize: "20px", fontWeight: 600, letterSpacing: "-.015em" }}>{r.name}</h2>
            <span className={`num card-${verdictTone[r.verdict] ?? "waiting"}`}
                  style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r-sm)",
                           padding: "8px 14px", fontSize: "11.5px", letterSpacing: ".12em",
                           color: `var(--${verdictTone[r.verdict] ?? "waiting"})` }}>
              {r.verdict} · {r.score}/100
            </span>
          </div>

          <div className="mt-6 flex flex-wrap gap-x-9 gap-y-3">
            <Inline label="Profit / unit" value={`$${r.economics.profit_per_unit}`} />
            <Inline label="Margin" value={pct(r.economics.margin)} />
            <Inline label="ROI" value={pct(r.economics.roi)} />
            <Inline label="Per month" value={`~$${dollars(r.economics.monthly_profit)}`} dim />
          </div>

          {r.explanation && (
            <p style={{ margin: "22px 0 0", paddingTop: "20px", borderTop: "1px solid var(--line-faint)",
                        fontSize: "13.5px", lineHeight: 1.75, color: "var(--ink-2)" }}>
              {r.explanation}
            </p>
          )}

          {r.inputs && (
            <div className="mt-6">
              <UnitEconomics
                price={r.inputs.price}
                cogs={r.inputs.cogs}
                referral={r.economics.referral_fee}
                fba={r.economics.fba_fee_total}
                ads={r.inputs.ppc_per_unit ?? 0}
                profit={r.economics.profit_per_unit}
              />
            </div>
          )}

          <div className="mt-8 grid gap-8 lg:grid-cols-2">
            <div>
              <p className="lbl">Score breakdown</p>
              <ul className="mt-4 space-y-3">
                {Object.keys(weights).map((k) => {
                  const s = r.subscores[k] ?? 0;
                  return (
                    <li key={k}>
                      <div className="flex justify-between" style={{ fontSize: "12.5px", color: "var(--ink-2)" }}>
                        <span>{criterionLabels[k] ?? k}</span>
                        <span className="num" style={{ color: "var(--ink-4)" }}>
                          {Math.round(weights[k] * s)}/{weights[k]}
                        </span>
                      </div>
                      <div className="mt-2 overflow-hidden"
                           style={{ height: "5px", borderRadius: "999px", background: "var(--raised)" }}>
                        <div style={{ height: "100%", width: `${Math.round(s * 100)}%`,
                                      borderRadius: "999px", background: barColour(s), opacity: .8 }} />
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>

            <div>
              <p className="lbl">What-if — the safety cushion</p>
              <dl className="mt-4 space-y-3">
                <WhatIf label="Break-even price (profit $0)" value={money(r.whatif.break_even_price)} />
                <WhatIf label="Min price at a 15% floor" value={money(r.whatif.price_for_floor)} />
                <WhatIf label="Price for a 30% target" value={money(r.whatif.price_for_target)} />
                <WhatIf label="Max landed cost, staying ≥15%" value={money(r.whatif.max_cogs_at_floor)} />
              </dl>
            </div>
          </div>

          <div className="mt-8 grid gap-6 md:grid-cols-2">
            <div>
              <p className="num flex items-center gap-2" style={{ margin: 0, fontSize: "10.5px", letterSpacing: ".16em", color: "var(--proven)" }}>
                <Icon name="check" size={13} /> IN ITS FAVOUR
              </p>
              <ul className="mt-3 space-y-2">
                {r.pros.map((x) => (
                  <li key={x} style={{ fontSize: "13px", lineHeight: 1.6, color: "var(--ink-2)" }}>{x}</li>
                ))}
              </ul>
            </div>
            <div>
              <p className="num flex items-center gap-2" style={{ margin: 0, fontSize: "10.5px", letterSpacing: ".16em", color: "var(--unproven)" }}>
                <Icon name="alert" size={13} /> AGAINST IT
              </p>
              <ul className="mt-3 space-y-2">
                {r.risks.map((x) => (
                  <li key={x} style={{ fontSize: "13px", lineHeight: 1.6, color: "var(--ink-2)" }}>{x}</li>
                ))}
              </ul>
            </div>
          </div>
        </section>
      ))}

      {isPublic && results.length > 0 && <PublicInvitation />}

      {!isPublic && history.length > 0 && (
        <section className="mt-12">
          <div className="flex items-center gap-3">
            <IconPlate name="clock" size={32} />
            <h2 className="lbl" style={{ margin: 0 }}>Recent evaluations</h2>
          </div>
          <div className="card mt-4">
            {history.map((h, i) => (
              <div key={h.id} className="flex flex-wrap items-center justify-between gap-4 px-6 py-4"
                   style={{ borderTop: i === 0 ? "none" : "1px solid var(--line-faint)" }}>
                <div className="min-w-0">
                  <p className="truncate" style={{ margin: 0, fontSize: "14px" }}>{h.name}</p>
                  <p className="num" style={{ margin: "5px 0 0", fontSize: "11.5px", color: "var(--ink-4)" }}>
                    MARGIN {pct(h.economics.margin)} · ~${dollars(h.economics.monthly_profit)}/MO ·
                    {" "}{new Date(h.created_at).toLocaleDateString()}
                  </p>
                </div>
                <span className="num shrink-0" style={{ fontSize: "11px", letterSpacing: ".12em",
                    color: `var(--${verdictTone[h.verdict] ?? "waiting"})` }}>
                  {h.verdict} · {h.score}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}

function Inline({ label, value, dim }: { label: string; value: string; dim?: boolean }) {
  return (
    <span className="flex items-baseline gap-2.5">
      <span className="lbl">{label}</span>
      <span className="num" style={{ fontSize: "14px", color: dim ? "var(--ink-3)" : "var(--ink)" }}>
        {value}
      </span>
    </span>
  );
}

function WhatIf({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt style={{ fontSize: "12.5px", color: "var(--ink-3)" }}>{label}</dt>
      <dd className="num" style={{ margin: 0, fontSize: "13px" }}>{value}</dd>
    </div>
  );
}

/* What a stranger sees after their first verdict. No promise of returns: the
   Operator proposes, a person approves, and a measurement afterwards says what
   it was worth. That is the pitch, and it is also the truth. */
function PublicInvitation() {
  return (
    <section className="card-proven mt-10 px-6 py-7 sm:px-8"
             style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }}
             data-testid="public-invitation">
      <p className="lbl" style={{ color: "var(--proven)" }}>That was one product, by hand</p>
      <h2 style={{ margin: "12px 0 0", fontSize: "24px", fontWeight: 600, letterSpacing: "-.02em" }}>
        The Operator does this across your whole Shopify catalogue, every day
      </h2>
      <p style={{ margin: "14px 0 0", maxWidth: "68ch", fontSize: "14px", lineHeight: 1.7, color: "var(--ink-2)" }}>
        Connect a store read-only and it imports your orders, products and stock, scores every
        product on the same scale you just saw, and proposes the changes worth making — a price,
        a reorder, a product to drop. It never touches your shop without your approval, and it
        measures afterwards, from your own numbers, whether a change earned anything.
      </p>
      <div className="mt-6 flex flex-wrap items-center gap-3">
        <a href="/signin" className="btn-primary inline-flex items-center gap-2">
          <Icon name="lock" size={13} /> START THE FREE PILOT
        </a>
        <a href="/pricing" className="btn-quiet">WHAT IT COSTS</a>
        <span className="num" style={{ fontSize: "10.5px", letterSpacing: ".1em", color: "var(--ink-5)" }}>
          NO CARD · SHOPIFY READ-ONLY · CANCEL ANY TIME
        </span>
      </div>
    </section>
  );
}
