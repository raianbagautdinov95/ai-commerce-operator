"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  beginShopifyAuthorization, getOnboarding, retryShopifyNotifications, scanCommerce,
  startShopifySync,
  type Onboarding, type OnboardingAction, type OnboardingStatus, type OnboardingStep,
} from "../../lib/api";
import { Icon, IconPlate, type IconName } from "../icons";

/* ---------------------------------------------------------------------------
   Setting up, and what is actually true about it.

   Every status on this screen comes from /api/onboarding, which derives each one
   from the database — a credential that decrypts, a delivery that verified, a
   metric row with a date on it. None of it is recomputed here. Two
   implementations of "connected" is one more than can be kept honest, and the
   second one always ends up being the optimistic one.

   There is no progress percentage. Six steps out of eight tells a customer
   nothing about whether their orders are arriving, and it averages a broken
   step into a reassuring number — which is the specific way a setup screen
   lies.
   --------------------------------------------------------------------------- */

const TONE: Record<OnboardingStatus, { colour: string; icon: IconName; word: string }> = {
  complete: { colour: "var(--proven)", icon: "check", word: "Done" },
  in_progress: { colour: "var(--waiting)", icon: "clock", word: "Working" },
  needs_attention: { colour: "var(--unproven)", icon: "alert", word: "Needs you" },
  not_started: { colour: "var(--ink-5)", icon: "box", word: "Not started" },
};

/** The headline. `needs_attention` outranks everything: a broken required step
 *  must never be averaged away by the seven around it that are fine.
 *
 *  "Not started" is keyed on whether a store has been connected, not on whether
 *  every step is untouched — signing in completes the account step by
 *  definition, so the all-untouched reading would never be true and a customer
 *  who has done nothing would be told they were part-way through. */
function overall(data: Onboarding): OnboardingStatus {
  const statuses = data.steps.map((s) => s.status);
  if (statuses.includes("needs_attention")) return "needs_attention";
  if (statuses.every((s) => s === "complete")) return "complete";
  const store = data.steps.find((s) => s.key === "oauth");
  if (!store || store.status === "not_started") return "not_started";
  return "in_progress";
}

const HEADLINE: Record<OnboardingStatus, { title: string; blurb: string }> = {
  complete: {
    title: "Set up",
    blurb: "Your store is connected, its orders are arriving, and the Operator has "
      + "read them. It will keep watching and will change nothing without asking you.",
  },
  in_progress: {
    title: "Nearly there",
    blurb: "Some steps are still running or waiting on data. Nothing below needs "
      + "fixing — this is what the middle looks like.",
  },
  needs_attention: {
    title: "Something needs you",
    blurb: "One step below is not working. It is marked, it says what happened, and "
      + "it offers the one safe thing to do about it.",
  },
  not_started: {
    title: "Let's connect your store",
    blurb: "Four things, and the Operator reads your shop rather than changing it. "
      + "Nothing here touches a product, a price or an order.",
  },
};

/** A human sentence for anything that went wrong, and never the machine's own.
 *  A stack trace, a tenant id or an OAuth parameter on this screen is a leak
 *  wearing the clothes of a helpful error. */
function humanError(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error ?? "");
  if (/failed to fetch|networkerror|load failed/i.test(raw)) {
    return "We could not reach the service just now. Nothing has been lost and "
      + "nothing has changed — try again in a moment.";
  }
  if (/^API error 5\d\d/.test(raw)) {
    return "The service had a problem with that. Nothing has changed; you can "
      + "safely try again.";
  }
  return "That did not work. Nothing has changed, and trying again is safe.";
}

export default function OnboardingPage() {
  const [data, setData] = useState<Onboarding | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // A ref rather than state: two clicks land in the same tick, and state set in
  // the first has not been read by the second.
  const running = useRef(false);

  const refresh = useCallback(async () => {
    try {
      setData(await getOnboarding());
      setError(null);
    } catch (e) {
      setError(humanError(e));
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function run(step: OnboardingStep) {
    if (running.current || !step.action) return;
    running.current = true;
    setBusy(step.key);
    setNote(null);
    setError(null);
    try {
      switch (step.action) {
        case "connect_shopify": {
          // The backend hands back the URL to send them to; this does not
          // assemble one, because a client that builds an OAuth URL is a client
          // that can build a wrong one.
          const shop = window.prompt(
            "Your Shopify address, ending .myshopify.com");
          if (!shop) break;
          window.location.href = await beginShopifyAuthorization(shop.trim());
          return;
        }
        case "retry_notifications":
          await retryShopifyNotifications();
          setNote("Asked Shopify to send order notifications here again.");
          break;
        case "run_sync":
          await startShopifySync();
          setNote("Reading your last 30 days of orders. This runs in the "
            + "background — the step below updates when it finishes.");
          break;
        case "run_analysis": {
          const result = await scanCommerce();
          setNote(result.opened > 0
            ? `Read ${result.observed_days} days and opened ${result.opened} proposal(s).`
            : `Read ${result.observed_days} days and found nothing worth proposing.`);
          break;
        }
        case "open_proposals":
          window.location.href = "/proposals";
          return;
        case "open_help":
          if (step.help_url) window.location.href = step.help_url;
          return;
      }
      await refresh();
    } catch (e) {
      setError(humanError(e));
    } finally {
      running.current = false;
      setBusy(null);
    }
  }

  const status = data ? overall(data) : "not_started";
  const headline = HEADLINE[status];

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex items-center gap-3">
          <IconPlate name="plug" />
          <p className="lbl">Setup</p>
        </div>
        <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
          {data ? headline.title : "Setup"}
        </h1>
        {/* Announced rather than merely repainted: somebody using a screen
            reader must hear a step break, not discover it later. */}
        <p aria-live="polite"
           style={{ margin: "16px 0 0", maxWidth: "68ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          {data ? headline.blurb : "Reading where you are…"}
        </p>
      </section>

      {error && (
        <div role="alert" className="card-unproven mb-6 flex items-start gap-3 px-5 py-4">
          <Icon name="alert" size={16} stroke="var(--unproven)" className="mt-0.5 shrink-0" />
          <div>
            <p style={{ margin: 0, fontSize: "13.5px", lineHeight: 1.6 }}>{error}</p>
            <Support data={data} />
          </div>
        </div>
      )}

      {note && (
        <p aria-live="polite" className="num mb-6"
           style={{ fontSize: "12px", color: "var(--ink-4)" }}>{note}</p>
      )}

      <ol className="space-y-3" style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {(data?.steps ?? []).map((step, index) => (
          <StepRow key={step.key} step={step} index={index + 1}
                   busy={busy === step.key} onRun={() => run(step)} />
        ))}
      </ol>

      {data && status !== "complete" && <Support data={data} standalone />}
    </main>
  );
}

function StepRow({ step, index, busy, onRun }: {
  step: OnboardingStep; index: number; busy: boolean; onRun: () => void;
}) {
  const tone = TONE[step.status];
  const attention = step.status === "needs_attention";
  return (
    <li className={attention ? "card-unproven" : "card"}
        style={{ padding: "20px 22px",
                 ...(attention ? { borderWidth: "1px", borderStyle: "solid",
                                   borderRadius: "var(--r)" } : {}) }}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-4" style={{ minWidth: "min(100%, 28ch)" }}>
          <span aria-hidden="true" className="num shrink-0"
                style={{ fontSize: "11px", color: "var(--ink-5)", marginTop: "3px" }}>
            {String(index).padStart(2, "0")}
          </span>
          <div>
            <h2 style={{ margin: 0, fontSize: "15px", fontWeight: 600 }}>{step.title}</h2>
            <p style={{ margin: "6px 0 0", maxWidth: "62ch", fontSize: "13px",
                        lineHeight: 1.65, color: "var(--ink-2)" }}>
              {step.detail}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {/* The word, not only the colour: a state told in colour alone is a
              state some people cannot read. */}
          <span className="num inline-flex items-center gap-2 shrink-0"
                style={{ fontSize: "11px", letterSpacing: ".08em", color: tone.colour }}>
            <Icon name={tone.icon} size={13} stroke={tone.colour} />
            {tone.word.toUpperCase()}
          </span>
          {step.action && step.action_label && (
            <button onClick={onRun} disabled={busy}
                    className={attention ? "btn-primary" : "btn-quiet"}
                    style={{ padding: "9px 14px" }}>
              {busy ? "WORKING…" : step.action_label}
            </button>
          )}
        </div>
      </div>
    </li>
  );
}

/** Baked in as a fallback, because the address arrives on the same call that
 *  just failed. Support that is only reachable while the service is healthy is
 *  support for the times nobody needs it. */
const FALLBACK_SUPPORT_EMAIL = process.env.NEXT_PUBLIC_SUPPORT_EMAIL ?? null;
const FALLBACK_SUPPORT_RESPONSE = process.env.NEXT_PUBLIC_SUPPORT_RESPONSE_TIME ?? null;

function Support({ data, standalone = false }: { data: Onboarding | null; standalone?: boolean }) {
  const email = data?.support_email ?? FALLBACK_SUPPORT_EMAIL;
  const responseTime = data?.support_response_time ?? FALLBACK_SUPPORT_RESPONSE;
  if (!email) return null;
  const body = (
    <p style={{ margin: standalone ? 0 : "8px 0 0", fontSize: "12.5px",
                lineHeight: 1.6, color: "var(--ink-4)" }}>
      Stuck? <a href={`mailto:${email}`} style={{ color: "var(--ink-2)" }}>
        {email}
      </a>
      {responseTime ? ` — we answer ${responseTime}.` : "."}
      {data?.support_url && (
        <> <a href={data.support_url} style={{ color: "var(--ink-2)" }}>Help</a>.</>
      )}
    </p>
  );
  return standalone ? <div className="mt-8">{body}</div> : body;
}
