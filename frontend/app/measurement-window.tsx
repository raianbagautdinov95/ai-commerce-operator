"use client";

import { useState } from "react";
import { Icon } from "./icons";

/* ---------------------------------------------------------------------------
   The one form that asks for a stretch of money.

   It is asked twice in the product's life — the fortnight before a change, and
   the stretch after it — and those two numbers are the entire basis of the
   payroll. For a while the proposals screen asked with a form and the payroll
   asked with three browser prompts in a row, which is the same question posed
   two ways in one product; whichever one somebody met first taught them the
   wrong thing about the other.

   Nothing here is pre-filled but the length of the window. A payroll built on
   figures the app guessed would be a number nobody should believe, so it asks
   and refuses to submit until all three are real.
   --------------------------------------------------------------------------- */

export type MetricWindow = { days: number; spend: number; revenue: number };

export default function MeasurementWindowForm({
  explanation, defaultDays, currency, busy, submitLabel, onSubmit, onCancel,
}: {
  explanation: string;
  defaultDays: number;
  currency: string;
  busy: boolean;
  submitLabel: string;
  onSubmit: (window: MetricWindow) => void;
  onCancel?: () => void;
}) {
  const [days, setDays] = useState(String(defaultDays));
  const [spend, setSpend] = useState("");
  const [revenue, setRevenue] = useState("");

  const numeric = (value: string) => value !== "" && !Number.isNaN(Number(value));
  const ready = Number(days) > 0 && numeric(spend) && numeric(revenue);

  function submit() {
    if (!ready || busy) return;
    onSubmit({ days: Number(days), spend: Number(spend), revenue: Number(revenue) });
  }

  return (
    <div className="px-6 py-5"
         style={{ borderTop: "1px solid var(--line-faint)", background: "#05070A" }}>
      <p style={{ margin: 0, maxWidth: "78ch", fontSize: "13px", lineHeight: 1.7, color: "var(--ink-2)" }}>
        {explanation}
      </p>
      <div className="mt-4 flex flex-wrap items-end gap-3"
           onKeyDown={(e) => e.key === "Enter" && submit()}>
        <Field label="Days" value={days} onChange={setDays} width="90px" />
        <Field label={`Spent (${currency})`} value={spend} onChange={setSpend} width="150px" />
        <Field label={`Earned (${currency})`} value={revenue} onChange={setRevenue} width="150px" />
        <button onClick={submit} disabled={busy || !ready}
                className="btn-primary inline-flex items-center gap-2">
          <Icon name="check" size={13} /> {busy ? "RECORDING…" : submitLabel}
        </button>
        {onCancel && (
          <button onClick={onCancel} disabled={busy} className="btn-quiet">CANCEL</button>
        )}
      </div>
      {!ready && (
        <p className="num" style={{ margin: "12px 0 0", fontSize: "10.5px", letterSpacing: ".1em", color: "var(--ink-6)" }}>
          ALL THREE ARE NEEDED — NOTHING IS ASSUMED FOR YOU
        </p>
      )}
    </div>
  );
}

function Field({ label, value, onChange, width }: {
  label: string; value: string; onChange: (v: string) => void; width: string;
}) {
  return (
    <label style={{ display: "block" }}>
      <span className="lbl">{label}</span>
      <input className="field num" style={{ width, marginTop: "7px" }} inputMode="decimal"
             value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}
