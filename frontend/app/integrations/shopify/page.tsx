"use client";

import { useEffect, useState } from "react";
import {
  beginShopifyAuthorization, disconnectShopify, getShopifyConnection, retryShopifyNotifications,
  startShopifySync, waitForBackgroundJob, type ShopifyConnection,
} from "../../../lib/api";
import { Icon, IconPlate } from "../../icons";

/* Order notifications are the part worth a whole panel. When they stop, the
   store looks quiet rather than broken — which is the same failure the channels
   list exists to catch, one level down. */

export default function ShopifyIntegrationPage() {
  const [connection, setConnection] = useState<ShopifyConnection | null>(null);
  const [shop, setShop] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    if (query.get("connected") === "1") {
      setNotice("Shopify is connected. Your store is ready for a first read-only sync.");
      window.history.replaceState({}, "", "/integrations/shopify");
    }
    const launchedShop = query.get("from") === "shopify" ? query.get("shop")?.trim().toLowerCase() : null;
    getShopifyConnection().then(async (current) => {
      setConnection(current);
      // A launch from Shopify is an explicit request to connect that shop. The
      // address is only used to begin normal OAuth; the backend still validates
      // it and Shopify is still the screen that grants every permission.
      if (!current.connected && launchedShop &&
          /^[a-z0-9][a-z0-9-]{0,58}\.myshopify\.com$/.test(launchedShop)) {
        setShop(launchedShop); setBusy(true);
        setNotice(`We found ${launchedShop}. Opening Shopify's secure approval…`);
        try { window.location.assign(await beginShopifyAuthorization(launchedShop)); }
        catch (e) { setError((e as Error).message); setBusy(false); }
      }
    }).catch((e) => setError((e as Error).message));
  }, []);

  async function connect() {
    setBusy(true); setError(null);
    try { window.location.assign(await beginShopifyAuthorization(shop)); }
    catch (e) { setError((e as Error).message); setBusy(false); }
  }

  async function sync() {
    setBusy(true); setError(null); setNotice(null);
    try {
      const job = await startShopifySync();
      const completed = await waitForBackgroundJob(job.job_id, 10 * 60_000);
      const orders = Number(completed.result?.orders ?? 0);
      setConnection(await getShopifyConnection());
      setNotice(`Sync complete: ${orders} order${orders === 1 ? "" : "s"} imported.`);
    }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  async function retryNotifications() {
    setBusy(true); setError(null);
    try { setConnection(await retryShopifyNotifications()); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  async function changeStore() {
    const currentShop = connection?.shop;
    if (!currentShop || !window.confirm(
      `Disconnect ${currentShop}? This removes the Operator's read-only access and does not change Shopify products, orders, or prices.`
    )) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      await disconnectShopify();
      setConnection(await getShopifyConnection());
      setShop("");
      setNotice(`${currentShop} was disconnected. Enter the store you want to connect next.`);
    }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  const connected = Boolean(connection?.connected);
  const notifications = connection?.notifications;

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
            S
          </span>
          <div>
            <p className="lbl">Sales channel</p>
            <h1 style={{ margin: "6px 0 0", fontSize: "28px", fontWeight: 600, letterSpacing: "-.025em" }}>
              Shopify
            </h1>
          </div>
        </div>
        <p style={{ margin: "20px 0 0", maxWidth: "68ch", fontSize: "14.5px", lineHeight: 1.7, color: "var(--ink-2)" }}>
          Connect your own store. The Operator imports aggregate performance and never stores a
          customer email from an order notification.
        </p>
      </section>

      <div className="card p-6 sm:p-8">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="lbl">Connection</p>
            <h2 style={{ margin: "8px 0 0", fontSize: "19px", fontWeight: 600 }}>
              {connected ? "Store connected" : "Connect your store in 2 steps"}
            </h2>
            {connection?.shop && (
              <p className="num" style={{ margin: "8px 0 0", fontSize: "12.5px", color: "var(--ink-3)" }}>
                {connection.shop}
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

        {!connected && connection?.reason && (
          <div className="card-unproven mt-7 p-5"
               style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }}>
            <p style={{ margin: 0, fontSize: "14.5px", fontWeight: 600 }}>
              Access to {connection.shop} was withdrawn
            </p>
            <p style={{ margin: "8px 0 0", maxWidth: "70ch", fontSize: "13px", lineHeight: 1.65, color: "var(--ink-2)" }}>
              This happens when the app is uninstalled from the store, or its access is revoked.
              The stored credential was deleted. Reconnect below to resume.
            </p>
            <p className="num" style={{ margin: "14px 0 0", padding: "10px 14px", background: "#05070A",
                 borderRadius: "var(--r-sm)", fontSize: "11.5px", lineHeight: 1.6, color: "var(--unproven)" }}>
              {connection.reason}
            </p>
          </div>
        )}

        {!connected && (
          <div className="mt-7">
            <p className="lbl">Step 1 of 2</p>
            <label className="mt-2 block" htmlFor="shop" style={{ fontSize: "14.5px", fontWeight: 600 }}>
              Your Shopify store address
            </label>
            <div className="mt-2 flex flex-col gap-3 sm:flex-row">
              <input id="shop" value={shop} onChange={(e) => setShop(e.target.value)}
                     placeholder="your-store.myshopify.com"
                     className="field num" style={{ flex: 1, minWidth: 0 }} />
              <button onClick={connect} disabled={busy || !shop.trim()}
                      className="btn-primary shrink-0 inline-flex items-center gap-2">
                <Icon name="plug" size={13} /> {busy ? "OPENING SHOPIFY…" : "CONTINUE TO SHOPIFY"}
              </button>
            </div>
            <p style={{ margin: "10px 0 0", fontSize: "12px", color: "var(--ink-5)" }}>
              Use the permanent .myshopify.com address from Shopify Admin — not your public storefront address.
            </p>
            <div className="mt-4 p-4" style={{ background: "var(--raised)", borderRadius: "var(--r-sm)" }}>
              <p className="lbl">Step 2 of 2</p>
              <p style={{ margin: "7px 0 0", fontSize: "12.5px", lineHeight: 1.65, color: "var(--ink-3)" }}>
                Shopify will ask you to approve read-only access. After approval, you return here automatically — no codes or technical pages.
              </p>
            </div>
            <p style={{ margin: "14px 0 0", fontSize: "12px", color: "var(--ink-5)" }}>
              The Operator cannot change products, prices, or orders.
            </p>
            <p style={{ margin: "8px 0 0", fontSize: "12px", color: "var(--ink-5)" }}>
              Open the Operator from Shopify Admin and we fill in this address automatically.
            </p>
          </div>
        )}

        {connected && (
          <div className="mt-7 flex flex-wrap items-center justify-between gap-4 p-5"
               style={{ background: "var(--raised)", borderRadius: "var(--r)" }}>
            <div>
              <p style={{ margin: 0, fontSize: "14.5px", fontWeight: 600 }}>Read-only performance sync</p>
              <p style={{ margin: "7px 0 0", fontSize: "12.5px", color: "var(--ink-3)" }}>
                Rebuild the last 30 days of aggregate orders and revenue.
              </p>
            </div>
            <button onClick={sync} disabled={busy} className="btn-quiet shrink-0">
              {busy ? "SYNCING…" : "SYNC LAST 30 DAYS"}
            </button>
          </div>
        )}

        {connected && (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-4 p-5"
               style={{ border: "1px solid var(--line)", borderRadius: "var(--r)" }}>
            <div>
              <p style={{ margin: 0, fontSize: "14.5px", fontWeight: 600 }}>Need a different Shopify store?</p>
              <p style={{ margin: "7px 0 0", fontSize: "12.5px", color: "var(--ink-3)" }}>
                Disconnect this read-only channel before connecting another one. Products, orders, and prices are never changed.
              </p>
            </div>
            <button onClick={changeStore} disabled={busy} className="btn-quiet shrink-0">
              {busy ? "DISCONNECTING…" : "CHANGE STORE"}
            </button>
          </div>
        )}

        {connected && (
          <div className={`mt-4 p-5 ${notifications === "active" ? "card-proven" : "card-waiting"}`}
               style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }}>
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <p className="flex items-center gap-2.5" style={{ margin: 0, fontSize: "14.5px", fontWeight: 600 }}>
                  <span className={`dot ${notifications === "active" ? "dot-proven" : "dot-waiting"}`} />
                  {notifications === "active" ? "Order notifications are on"
                    : notifications === "stale" ? "Notifications are going to the wrong address"
                    : "Order notifications are off"}
                </p>
                <p style={{ margin: "9px 0 0", maxWidth: "62ch", fontSize: "12.5px", lineHeight: 1.65, color: "var(--ink-2)" }}>
                  {notifications === "active"
                    ? `Shopify tells the Operator the moment an order arrives — ${connection!.topics.length} events subscribed.`
                    : notifications === "stale"
                      ? "Shopify is still calling the address this server used to have, so nothing is arriving. A quiet store and a broken hook look identical from here."
                      : "The Operator only sees new orders when you press Sync. Turn notifications on so it reacts as they arrive."}
                </p>
                {notifications !== "active" && connection?.reason && (
                  <p className="num" style={{ margin: "12px 0 0", padding: "10px 14px", background: "#05070A",
                       borderRadius: "var(--r-sm)", fontSize: "11.5px", lineHeight: 1.6, color: "var(--waiting)" }}>
                    {connection.reason}
                  </p>
                )}
              </div>
              {notifications !== "active" && (
                <button onClick={retryNotifications} disabled={busy} className="btn-primary shrink-0">
                  {busy ? "TURNING ON…" : notifications === "stale" ? "POINT THEM HERE" : "TURN THEM ON"}
                </button>
              )}
            </div>
          </div>
        )}

        {error && (
          <p className="card-unproven mt-5 flex items-start gap-3 px-4 py-3"
             style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                      fontSize: "13px", color: "var(--unproven)" }}>
            <Icon name="alert" size={16} className="mt-0.5 shrink-0" /> {error}
          </p>
        )}
        {notice && (
          <p className="card-proven mt-5 flex items-start gap-3 px-4 py-3"
             style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                      fontSize: "13px", color: "var(--ink-2)" }}>
            <Icon name="check" size={16} stroke="var(--proven)" className="mt-0.5 shrink-0" /> {notice}
          </p>
        )}
      </div>

      <div className="mt-4 grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))" }}>
        {([
          ["shield", "Read-only first", "No product, price or order is changed."],
          ["check", "Verified events", "Signatures and duplicate deliveries are both checked."],
          ["lock", "Privacy by design", "Only aggregate sales metrics are kept."],
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
