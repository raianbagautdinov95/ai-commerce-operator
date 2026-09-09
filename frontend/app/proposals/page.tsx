"use client";

import { useEffect, useState } from "react";
import {
  confirmCommerceAction, dismissAction, getGuardrails, listActions, markActionApplied,
  scanCommerce,
  type GuardrailPolicy, type OperatorAction,
} from "../../lib/api";
import { Icon, IconPlate } from "../icons";
import MeasurementWindowForm, { type MetricWindow } from "../measurement-window";

/* ---------------------------------------------------------------------------
   What the Operator found, and what you decide about it.

   The queue used to have one exit. A proposal could be applied or it could sit
   there, so the only way to say no was to ignore it — and a list nobody can
   empty stops being read, which costs more than any single bad suggestion.
   Both answers are here, and neither is the default.

   "I did this" asks for the fortnight before the change, because that is what
   the proposal will be judged against. Refusing to record a number the store
   has not measured is the whole reason the payroll can be believed, so the
   form asks rather than assuming.
   --------------------------------------------------------------------------- */

const money = (value: number | null | undefined, currency: string) =>
  value == null ? "—"
    : value.toLocaleString(undefined, { style: "currency", currency, maximumFractionDigits: 2 });


/** Ask the Operator to read this store's sales.
 *
 *  Offered whether or not the queue is empty. It first existed only in the
 *  empty state, which is the one moment a seller is least likely to want it:
 *  they had just been told nothing was waiting on them. Someone looking at a
 *  list of proposals and wondering whether it is current has no other way to
 *  ask.
 *
 *  Defined once because it appears twice, and two copies of a button drift.
 */
function ScanButton({ onScan, scanning }: { onScan: () => void; scanning: boolean }) {
  return (
    <button onClick={onScan} disabled={scanning}
            className="btn-primary inline-flex items-center gap-2">
      <Icon name="search" size={13} />
      {scanning ? "READING YOUR SALES…" : "READ MY SALES NOW"}
    </button>
  );
}

export default function ProposalsPage() {
  const [proposals, setProposals] = useState<OperatorAction[] | null>(null);
  const [decided, setDecided] = useState<OperatorAction[]>([]);
  const [limits, setLimits] = useState<GuardrailPolicy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [applying, setApplying] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanNote, setScanNote] = useState<string | null>(null);
  const [confirmNote, setConfirmNote] =
    useState<{ id: string; ok: boolean; text: string } | null>(null);

  async function refresh() {
    try {
      const [all, policy] = await Promise.all([listActions(), getGuardrails()]);
      setProposals(all.filter((a) => a.status === "proposed"));
      setDecided(all.filter((a) => a.status === "dismissed").slice(0, 6));
      setLimits(policy);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
      setProposals([]);
    }
  }

  useEffect(() => { refresh(); }, []);

  /** Read this store's own sales and open whatever they support proposing. */
  async function scan() {
    setScanning(true);
    setScanNote(null);
    try {
      const result = await scanCommerce();
      await refresh();
      // An empty result is an answer, not a failure, and it says which: too
      // little history, or enough history with nothing worth your attention.
      setScanNote(
        result.opened > 0
          ? `Opened ${result.opened} from ${result.observed_days} days of sales.`
          : result.already_open > 0
            ? "Everything it found is already in your queue."
            : result.observed_days === 0
              ? "No sales have been synced yet, so there is nothing to read."
              : `Read ${result.observed_days} days and found nothing worth proposing.`,
      );
    } catch (e) {
      setScanNote((e as Error).message);
    } finally {
      setScanning(false);
    }
  }

  async function decline(action: OperatorAction, note: string) {
    setBusy(action.id);
    try {
      await dismissAction(action.id, note || undefined);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  /** A restock is confirmed by asking Shopify, not by filling in a form. */
  async function confirmRestock(action: OperatorAction) {
    setBusy(action.id);
    setConfirmNote(null);
    try {
      const result = await confirmCommerceAction(action.id);
      setConfirmNote({ id: action.id, ok: result.confirmed, text: result.reason });
      if (result.confirmed) await refresh();
    } catch (e) {
      setConfirmNote({ id: action.id, ok: false, text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  async function accept(action: OperatorAction, baseline: MetricWindow) {
    setBusy(action.id);
    try {
      await markActionApplied(action.id, baseline);
      setApplying(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex items-center gap-3">
          <IconPlate name="spark" />
          <p className="lbl">Proposals</p>
        </div>
        <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
          What it found, and what you decide
        </h1>
        <p style={{ margin: "16px 0 0", maxWidth: "68ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          Each of these is a change the Operator would make. Nothing here has happened yet.
          Saying no is an answer and gets recorded as one — a proposal you ignore is worse
          than a proposal you decline, because the list stops being worth reading.
        </p>
      </section>

      {limits && (
        <div className={`mb-6 flex flex-wrap items-center justify-between gap-5 p-5 ${limits.enabled ? "card" : "card-unproven"}`}
             style={limits.enabled ? undefined
               : { borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }}>
          <div className="flex items-start gap-3">
            <Icon name="shield" size={17}
                  stroke={limits.enabled ? "var(--proven)" : "var(--unproven)"}
                  className="mt-0.5 shrink-0" />
            <p style={{ margin: 0, maxWidth: "78ch", fontSize: "13px", lineHeight: 1.7, color: "var(--ink-2)" }}>
              {limits.enabled
                ? `On its own the Operator may make ${limits.remaining_today} more change(s) today, `
                  + `up to ${limits.auto_apply_below} at stake each. Everything above that waits `
                  + `here for you.`
                : "The Operator is switched off. It will keep finding things and will change "
                  + "nothing, here or anywhere else, until you turn it back on."}
            </p>
          </div>
          <a href="/payroll" className="num shrink-0" style={{ fontSize: "11px", letterSpacing: ".1em", color: "var(--ink-4)" }}>
            LIMITS →
          </a>
        </div>
      )}

      {error && (
        <div className="card-unproven mb-6 flex items-start gap-3 px-5 py-4"
             style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                      fontSize: "13.5px", color: "var(--unproven)" }}>
          <Icon name="alert" size={17} className="mt-0.5 shrink-0" /> {error}
        </div>
      )}

      {proposals === null && (
        <p className="num" style={{ fontSize: "11.5px", letterSpacing: ".14em", color: "var(--ink-5)" }}>
          READING THE QUEUE…
        </p>
      )}

      {proposals !== null && proposals.length > 0 && (
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <ScanButton onScan={scan} scanning={scanning} />
          {scanNote && (
            <p className="num" style={{ margin: 0, fontSize: "12px", color: "var(--ink-4)" }}>
              {scanNote}
            </p>
          )}
        </div>
      )}

      {proposals !== null && proposals.length === 0 && (
        <div className="card flex flex-col items-start gap-4 p-9">
          <IconPlate name="check" tone="proven" />
          <h2 style={{ margin: 0, fontSize: "18px", fontWeight: 600 }}>Nothing is waiting on you</h2>
          <p style={{ margin: 0, maxWidth: "60ch", fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-2)" }}>
            An empty queue means the Operator has not found anything worth changing since you
            last looked — not that it is idle. What it has already proved is on the payroll.
          </p>
          {scanNote && (
            <p className="num" style={{ margin: 0, fontSize: "12px", color: "var(--ink-4)" }}>
              {scanNote}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <ScanButton onScan={scan} scanning={scanning} />
            <a href="/payroll" className="btn-quiet inline-flex items-center gap-2">
              <Icon name="profit" size={13} /> SEE THE PAYROLL
            </a>
          </div>
        </div>
      )}

      <div className="space-y-4">
        {(proposals ?? []).map((action) => (
          <Proposal
            key={action.id}
            action={action}
            busy={busy === action.id}
            open={applying === action.id}
            onOpen={() => setApplying(applying === action.id ? null : action.id)}
            onAccept={(baseline) => accept(action, baseline)}
            onDecline={(note) => decline(action, note)}
            onConfirmRestock={() => confirmRestock(action)}
            confirmNote={confirmNote?.id === action.id ? confirmNote : null}
          />
        ))}
      </div>

      {decided.length > 0 && (
        <section className="mt-12">
          <div className="flex items-center gap-3">
            <IconPlate name="list" size={32} />
            <h2 className="lbl" style={{ margin: 0 }}>Recently declined</h2>
          </div>
          <div className="card mt-4">
            {decided.map((action, i) => (
              <div key={action.id} className="flex flex-wrap items-center justify-between gap-4 px-6 py-4"
                   style={{ borderTop: i === 0 ? "none" : "1px solid var(--line-faint)" }}>
                <div className="min-w-0">
                  <p style={{ margin: 0, fontSize: "13.5px" }}>
                    {action.action_type.replaceAll("_", " ").toLowerCase()}
                    <span className="num" style={{ color: "var(--ink-4)" }}> · {action.target}</span>
                  </p>
                  {action.note && (
                    <p style={{ margin: "5px 0 0", fontSize: "12px", color: "var(--ink-5)" }}>
                      “{action.note}”
                    </p>
                  )}
                </div>
                <span className="num" style={{ fontSize: "11px", letterSpacing: ".12em", color: "var(--ink-5)" }}>
                  DECLINED
                </span>
              </div>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}

function Proposal({ action, busy, open, onOpen, onAccept, onDecline,
                   onConfirmRestock, confirmNote }: {
  action: OperatorAction;
  busy: boolean;
  open: boolean;
  onOpen: () => void;
  onAccept: (baseline: MetricWindow) => void;
  onConfirmRestock: () => void;
  confirmNote: { ok: boolean; text: string } | null;
  onDecline: (note: string) => void;
}) {
  const [note, setNote] = useState("");
  const [declining, setDeclining] = useState(false);

  const currency = action.currency || "USD";
  const evidence = action.evidence_mode;
  // A restock is the one commerce action the store can be asked about, so its
  // confirmation is a question to Shopify rather than a form to fill in.
  const witnessed = action.module === "commerce" && action.action_type === "RESTOCK_PRODUCT";

  return (
    <article className="card">
      <div className="flex flex-wrap items-start justify-between gap-6 p-6">
        <div className="min-w-0">
          <p className="num" style={{ margin: 0, fontSize: "11px", letterSpacing: ".12em", color: "var(--ink-4)" }}>
            {action.module.toUpperCase()}
          </p>
          <h2 style={{ margin: "9px 0 0", fontSize: "18px", fontWeight: 600 }}>
            {action.action_type.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase())}
          </h2>
          <p className="num" style={{ margin: "7px 0 0", fontSize: "13px", color: "var(--ink-2)" }}>
            {action.target}
          </p>
          {action.note && (
            <p style={{ margin: "12px 0 0", maxWidth: "72ch", fontSize: "13px", lineHeight: 1.7, color: "var(--ink-3)" }}>
              {action.note}
            </p>
          )}
        </div>

        <div className="text-right">
          <p className="lbl">Predicted</p>
          <p className="num" style={{ margin: "8px 0 0", fontSize: "22px", fontWeight: 500, letterSpacing: "-.03em" }}>
            {money(action.projected_impact, currency)}
          </p>
          <p className="num" style={{ margin: "7px 0 0", fontSize: "10.5px", letterSpacing: ".1em",
               color: evidence === "real" ? "var(--proven)" : evidence === "demo" ? "var(--ink-4)" : "var(--waiting)" }}>
            {evidence === "real" ? "VERIFIED SOURCE"
              : evidence === "demo" ? "DEMONSTRATION DATA" : "UNVERIFIED SOURCE"}
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-4 px-6 py-4"
           style={{ borderTop: "1px solid var(--line-faint)" }}>
        <p className="num" style={{ margin: 0, fontSize: "11px", letterSpacing: ".08em", color: "var(--ink-5)" }}>
          {witnessed
            ? `CHECKED AGAINST SHOPIFY, THEN MEASURED OVER ${action.measurement_days} DAYS`
            : `MEASURED OVER ${action.measurement_days} DAYS AFTER YOU APPLY IT`}
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => { setDeclining(false); witnessed ? onConfirmRestock() : onOpen(); }}
            disabled={busy}
            className="btn-primary inline-flex items-center gap-2" style={{ padding: "10px 16px" }}>
            <Icon name="check" size={13} />
            {busy && witnessed ? "ASKING SHOPIFY…" : "I DID THIS"}
          </button>
          <button onClick={() => { setDeclining(!declining); }} disabled={busy}
                  className="btn-quiet inline-flex items-center gap-2" style={{ padding: "10px 16px" }}>
            <Icon name="alert" size={13} /> DECLINE
          </button>
        </div>
      </div>

      {confirmNote && (
        <div className="px-6 py-4"
             style={{ borderTop: "1px solid var(--line-faint)",
                      fontSize: "13px", lineHeight: 1.65,
                      color: confirmNote.ok ? "var(--proven)" : "var(--waiting)" }}>
          {confirmNote.text}
        </div>
      )}

      {declining && (
        <div className="px-6 py-5" style={{ borderTop: "1px solid var(--line-faint)", background: "#05070A" }}>
          <p style={{ margin: 0, fontSize: "13px", lineHeight: 1.65, color: "var(--ink-2)" }}>
            Why not? Optional — a declined proposal is recorded either way, and a reason is
            what stops the same suggestion coming back.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <input className="field" style={{ flex: "1 1 320px" }} value={note}
                   onChange={(e) => setNote(e.target.value)}
                   placeholder="We bid on that term on purpose"
                   onKeyDown={(e) => e.key === "Enter" && onDecline(note)} />
            <button onClick={() => onDecline(note)} disabled={busy} className="btn-quiet">
              {busy ? "RECORDING…" : "DECLINE IT"}
            </button>
          </div>
        </div>
      )}

      {open && (
        <MeasurementWindowForm
          explanation="The fortnight before you made this change is what it will be judged against."
          defaultDays={14}
          currency={currency}
          busy={busy}
          submitLabel="PUT IT IN EFFECT"
          onSubmit={onAccept}
          onCancel={onOpen}
        />
      )}
    </article>
  );
}
