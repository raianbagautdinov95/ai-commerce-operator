"use client";

import { useEffect, useState } from "react";
import {
  API_BASE,
  getCommerceDashboard, getDiscoveryHistory, getHistory, getKeepaStatus, getPortfolio, getQueue,
  getPayroll, getRoiDashboard, listActions, seedDemoStore, NotAuthenticated,
  type DiscoveryHistoryItem, type HistoryItem, type KeepaStatus, type OperatorAction,
  type CommerceDashboard, type Payroll, type Portfolio, type RoiDashboard,
} from "../../lib/api";
import ProfitChart, { type ChartAction } from "../profit-chart";
import { Icon, IconPlate, type IconName } from "../icons";

const verdictColor: Record<string, string> = {
  BUY: "var(--proven)",
  CAUTION: "var(--waiting)",
  AVOID: "var(--unproven)",
};

const steps: { href: string; number: string; title: string; text: string; icon: IconName }[] = [
  { href: "/integrations", number: "01", title: "Connect", text: "Link every sales channel", icon: "plug" },
  { href: "/discover", number: "02", title: "Discover", text: "Find demand", icon: "search" },
  { href: "/suppliers", number: "03", title: "Source", text: "Validate costs", icon: "truck" },
  { href: "/", number: "04", title: "Evaluate", text: "Score economics", icon: "gem" },
  { href: "/autopilot", number: "05", title: "Operate", text: "Scale winners", icon: "bolt" },
];

const moduleIcon: Record<string, IconName> = {
  ppc: "target",
  inventory: "box",
  pricing: "tag",
  listing: "tag",
};

/** Real actions become chart markers; nothing is invented to fill the plot. */
function toChartActions(actions: OperatorAction[], currency: string): ChartAction[] {
  return actions
    .filter((a) => a.applied_at)
    .sort((a, b) => (b.applied_at ?? "").localeCompare(a.applied_at ?? ""))
    .map((a) => {
      const proven = a.status === "measured" && a.evidence_mode === "real" && a.impact && !a.impact.provisional;
      const tone: ChartAction["tone"] = a.status === "reverted" ? "unproven" : proven ? "proven" : "waiting";
      const detail = a.status === "reverted"
        ? "Undone"
        : proven && a.impact
          ? `${a.impact.net >= 0 ? "+" : ""}${money(a.impact.net, a.currency || currency)} proven`
          : "Still measuring";
      return {
        id: a.id,
        date: (a.applied_at ?? "").slice(0, 10),
        title: a.action_type.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase()),
        detail,
        tone,
        icon: moduleIcon[a.module] ?? "operator",
      };
    });
}

export default function Dashboard() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [pending, setPending] = useState(0);
  const [keepa, setKeepa] = useState<KeepaStatus | null>(null);
  const [searches, setSearches] = useState<DiscoveryHistoryItem[]>([]);
  const [evals, setEvals] = useState<HistoryItem[]>([]);
  const [roi, setRoi] = useState<RoiDashboard | null>(null);
  const [commerce, setCommerce] = useState<CommerceDashboard | null>(null);
  const [payroll, setPayroll] = useState<Payroll | null>(null);
  const [actions, setActions] = useState<OperatorAction[]>([]);
  const [demoBusy, setDemoBusy] = useState(false);
  const [demoError, setDemoError] = useState<string | null>(null);
  const [unreachable, setUnreachable] = useState(false);

  useEffect(() => {
    // Every panel used to swallow its own failure, so three different problems
    // looked identical: an empty store, a silent server, and a refused token.
    // They need different things from the person reading the screen, so the
    // difference has to survive to the screen. Refusals are counted apart and
    // left to the session gate, which sends them to sign in.
    let failures = 0;
    let refusals = 0;
    const load = <T,>(promise: Promise<T>, apply: (value: T) => void) =>
      promise.then(apply).catch((error) => {
        failures += 1;
        if (error instanceof NotAuthenticated) refusals += 1;
      });

    Promise.all([
      load(getPortfolio(), setPortfolio),
      load(getQueue("pending"), (q) => setPending(q.results.length)),
      load(getKeepaStatus(), setKeepa),
      load(getDiscoveryHistory(5), setSearches),
      load(getHistory(5), setEvals),
      load(getRoiDashboard(30), setRoi),
      load(getCommerceDashboard(30), setCommerce),
      load(getPayroll(30), setPayroll),
      load(listActions(), setActions),
    ]).then(() => setUnreachable(failures === 9 && refusals === 0));
  }, []);

  async function createDemo() {
    setDemoBusy(true); setDemoError(null);
    try {
      const seeded = await seedDemoStore();
      const [nextRoi, nextCommerce] = await Promise.all([
        getRoiDashboard(30, seeded.marketplace_id),
        getCommerceDashboard(30),
      ]);
      setRoi(nextRoi); setCommerce(nextCommerce);
    } catch (error) { setDemoError((error as Error).message); }
    finally { setDemoBusy(false); }
  }

  const hasProfit = roi?.profit != null;
  const live = commerce?.channels.length ?? 0;
  const unit = roi?.currency ?? commerce?.currency ?? "USD";
  const revenue = live ? commerce!.revenue : roi?.revenue ?? 0;
  const netProfit = live ? commerce!.profit : roi?.profit ?? null;
  const orders = live ? commerce!.orders : roi?.order_items ?? 0;
  // Every number on this screen must come from the same place. The chart used
  // to read the Amazon series unconditionally, so a connected Shopify store
  // showed a real total beside an empty chart telling you to connect a channel.
  const chartDays = live
    ? commerce!.daily.map((d) => ({ date: d.date, revenue: d.revenue, order_items: d.orders }))
    : roi?.daily ?? [];
  const chartMargin = live ? commerce!.margin : roi?.margin ?? null;
  const proposals = actions.filter((a) => a.status === "proposed");
  const projected = proposals.reduce((sum, a) => sum + (a.projected_impact ?? 0), 0);

  return <main className="pb-20">
    {unreachable && <Notice tone="unproven">
      <strong style={{ color: "var(--ink)" }}>The server did not answer.</strong>{" "}
      Everything below reads as empty because nothing could be loaded, not because there is
      nothing to show. Tried <span className="num">{API_BASE}</span> — check the API is running
      and that <span className="num">NEXT_PUBLIC_API_BASE</span> points at it.
    </Notice>}

    <section className="px-6 pb-8 pt-11 lg:px-11">
      <div className="flex flex-wrap items-start justify-between gap-8">
        <div>
          <div className="mb-5 flex items-center gap-3">
            <span className="lbl">Today</span>
            {roi?.demo_data && <Tag tone="waiting">DEMONSTRATION DATA</Tag>}
          </div>
          <h1 style={{ margin: 0, fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em", lineHeight: 1.2 }}>
            Know what is making money.<br />
            <span style={{ color: "var(--ink-4)" }}>Act before it stops.</span>
          </h1>
        </div>
        <div className="flex items-center gap-5">
          <Freshness value={live ? commerce!.freshness : roi?.freshness ?? "no_data"}
                     syncedAt={live ? commerce!.last_synced_at : roi?.last_synced_at ?? null} />
          <a href="/report" className="btn-quiet inline-flex items-center gap-2">
            <Icon name="list" size={14} /> DAILY BRIEF
          </a>
        </div>
      </div>
    </section>

    <div className="space-y-6 px-6 lg:px-11">

      {/* ---- the performance instrument ---- */}
      <section className="card p-6 sm:p-8">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div>
            <p className="lbl">Performance</p>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <h2 style={{ margin: 0, fontSize: "24px", fontWeight: 600, letterSpacing: "-.02em" }}>
                Revenue momentum
              </h2>
              {payroll != null && payroll.settled_impact > 0 && <Tag tone="proven">OPERATOR AT WORK</Tag>}
            </div>
            <p style={{ margin: "8px 0 0", fontSize: "13.5px", color: "var(--ink-3)" }}>
              Revenue and net profit, with every change the operator actually made.
            </p>
          </div>
          <span className="num flex items-center gap-2.5"
                style={{ border: "1px solid var(--line)", borderRadius: "var(--r-sm)", padding: "10px 14px",
                         fontSize: "11.5px", letterSpacing: ".08em", color: "var(--ink-2)" }}>
            <Icon name="calendar" size={14} /> LAST {roi?.period_days ?? 30} DAYS
          </span>
        </div>

        <div className="mt-7 flex flex-wrap items-end gap-x-12 gap-y-5">
          <div>
            <p className="num" style={{ margin: 0, fontSize: "42px", fontWeight: 500, letterSpacing: "-.04em", lineHeight: 1 }}>
              {money(revenue, unit)}
            </p>
            {roi?.revenue_change_percentage != null && (
              <p className="num flex items-center gap-1.5" style={{ margin: "12px 0 0", fontSize: "12.5px",
                   color: roi.revenue_change_percentage >= 0 ? "var(--proven)" : "var(--unproven)" }}>
                <Icon name="impact" size={13} />
                {roi.revenue_change_percentage >= 0 ? "+" : ""}{roi.revenue_change_percentage}%
                <span style={{ color: "var(--ink-4)" }}>vs previous {roi.period_days} days</span>
              </p>
            )}
          </div>
          <div className="flex flex-col gap-2.5">
            <Series colour="#4ADE80" label="Revenue" value={money(revenue, unit)} />
            <Series colour={netProfit == null ? "var(--ink-6)" : "#8B8CF0"} label="Net profit"
                    value={netProfit == null ? "Withheld" : money(netProfit, unit)} />
          </div>
        </div>

        <ProfitChart
          daily={chartDays}
          margin={chartMargin}
          currency={unit}
          actions={toChartActions(actions, unit)}
          empty={live ? "One day of sales so far. The line appears on the second." : undefined}
        />

        <div className="mt-8 grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))" }}>
          <Kpi icon="revenue" label="Total revenue" value={money(revenue, unit)}
               delta={roi?.revenue_change_percentage ?? null} />
          <Kpi icon="profit" label="Net profit" tone={netProfit == null ? "waiting" : "proven"}
               value={netProfit == null ? "Withheld" : money(netProfit, unit)}
               note={netProfit == null ? "Costs incomplete" : undefined} />
          <Kpi icon="orders" label="Orders" value={orders.toLocaleString()}
               note={live ? `${commerce!.units.toLocaleString()} units` : undefined} />
          <Kpi icon="margin" label="Profit margin"
               value={roi?.margin == null ? "—" : `${(roi.margin * 100).toFixed(1)}%`}
               note={roi?.margin == null ? "Needs full costs" : undefined} />
          <a href="/payroll" className="block">
            <Kpi icon="impact" label={`Proven impact · ${payroll?.period_days ?? 30}d`}
                 tone={payroll?.settled_impact ? "proven" : undefined}
                 value={money(payroll?.settled_impact ?? null, unit)}
                 note={payroll?.settled_impact ? "Measured actions only →" : "Nothing proven yet →"} />
          </a>
        </div>
      </section>

      {proposals.length > 0 && (
        <a href="/proposals" className="card flex flex-wrap items-center justify-between gap-5 px-6 py-5">
          <span className="flex items-center gap-4">
            <IconPlate name="spark" tone="proven" />
            <span style={{ fontSize: "14px", lineHeight: 1.6, color: "var(--ink-2)" }}>
              The operator has <strong style={{ color: "var(--ink)" }}>{proposals.length} proposal(s)</strong> waiting
              for your decision
              {projected > 0 && <> — <span className="num">{money(projected, unit)}</span> projected, none of it proven yet</>}.
            </span>
          </span>
          <span className="num flex items-center gap-2" style={{ fontSize: "11.5px", letterSpacing: ".1em", color: "var(--proven)" }}>
            REVIEW THEM <Icon name="chevron" size={14} className="-rotate-90" />
          </span>
        </a>
      )}

      {!roi?.demo_data && <DemoBanner busy={demoBusy} error={demoError} onCreate={createDemo} />}

      <section className="grid gap-6 lg:grid-cols-2">
        <div className="card p-6 sm:p-8">
          <div className="flex items-center gap-3">
            <IconPlate name="margin" size={34} />
            <div>
              <p className="lbl">Cost structure</p>
              <h2 style={{ margin: "4px 0 0", fontSize: "18px", fontWeight: 600, letterSpacing: "-.015em" }}>Where revenue goes</h2>
            </div>
          </div>
          <div className="mt-7 space-y-6">
            <CostRow label="Landed COGS" value={roi?.landed_cogs} revenue={roi?.revenue} currency={unit} />
            <CostRow label="Amazon fees" value={roi?.amazon_fees} revenue={roi?.revenue} currency={unit} />
            <CostRow label="Advertising" value={roi?.advertising_spend} revenue={roi?.revenue} currency={unit} />
          </div>
          <div className={`mt-8 p-4 ${hasProfit ? "card-proven" : "card-waiting"}`}
               style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }}>
            <div className="flex items-baseline justify-between gap-4">
              <span style={{ fontSize: "13.5px", color: "var(--ink-2)" }}>Retained profit</span>
              <span className="num" style={{ fontSize: "18px", fontWeight: 500, color: hasProfit ? "var(--proven)" : "var(--waiting)" }}>
                {hasProfit ? money(roi?.profit, unit) : "Withheld"}
              </span>
            </div>
            {roi?.cogs_coverage_percentage != null &&
              <p className="num" style={{ margin: "10px 0 0", fontSize: "11.5px", color: "var(--ink-4)" }}>
                SKU COST COVERAGE · {roi.cogs_coverage_percentage}%
              </p>}
          </div>
        </div>

        {commerce && commerce.channels.length > 0
          ? <ChannelMix commerce={commerce} />
          : <div className="card flex flex-col items-start justify-center gap-4 p-6 sm:p-8">
              <IconPlate name="plug" />
              <h2 style={{ margin: 0, fontSize: "18px", fontWeight: 600 }}>No channel is connected</h2>
              <p style={{ margin: 0, maxWidth: "48ch", fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-2)" }}>
                Every number on this page comes from a connected channel. A channel that stopped
                answering looks exactly like a quiet week, so this is the first thing to check.
              </p>
              <a href="/integrations" className="btn-quiet inline-flex items-center gap-2">
                <Icon name="plug" size={14} /> CONNECT A CHANNEL
              </a>
            </div>}
      </section>

      {!hasProfit && <Notice tone="waiting" flush>
        <strong style={{ color: "var(--ink)" }}>Profit is protected from guessing.</strong>{" "}
        Add landed COGS, Amazon fees and ad spend for the same period to unlock verified margin
        and ROI. Until then this number stays empty rather than optimistic.
      </Notice>}

      <section className="card p-6 sm:p-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="lbl">Operating system</p>
            <h2 style={{ margin: "8px 0 0", fontSize: "20px", fontWeight: 600, letterSpacing: "-.015em" }}>From signal to decision</h2>
          </div>
          <a href="/operator" className="num flex items-center gap-2" style={{ fontSize: "11.5px", letterSpacing: ".1em", color: "var(--proven)" }}>
            OPEN THE OPERATOR <Icon name="chevron" size={14} className="-rotate-90" />
          </a>
        </div>
        <div className="mt-7 grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
          {steps.map((step) => (
            <a key={step.href} href={step.href} className="card p-5 transition-colors hover:border-[color:var(--line-soft)]">
              <div className="flex items-center justify-between">
                <IconPlate name={step.icon} size={34} />
                <span className="num" style={{ fontSize: "11px", letterSpacing: ".14em", color: "var(--ink-5)" }}>{step.number}</span>
              </div>
              <p style={{ margin: "20px 0 0", fontSize: "15px", fontWeight: 600 }}>{step.title}</p>
              <p style={{ margin: "6px 0 0", fontSize: "12.5px", color: "var(--ink-3)" }}>{step.text}</p>
            </a>
          ))}
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        <Mini icon="gem" label="Pipeline capital" value={`$${(portfolio?.total_upfront_investment ?? 0).toLocaleString()}`} detail={`${pending} plans awaiting review`} />
        <Mini icon="impact" label="Projected profit" value={`~$${(portfolio?.total_monthly_profit ?? 0).toLocaleString()}`} detail="Approved portfolio / month — projected, not proven" />
        <Mini icon="search" label="Research capacity" tone={keepa?.enabled ? "neutral" : "waiting"}
              value={keepa?.enabled ? (keepa.tokens_left?.toLocaleString() ?? "—") : "Offline"} detail="Keepa tokens available" />
      </section>

      <section className="grid gap-6 lg:grid-cols-2">
        <Activity icon="search" title="Recent market searches" empty="No searches yet — start with Discover.">
          {searches.map((item) => (
            <a key={item.id} href="/discover" className="flex items-center justify-between py-3.5"
               style={{ borderTop: "1px solid var(--line-faint)" }}>
              <div>
                <p style={{ margin: 0, fontSize: "14px" }}>{item.niche}</p>
                <p className="num" style={{ margin: "5px 0 0", fontSize: "11.5px", color: "var(--ink-4)" }}>
                  {item.results_count} CANDIDATES
                </p>
              </div>
              <Icon name="chevron" size={15} className="-rotate-90" stroke="var(--ink-6)" />
            </a>
          ))}
        </Activity>
        <Activity icon="gem" title="Recent product decisions" empty="No evaluations yet — run Product Hunter.">
          {evals.map((item) => (
            <div key={item.id} className="flex items-center justify-between gap-4 py-3.5"
                 style={{ borderTop: "1px solid var(--line-faint)" }}>
              <div className="min-w-0">
                <p className="truncate" style={{ margin: 0, fontSize: "14px" }}>{item.name}</p>
                <p className="num" style={{ margin: "5px 0 0", fontSize: "11.5px", color: "var(--ink-4)" }}>
                  SCORE {item.score}/100
                </p>
              </div>
              <span className="num" style={{ fontSize: "11px", letterSpacing: ".12em", color: verdictColor[item.verdict] ?? "var(--ink-3)" }}>
                {item.verdict}
              </span>
            </div>
          ))}
        </Activity>
      </section>
    </div>
  </main>;
}

/* ---------------------------------------------------------------------------
   Pieces. Only three colours are allowed to be saturated, and each one is a
   claim: green means proven, amber means waiting on something, red means it
   did not work. Everything else is an icon and a number.
   --------------------------------------------------------------------------- */

type Tone = "proven" | "waiting" | "unproven";

const toneColor: Record<Tone, string> = {
  proven: "var(--proven)",
  waiting: "var(--waiting)",
  unproven: "var(--unproven)",
};

function Series({ colour, label, value }: { colour: string; label: string; value: string }) {
  return (
    <span className="flex items-center gap-3">
      <span style={{ width: "7px", height: "7px", borderRadius: "999px", background: colour }} />
      <span style={{ fontSize: "13px", color: "var(--ink-2)", minWidth: "72px" }}>{label}</span>
      <span className="num" style={{ fontSize: "13px" }}>{value}</span>
    </span>
  );
}

function Kpi({ icon, label, value, delta, note, tone }: {
  icon: IconName; label: string; value: string; delta?: number | null; note?: string; tone?: Tone;
}) {
  return (
    <div className="card h-full p-5">
      <div className="flex items-center gap-3">
        <IconPlate name={icon} tone={tone ?? "neutral"} size={34} />
        <p style={{ margin: 0, fontSize: "12.5px", color: "var(--ink-3)" }}>{label}</p>
      </div>
      <div className="mt-4 flex flex-wrap items-baseline gap-2.5">
        <p className="num" style={{ margin: 0, fontSize: "22px", fontWeight: 500, letterSpacing: "-.03em",
             color: tone ? toneColor[tone] : "var(--ink)" }}>
          {value}
        </p>
        {delta != null && (
          <span className="num" style={{ fontSize: "11.5px", color: delta >= 0 ? "var(--proven)" : "var(--unproven)" }}>
            {delta >= 0 ? "↑" : "↓"} {Math.abs(delta)}%
          </span>
        )}
      </div>
      {note && <p style={{ margin: "7px 0 0", fontSize: "11.5px", color: "var(--ink-5)" }}>{note}</p>}
    </div>
  );
}

function Mini({ icon, label, value, detail, tone }: {
  icon: IconName; label: string; value: string; detail: string; tone?: "neutral" | Tone;
}) {
  return (
    <div className="card flex items-center gap-4 p-5">
      <IconPlate name={icon} tone={tone ?? "neutral"} />
      <div className="min-w-0">
        <p className="lbl">{label}</p>
        <p className="num" style={{ margin: "8px 0 0", fontSize: "21px", fontWeight: 500, letterSpacing: "-.03em",
             color: tone && tone !== "neutral" ? toneColor[tone] : "var(--ink)" }}>
          {value}
        </p>
        <p style={{ margin: "6px 0 0", fontSize: "11.5px", color: "var(--ink-5)" }}>{detail}</p>
      </div>
    </div>
  );
}

function Notice({ tone, flush, children }: { tone: Tone; flush?: boolean; children: React.ReactNode }) {
  const icon: IconName = tone === "unproven" ? "alert" : tone === "waiting" ? "clock" : "check";
  return (
    <div className={`card-${tone} ${flush ? "" : "mx-6 my-6 lg:mx-11"} flex gap-4 px-5 py-4`}
         style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)", fontSize: "13.5px", lineHeight: 1.65, color: "var(--ink-2)" }}>
      <Icon name={icon} size={18} stroke={toneColor[tone]} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </div>
  );
}

function Tag({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span className={`num card-${tone}`}
          style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r-sm)", padding: "4px 9px", fontSize: "10px", letterSpacing: ".14em", color: toneColor[tone] }}>
      {children}
    </span>
  );
}

function Activity({ icon, title, empty, children }: { icon: IconName; title: string; empty: string; children: React.ReactNode }) {
  const has = Array.isArray(children) ? children.length > 0 : !!children;
  return (
    <div className="card p-6">
      <div className="flex items-center gap-3">
        <IconPlate name={icon} size={32} />
        <h3 style={{ margin: 0, fontSize: "15px", fontWeight: 600 }}>{title}</h3>
      </div>
      <div className="mt-3">{has ? children : <p style={{ padding: "26px 0", fontSize: "13.5px", color: "var(--ink-5)" }}>{empty}</p>}</div>
    </div>
  );
}

function DemoBanner({ busy, error, onCreate }: { busy: boolean; error: string | null; onCreate: () => void }) {
  return (
    <section className="card flex flex-wrap items-center justify-between gap-6 p-6 sm:p-8"
             style={{ borderStyle: "dashed" }}>
      <div className="flex gap-4">
        <IconPlate name="image" />
        <div>
          <p className="lbl">Demonstration mode</p>
          <h2 style={{ margin: "8px 0 0", fontSize: "18px", fontWeight: 600 }}>See the whole operator without connecting a store</h2>
          <p style={{ margin: "8px 0 0", maxWidth: "70ch", fontSize: "13.5px", lineHeight: 1.6, color: "var(--ink-2)" }}>
            60 days of Amazon, Shopify and WooCommerce performance. Everything it produces stays
            labelled as demonstration data and never counts as money the Operator earned.
          </p>
          {error && <p className="num" style={{ margin: "10px 0 0", fontSize: "12px", color: "var(--unproven)" }}>{error}</p>}
        </div>
      </div>
      <button onClick={onCreate} disabled={busy} className="btn-quiet shrink-0">
        {busy ? "BUILDING…" : "LAUNCH DEMO"}
      </button>
    </section>
  );
}

function ChannelMix({ commerce }: { commerce: CommerceDashboard }) {
  // Channels are told apart by position and label, not by a rainbow: saturated
  // colour is reserved for claims about whether money was proven.
  const shade = ["#8FA1A8", "#6B7C84", "#516066", "#3E4C52", "#33403F"];
  return (
    <section className="card p-6 sm:p-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <IconPlate name="plug" size={34} />
          <div>
            <p className="lbl">Channel mix</p>
            <h2 style={{ margin: "4px 0 0", fontSize: "18px", fontWeight: 600, letterSpacing: "-.015em" }}>Every channel, one profit view</h2>
          </div>
        </div>
        <span className="num" style={{ fontSize: "11.5px", letterSpacing: ".1em", color: "var(--ink-4)" }}>
          {commerce.channels.length} CONNECTED
        </span>
      </div>
      <div className="mt-7 flex gap-px overflow-hidden" style={{ height: "10px", borderRadius: "999px", background: "var(--raised)" }}>
        {commerce.channels.map((channel, i) => (
          <div key={channel.channel_id}
               style={{ width: `${channel.share_percentage}%`, background: shade[i % shade.length] }}
               title={`${channel.display_name}: ${channel.share_percentage}%`} />
        ))}
      </div>
      <div className="mt-6">
        {commerce.channels.map((channel, i) => (
          <div key={channel.channel_id} className="flex items-center justify-between gap-4 py-3.5"
               style={{ borderTop: i === 0 ? "none" : "1px solid var(--line-faint)" }}>
            <div className="flex min-w-0 items-center gap-3">
              <span style={{ width: "8px", height: "8px", borderRadius: "999px", background: shade[i % shade.length], flexShrink: 0 }} />
              <div className="min-w-0">
                <p className="truncate" style={{ margin: 0, fontSize: "14px", fontWeight: 600 }}>{channel.display_name}</p>
                <p className="num" style={{ margin: "4px 0 0", fontSize: "11px", color: "var(--ink-5)" }}>
                  {channel.share_percentage}% OF REVENUE
                </p>
              </div>
            </div>
            <div className="text-right">
              <p className="num" style={{ margin: 0, fontSize: "16px", fontWeight: 500 }}>{money(channel.revenue, channel.currency)}</p>
              <p className="num" style={{ margin: "4px 0 0", fontSize: "11px", color: "var(--ink-4)" }}>
                {channel.profit == null ? "PROFIT WITHHELD" : `${money(channel.profit, channel.currency)} PROFIT`}
              </p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function Freshness({ value, syncedAt }: { value: RoiDashboard["freshness"]; syncedAt: string | null }) {
  const good = value === "fresh";
  const label = value === "no_data" ? "NO DATA" : good ? "LIVE & SYNCED" : value === "stale" ? "SYNC RECOMMENDED" : "OUTDATED";
  return (
    <div className="text-right">
      <div className="num flex items-center justify-end gap-2.5"
           style={{ fontSize: "11.5px", letterSpacing: ".08em", color: good ? "var(--proven)" : "var(--waiting)" }}>
        <span className={`dot ${good ? "dot-proven" : "dot-waiting"}`} />
        {label}
      </div>
      {syncedAt && <p className="num" style={{ margin: "7px 0 0", fontSize: "11px", color: "var(--ink-5)" }}>{new Date(syncedAt).toLocaleString()}</p>}
    </div>
  );
}

function CostRow({ label, value, revenue, currency }: { label: string; value: number | null | undefined; revenue: number | null | undefined; currency: string }) {
  const share = value != null && revenue ? Math.min(value / revenue * 100, 100) : 0;
  return (
    <div>
      <div className="flex items-end justify-between gap-4">
        <div>
          <p style={{ margin: 0, fontSize: "13.5px", color: "var(--ink-2)" }}>{label}</p>
          <p className="num" style={{ margin: "5px 0 0", fontSize: "11.5px", color: "var(--ink-5)" }}>
            {share ? `${share.toFixed(1)}% OF REVENUE` : "NOT IMPORTED"}
          </p>
        </div>
        <p className="num" style={{ margin: 0, fontSize: "15px", fontWeight: 500, color: value == null ? "var(--ink-5)" : "var(--ink)" }}>
          {value == null ? "—" : money(value, currency)}
        </p>
      </div>
      <div className="mt-3 overflow-hidden" style={{ height: "6px", borderRadius: "999px", background: "var(--raised)" }}>
        <div style={{ height: "100%", width: `${share}%`, borderRadius: "999px", background: "#3E4C52" }} />
      </div>
    </div>
  );
}

function money(value: number | null | undefined, currency: string | null | undefined) {
  return value == null ? "—" : value.toLocaleString(undefined, { style: "currency", currency: currency ?? "USD", maximumFractionDigits: 0 });
}
