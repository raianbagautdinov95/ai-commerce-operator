"use client";

import { useEffect, useState } from "react";
import { getLegalDetails, type LegalDetails } from "../../lib/api";
import { IconPlate } from "../icons";

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
        <p className="font-semibold">Plain-language terms for the pilot.</p>
        <p className="mt-1">
          These terms state how the service is sold today and do not reduce any
          protection that cannot be reduced under applicable law. They should be
          reviewed by a qualified lawyer before a wider launch.
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

        <Section title="Billing, cancellation, refunds and tax">
          Prices, billing period and taxes are shown before you pay. Paid plans
          renew until cancelled through the billing portal; cancellation stops the
          next renewal and access continues until the end of the paid period.
          If you are a consumer in the EU or UK, you may cancel a distance contract
          within 14 days of purchase by emailing the contact above. We will refund
          payments due under applicable law within the required time. For a
          business customer, fees for an already-started billing period are not
          refundable except where required by law or where we made an error.
        </Section>

        <Section title="Digital service and withdrawal right">
          We do not remove a consumer&apos;s withdrawal right merely because access
          was created. A right of withdrawal may end early only where you gave
          express consent at checkout for immediate performance, acknowledged the
          consequence, and received that confirmation on a durable medium. Until
          that checkout flow exists, the normal statutory withdrawal rights apply.
        </Section>

        <Section title="Liability">
          Nothing in these terms excludes or limits rights that cannot be excluded
          by law. The service provides information and proposals, not a guarantee
          of profit, sales, availability, compliance or a particular business
          result. To the extent permitted by law, we are not responsible for
          indirect or consequential loss arising from decisions made using the
          service.
        </Section>

        <Section title="Governing law and disputes">
          These terms are governed by the laws of Finland. If you are a consumer,
          you keep any mandatory protection provided by the law of your country of
          residence. Before starting formal proceedings, please contact us so we
          can try to resolve the issue directly.
        </Section>
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
