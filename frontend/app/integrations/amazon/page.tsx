"use client";

import { useEffect, useState } from "react";
import {
  beginAmazonAuthorization, getAmazonConnection, getAmazonSyncStatus,
  getAmazonSalesSummary, saveAmazonSkuCosts, startAmazonFinancesSync, startAmazonSalesReport, startAmazonSync, waitForBackgroundJob,
  type AmazonConnection, type AmazonSalesSummary, type AmazonSkuCost, type AmazonSyncStatus,
} from "../../../lib/api";
import CsvImport from "../../../components/CsvImport";
import { num, str } from "../../../lib/csv";
import { Icon, IconPlate } from "../../icons";

export default function AmazonIntegrationPage() {
  const [connection, setConnection] = useState<AmazonConnection | null>(null);
  const [sync, setSync] = useState<AmazonSyncStatus | null>(null);
  const [sales, setSales] = useState<AmazonSalesSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [costDraft, setCostDraft] = useState<AmazonSkuCost[]>([]);
  const [costMessage, setCostMessage] = useState<string | null>(null);

  async function refresh() {
    const [account, status] = await Promise.all([getAmazonConnection(), getAmazonSyncStatus()]);
    setConnection(account); setSync(status);
    const marketplaceId = marketplaceIds(account)[0];
    if (marketplaceId) setSales(await getAmazonSalesSummary(marketplaceId));
  }
  useEffect(() => { refresh().catch((e) => setError((e as Error).message)); }, []);

  async function connect() {
    setBusy(true); setError(null);
    try { window.location.assign(await beginAmazonAuthorization()); }
    catch (e) { setError((e as Error).message); setBusy(false); }
  }
  async function runSync() {
    setBusy(true); setError(null);
    try {
      const job = await startAmazonSync("EU");
      await waitForBackgroundJob(job.job_id, 10 * 60_000);
      await refresh();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function runSalesReport() {
    const marketplaceId = connection ? marketplaceIds(connection)[0] : undefined;
    if (!marketplaceId) { setError("No active Amazon marketplace was found."); return; }
    setBusy(true); setError(null);
    try {
      const job = await startAmazonSalesReport(marketplaceId);
      await waitForBackgroundJob(job.job_id, 12 * 60_000);
      setSales(await getAmazonSalesSummary(marketplaceId));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function runFinancesSync() {
    const marketplaceId = connection ? marketplaceIds(connection)[0] : undefined;
    if (!marketplaceId) { setError("No active Amazon marketplace was found."); return; }
    setBusy(true); setError(null);
    try {
      const job = await startAmazonFinancesSync(marketplaceId);
      await waitForBackgroundJob(job.job_id, 12 * 60_000);
      await refresh();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  function loadCosts(rows: Record<string, string>[]) {
    const parsed = rows.map((row) => ({
      sku: str(row, ["sku", "seller sku", "seller_sku"]),
      landed_cost: num(row, ["landed_cost", "landed cost", "cogs", "cost"]),
      currency: str(row, ["currency", "currency code"]) || "USD",
    })).filter((row) => row.sku && row.landed_cost >= 0);
    setCostDraft(parsed); setCostMessage(null); return parsed.length;
  }
  async function saveCosts() {
    const marketplaceId = connection ? marketplaceIds(connection)[0] : undefined;
    if (!marketplaceId || !costDraft.length) return;
    setBusy(true); setError(null);
    try {
      const result = await saveAmazonSkuCosts(marketplaceId, costDraft);
      setCostMessage(`Saved landed COGS for ${result.results.length} SKU(s).`);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  const connected = Boolean(connection?.connected);

  return (
    <main className="mx-auto px-6 pb-20 lg:px-11" style={{ maxWidth: "900px" }}>
      <section className="pb-9 pt-11">
        <a href="/integrations" className="num inline-flex items-center gap-2"
           style={{ fontSize: "11px", letterSpacing: ".1em", color: "var(--ink-4)" }}>
          <Icon name="chevron" size={13} className="rotate-90" /> ALL CHANNELS
        </a>
        <div className="mt-7 flex items-center gap-4">
          <span className="num flex items-center justify-center"
                style={{ width: "52px", height: "52px", borderRadius: "var(--r)",
                         border: "1px solid var(--line)", background: "var(--raised)",
                         fontSize: "20px", color: "var(--ink-2)" }}>
            a
          </span>
          <div>
            <p className="lbl">Sales channel</p>
            <h1 style={{ margin: "6px 0 0", fontSize: "28px", fontWeight: 600, letterSpacing: "-.025em" }}>
              Amazon
            </h1>
          </div>
        </div>
        <p style={{ margin: "20px 0 0", maxWidth: "68ch", fontSize: "14.5px", lineHeight: 1.7, color: "var(--ink-2)" }}>
          A read-only import of listings, FBA inventory, sales and the fees Amazon actually
          charged. Nothing is published and nothing is changed.
        </p>
      </section>

      {error && (
        <p className="card-unproven mb-5 flex items-start gap-3 px-5 py-4"
           style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                    fontSize: "13.5px", color: "var(--unproven)" }}>
          <Icon name="alert" size={17} className="mt-0.5 shrink-0" /> {error}
        </p>
      )}

      <section className="card p-6 sm:p-7">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="lbl">Connection</p>
            <h2 className="flex items-center gap-2.5" style={{ margin: "9px 0 0", fontSize: "19px", fontWeight: 600 }}>
              <span className={`dot ${connected ? "dot-proven" : ""}`}
                    style={connected ? undefined : { background: "var(--ink-6)", boxShadow: "none" }} />
              {connected ? "Connected" : "Not connected"}
            </h2>
            {connection?.seller_id && (
              <p className="num" style={{ margin: "9px 0 0", fontSize: "12.5px", color: "var(--ink-3)" }}>
                SELLER {connection.seller_id}
              </p>
            )}
          </div>
          {!connected && (
            <button disabled={busy} onClick={connect}
                    className="btn-primary shrink-0 inline-flex items-center gap-2">
              <Icon name="plug" size={13} /> CONNECT AMAZON
            </button>
          )}
        </div>
      </section>

      {connected && (
        <div className="mt-4 space-y-4">
          <Row
            icon="box"
            title="Listings and inventory"
            detail={`Last run ${sync?.status ?? "never"} · ${sync?.listings ?? 0} listings · ${sync?.inventory ?? 0} inventory rows`}
            footnote={sync?.completed_at ? `Completed ${new Date(sync.completed_at).toLocaleString()}` : undefined}
            action={<button disabled={busy} onClick={runSync} className="btn-quiet shrink-0">
              {busy ? "SYNCING…" : "SYNC NOW"}
            </button>}
          />

          <section className="card p-6 sm:p-7">
            <div className="flex flex-wrap items-start justify-between gap-5">
              <div className="flex gap-4">
                <IconPlate name="revenue" size={34} />
                <div>
                  <p style={{ margin: 0, fontSize: "15px", fontWeight: 600 }}>Sales and traffic · 30 days</p>
                  <p style={{ margin: "7px 0 0", fontSize: "12.5px", lineHeight: 1.6, color: "var(--ink-3)" }}>
                    Aggregated read-only metrics. No orders and no customer data are stored.
                  </p>
                  {sales && (
                    <>
                      <p className="num" style={{ margin: "18px 0 0", fontSize: "26px", fontWeight: 500, letterSpacing: "-.03em" }}>
                        {sales.total_sales.toLocaleString(undefined, { style: "currency", currency: sales.currency ?? "USD" })}
                      </p>
                      <p className="num" style={{ margin: "8px 0 0", fontSize: "11.5px", color: "var(--ink-4)" }}>
                        {sales.total_units} UNITS · {sales.total_order_items} ORDER ITEMS · {sales.results.length} DAYS
                      </p>
                    </>
                  )}
                  {sales?.last_synced_at && (
                    <p className="num" style={{ margin: "8px 0 0", fontSize: "11px", color: "var(--ink-5)" }}>
                      UPDATED {new Date(sales.last_synced_at).toLocaleString()}
                    </p>
                  )}
                </div>
              </div>
              <button disabled={busy} onClick={runSalesReport} className="btn-quiet shrink-0">
                {busy ? "IMPORTING…" : "IMPORT REPORT"}
              </button>
            </div>
          </section>

          <Row
            icon="margin"
            title="The fees Amazon actually charged"
            detail="Read-only Finance transactions, so profit rests on real fees rather than an estimate. Needs the Finance and Accounting role on the connection."
            action={<button disabled={busy} onClick={runFinancesSync} className="btn-quiet shrink-0">
              {busy ? "IMPORTING…" : "IMPORT FEES"}
            </button>}
          />

          <section className="card p-6 sm:p-7">
            <div className="flex gap-4">
              <IconPlate name="truck" size={34} tone="waiting" />
              <div>
                <p style={{ margin: 0, fontSize: "15px", fontWeight: 600 }}>Landed cost per SKU</p>
                <p style={{ margin: "7px 0 0", maxWidth: "70ch", fontSize: "12.5px", lineHeight: 1.65, color: "var(--ink-3)" }}>
                  Supplier price plus freight, per SKU. Amazon cannot tell anyone this, and without
                  it profit stays withheld everywhere in the app rather than being guessed at.
                  Columns: SKU, landed_cost, currency.
                </p>
              </div>
            </div>

            <CsvImport hint="Paste SKU, landed_cost and currency columns from a spreadsheet." onRows={loadCosts} />

            {costDraft.length > 0 && (
              <div className="mt-5 flex flex-wrap items-center gap-4">
                <button disabled={busy} onClick={saveCosts} className="btn-primary">
                  SAVE {costDraft.length} COST{costDraft.length === 1 ? "" : "S"}
                </button>
                <span className="num" style={{ fontSize: "11.5px", color: "var(--ink-4)" }}>
                  {costDraft.slice(0, 3).map((row) => `${row.sku} ${row.landed_cost} ${row.currency}`).join(" · ")}
                  {costDraft.length > 3 && " …"}
                </span>
              </div>
            )}

            {costMessage && (
              <p className="num mt-4 flex items-center gap-2"
                 style={{ fontSize: "11.5px", letterSpacing: ".08em", color: "var(--proven)" }}>
                <Icon name="check" size={14} /> {costMessage.toUpperCase()}
              </p>
            )}
          </section>
        </div>
      )}
    </main>
  );
}

function Row({ icon, title, detail, footnote, action }: {
  icon: "box" | "margin"; title: string; detail: string; footnote?: string;
  action: React.ReactNode;
}) {
  return (
    <section className="card p-6 sm:p-7">
      <div className="flex flex-wrap items-start justify-between gap-5">
        <div className="flex gap-4">
          <IconPlate name={icon} size={34} />
          <div>
            <p style={{ margin: 0, fontSize: "15px", fontWeight: 600 }}>{title}</p>
            <p style={{ margin: "7px 0 0", maxWidth: "70ch", fontSize: "12.5px", lineHeight: 1.65, color: "var(--ink-3)" }}>
              {detail}
            </p>
            {footnote && (
              <p className="num" style={{ margin: "8px 0 0", fontSize: "11px", color: "var(--ink-5)" }}>
                {footnote.toUpperCase()}
              </p>
            )}
          </div>
        </div>
        {action}
      </div>
    </section>
  );
}

function marketplaceIds(connection: AmazonConnection): string[] {
  return connection.marketplaces.flatMap((entry) => {
    const marketplace = entry.marketplace;
    if (!marketplace || typeof marketplace !== "object") return [];
    const id = (marketplace as Record<string, unknown>).id;
    return typeof id === "string" ? [id] : [];
  });
}
