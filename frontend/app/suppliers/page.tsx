"use client";

import { useState } from "react";
import { searchSuppliers, type SupplierResult } from "../../lib/api";
import { Icon, IconPlate } from "../icons";

/* The landed cost is the number no marketplace can give you, and it is the one
   the whole verdict turns on. What this screen produces is still an estimate —
   the cheapest offer plus a freight and duties guess — so it is amber, like
   every other figure that is money you would have to pay rather than money
   anybody has measured. */

const flag: Record<string, string> = { CN: "🇨🇳", VN: "🇻🇳", IN: "🇮🇳" };

export default function SupplierFinder() {
  const [query, setQuery] = useState("silicone baking molds");
  const [data, setData] = useState<SupplierResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSearch() {
    setLoading(true);
    setError(null);
    try {
      setData(await searchSuppliers({ query, limit: 10 }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function useCost(cost: number) {
    localStorage.setItem("ph_prefill", JSON.stringify({ name: query, cogs: cost }));
    window.location.href = "/";
  }

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex items-center gap-3">
          <IconPlate name="truck" />
          <p className="lbl">Suppliers</p>
        </div>
        <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
          What it would actually cost you
        </h1>
        <p style={{ margin: "16px 0 0", maxWidth: "70ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          Find suppliers and get a landed cost — the figure Product Hunter needs and no
          marketplace data can give you. It stays an estimate until you have a quote in hand,
          and the whole verdict on a product moves with it.
        </p>

        <div className="mt-8 flex flex-wrap items-end gap-3">
          <label style={{ display: "block" }}>
            <span className="lbl">Product to source</span>
            <input className="field" style={{ width: "360px", marginTop: "7px" }}
                   value={query}
                   onChange={(e) => setQuery(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && onSearch()}
                   placeholder="silicone baking molds" />
          </label>
          <button onClick={onSearch} disabled={loading}
                  className="btn-primary inline-flex items-center gap-2">
            <Icon name="search" size={13} /> {loading ? "SEARCHING…" : "FIND SUPPLIERS"}
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

      {data && (
        <>
          <p className="card-waiting flex items-start gap-3 px-5 py-4"
             style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                      fontSize: "12.5px", lineHeight: 1.6, color: "var(--ink-2)" }}>
            <Icon name="alert" size={15} stroke="var(--waiting)" className="mt-0.5 shrink-0" />
            {data.note}
          </p>

          {data.suggested_cogs != null && (
            <div className="card-waiting mt-4 flex flex-wrap items-center justify-between gap-5 p-6"
                 style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }}>
              <div>
                <p className="lbl">Suggested landed cost</p>
                <p className="num" style={{ margin: "12px 0 0", fontSize: "32px", fontWeight: 500,
                     letterSpacing: "-.03em", color: "var(--waiting)" }}>
                  ${data.suggested_cogs.toFixed(2)}
                </p>
                <p style={{ margin: "10px 0 0", maxWidth: "52ch", fontSize: "12px", lineHeight: 1.6, color: "var(--ink-4)" }}>
                  The cheapest offer plus an estimate of freight and duties. Nobody has paid it —
                  a real quote will move it, and every margin downstream with it.
                </p>
              </div>
              <button onClick={() => useCost(data.suggested_cogs as number)}
                      className="btn-primary shrink-0 inline-flex items-center gap-2">
                <Icon name="gem" size={13} /> SCORE IT WITH THIS COST
              </button>
            </div>
          )}

          <section className="card mt-4 overflow-x-auto">
            <table className="w-full" style={{ borderCollapse: "collapse", minWidth: "820px" }}>
              <thead>
                <tr style={{ background: "var(--raised)", borderBottom: "1px solid var(--line)" }}>
                  <th className="lbl px-4 py-3.5 text-left">Supplier</th>
                  <th className="lbl px-4 py-3.5 text-right">Unit price</th>
                  <th className="lbl px-4 py-3.5 text-right">Landed cost</th>
                  <th className="lbl px-4 py-3.5 text-right">MOQ</th>
                  <th className="lbl px-4 py-3.5 text-right">Lead time</th>
                  <th className="lbl px-4 py-3.5 text-right">Rating</th>
                  <th className="px-4 py-3.5" />
                </tr>
              </thead>
              <tbody>
                {data.offers.map((o, i) => (
                  <tr key={o.supplier}
                      style={{ borderTop: "1px solid var(--line-faint)",
                               background: i === 0 ? "rgba(251,191,36,.03)" : undefined }}>
                    <td className="px-4 py-3.5" style={{ fontSize: "13.5px" }}>
                      {flag[o.country] ?? ""} {o.supplier}
                      {i === 0 && (
                        <span className="num" style={{ marginLeft: "10px", fontSize: "10px",
                             letterSpacing: ".12em", color: "var(--waiting)" }}>
                          CHEAPEST
                        </span>
                      )}
                    </td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px", color: "var(--ink-3)" }}>
                      ${o.unit_price.toFixed(2)}
                    </td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px", fontWeight: 500 }}>
                      ${o.est_landed_cost.toFixed(2)}
                    </td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px" }}>{o.moq.toLocaleString()}</td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px" }}>{o.lead_time_days}d</td>
                    <td className="num px-4 py-3.5 text-right" style={{ fontSize: "13px" }}>{o.rating.toFixed(1)}★</td>
                    <td className="px-4 py-3.5 text-right">
                      <button onClick={() => useCost(o.est_landed_cost)} className="num whitespace-nowrap"
                              style={{ fontSize: "11px", letterSpacing: ".1em", color: "var(--proven)" }}>
                        USE THIS COST →
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <p className="num" style={{ margin: "14px 0 0", fontSize: "10.5px", letterSpacing: ".1em", color: "var(--ink-5)" }}>
            LANDED COST INCLUDES AN ESTIMATE OF FREIGHT AND DUTIES · CONFIRM IT WITH THE SUPPLIER
          </p>
        </>
      )}
    </main>
  );
}
