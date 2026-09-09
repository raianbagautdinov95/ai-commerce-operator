"use client";

import { useEffect, useRef, useState } from "react";
import { Icon, type IconName } from "./icons";

/* ---------------------------------------------------------------------------
   The performance chart.

   Three kinds of line live here and they are never allowed to look alike:

   - Revenue is measured. Solid.
   - Net profit is derived — the period's margin applied to each day, because
     costs are not reported daily. It is drawn thinner and the legend says so.
     When costs are incomplete the margin is unknown and the line is absent
     rather than guessed.
   - The forecast is a straight-line fit of the window, extended forward. It is
     dotted, labelled, and never added to any total anywhere in the app.

   The markers are real operator actions at the date they were applied. Their
   colour is the usual claim: green proven, amber still measuring, red undone.
   --------------------------------------------------------------------------- */

export type ChartAction = {
  id: string;
  date: string;
  title: string;
  detail: string;
  tone: "proven" | "waiting" | "unproven";
  icon: IconName;
};

export type ChartDay = { date: string; revenue: number; order_items: number };

const TONE: Record<ChartAction["tone"], string> = {
  proven: "#4ADE80",
  waiting: "#FBBF24",
  unproven: "#F87171",
};

const REVENUE = "#4ADE80";
const PROFIT = "#8B8CF0";

/** Days of history required before any projection is drawn. */
const MIN_DAYS_FOR_PROJECTION = 7;
/** Of those, days that actually sold something. Two points define a line
 *  perfectly and predict nothing. */
const MIN_SELLING_DAYS_FOR_PROJECTION = 3;

/* --- maths, all of it deterministic --------------------------------------- */

function leastSquares(values: number[]) {
  const n = values.length;
  let sx = 0, sy = 0, sxx = 0, sxy = 0;
  for (let i = 0; i < n; i++) { sx += i; sy += values[i]; sxx += i * i; sxy += i * values[i]; }
  const denominator = n * sxx - sx * sx;
  if (!denominator) return { intercept: values[n - 1] ?? 0, slope: 0 };
  const slope = (n * sxy - sx * sy) / denominator;
  return { slope, intercept: (sy - slope * sx) / n };
}

function niceCeiling(value: number) {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / (magnitude / 2)) * (magnitude / 2);
}

/** Catmull-Rom through the points, expressed as cubic beziers. */
function smooth(points: [number, number][]) {
  if (points.length < 2) return "";
  let d = `M ${points[0][0]} ${points[0][1]}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i - 1] ?? points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] ?? p2;
    d += ` C ${p1[0] + (p2[0] - p0[0]) / 6} ${p1[1] + (p2[1] - p0[1]) / 6},`
       + ` ${p2[0] - (p3[0] - p1[0]) / 6} ${p2[1] - (p3[1] - p1[1]) / 6},`
       + ` ${p2[0]} ${p2[1]}`;
  }
  return d;
}

function compact(value: number, currency: string) {
  const symbol = currency === "EUR" ? "€" : currency === "GBP" ? "£" : "$";
  if (Math.abs(value) >= 1000) return `${symbol}${(value / 1000).toFixed(1)}K`;
  return `${symbol}${Math.round(value)}`;
}

function full(value: number, currency: string) {
  return value.toLocaleString(undefined, { style: "currency", currency, maximumFractionDigits: 0 });
}

function dayLabel(date: string) {
  return new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/* --- the component -------------------------------------------------------- */

export default function ProfitChart({
  daily, margin, currency, actions, forecastDays = 7, empty,
}: {
  daily: ChartDay[];
  margin: number | null;
  currency: string | null | undefined;
  actions: ChartAction[];
  forecastDays?: number;
  /** What to say when there is not yet a line to draw. The default tells you to
   *  connect a channel, which is wrong advice once one is connected. */
  empty?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(920);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    setWidth(element.clientWidth);
    return () => observer.disconnect();
  }, []);

  const unit = currency ?? "USD";

  if (daily.length < 2) {
    return (
      <div className="mt-8 flex items-center justify-center px-6 text-center"
           style={{ height: "300px", border: "1px dashed var(--line)", borderRadius: "var(--r)", fontSize: "13.5px", color: "var(--ink-5)" }}>
        {empty ?? "Connect a sales channel, or launch the demonstration, to reveal performance."}
      </div>
    );
  }

  // A trend needs observations, not two dots. Fitted through 25 August and
  // 6 September alone, the line projected $234 from a store that had earned
  // $48 — five times the real total, in green, on the first screen anyone
  // sees. Below this much history nothing is drawn at all, and the legend
  // drops with it: when in doubt the number goes down, never up.
  const sellingDays = daily.reduce((n, d) => n + (d.revenue > 0 ? 1 : 0), 0);
  const horizon = daily.length >= MIN_DAYS_FOR_PROJECTION
    && sellingDays >= MIN_SELLING_DAYS_FOR_PROJECTION ? forecastDays : 0;

  const padL = 56, padR = horizon > 0 ? 76 : 18, padT = 18, padB = 30;
  const height = 300;
  const plotW = Math.max(width - padL - padR, 120);
  const plotH = height - padT - padB;
  const historyW = horizon > 0 ? plotW * 0.76 : plotW;

  const revenue = daily.map((d) => d.revenue);
  const profit = margin == null ? null : revenue.map((v) => v * margin);

  // The fit runs over positions in `daily`, so it is only a per-day slope while
  // the series has one entry per calendar day. The API fills the quiet days in
  // for exactly that reason; without them a fortnight's gap counted as one step
  // and the slope came out roughly twelve times too steep.
  const fit = leastSquares(revenue);
  const projection = horizon > 0
    ? Array.from({ length: horizon }, (_, k) =>
        Math.max(fit.intercept + fit.slope * (revenue.length - 1 + k + 1), 0))
    : [];
  const projectedProfit = margin == null ? [] : projection.map((v) => v * margin);

  const max = niceCeiling(Math.max(...revenue, ...projection, 1) * 1.12);
  const x = (i: number) => padL + (i / (daily.length - 1)) * historyW;
  const xf = (k: number) => padL + historyW + ((k + 1) / Math.max(projection.length, 1)) * (plotW - historyW);
  const y = (v: number) => padT + plotH * (1 - v / max);

  const revPoints = revenue.map((v, i) => [x(i), y(v)] as [number, number]);
  const profitPoints = profit?.map((v, i) => [x(i), y(v)] as [number, number]);
  const revForecast = [revPoints[revPoints.length - 1], ...projection.map((v, k) => [xf(k), y(v)] as [number, number])];
  const profitForecast = profitPoints
    ? [profitPoints[profitPoints.length - 1], ...projectedProfit.map((v, k) => [xf(k), y(v)] as [number, number])]
    : null;

  const areaTo = (points: [number, number][]) =>
    `${smooth(points)} L ${points[points.length - 1][0]} ${padT + plotH} L ${points[0][0]} ${padT + plotH} Z`;

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => max * f);

  // Markers only where the applied date lands inside the window, and never so
  // many that they stop being readable.
  const byDate = new Map(daily.map((d, i) => [d.date, i]));
  const placed: { action: ChartAction; index: number; labelX: number }[] = [];
  for (const action of actions) {
    const index = byDate.get(action.date);
    if (index == null) continue;
    const labelX = Math.min(Math.max(x(index) - 18, padL), padL + historyW - 168);
    if (placed.some((p) => Math.abs(p.labelX - labelX) < 176)) continue;
    placed.push({ action, index, labelX });
    if (placed.length === 3) break;
  }

  const xLabels = daily.filter((_, i) => i % Math.ceil(daily.length / 7) === 0);

  return (
    <div ref={box} className="relative mt-7 w-full">
      <svg width={width} height={height} style={{ display: "block", overflow: "visible" }}
           onMouseLeave={() => setHover(null)}>
        <defs>
          <linearGradient id="revfill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={REVENUE} stopOpacity=".22" />
            <stop offset="100%" stopColor={REVENUE} stopOpacity="0" />
          </linearGradient>
          <linearGradient id="proffill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={PROFIT} stopOpacity=".2" />
            <stop offset="100%" stopColor={PROFIT} stopOpacity="0" />
          </linearGradient>
        </defs>

        {ticks.map((value) => (
          <g key={value}>
            <line x1={padL} y1={y(value)} x2={padL + plotW} y2={y(value)} stroke="var(--line-faint)" strokeWidth="1" />
            <text x={padL - 12} y={y(value) + 4} textAnchor="end"
                  fill="var(--ink-5)" fontSize="10.5" fontFamily="IBM Plex Mono, monospace">
              {compact(value, unit)}
            </text>
          </g>
        ))}

        {profitPoints && <path d={areaTo(profitPoints)} fill="url(#proffill)" />}
        <path d={areaTo(revPoints)} fill="url(#revfill)" />

        {profitPoints && <path d={smooth(profitPoints)} fill="none" stroke={PROFIT} strokeWidth="1.75" />}
        <path d={smooth(revPoints)} fill="none" stroke={REVENUE} strokeWidth="2.25" />

        {projection.length > 0 && (
          <>
            <line x1={padL + historyW} y1={padT} x2={padL + historyW} y2={padT + plotH}
                  stroke="var(--line-soft)" strokeWidth="1" strokeDasharray="3 4" />
            {profitForecast && (
              <path d={smooth(profitForecast)} fill="none" stroke={PROFIT} strokeWidth="1.75"
                    strokeDasharray="1.5 5" strokeLinecap="round" opacity=".85" />
            )}
            <path d={smooth(revForecast)} fill="none" stroke={REVENUE} strokeWidth="2"
                  strokeDasharray="1.5 5" strokeLinecap="round" opacity=".85" />

            <text x={padL + plotW + 8} y={y(projection[projection.length - 1]) - 2}
                  fill={REVENUE} fontSize="13" fontWeight="600" fontFamily="IBM Plex Mono, monospace">
              {compact(projection[projection.length - 1], unit)}
            </text>
            <text x={padL + plotW + 8} y={y(projection[projection.length - 1]) + 12}
                  fill="var(--ink-5)" fontSize="9" letterSpacing="1.1" fontFamily="IBM Plex Mono, monospace">
              PROJECTION
            </text>
            {projectedProfit.length > 0 && (
              <text x={padL + plotW + 8} y={y(projectedProfit[projectedProfit.length - 1]) + 4}
                    fill={PROFIT} fontSize="13" fontWeight="600" fontFamily="IBM Plex Mono, monospace">
                {compact(projectedProfit[projectedProfit.length - 1], unit)}
              </text>
            )}
          </>
        )}

        {/* the last measured point */}
        <circle cx={revPoints[revPoints.length - 1][0]} cy={revPoints[revPoints.length - 1][1]}
                r="6" fill="#08090B" stroke={REVENUE} strokeWidth="2.5" />

        {placed.map(({ action, index }) => (
          <g key={action.id}>
            <line x1={x(index)} y1={padT + 4} x2={x(index)} y2={y(revenue[index]) - 7}
                  stroke={TONE[action.tone]} strokeWidth="1" strokeDasharray="2 4" opacity=".65" />
            <circle cx={x(index)} cy={y(revenue[index])} r="4.5" fill={TONE[action.tone]} />
          </g>
        ))}

        {xLabels.map((d) => (
          <text key={d.date} x={x(byDate.get(d.date)!)} y={padT + plotH + 20} textAnchor="middle"
                fill="var(--ink-5)" fontSize="10.5" fontFamily="IBM Plex Mono, monospace">
            {dayLabel(d.date)}
          </text>
        ))}

        {hover != null && (
          <line x1={x(hover)} y1={padT} x2={x(hover)} y2={padT + plotH}
                stroke="var(--line-soft)" strokeWidth="1" />
        )}

        <rect x={padL} y={padT} width={historyW} height={plotH} fill="transparent"
              onMouseMove={(event) => {
                const bounds = event.currentTarget.getBoundingClientRect();
                const ratio = (event.clientX - bounds.left) / bounds.width;
                setHover(Math.min(daily.length - 1, Math.max(0, Math.round(ratio * (daily.length - 1)))));
              }} />
      </svg>

      {/* markers sit in HTML above the plot so their text stays selectable */}
      {placed.map(({ action, labelX }) => (
        <div key={action.id} className="pointer-events-none absolute"
             style={{ left: labelX, top: 0, width: "170px" }}>
          <div className="flex items-start gap-2.5">
            <span className="flex shrink-0 items-center justify-center"
                  style={{ width: "26px", height: "26px", borderRadius: "999px",
                           border: `1px solid ${TONE[action.tone]}`, color: TONE[action.tone],
                           background: "rgba(8,9,11,.9)" }}>
              <Icon name={action.icon} size={13} />
            </span>
            <div style={{ marginTop: "-1px" }}>
              <p className="num" style={{ margin: 0, fontSize: "10.5px", color: "var(--ink-5)" }}>
                {dayLabel(action.date).toUpperCase()}
              </p>
              <p style={{ margin: "3px 0 0", fontSize: "12.5px", fontWeight: 600, lineHeight: 1.25 }}>{action.title}</p>
              <p className="num" style={{ margin: "3px 0 0", fontSize: "11px", color: TONE[action.tone] }}>{action.detail}</p>
            </div>
          </div>
        </div>
      ))}

      {hover != null && (
        <div className="pointer-events-none absolute"
             style={{
               left: Math.min(Math.max(x(hover) - 92, 0), Math.max(width - 190, 0)),
               top: Math.min(y(revenue[hover]) + 16, height - 40),
               width: "184px", background: "rgba(10,14,17,.97)", border: "1px solid var(--line-soft)",
               borderRadius: "var(--r)", padding: "13px 15px",
               boxShadow: "0 18px 40px -18px rgba(0,0,0,.95)",
             }}>
          <p className="num" style={{ margin: 0, fontSize: "11.5px", color: "var(--ink-2)" }}>
            {dayLabel(daily[hover].date).toUpperCase()}
          </p>
          <Row colour={REVENUE} label="Revenue" value={full(revenue[hover], unit)} />
          {profit
            ? <Row colour={PROFIT} label="Net profit" value={full(profit[hover], unit)} />
            : <Row colour="var(--waiting)" label="Net profit" value="Withheld" />}
          <Row label="Orders" value={String(daily[hover].order_items)} />
        </div>
      )}

      <div className="mt-5 flex flex-wrap items-center justify-center gap-x-7 gap-y-3">
        <Key><span style={{ width: "16px", height: "2px", background: REVENUE, display: "inline-block" }} /> Revenue</Key>
        <Key>
          <span style={{ width: "16px", height: "2px", background: profit ? PROFIT : "var(--ink-6)", display: "inline-block" }} />
          {profit ? "Net profit · period margin per day" : "Net profit · withheld until costs are complete"}
        </Key>
        <Key><span className="dot dot-proven" /> Operator actions</Key>
        {projection.length > 0 && (
          <Key>
            <span style={{ width: "16px", height: "0", borderTop: "2px dotted var(--ink-4)", display: "inline-block" }} />
            Projection · not measured
          </Key>
        )}
      </div>
    </div>
  );
}

function Row({ colour, label, value }: { colour?: string; label: string; value: string }) {
  return (
    <div className="mt-2.5 flex items-center justify-between gap-4">
      <span className="flex items-center gap-2" style={{ fontSize: "12.5px", color: "var(--ink-2)" }}>
        {colour && <span style={{ width: "6px", height: "6px", borderRadius: "999px", background: colour }} />}
        {label}
      </span>
      <span className="num" style={{ fontSize: "12.5px" }}>{value}</span>
    </div>
  );
}

function Key({ children }: { children: React.ReactNode }) {
  return (
    <span className="flex items-center gap-2.5" style={{ fontSize: "12px", color: "var(--ink-3)" }}>
      {children}
    </span>
  );
}
