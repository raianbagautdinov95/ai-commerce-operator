/* One stroke weight, one grid, one size. Icons label a thing; they never carry
   a claim about money on their own — that is what colour is for. */

type Props = { size?: number; className?: string; stroke?: string };

const base = (size: number, stroke: string) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none" as const,
  stroke,
  strokeWidth: 1.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
});

export type IconName =
  | "revenue" | "profit" | "orders" | "margin" | "impact"
  | "operator" | "tag" | "box" | "calendar" | "chevron"
  | "spark" | "check" | "plug" | "search" | "gem"
  | "target" | "bolt" | "shield" | "clock" | "alert"
  | "image" | "truck" | "list" | "lock" | "key";

const PATHS: Record<IconName, React.ReactNode> = {
  revenue: <><path d="M3 20h18" /><rect x="5" y="11" width="3.5" height="6" rx="1" /><rect x="10.25" y="7" width="3.5" height="10" rx="1" /><rect x="15.5" y="4" width="3.5" height="13" rx="1" /></>,
  profit: <><path d="M12 3v18" /><path d="M16 7.5c0-1.7-1.8-2.5-4-2.5s-4 .8-4 2.5S9.8 10 12 10s4 .8 4 2.5S14.2 15 12 15s-4-.8-4-2.5" /></>,
  orders: <><path d="M5.5 8h13l-1 11.5a1.5 1.5 0 0 1-1.5 1.4H8a1.5 1.5 0 0 1-1.5-1.4Z" /><path d="M8.75 8V6a3.25 3.25 0 0 1 6.5 0v2" /></>,
  margin: <><circle cx="12" cy="12" r="8.5" /><path d="M12 3.5v8.5h8.5" /></>,
  impact: <><path d="M3.5 17 9 11l3.5 3.5L20 7" /><path d="M15 7h5v5" /></>,
  operator: <><rect x="4" y="8" width="16" height="12" rx="2.5" /><path d="M12 4.5V8" /><circle cx="12" cy="3.5" r="1.2" /><path d="M9 13v1.5M15 13v1.5" /><path d="M1.5 13v3M22.5 13v3" /></>,
  tag: <><path d="M3.5 11.2V5.4A1.9 1.9 0 0 1 5.4 3.5h5.8a2 2 0 0 1 1.4.6l7.3 7.3a2 2 0 0 1 0 2.8l-5.7 5.7a2 2 0 0 1-2.8 0L4.1 12.6a2 2 0 0 1-.6-1.4Z" /><circle cx="8.2" cy="8.2" r="1.4" /></>,
  box: <><path d="M12 3 20 7.2v9.6L12 21l-8-4.2V7.2Z" /><path d="M4 7.2 12 11.5l8-4.3" /><path d="M12 11.5V21" /></>,
  calendar: <><rect x="3.5" y="5" width="17" height="15.5" rx="2.5" /><path d="M3.5 9.75h17" /><path d="M8 3v4M16 3v4" /></>,
  chevron: <path d="m6.5 9.5 5.5 5.5 5.5-5.5" />,
  spark: <><path d="M12 3.5 13.9 9 19.5 11l-5.6 2L12 18.5 10.1 13 4.5 11 10.1 9Z" /><path d="M18.5 4v3M20 5.5h-3" /></>,
  check: <><rect x="3.5" y="3.5" width="17" height="17" rx="3" /><path d="M8 12.4l2.7 2.7L16.5 9.3" /></>,
  plug: <><path d="M9 3.5v5M15 3.5v5" /><path d="M6 8.5h12v3a6 6 0 0 1-6 6 6 6 0 0 1-6-6Z" /><path d="M12 17.5v3" /></>,
  search: <><circle cx="10.8" cy="10.8" r="6.3" /><path d="m15.5 15.5 4.5 4.5" /></>,
  gem: <><path d="m12 3 6 4.5-6 13.5L6 7.5Z" /><path d="M6 7.5h12" /></>,
  target: <><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="4.5" /><circle cx="12" cy="12" r=".9" fill="currentColor" stroke="none" /></>,
  bolt: <path d="M13.5 3 6 13.2h5L10.5 21 18 10.8h-5Z" />,
  shield: <><path d="M12 3.2 19.5 6v6.2c0 4-3.1 7.2-7.5 8.6-4.4-1.4-7.5-4.6-7.5-8.6V6Z" /><path d="M9.2 12.2l2 2 3.6-3.8" /></>,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5.3l3.3 2" /></>,
  alert: <><path d="M12 4.2 21 19.5H3Z" /><path d="M12 10v4" /><circle cx="12" cy="16.8" r=".9" fill="currentColor" stroke="none" /></>,
  image: <><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><circle cx="9" cy="10" r="1.7" /><path d="m4.5 17.5 4.7-4.3 4 3.4 2.6-2.3 3.7 3.2" /></>,
  truck: <><path d="M2.5 6.5h10.5v9.5H2.5Z" /><path d="M13 10h3.6l2.9 3v3H13Z" /><circle cx="7" cy="18" r="1.8" /><circle cx="17" cy="18" r="1.8" /></>,
  list: <><path d="M8.5 6.5h11M8.5 12h11M8.5 17.5h11" /><path d="M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01" /></>,
  lock: <><rect x="4.5" y="10" width="15" height="10.5" rx="2.5" /><path d="M8 10V7.5a4 4 0 0 1 8 0V10" /></>,
  key: <><circle cx="8" cy="12" r="4" /><path d="M12 12h9" /><path d="M17.5 12v3.2M20 12v2.2" /></>,
};

export function Icon({ name, size = 18, className, stroke = "currentColor" }: Props & { name: IconName }) {
  return (
    <svg {...base(size, stroke)} className={className} aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}

/* A tinted plate behind an icon — used where an icon leads a figure. */
export function IconPlate({ name, tone = "neutral", size = 38 }: { name: IconName; tone?: "neutral" | "proven" | "waiting" | "unproven"; size?: number }) {
  const colour = {
    neutral: "var(--ink-2)",
    proven: "var(--proven)",
    waiting: "var(--waiting)",
    unproven: "var(--unproven)",
  }[tone];
  const bg = {
    neutral: "rgba(148,190,210,.05)",
    proven: "rgba(74,222,128,.09)",
    waiting: "rgba(251,191,36,.09)",
    unproven: "rgba(248,113,113,.09)",
  }[tone];
  return (
    <span className="flex shrink-0 items-center justify-center"
          style={{ width: size, height: size, borderRadius: "10px", background: bg, color: colour }}>
      <Icon name={name} size={Math.round(size * 0.5)} />
    </span>
  );
}
