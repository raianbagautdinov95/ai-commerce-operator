"use client";

import { useEffect, useState } from "react";
import {
  getAmazonConnection, getShopifyConnection, getWooCommerceConnection,
} from "../../lib/api";
import { Icon, IconPlate } from "../icons";

/* ---------------------------------------------------------------------------
   Where the numbers come from.

   This page used to be a brochure: five cards saying every channel was
   "Ready", whether or not anything was connected and whether or not it still
   answered. That is the one failure worth building a screen around — a channel
   that stopped replying looks exactly like a quiet week, so the store sees
   falling revenue and never learns the truth is that nobody is reading.

   So the order is by trouble, not by alphabet: broken first, degraded next,
   healthy after that, and the ones you could add at the bottom.
   --------------------------------------------------------------------------- */

type Health = "broken" | "degraded" | "healthy" | "absent" | "unknown";

type Channel = {
  key: string;
  name: string;
  href: string;
  /** Only Shopify has run against a live account; the rest say so. */
  experimental?: boolean;
  /** What this channel is for, in the seller's words. */
  purpose: string;
  health: Health;
  /** The account, shop domain or seller id — whatever names it. */
  account: string | null;
  /** Why it is broken or degraded, said plainly. Never invented. */
  reason: string | null;
  facts: { label: string; value: string; tone?: "proven" | "waiting" | "unproven" }[];
};

const PLANNED = [
  { name: "eBay", purpose: "Marketplace orders and performance" },
  { name: "Etsy", purpose: "Shop listings, orders and fees" },
];

const RANK: Record<Health, number> = {
  broken: 0, unknown: 1, degraded: 2, healthy: 3, absent: 4,
};

const TONE: Record<Health, "proven" | "waiting" | "unproven" | null> = {
  broken: "unproven", unknown: "waiting", degraded: "waiting",
  healthy: "proven", absent: null,
};

const HEADLINE: Record<Health, string> = {
  broken: "NOT ANSWERING",
  unknown: "COULD NOT BE CHECKED",
  degraded: "READING, NOT WRITING",
  healthy: "CONNECTED",
  absent: "NOT CONNECTED",
};

export default function IntegrationsPage() {
  const [channels, setChannels] = useState<Channel[] | null>(null);

  useEffect(() => {
    // Each channel is asked separately and a failure is reported as a failure.
    // Folding them into one "something went wrong" would hide which channel is
    // the one that stopped answering, which is the entire question here.
    Promise.all([
      getShopifyConnection().then(toShopify).catch(() => unknownChannel(
        "shopify", "Shopify", "/integrations/shopify",
        "Orders, products and inventory from your own store")),
      getAmazonConnection().then(toAmazon).catch(() => unknownChannel(
        "amazon", "Amazon", "/integrations/amazon",
        "Listings, FBA inventory, fees, sales and advertising", true)),
      getWooCommerceConnection().then(toWoo).catch(() => unknownChannel(
        "woocommerce", "WooCommerce", "/integrations/woocommerce",
        "Read-only access to a WordPress store", true)),
    ]).then((rows) => setChannels(rows.sort((a, b) => RANK[a.health] - RANK[b.health])));
  }, []);

  const live = (channels ?? []).filter((c) => c.health === "healthy" || c.health === "degraded");
  const dark = (channels ?? []).filter((c) => c.health === "broken" || c.health === "unknown");

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex items-center gap-3">
          <IconPlate name="plug" />
          <p className="lbl">Channels</p>
        </div>
        <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
          Where the numbers come from
        </h1>
        <p style={{ margin: "16px 0 0", maxWidth: "66ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          Nothing on the other screens is worth more than these connections. A channel that
          stopped answering is the one failure that looks exactly like everything being fine.
        </p>
        {channels !== null && (
          <p className="num" style={{ margin: "18px 0 0", fontSize: "11.5px", letterSpacing: ".1em", color: "var(--ink-4)" }}>
            {live.length} CONNECTED
            {dark.length > 0 && <span style={{ color: "var(--unproven)" }}> · {dark.length} DARK</span>}
          </p>
        )}
      </section>

      {channels === null && (
        <p className="num" style={{ fontSize: "11.5px", letterSpacing: ".14em", color: "var(--ink-5)" }}>
          ASKING EACH CHANNEL…
        </p>
      )}

      <div className="space-y-4">
        {(channels ?? []).map((channel) => <ChannelCard key={channel.key} channel={channel} />)}
      </div>

      <div className="card mt-4 flex flex-wrap items-center justify-between gap-6 p-6"
           style={{ borderStyle: "dashed" }}>
        <div>
          <p style={{ margin: 0, fontSize: "15px", fontWeight: 600 }}>Not built yet</p>
          <p style={{ margin: "7px 0 0", maxWidth: "62ch", fontSize: "13.5px", lineHeight: 1.6, color: "var(--ink-2)" }}>
            Only channels that have been verified end to end are offered. Listing one that has
            never run against a real account would be the same lie as a green dot on a dead
            connection.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {PLANNED.map((row) => (
            <span key={row.name} className="num" title={row.purpose}
                  style={{ border: "1px solid var(--line)", borderRadius: "var(--r-sm)",
                           padding: "10px 16px", fontSize: "11.5px", letterSpacing: ".1em",
                           color: "var(--ink-5)" }}>
              {row.name.toUpperCase()}
            </span>
          ))}
        </div>
      </div>
    </main>
  );
}

function ChannelCard({ channel }: { channel: Channel }) {
  const tone = TONE[channel.health];
  const cardClass = tone === "unproven" ? "card-unproven"
    : tone === "waiting" ? "card-waiting" : "card";

  return (
    <section className={cardClass}
             style={cardClass === "card" ? undefined
               : { borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }}>
      <div className="flex flex-wrap items-start justify-between gap-6 p-6">
        <div className="flex min-w-0 gap-4">
          <span className={`dot ${tone ? `dot-${tone}` : ""}`}
                style={{ marginTop: "8px", flexShrink: 0,
                         background: tone ? undefined : "var(--ink-6)",
                         boxShadow: tone ? undefined : "none" }} />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <p style={{ margin: 0, fontSize: "17px", fontWeight: 600 }}>{channel.name}</p>
              {channel.experimental && (
                <span className="num" title="Never run against a live account. Kept working, not promised."
                      style={{ border: "1px solid var(--line)", borderRadius: "var(--r-sm)",
                               padding: "3px 8px", fontSize: "9.5px", letterSpacing: ".14em",
                               color: "var(--ink-4)" }}>
                  EXPERIMENTAL
                </span>
              )}
            </div>
            <p className="num" style={{ margin: "7px 0 0", fontSize: "11px", letterSpacing: ".12em",
                 color: tone ? `var(--${tone})` : "var(--ink-5)" }}>
              {HEADLINE[channel.health]}
              {channel.account && <span style={{ color: "var(--ink-4)" }}> · {channel.account}</span>}
            </p>
            <p style={{ margin: "10px 0 0", maxWidth: "72ch", fontSize: "13.5px", lineHeight: 1.6, color: "var(--ink-2)" }}>
              {channel.purpose}
            </p>
          </div>
        </div>

        <a href={channel.href}
           className={channel.health === "broken" ? "btn-primary shrink-0 inline-flex items-center gap-2"
                                                  : "btn-quiet shrink-0 inline-flex items-center gap-2"}>
          <Icon name="plug" size={13} />
          {channel.health === "absent" ? "CONNECT"
            : channel.health === "broken" ? "RECONNECT" : "SETTINGS"}
        </a>
      </div>

      {channel.facts.length > 0 && (
        <div className="grid gap-px"
             style={{ borderTop: "1px solid var(--line-faint)",
                      gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
          {channel.facts.map((fact) => (
            <div key={fact.label} className="px-6 py-4">
              <p className="lbl">{fact.label}</p>
              <p className="num" style={{ margin: "8px 0 0", fontSize: "13px",
                   color: fact.tone ? `var(--${fact.tone})` : "var(--ink)" }}>
                {fact.value}
              </p>
            </div>
          ))}
        </div>
      )}

      {channel.reason && (
        <div className="px-6 py-3.5"
             style={{ borderTop: "1px solid var(--line-faint)", background: "#05070A",
                      borderBottomLeftRadius: "var(--r)", borderBottomRightRadius: "var(--r)" }}>
          <p className="num" style={{ margin: 0, fontSize: "11.5px",
               color: tone === "unproven" ? "var(--unproven)" : "var(--waiting)" }}>
            {channel.reason}
          </p>
        </div>
      )}
    </section>
  );
}

/* --- turning each account endpoint into the same shape -------------------- */

function unknownChannel(key: string, name: string, href: string, purpose: string,
                        experimental = false): Channel {
  return {
    key, name, href, purpose, experimental,
    health: "unknown",
    account: null,
    reason: "The server did not answer when asked about this channel, so its state is "
          + "genuinely unknown — not healthy, and not proven broken either.",
    facts: [],
  };
}

function toShopify(row: Awaited<ReturnType<typeof getShopifyConnection>>): Channel {
  const base = {
    key: "shopify", name: "Shopify", href: "/integrations/shopify",
    purpose: "Orders, products and inventory from your own store",
  };
  if (!row.connected) {
    return { ...base, health: "absent", account: null, reason: row.reason ?? null, facts: [] };
  }
  // A revoked token comes back as connected:false with a reason, but a stored
  // credential that has started failing shows up here — say which it is.
  const notifications = row.notifications;
  const health: Health = row.reason ? "broken" : notifications === "active" ? "healthy" : "degraded";
  return {
    ...base,
    health,
    account: row.shop,
    reason: row.reason,
    facts: [
      {
        label: "Order notifications",
        value: notifications === "active" ? `ON · ${row.topics.length} TOPICS`
             : notifications === "stale" ? "STALE — NOT ARRIVING"
             : "NOT SET UP YET",
        tone: notifications === "active" ? "proven" : "waiting",
      },
      { label: "Changes allowed", value: "NONE · READS ONLY" },
    ],
  };
}

function toAmazon(row: Awaited<ReturnType<typeof getAmazonConnection>>): Channel {
  const base = {
    key: "amazon", name: "Amazon", href: "/integrations/amazon",
    purpose: "Listings, FBA inventory, fees, sales and advertising",
    experimental: true,
  };
  if (!row.connected) {
    return { ...base, health: "absent", account: null, reason: null, facts: [] };
  }
  return {
    ...base,
    health: "healthy",
    account: row.seller_id,
    reason: null,
    facts: [
      { label: "Marketplaces", value: String(row.marketplaces.length) },
      { label: "Changes allowed", value: "NONE · READS ONLY" },
    ],
  };
}

function toWoo(row: Awaited<ReturnType<typeof getWooCommerceConnection>>): Channel {
  const base = {
    key: "woocommerce", name: "WooCommerce", href: "/integrations/woocommerce",
    purpose: "Read-only access to a WordPress store",
    experimental: true,
  };
  if (!row.connected) {
    return { ...base, health: "absent", account: null, reason: null, facts: [] };
  }
  return {
    ...base,
    health: "healthy",
    account: row.store_url,
    reason: null,
    facts: [{ label: "Changes allowed", value: "NONE · READS ONLY" }],
  };
}
