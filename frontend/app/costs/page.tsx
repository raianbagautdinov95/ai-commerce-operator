"use client";

/**
 * What each product costs to buy — the number that turns a measured restock
 * into a measured result.
 *
 * The screen is built around one fact: an absent cost is not a zero. Without
 * it a restock can be shown to have sold ten units that could not otherwise
 * have sold, and still cannot say what that was worth. So the products that
 * are blocking a measurement come first, each one saying which measurement.
 *
 * Where a figure came from is never flattened away. Shopify's own number is
 * marked as read from the shop; a figure somebody typed is marked as reported,
 * because nobody checked it. Both are usable. Only one is evidence.
 */

import { useCallback, useEffect, useState } from "react";
import {
  getProductCosts, setProductCost,
  type ProductCostEntry, type ProductCosts,
} from "../../lib/api";
import { Icon, IconPlate } from "../icons";

function money(value: number, currency: string) {
  return new Intl.NumberFormat(undefined, {
    style: "currency", currency, maximumFractionDigits: 4,
  }).format(value);
}

function humanError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message || "The server did not answer.";
}

/** Everything standing between this variant and a priced result. */
function blockers(entry: ProductCostEntry): string[] {
  const out: string[] = [];
  if (!entry.attributable_to_variant) {
    out.push("Shopify did not say which variant these units were, so no cost "
      + "can honestly be attached to them.");
  }
  if (entry.unit_cost === null) {
    out.push("No unit cost on record.");
  } else if (!entry.currency_matches) {
    out.push(`The cost is in ${entry.cost_currency} and the sales are in `
      + `${entry.sale_currency}. Nothing here converts between them.`);
  }
  return out;
}

function Provenance({ entry }: { entry: ProductCostEntry }) {
  if (entry.unit_cost === null) {
    return (
      <span className="num" style={{ fontSize: "11.5px", color: "var(--waiting)" }}>
        NOT KNOWN
      </span>
    );
  }
  const reported = entry.verification === "reported";
  return (
    <span className="num" style={{ fontSize: "11.5px", color: reported ? "var(--ink-4)" : "var(--proven)" }}>
      {reported ? "REPORTED BY YOU" : "READ FROM SHOPIFY"}
      {entry.applies_to_every_variant && " · APPLIES TO EVERY VARIANT"}
      {entry.effective_from && ` · FROM ${entry.effective_from}`}
    </span>
  );
}

function CostForm({ entry, currency, onSaved }: {
  entry: ProductCostEntry;
  currency: string;
  onSaved: () => void;
}) {
  const [purchase, setPurchase] = useState("");
  const [extra, setExtra] = useState("");
  const [from, setFrom] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await setProductCost({
        product_id: entry.product_id,
        variant_id: entry.variant_id,
        variant_title: entry.variant_title,
        purchase_amount: Number(purchase),
        extra_amount: extra ? Number(extra) : 0,
        currency: entry.sale_currency || currency,
        effective_from: from || undefined,
      });
      setPurchase(""); setExtra(""); setFrom("");
      onSaved();
    } catch (e) {
      setError(humanError(e));
    } finally {
      setBusy(false);
    }
  }

  const label = { display: "block", fontSize: "11px", letterSpacing: ".08em",
                  color: "var(--ink-4)", marginBottom: "4px" } as const;
  const field = { width: "100%", background: "#05070A", border: "1px solid var(--line)",
                  borderRadius: "8px", padding: "8px 10px", fontSize: "13px",
                  color: "var(--ink-1)" } as const;

  return (
    <form onSubmit={save} className="mt-4">
      <div className="flex flex-wrap gap-3">
        <div style={{ minWidth: "150px", flex: "1 1 150px" }}>
          <label className="num" style={label} htmlFor={`buy-${entry.variant_id}`}>
            PAID PER UNIT
          </label>
          <input id={`buy-${entry.variant_id}`} required type="number" min="0" step="0.0001"
                 value={purchase} onChange={(e) => setPurchase(e.target.value)}
                 style={field} placeholder="0.00" />
        </div>
        <div style={{ minWidth: "150px", flex: "1 1 150px" }}>
          <label className="num" style={label} htmlFor={`extra-${entry.variant_id}`}>
            EVERYTHING ELSE PER UNIT
          </label>
          <input id={`extra-${entry.variant_id}`} type="number" min="0" step="0.0001"
                 value={extra} onChange={(e) => setExtra(e.target.value)}
                 style={field} placeholder="freight, duty, packing" />
        </div>
        <div style={{ minWidth: "150px", flex: "1 1 150px" }}>
          <label className="num" style={label} htmlFor={`from-${entry.variant_id}`}>
            TRUE FROM
          </label>
          <input id={`from-${entry.variant_id}`} type="date" value={from}
                 onChange={(e) => setFrom(e.target.value)} style={field} />
        </div>
      </div>
      <p style={{ margin: "8px 0 0", fontSize: "12px", lineHeight: 1.6, color: "var(--ink-4)" }}>
        Recorded in {entry.sale_currency}, the currency these units sold in. A date
        in the past prices the sales from that day onwards; results already
        measured are never restated.
      </p>
      {error && (
        <p style={{ margin: "8px 0 0", fontSize: "12.5px", color: "var(--unproven)" }}>{error}</p>
      )}
      <button type="submit" disabled={busy || !purchase}
              className="num mt-3"
              style={{ border: "1px solid var(--line)", borderRadius: "999px",
                       padding: "7px 16px", fontSize: "11.5px", letterSpacing: ".1em",
                       opacity: busy || !purchase ? 0.5 : 1 }}>
        {busy ? "SAVING…" : "RECORD THIS COST"}
      </button>
    </form>
  );
}

export default function ProductCostsPage() {
  const [data, setData] = useState<ProductCosts | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await getProductCosts());
      setError(null);
    } catch (e) {
      setError(humanError(e));
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const entries = data?.entries ?? [];
  // Anything holding up a measurement first: that is the only thing on this
  // screen with a cost of its own for leaving it undone.
  const ordered = [...entries].sort((a, b) => {
    const weight = (entry: ProductCostEntry) =>
      (entry.waiting_measurements.length > 0 && blockers(entry).length > 0 ? 0 : 1);
    return weight(a) - weight(b) || b.units_sold - a.units_sold;
  });

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex items-center gap-3">
          <IconPlate name="tag" />
          <p className="lbl">Costs</p>
        </div>
        <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
          What your products cost you
        </h1>
        <p style={{ margin: "16px 0 0", maxWidth: "68ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          A restock can be proven to have sold units that could not otherwise
          have sold, and still not say what that was worth. This is the missing
          half. Where Shopify holds a unit cost it is read automatically; where
          it does not, this is where you say.
        </p>
      </section>

      {error && (
        <p className="card-waiting p-5" style={{ borderWidth: "1px", borderStyle: "solid",
             borderRadius: "var(--r)", fontSize: "13.5px", color: "var(--ink-2)" }}>
          {error}
        </p>
      )}

      {data && data.blocking > 0 && (
        <p className="num" style={{ margin: "0 0 18px", fontSize: "12px", color: "var(--waiting)" }}>
          <Icon name="alert" size={14} />{" "}
          {data.blocking} PRODUCT(S) ARE HOLDING UP A MEASUREMENT
        </p>
      )}

      {data && entries.length === 0 && (
        <p style={{ fontSize: "14px", lineHeight: 1.7, color: "var(--ink-3)" }}>
          Nothing has sold in the last 90 days, so there is nothing to cost yet.
        </p>
      )}

      <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
        {ordered.map((entry) => {
          const stuck = blockers(entry);
          return (
            <li key={`${entry.product_id}:${entry.variant_id ?? "none"}`}
                className="mb-4 p-5"
                style={{ background: "var(--raised)", borderRadius: "var(--r)",
                         border: "1px solid var(--line-faint)" }}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div>
                  <p style={{ margin: 0, fontSize: "15px", fontWeight: 600 }}>
                    {entry.product_title}
                    {entry.variant_title && (
                      <span style={{ color: "var(--ink-3)", fontWeight: 400 }}>
                        {" · "}{entry.variant_title}
                      </span>
                    )}
                  </p>
                  <p className="num" style={{ margin: "4px 0 0", fontSize: "11.5px", color: "var(--ink-4)" }}>
                    {entry.units_sold} SOLD · LAST {entry.last_sold}
                  </p>
                </div>
                <div style={{ textAlign: "right" }}>
                  <p className="num" style={{ margin: 0, fontSize: "15px" }}>
                    {entry.unit_cost === null
                      ? "—"
                      : money(entry.unit_cost, entry.cost_currency || entry.sale_currency)}
                  </p>
                  <Provenance entry={entry} />
                </div>
              </div>

              {entry.waiting_measurements.length > 0 && (
                <p style={{ margin: "10px 0 0", fontSize: "12.5px", lineHeight: 1.6,
                            color: "var(--ink-3)" }}>
                  Waiting on this: {entry.waiting_measurements.join(", ")}.
                </p>
              )}

              {stuck.map((reason) => (
                <p key={reason} style={{ margin: "8px 0 0", fontSize: "12.5px",
                                         lineHeight: 1.6, color: "var(--waiting)" }}>
                  {reason}
                </p>
              ))}

              {entry.attributable_to_variant && (
                <CostForm entry={entry} currency={data?.currency ?? "USD"}
                          onSaved={refresh} />
              )}
            </li>
          );
        })}
      </ul>
    </main>
  );
}
