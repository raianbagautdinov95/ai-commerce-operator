"use client";

import { useEffect, useState } from "react";
import { getLegalDetails, type LegalDetails } from "../../lib/api";
import { IconPlate } from "../icons";

export default function PrivacyPage() {
  const [legal, setLegal] = useState<LegalDetails | null>(null);

  useEffect(() => { getLegalDetails().then(setLegal).catch(() => setLegal(null)); }, []);

  return (
    <main className="mx-auto px-6 pb-20 pt-11" style={{ maxWidth: "780px" }}>
      <div className="flex items-center gap-3">
        <IconPlate name="shield" />
        <p className="lbl">Legal</p>
      </div>
      <h1 style={{ margin: "16px 0 0", fontSize: "30px", fontWeight: 600, letterSpacing: "-.025em" }}>
        Privacy Policy
      </h1>
      <p style={{ margin: "10px 0 0", fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-3)" }}>
        How this service handles data about you and your store.
      </p>

      {legal && !legal.complete && (
        <div className="card-waiting mt-7 p-5" style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)", fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-2)" }}>
          <p className="font-semibold">This policy is not finished.</p>
          <p className="mt-1">
            Nobody is named as accountable for your data yet
            {legal.missing.length > 0 && <> (missing: {legal.missing.join(", ")})</>}. Until
            that is filled in, treat this service as a private trial rather than
            one you can rely on for a business.
          </p>
        </div>
      )}

      <article className="mt-10 space-y-4 [&_h2]:pt-8 [&_h2]:text-[19px] [&_h2]:font-semibold [&_h2]:tracking-[-0.015em] [&_p]:text-[14px] [&_p]:leading-[1.8] [&_p]:text-[color:var(--ink-2)] [&_li]:text-[14px] [&_li]:leading-[1.8] [&_li]:text-[color:var(--ink-2)] [&_ul]:list-disc [&_ul]:pl-6 [&_ul]:space-y-2 [&_a]:text-[color:var(--proven)]">
        <h2>Who is responsible</h2>
        {legal?.complete ? (
          <p>
            <strong>{legal.entity}</strong>, {legal.address}.
            {legal.business_id && <>
              {" "}Registered
              {legal.business_type ? ` as ${legal.business_type}` : ""}
              {legal.country ? ` in ${legal.country}` : ""}
              , business ID <span className="num">{legal.business_id}</span>.
            </>}
            {" "}Questions about your data, or a request to see, correct or delete it:{" "}
            <a href={`mailto:${legal.privacy_contact}`}>{legal.privacy_contact}</a>.
            {legal.representative && <> EU representative: {legal.representative}.</>}
          </p>
        ) : (
          <p className="num" style={{ background: "#05070A", border: "1px solid var(--line)", borderRadius: "var(--r)", padding: "12px 16px", fontSize: "12px", lineHeight: 1.7, color: "var(--waiting)" }}>
            Not yet stated. The operator of this deployment has not configured a
            legal entity or a privacy contact.
          </p>
        )}

        <h2>What is collected</h2>
        <ul>
          <li>Your account: the email address a token was issued for.</li>
          <li>Access tokens for the shops you connect, encrypted at rest.</li>
          <li>Aggregate commerce figures: daily revenue, refunds, order and unit counts.</li>
          <li>Product research, recommendations, and the ledger of actions taken.</li>
          <li>Audit records of who changed what, and when.</li>
          <li>Operational logs and error reports.</li>
        </ul>

        <h2>What is deliberately not collected</h2>
        <p>
          Order webhooks and syncs read totals, not people. Customer names, email
          addresses, billing and shipping addresses from your store are not stored
          by this service. Card details never reach it; payment is handled by the
          payment provider directly.
        </p>

        <h2>Why, and on what basis</h2>
        <ul>
          <li>To provide the service you asked for — performance of a contract.</li>
          <li>To keep accounts secure and detect abuse — legitimate interests.</li>
          <li>To bill a subscription — performance of a contract.</li>
          <li>To meet accounting and legal obligations — legal obligation.</li>
        </ul>
        <p>
          AI explanations receive only the business figures for the feature in use —
          never credentials, never customer data. The figures themselves are always
          computed here; the model explains numbers, it does not produce them.
        </p>

        <h2>Who else receives data</h2>
        {legal && legal.subprocessors.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-[#5A6B72]">
                <tr>
                  <th className="py-2 pr-4 font-semibold">Provider</th>
                  <th className="py-2 pr-4 font-semibold">Purpose</th>
                  <th className="py-2 font-semibold">What reaches them</th>
                </tr>
              </thead>
              <tbody>
                {legal.subprocessors.map((s) => (
                  <tr key={s.name} className="border-t border-[#182229] align-top">
                    <td className="py-2 pr-4 font-medium text-[#E8EDEF]">{s.name}</td>
                    <td className="py-2 pr-4">{s.purpose}</td>
                    <td className="py-2">{s.data}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p>No third-party integrations are enabled on this deployment.</p>
        )}
        <p className="text-sm">
          This list is generated from the integrations actually switched on here,
          so it cannot quietly fall out of date.
        </p>

        <h2>How long it is kept</h2>
        {legal && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <tbody>
                {legal.retention.map((r) => (
                  <tr key={r.category} className="border-t border-[#182229] align-top">
                    <td className="py-2 pr-6 font-medium text-[#E8EDEF]">{r.category}</td>
                    <td className="py-2">{r.period}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <h2>Your rights</h2>
        <p>
          You can see, take a copy of, correct or delete your data. The{" "}
          <a href="/privacy-center">Privacy Center</a> does the first two immediately
          and starts a deletion request for the last.
        </p>
        <p>
          Deletion is a request rather than an instant wipe, on purpose: it leaves
          room to confirm who is asking, and some records — invoices, and the audit
          trail that makes a deletion checkable afterwards — are kept where the law
          requires it. If you are in the EU or UK you may also complain to your data
          protection authority.
        </p>

        <h2>Where data is held</h2>
        <p>
          On the infrastructure this deployment runs on, and with the providers
          listed above, some of which operate outside your country. Where that
          happens it is covered by the transfer terms in those providers&apos;
          data processing agreements.
        </p>
      </article>
    </main>
  );
}
