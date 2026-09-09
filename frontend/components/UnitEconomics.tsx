"use client";

// Where each dollar of the selling price goes: COGS + fees + ads + profit.
export default function UnitEconomics({
  price,
  cogs,
  referral,
  fba,
  ads,
  profit,
}: {
  price: number;
  cogs: number;
  referral: number;
  fba: number;
  ads: number;
  profit: number;
}) {
  if (!price) return null;
  const segments = [
    { label: "COGS", value: cogs, color: "bg-slate-400" },
    { label: "Referral", value: referral, color: "bg-amber-400" },
    { label: "FBA", value: fba, color: "bg-orange-400" },
    { label: "Ads", value: ads, color: "bg-rose-400" },
    { label: "Profit", value: Math.max(profit, 0), color: "bg-green-500" },
  ].filter((s) => s.value > 0);

  return (
    <div className="mt-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        Unit economics · ${price.toFixed(2)} price
      </p>
      <div className="mt-2 flex h-4 w-full overflow-hidden rounded">
        {segments.map((s) => (
          <div
            key={s.label}
            className={s.color}
            style={{ width: `${(s.value / price) * 100}%` }}
            title={`${s.label}: $${s.value.toFixed(2)}`}
          />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {segments.map((s) => (
          <span key={s.label} className="flex items-center gap-1.5">
            <span className={`inline-block h-2.5 w-2.5 rounded-sm ${s.color}`} />
            <span className="text-slate-600">{s.label}</span>
            <span className="font-medium tabular-nums text-slate-800">${s.value.toFixed(2)}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
