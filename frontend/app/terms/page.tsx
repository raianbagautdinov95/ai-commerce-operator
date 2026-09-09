"use client";

import { useEffect, useState } from "react";
import { getLegalDetails, type LegalDetails } from "../../lib/api";
import { IconPlate } from "../icons";

/**
 * What the product actually does is stated here as fact, because it is checkable
 * against the code. What a lawyer has to decide — liability, governing law,
 * refunds — is left visibly open rather than filled with plausible wording.
 * A clause that reads as reviewed and was not is worse than an obvious gap.
 */
export default function TermsPage() {
  const [legal, setLegal] = useState<LegalDetails | null>(null);

  useEffect(() => { getLegalDetails().then(setLegal).catch(() => setLegal(null)); }, []);

  return (
    <main className="mx-auto px-6 pb-20 pt-11" style={{ maxWidth: "780px" }}>
      <div className="flex items-center gap-3">
        <IconPlate name="shield" />
        <p className="lbl">Legal</p>
      </div>
      <h1 style={{ margin: "16px 0 0", fontSize: "30px", fontWeight: 600, letterSpacing: "-.025em" }}>
        Terms of Service
      </h1>

      <div className="card-waiting mt-7 p-5" style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)", fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-2)" }}>
        <p className="font-semibold">Not yet reviewed by a lawyer.</p>
        <p className="mt-1">
          The sections describing what the product does are accurate and checkable.
          The three marked <em>open</em> below — liability, governing law and
          refunds — are deliberately blank rather than filled with plausible
          wording, because a clause that reads as reviewed and is not would be
          worse than a visible gap.
        </p>
      </div>

      <div className="mt-10 space-y-7" style={{ color: "var(--ink-2)" }}>
        <Section title="Who provides this service">
          {legal?.complete ? (
            <>
              {legal.entity}, {legal.address}. Contact:{" "}
              <a href={`mailto:${legal.privacy_contact}`}>{legal.privacy_contact}</a>.
            </>
          ) : (
            <span className="text-[#7C8F97]">
              Not yet stated. Until an operating entity is named, treat this as a
              private trial and not a service you can hold anyone to.
            </span>
          )}
        </Section>

        <Section title="What the service does">
          It reads your store&apos;s own data, scores it with fixed arithmetic, and
          proposes actions. It does not guarantee profit, marketplace approval,
          stock availability or legal compliance. Figures it reports about your
          business are only as good as the data your channels return.
        </Section>

        <Section title="What it may do on its own">
          A new store runs in <strong>propose-only</strong> mode: nothing is changed
          in your account until you apply it. You can raise that ceiling yourself,
          and the limits you set are enforced on every change — including a switch
          that stops the operator entirely and cannot be overridden by anything
          else. Every change records the value it replaced, so it can be undone,
          and every change and every refusal is logged.
        </Section>

        <Section title="What stays your responsibility">
          Prices, refunds, advertising budgets, purchasing, publishing and anything
          else with financial or legal effect remain yours. Approving a proposal is
          your decision; the operator having suggested it does not transfer that.
        </Section>

        <Section title="Accounts and acceptable use">
          Keep your access token to yourself — anyone holding it acts as you. Do not
          use the service to break the law, marketplace rules, or anyone
          else&apos;s rights.
        </Section>

        <Section title="Money claims">
          Where the service reports savings, it counts only changes it could read
          back out of your account afterwards. Predicted, demo and unverified
          figures are shown separately and never inside the total. This is a
          statement of how the arithmetic works, not a promise of a result.
        </Section>

        <Section title="Your data">
          Covered by the <a href="/privacy">Privacy Policy</a>. You can export or
          request deletion at any time from the <a href="/privacy-center">Privacy Center</a>.
        </Section>

        <Section title="Availability">
          The service depends on third-party APIs and can be interrupted by them or
          by maintenance. No uptime is promised.
        </Section>

        <OpenSection title="Billing, refunds and tax">
          Paid plans renew on the terms shown at checkout, and cancellation runs
          through the billing portal.
        </OpenSection>

        <OpenSection title="Liability">
          Any limitation of liability has to be written for a specific
          jurisdiction. Until it is, none is claimed here.
        </OpenSection>

        <OpenSection title="Governing law and disputes">
          Depends on the operating entity above, which is not yet named.
        </OpenSection>
      </div>
    </main>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="text-xl font-semibold text-[#E8EDEF]">{title}</h2>
      <p className="mt-2 leading-7">{children}</p>
    </section>
  );
}

function OpenSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-[12px] border border-dashed border-[#26343D] p-5">
      <h2 className="flex flex-wrap items-center gap-3 text-xl font-semibold text-[#E8EDEF]">
        {title}
        <span className="rounded-full bg-amber-400/10 px-2.5 py-1 text-[11px] font-bold uppercase tracking-wider text-amber-300">
          Open
        </span>
      </h2>
      <p className="mt-2 leading-7">{children}</p>
      <p style={{ margin: "10px 0 0", fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-3)" }}>
        Needs a lawyer. Left blank on purpose.
      </p>
    </section>
  );
}
