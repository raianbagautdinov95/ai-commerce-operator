/**
 * Where "HOW TO FIX THIS" goes when order notifications are being refused.
 *
 * The setup screen has offered this link since the check was written; the page
 * did not exist, so the button led to a 404 at the one moment it matters — the
 * moment a store's orders have stopped arriving.
 *
 * It deliberately fetches nothing. Somebody reaches this page because part of
 * the system is misbehaving, and a help page that needs a working API to render
 * is a help page that is blank exactly when it is needed. The support address
 * is baked in at build time for the same reason.
 */
import { IconPlate } from "../../icons";

const SUPPORT_EMAIL = process.env.NEXT_PUBLIC_SUPPORT_EMAIL ?? null;

export default function FailingSignaturePage() {
  return (
    <main className="mx-auto px-6 pb-20 pt-11" style={{ maxWidth: "780px" }}>
      <div className="flex items-center gap-3">
        <IconPlate name="alert" />
        <p className="lbl">Help</p>
      </div>
      <h1 style={{ margin: "16px 0 0", fontSize: "30px", fontWeight: 600, letterSpacing: "-.025em" }}>
        Order notifications are being refused
      </h1>
      <p style={{ margin: "10px 0 0", fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-3)" }}>
        Shopify is sending them. This server is turning them away.
      </p>

      <article className="mt-10 space-y-4 [&_h2]:pt-8 [&_h2]:text-[19px] [&_h2]:font-semibold [&_h2]:tracking-[-0.015em] [&_p]:text-[14px] [&_p]:leading-[1.8] [&_p]:text-[color:var(--ink-2)] [&_li]:text-[14px] [&_li]:leading-[1.8] [&_li]:text-[color:var(--ink-2)] [&_ul]:list-disc [&_ul]:pl-6 [&_ul]:space-y-2 [&_a]:text-[color:var(--proven)]">
        <h2>What is happening</h2>
        <p>
          Every notification Shopify sends is signed with your app&rsquo;s secret,
          and this server checks that signature before it believes a word of it.
          Right now the two sides are signing and checking with different
          secrets, so each delivery is refused.
        </p>
        <p>
          Nothing else shows it. The subscriptions still exist, they still point
          at the right address, and your Shopify admin reports them as active —
          because they are. The only visible symptom is a revenue figure that
          stopped moving.
        </p>

        <h2>Your orders are not lost</h2>
        <p>
          Shopify keeps every order whether or not this server hears about it.
          Once the signature verifies again, a sync re-reads your recent orders
          and the figures catch up. Nothing is missing from your store, and
          nothing needs to be re-entered.
        </p>
        <p>
          What is genuinely lost is the time in between: while deliveries are
          being refused, the operator is working from an incomplete picture and
          will not propose anything about the days it never saw.
        </p>

        <h2>This is not something you can fix from Shopify</h2>
        <p>
          Reconnecting the store or turning the notifications on again will not
          help — the subscriptions were never the problem. The disagreement is
          about the signing secret, which lives in this deployment&rsquo;s
          configuration. It is ours to fix, not yours.
        </p>
        {SUPPORT_EMAIL ? (
          <p>
            Tell us it is happening and we will: <a href={"mailto:" + SUPPORT_EMAIL}>{SUPPORT_EMAIL}</a>.
          </p>
        ) : (
          <p className="num" style={{ background: "#05070A", border: "1px solid var(--line)", borderRadius: "var(--r)", padding: "12px 16px", fontSize: "12px", lineHeight: 1.7, color: "var(--waiting)" }}>
            No support address is configured on this deployment, so there is
            nowhere here to report it to. Whoever runs this server has to be
            told another way.
          </p>
        )}

        <h2>If you run this deployment</h2>
        <p>
          This is what a half-finished rotation of <span className="num">SHOPIFY_CLIENT_SECRET</span> looks
          like. Shopify begins signing with the new secret the moment it is saved
          in the Partner Dashboard; if the server is still holding the old one,
          every delivery fails the check and answers 401.
        </p>
        <ul>
          <li>
            Put the same secret on both sides — the value in the Partner
            Dashboard and <span className="num">SHOPIFY_CLIENT_SECRET</span> in
            the environment — then restart the API so it is actually read.
          </li>
          <li>
            The steps, and the order to do them in, are in{" "}
            <span className="num">ops/SECRETS.md</span>.
          </li>
          <li>
            Refusals are counted globally, never per shop: a rejected request has
            proved nothing about who sent it, so this warning says
            &ldquo;signatures are being refused&rdquo; and never names a store.
          </li>
        </ul>

        <h2>How you will know it is fixed</h2>
        <p>
          The warning clears itself the moment one delivery verifies — it is not
          a flag anybody has to reset. Place a test order in the store; the
          setup step goes back to reporting how many notifications are
          subscribed, and the order appears in your figures.
        </p>
        <p>
          If no order is placed, the warning also expires on its own after about
          36 hours. That is a timeout, not a repair: it means nothing has been
          refused recently, not that anything now works.
        </p>

        <p style={{ paddingTop: "24px" }}>
          <a href="/onboarding">Back to setup</a>
        </p>
      </article>
    </main>
  );
}
