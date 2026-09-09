"use client";

import { Fragment, useEffect, useState } from "react";
import {
  getGuardrails, getPayroll, listActions, markActionApplied, measureAction,
  revertAction, updateGuardrails,
  type GuardrailPolicy, type OperatorAction, type Payroll,
} from "../../lib/api";
import { Icon, IconPlate } from "../icons";
import MeasurementWindowForm, { type MetricWindow } from "../measurement-window";

const statusLabel: Record<string, string> = {
  proposed: "SUGGESTED",
  applied: "IN EFFECT",
  measured: "PRICED",
  dismissed: "DECLINED",
  reverted: "UNDONE",
};

// A status is a claim about money, so it gets a colour only where it makes one:
// priced is proven, in effect is still waiting, undone did not work out.
const statusColor: Record<string, string> = {
  proposed: "var(--ink-3)",
  applied: "var(--waiting)",
  measured: "var(--proven)",
  dismissed: "var(--ink-5)",
  reverted: "var(--unproven)",
};

function money(value: number, currency: string) {
  return new Intl.NumberFormat(undefined, {
    style: "currency", currency, maximumFractionDigits: 2,
  }).format(value);
}

/** A day, as a day. The API sends ISO dates for window boundaries. */
function fmtDay(iso: string | null): string {
  if (!iso) return "—";
  const parsed = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(parsed.getTime())
    ? iso
    : parsed.toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" });
}

function fmtDate(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime())
    ? iso
    : parsed.toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" });
}

export default function PayrollPage() {
  const [payroll, setPayroll] = useState<Payroll | null>(null);
  const [actions, setActions] = useState<OperatorAction[]>([]);
  const [limits, setLimits] = useState<GuardrailPolicy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // Which row has its measurement-window form open, and which question it asks.
  const [asking, setAsking] = useState<{ id: string; mode: "apply" | "measure" } | null>(null);

  async function refresh() {
    try {
      const [p, a, g] = await Promise.all([
        getPayroll(30, true), listActions(), getGuardrails(),
      ]);
      setPayroll(p);
      setActions(a);
      setLimits(g);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function toggleOperator() {
    if (!limits) return;
    setBusy("limits");
    try {
      setLimits(await updateGuardrails({ enabled: !limits.enabled }));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function undo(action: OperatorAction) {
    // The Operator does not reach into the account here — you do, using these
    // values. Showing them as JSON asked somebody to read a data structure to
    // find out what their store is about to be set to.
    const restore = Object.entries(action.revert_to ?? {})
      .map(([field, value]) => `${field} → ${String(value)}`)
      .join(", ");
    if (!window.confirm(
      `Undo this change?\n\nSet ${action.target} back to: ${restore || "its previous value"}.\n\n`
      + "The Operator will not do it for you — this records the undo and hands you "
      + "the value to restore.")) return;
    setBusy(action.id);
    try {
      await revertAction(action.id);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => { refresh(); }, []);

  async function apply(action: OperatorAction, baseline: MetricWindow) {
    setBusy(action.id);
    try {
      await markActionApplied(action.id, baseline);
      setAsking(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function measure(action: OperatorAction, outcome: MetricWindow) {
    setBusy(action.id);
    try {
      await measureAction(action.id, outcome);
      setAsking(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const currency = payroll?.currency ?? "USD";
  const positive = (payroll?.net ?? 0) >= 0;

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex items-center gap-3">
          <IconPlate name="profit" />
          <p className="lbl">Operator payroll</p>
        </div>
        <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
          Did the operator earn its keep?
        </h1>
        <p style={{ margin: "16px 0 0", maxWidth: "66ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          Every suggestion you put into effect is priced against what happened before it. Only
          completed measurement windows count toward the total — a number here means somebody
          measured it, not that somebody hoped for it.
        </p>
      </section>

      {payroll && (
        <div className="card p-7 sm:p-9">
          <div className="flex flex-wrap items-end justify-between gap-8">
            <div>
              <p className="lbl">Net over the last {payroll.period_days} days</p>
              <p className={`num ${positive ? "glow-proven" : ""}`}
                 style={{ margin: "18px 0 0", fontSize: "52px", fontWeight: 500, letterSpacing: "-.04em", lineHeight: 1,
                          color: positive ? "var(--proven)" : "var(--unproven)" }}>
                {money(payroll.net, currency)}
              </p>
              <p style={{ margin: "16px 0 0", fontSize: "13.5px", color: "var(--ink-2)" }}>
                {money(payroll.settled_impact, currency)} proven impact, less{" "}
                {money(payroll.operator_cost, currency)} cost
                {payroll.operator_cost === 0 ? " (free trial)" : ""}
              </p>
            </div>
            <span className="num flex items-center gap-2"
                  style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r-sm)", padding: "9px 16px",
                           fontSize: "11.5px", letterSpacing: ".12em",
                           borderColor: payroll.paid_for_itself ? "rgba(74,222,128,.3)" : "var(--line)",
                           background: payroll.paid_for_itself ? "rgba(74,222,128,.05)" : "transparent",
                           color: payroll.paid_for_itself ? "var(--proven)" : "var(--ink-4)" }}>
              <Icon name={payroll.paid_for_itself ? "check" : "clock"} size={14} />
              {payroll.paid_for_itself ? "PAID FOR ITSELF" : "NOT YET PROVEN"}
            </span>
          </div>

          {(payroll.provisional_impact !== 0 || payroll.awaiting_measurement > 0) && (
            <div className="mt-8 p-5" style={{ background: "var(--raised)", borderRadius: "var(--r)", fontSize: "13.5px", lineHeight: 1.65, color: "var(--ink-2)" }}>
              {payroll.provisional_impact !== 0 && (
                <p style={{ margin: 0 }}>
                  <span className="num" style={{ color: "var(--waiting)" }}>
                    {money(payroll.provisional_impact, currency)}
                  </span>{" "}
                  measured but still inside its window, so it is not counted above.
                </p>
              )}
              {payroll.awaiting.length > 0 && (
                <div style={{ margin: payroll.provisional_impact !== 0 ? "14px 0 0" : 0 }}>
                  <p style={{ margin: 0 }}>
                    {payroll.awaiting.length} in effect, waiting to be priced.
                  </p>
                  {/* "Waiting" alone is something nobody can act on. A window
                      still running is the calendar; a window that closed while
                      nothing was syncing is a fault, and telling somebody to be
                      patient about it is wrong advice. */}
                  <ul style={{ margin: "12px 0 0", padding: 0, listStyle: "none" }}>
                    {payroll.awaiting.map((item) => (
                      <li key={item.action_id}
                          style={{ marginTop: "10px", paddingTop: "10px",
                                   borderTop: "1px solid var(--line-faint)" }}>
                        <p style={{ margin: 0, fontWeight: 600, color: "var(--ink-1)" }}>
                          {item.target}
                        </p>
                        {item.verified_on_hand !== null && (
                          <p className="num" style={{ margin: "4px 0 0", fontSize: "11.5px", color: "var(--proven)" }}>
                            SHOPIFY CONFIRMED {item.verified_on_hand} IN STOCK
                            {item.stock_before !== null && `, UP FROM ${item.stock_before}`}
                          </p>
                        )}
                        <p className="num" style={{ margin: "4px 0 0", fontSize: "11.5px", color: "var(--ink-4)" }}>
                          {fmtDate(item.applied_at)}
                          {item.window_last && ` → ${fmtDay(item.window_last)}`}
                        </p>
                        <p style={{ margin: "6px 0 0", fontSize: "12.5px",
                                    color: item.state === "window_open" ? "var(--ink-3)" : "var(--waiting)" }}>
                          {item.state === "window_open" && item.days_remaining !== null
                            ? `${item.days_remaining} full day(s) to go — measured on ${fmtDay(item.measure_on)}.`
                            : item.reason}
                          {/* A window nobody can price is not a wait, it is a
                              blank somebody can fill in — so it links to the
                              place that fills it. */}
                          {item.state === "awaiting_cost" && (
                            <>{" "}<a href="/costs" style={{ color: "var(--proven)" }}>
                              Add the unit cost
                            </a>.</>
                          )}
                        </p>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {(payroll.excluded_demo > 0 || payroll.excluded_unverified > 0) && (
            <p className="num" style={{ margin: "18px 0 0", fontSize: "11.5px", color: "var(--ink-5)" }}>
              NOT COUNTED · {payroll.excluded_demo} DEMO · {payroll.excluded_unverified} UNVERIFIED
            </p>
          )}

          {payroll.explanation && (
            <p style={{ margin: "26px 0 0", paddingTop: "22px", borderTop: "1px solid var(--line-faint)",
                        fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-2)" }}>
              {payroll.explanation}
            </p>
          )}
        </div>
      )}

      {limits && (
        <div className={`mt-6 p-6 ${limits.enabled ? "card" : "card-unproven"}`}
             style={limits.enabled ? undefined : { borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }}>
          <div className="flex flex-wrap items-center justify-between gap-6">
            <div>
              <div className="flex items-center gap-2.5">
                <Icon name="shield" size={16} stroke={limits.enabled ? "var(--proven)" : "var(--unproven)"} />
                <p style={{ margin: 0, fontSize: "15px", fontWeight: 600 }}>
                  {limits.enabled ? "Operator is on" : "Operator is switched off"}
                </p>
              </div>
              <p style={{ margin: "10px 0 0", maxWidth: "78ch", fontSize: "13px", lineHeight: 1.7, color: "var(--ink-2)" }}>
                {limits.enabled
                  ? `On its own it may make ${limits.remaining_today} more change(s) today, up to `
                    + `${limits.auto_apply_below} at stake each, never above `
                    + `${Math.round(limits.max_change_pct * 100)}% in one step, and never on anything `
                    + `spending more than ${limits.protected_spend_per_day} a day.`
                  : "No change can be made to your account, by the operator or from this screen, until you turn it back on."}
              </p>
            </div>
            <button onClick={toggleOperator} disabled={busy === "limits"}
                    className={limits.enabled ? "btn-quiet shrink-0" : "btn-primary shrink-0"}>
              {limits.enabled ? "SWITCH OFF" : "SWITCH ON"}
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="card-unproven mt-6 px-5 py-4"
             style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)", fontSize: "13.5px", color: "var(--unproven)" }}>
          {error}
        </div>
      )}

      <div className="flex items-center gap-3" style={{ marginTop: "48px" }}>
        <IconPlate name="list" size={32} />
        <h2 className="lbl" style={{ margin: 0 }}>Ledger</h2>
      </div>

      {actions.length === 0 ? (
        <p className="mt-4 p-9 text-center"
           style={{ border: "1px dashed var(--line)", borderRadius: "var(--r)", fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-5)" }}>
          Nothing recorded yet. Once a suggestion is put into effect it appears here and gets
          priced against the fortnight before it.
        </p>
      ) : (
        <div className="card mt-4 overflow-x-auto">
          <table className="w-full min-w-[760px]" style={{ borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--raised)", borderBottom: "1px solid var(--line)" }}>
                <th className="lbl px-5 py-3.5 text-left">What</th>
                <th className="lbl px-5 py-3.5 text-left">Status</th>
                <th className="lbl px-5 py-3.5 text-right">Predicted</th>
                <th className="lbl px-5 py-3.5 text-right">Actual</th>
                <th className="px-5 py-3.5" />
              </tr>
            </thead>
            <tbody>
              {actions.map((a) => (
                <Fragment key={a.id}>
                <tr style={{ borderBottom: asking?.id === a.id ? "none" : "1px solid var(--line-faint)" }}>
                  <td className="px-5 py-4">
                    <p style={{ margin: 0, fontSize: "14px" }}>
                      {a.action_type.replaceAll("_", " ").toLowerCase()}
                    </p>
                    <p className="num" style={{ margin: "5px 0 0", fontSize: "11.5px", color: "var(--ink-4)" }}>{a.target}</p>
                    <p className="num flex items-center gap-1.5" style={{ margin: "7px 0 0", fontSize: "10.5px", letterSpacing: ".12em",
                         color: a.evidence_mode === "real" ? "var(--proven)" : a.evidence_mode === "demo" ? "var(--ink-4)" : "var(--waiting)" }}>
                      <Icon name={a.evidence_mode === "real" ? "check" : a.evidence_mode === "demo" ? "image" : "alert"} size={12} />
                      {a.evidence_mode === "real" ? "VERIFIED SOURCE" : a.evidence_mode === "demo" ? "DEMONSTRATION DATA" : "UNVERIFIED"}
                    </p>
                  </td>
                  <td className="px-5 py-4">
                    <span className="num" style={{ fontSize: "11px", letterSpacing: ".12em", color: statusColor[a.status] ?? "var(--ink-3)" }}>
                      {statusLabel[a.status] ?? a.status}
                    </span>
                  </td>
                  <td className="num px-5 py-4 text-right" style={{ fontSize: "13.5px", color: "var(--ink-5)" }}>
                    {a.projected_impact === null ? "—" : money(a.projected_impact, a.currency)}
                  </td>
                  <td className="num px-5 py-4 text-right" style={{ fontSize: "13.5px" }}>
                    {a.impact ? (
                      <>
                        <span style={{ color: a.impact.net >= 0 ? "var(--proven)" : "var(--unproven)" }}>
                          {money(a.impact.net, a.currency)}
                        </span>
                        {a.impact.provisional && (
                          <span style={{ display: "block", marginTop: "5px", fontSize: "10.5px", letterSpacing: ".1em", color: "var(--waiting)" }}>
                            PROVISIONAL
                          </span>
                        )}
                      </>
                    ) : (
                      <span style={{ color: "var(--ink-6)" }}>—</span>
                    )}
                  </td>
                  <td className="px-5 py-4">
                    <div className="flex justify-end gap-2">
                      {a.status === "proposed" && (
                        <button onClick={() => setAsking(
                                  asking?.id === a.id ? null : { id: a.id, mode: "apply" })}
                                disabled={busy === a.id}
                                className="btn-quiet inline-flex items-center gap-1.5" style={{ padding: "8px 12px" }}>
                          <Icon name="check" size={13} /> I DID THIS
                        </button>
                      )}
                      {a.status === "applied" && (
                        <button onClick={() => setAsking(
                                  asking?.id === a.id ? null : { id: a.id, mode: "measure" })}
                                disabled={busy === a.id}
                                className="btn-primary inline-flex items-center gap-1.5" style={{ padding: "8px 12px" }}>
                          <Icon name="profit" size={13} /> PRICE IT
                        </button>
                      )}
                      {(a.status === "applied" || a.status === "measured") && a.revert_to && (
                        <button onClick={() => undo(a)} disabled={busy === a.id}
                                className="btn-quiet" style={{ padding: "8px 12px" }}>
                          UNDO
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
                {asking?.id === a.id && (
                  <tr style={{ borderBottom: "1px solid var(--line-faint)" }}>
                    <td colSpan={5} style={{ padding: 0 }}>
                      <MeasurementWindowForm
                        explanation={asking.mode === "apply"
                          ? "The fortnight before you made this change is what it will be judged against."
                          : `What happened since you made it. The window is ${a.measurement_days} days; `
                            + "anything shorter is measured but stays provisional until it closes."}
                        defaultDays={asking.mode === "apply" ? 14 : a.measurement_days}
                        currency={a.currency || currency}
                        busy={busy === a.id}
                        submitLabel={asking.mode === "apply" ? "PUT IT IN EFFECT" : "PRICE IT"}
                        onSubmit={(w) => asking.mode === "apply" ? apply(a, w) : measure(a, w)}
                        onCancel={() => setAsking(null)}
                      />
                    </td>
                  </tr>
                )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
