"use client";

import { useEffect, useState } from "react";
import {
  beginWooCommerceAuthorization, getWooCommerceConnection, startWooCommerceSync,
  waitForBackgroundJob, type WooCommerceConnection,
} from "../../../lib/api";
import { Icon, IconPlate } from "../../icons";

export default function WooCommerceIntegrationPage() {
  const [connection, setConnection] = useState<WooCommerceConnection | null>(null);
  const [storeUrl, setStoreUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getWooCommerceConnection().then(setConnection).catch((e) => setError((e as Error).message));
  }, []);

  async function connect() {
    setBusy(true); setError(null);
    try { window.location.assign(await beginWooCommerceAuthorization(storeUrl)); }
    catch (e) { setError((e as Error).message); setBusy(false); }
  }

  async function sync() {
    setBusy(true); setError(null);
    try {
      const job = await startWooCommerceSync();
      await waitForBackgroundJob(job.job_id, 10 * 60_000);
      setConnection(await getWooCommerceConnection());
    }
    catch (e) { setError((e as Error).message); }
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
                style={{ minWidth: "52px", height: "52px", padding: "0 10px", borderRadius: "var(--r)",
                         border: "1px solid var(--line)", background: "var(--raised)",
                         fontSize: "14px", color: "var(--ink-2)" }}>
            woo
          </span>
          <div>
            <p className="lbl">Sales channel</p>
            <h1 style={{ margin: "6px 0 0", fontSize: "28px", fontWeight: 600, letterSpacing: "-.025em" }}>
              WooCommerce
            </h1>
          </div>
        </div>
        <p style={{ margin: "20px 0 0", maxWidth: "68ch", fontSize: "14.5px", lineHeight: 1.7, color: "var(--ink-2)" }}>
          Authorize the Operator from your WordPress dashboard. It asks for read access only,
          and both generated credentials are encrypted before they are stored.
        </p>
      </section>

      <div className="card p-6 sm:p-8">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="lbl">Connection</p>
            <h2 style={{ margin: "8px 0 0", fontSize: "19px", fontWeight: 600 }}>
              {connected ? "Store connected" : "Connect WooCommerce"}
            </h2>
            {connection?.store_url && (
              <p className="num" style={{ margin: "8px 0 0", fontSize: "12.5px", color: "var(--ink-3)" }}>
                {connection.store_url}
              </p>
            )}
          </div>
          <span className="num flex items-center gap-2.5"
                style={{ fontSize: "11px", letterSpacing: ".1em",
                         color: connected ? "var(--proven)" : "var(--ink-5)" }}>
            <span className={`dot ${connected ? "dot-proven" : ""}`}
                  style={connected ? undefined : { background: "var(--ink-6)", boxShadow: "none" }} />
            {connected ? "CONNECTED" : "NOT CONNECTED"}
          </span>
        </div>

        {!connected && (
          <div className="mt-7">
            <label className="lbl" htmlFor="store-url">Your store address</label>
            <div className="mt-2 flex flex-col gap-3 sm:flex-row">
              <input id="store-url" value={storeUrl} onChange={(e) => setStoreUrl(e.target.value)}
                     placeholder="https://shop.example.com"
                     className="field num" style={{ flex: 1, minWidth: 0 }} />
              <button onClick={connect} disabled={busy || !storeUrl.trim()}
                      className="btn-primary shrink-0 inline-flex items-center gap-2">
                <Icon name="plug" size={13} /> {busy ? "OPENING STORE…" : "AUTHORIZE READ ACCESS"}
              </button>
            </div>
            <p style={{ margin: "10px 0 0", fontSize: "12px", color: "var(--ink-5)" }}>
              The store needs a public HTTPS address with WooCommerce enabled.
            </p>
          </div>
        )}

        {connected && (
          <div className="mt-7 flex flex-wrap items-center justify-between gap-4 p-5"
               style={{ background: "var(--raised)", borderRadius: "var(--r)" }}>
            <div>
              <p style={{ margin: 0, fontSize: "14.5px", fontWeight: 600 }}>Read-only performance sync</p>
              <p style={{ margin: "7px 0 0", fontSize: "12.5px", color: "var(--ink-3)" }}>
                Import 30 days of aggregate revenue, refunds, orders and units.
              </p>
            </div>
            <button onClick={sync} disabled={busy} className="btn-quiet shrink-0">
              {busy ? "SYNCING…" : "SYNC LAST 30 DAYS"}
            </button>
          </div>
        )}

        {error && (
          <p className="card-unproven mt-5 flex items-start gap-3 px-4 py-3"
             style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                      fontSize: "13px", color: "var(--unproven)" }}>
            <Icon name="alert" size={16} className="mt-0.5 shrink-0" /> {error}
          </p>
        )}
      </div>

      <div className="mt-4 grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))" }}>
        {([
          ["shield", "Read permission", "The generated key cannot edit your store."],
          ["lock", "Encrypted keys", "Consumer key and secret are encrypted at rest."],
          ["check", "Tenant isolated", "A store cannot be attached to another workspace."],
        ] as const).map(([icon, title, text]) => (
          <div key={title} className="card p-5">
            <IconPlate name={icon} size={32} />
            <p style={{ margin: "16px 0 0", fontSize: "14px", fontWeight: 600 }}>{title}</p>
            <p style={{ margin: "7px 0 0", fontSize: "12.5px", lineHeight: 1.6, color: "var(--ink-3)" }}>{text}</p>
          </div>
        ))}
      </div>
    </main>
  );
}
