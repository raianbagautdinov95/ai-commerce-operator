import type { Metadata } from "next";
import ProductFinder from "../../components/ProductFinder";
import { Icon } from "../icons";

/* ---------------------------------------------------------------------------
   The front door for somebody who searched for a calculator.

   A search ad for "amazon fba profit calculator" has to land on a calculator,
   not on a page about one: the tool is above the fold and works before a word
   of the pitch is read. Everything under it exists for two readers — the person
   who wants to know what the verdict is made of before trusting it, and the
   search engine deciding whether this page answers the query.

   Nothing here promises a return. The engine's thresholds are stated exactly
   as the code has them, so the page cannot drift from the product.
   --------------------------------------------------------------------------- */

const CANONICAL = "https://aicommerceoperator.com/";

export const metadata: Metadata = {
  title: "Amazon FBA Profit Calculator with a BUY / AVOID Verdict — Free, No Account",
  description:
    "Enter price, landed cost, FBA fee, ad cost and expected sales. Get margin, ROI, profit per unit and a BUY / CAUTION / AVOID verdict computed by a fixed formula — with the reasoning written out. Free, nothing stored.",
  alternates: { canonical: CANONICAL },
  openGraph: {
    title: "Is it worth selling? Free FBA profit check with a verdict",
    description:
      "Margin, ROI, profit per unit and a BUY / CAUTION / AVOID verdict — before you order stock. No account.",
    url: CANONICAL,
    siteName: "AI Commerce Operator",
    type: "website",
  },
  robots: { index: true, follow: true },
};

/* The verdict, as the engine actually computes it. Keep in step with
   backend/app/decision_engine.py — these are its constants, not marketing. */
const CRITERIA: [string, number, string][] = [
  ["Margin", 25, "Full points at 30% or better. Below 15%, the product is AVOID whatever else it scores."],
  ["Competition", 20, "How many brands dominate the niche and how many reviews the median listing has."],
  ["ROI", 15, "Profit per unit against landed cost. Full points at 100%."],
  ["Size", 12, "Small and light earns more: FBA fees and freight scale with the box."],
  ["Price", 10, "A selling price with room for fees and ads underneath it."],
  ["Demand", 8, "Steady year-round beats seasonal spikes."],
  ["Patent", 5, "A hard gate. Patent risk is an AVOID, not a deduction."],
  ["Certification", 5, "Whether the category needs one, and whether that is already handled."],
];

const FAQ: [string, string][] = [
  [
    "Is this really free?",
    "Yes. The check on this page needs no account and stores nothing you type. Up to five products at a time. The paid product is separate: an operator that connects to a Shopify store and runs the same analysis across the whole catalogue every day.",
  ],
  [
    "Where do the numbers come from?",
    "From you. Price, landed cost, FBA fee, ad cost per unit and an expected monthly sales figure are typed in; the referral fee defaults to Amazon's usual 15%. The arithmetic on those inputs is exact. The sales estimate is still a guess, so the projected monthly profit is labelled as projected, not measured.",
  ],
  [
    "How is the verdict decided?",
    "A fixed formula scores eight criteria out of 100 with the weights shown above. 70 or more is BUY, 50 to 69 is CAUTION, under 50 is AVOID. Two hard gates override the score: a margin under 15% and any patent risk are AVOID regardless. The same inputs give the same verdict every time.",
  ],
  [
    "Does an AI decide whether I should buy?",
    "No. The score and verdict are computed by the formula. A language model only writes the explanation underneath, from figures the formula already produced. It cannot change a verdict, and it cannot invent a number.",
  ],
  [
    "Is the FBA fee exact?",
    "Use the figure from Amazon's own FBA calculator for the product's size tier; this tool takes it as an input rather than guessing it. Fee schedules change, so confirm before committing.",
  ],
  [
    "What happens after the check?",
    "Nothing, unless you want it to. If you sell on Shopify, the Operator can connect read-only, import orders, products and stock, and propose the changes worth making — a price, a reorder, a product to drop — and measure afterwards, from your own numbers, whether a change earned anything. It never touches the shop without approval.",
  ],
];

function faqJsonLd() {
  return JSON.stringify({
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: FAQ.map(([q, a]) => ({
      "@type": "Question", name: q, acceptedAnswer: { "@type": "Answer", text: a },
    })),
  });
}

function appJsonLd() {
  return JSON.stringify({
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: "AI Commerce Operator — FBA profit check",
    applicationCategory: "BusinessApplication",
    operatingSystem: "Web",
    url: CANONICAL,
    offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
    description: metadata.description,
  });
}

export default function HomePage() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: faqJsonLd() }} />
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: appJsonLd() }} />

      <header className="px-6 pt-10 lg:px-11" style={{ maxWidth: "76ch" }}>
        <p className="lbl">Amazon FBA · Shopify · free, no account</p>
        <h1 style={{ margin: "14px 0 0", fontSize: "clamp(30px, 5vw, 44px)", fontWeight: 600,
                     letterSpacing: "-.03em", lineHeight: 1.08 }}>
          Amazon FBA profit calculator — with a verdict, not just a number
        </h1>
        <p style={{ margin: "18px 0 0", fontSize: "16px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          Type in a product you are thinking of buying. Get margin, ROI, profit per unit and
          a <strong style={{ color: "var(--proven)" }}>BUY</strong> /{" "}
          <strong style={{ color: "var(--waiting)" }}>CAUTION</strong> /{" "}
          <strong style={{ color: "var(--unproven)" }}>AVOID</strong> verdict computed by a fixed
          formula, with the reasoning written out. Most products lose money before the first
          order ships — not because the idea was bad, but because nobody ran these numbers first.
        </p>
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <a href="#check" className="btn-primary inline-flex items-center gap-2">
            <Icon name="gem" size={13} /> CHECK A PRODUCT NOW
          </a>
          <span className="num" style={{ fontSize: "10.5px", letterSpacing: ".1em", color: "var(--ink-5)" }}>
            30 SECONDS · NOTHING STORED · UP TO 5 PRODUCTS
          </span>
        </div>
      </header>

      <div id="check" style={{ scrollMarginTop: "24px" }}>
        <ProductFinder mode="public" heading="h2" />
      </div>

      <section className="px-6 pb-16 lg:px-11" style={{ maxWidth: "76ch" }} aria-labelledby="how">
        <p className="lbl">How the verdict is computed</p>
        <h2 id="how" style={{ margin: "12px 0 0", fontSize: "26px", fontWeight: 600, letterSpacing: "-.02em" }}>
          Eight criteria, one hundred points, two hard gates
        </h2>
        <p style={{ margin: "14px 0 0", fontSize: "14.5px", lineHeight: 1.7, color: "var(--ink-2)" }}>
          70 or more is <strong style={{ color: "var(--proven)" }}>BUY</strong>. 50 to 69 is{" "}
          <strong style={{ color: "var(--waiting)" }}>CAUTION</strong>. Under 50 is{" "}
          <strong style={{ color: "var(--unproven)" }}>AVOID</strong> — as is any product with a
          margin under 15% or a patent risk, whatever else it scores. These are the engine's
          constants, not a description of them.
        </p>
        <ul className="mt-6" style={{ listStyle: "none", margin: "24px 0 0", padding: 0, display: "grid", gap: "10px" }}>
          {CRITERIA.map(([name, points, note]) => (
            <li key={name} className="card flex items-start gap-4 px-5 py-4">
              <span className="num" style={{ minWidth: "3ch", fontSize: "18px", fontWeight: 600, color: "var(--ink)" }}>{points}</span>
              <span>
                <span style={{ fontSize: "14px", fontWeight: 600 }}>{name}</span>
                <span style={{ display: "block", marginTop: "4px", fontSize: "13px", lineHeight: 1.6, color: "var(--ink-2)" }}>{note}</span>
              </span>
            </li>
          ))}
        </ul>
        <p style={{ margin: "16px 0 0", fontSize: "13px", lineHeight: 1.7, color: "var(--ink-4)" }}>
          A language model writes the explanation under each verdict from figures the formula
          already produced. It cannot change a score and it cannot invent a number.
        </p>
      </section>

      <section className="px-6 pb-16 lg:px-11" style={{ maxWidth: "76ch" }} aria-labelledby="after">
        <p className="lbl">After the check</p>
        <h2 id="after" style={{ margin: "12px 0 0", fontSize: "26px", fontWeight: 600, letterSpacing: "-.02em" }}>
          That was one product, by hand. The Operator does it across your whole Shopify catalogue.
        </h2>
        <p style={{ margin: "14px 0 0", fontSize: "14.5px", lineHeight: 1.7, color: "var(--ink-2)" }}>
          Connect a store read-only and it imports your orders, products and stock every day,
          scores every product on the scale above, and proposes the changes worth making — a
          price, a reorder, a product to drop. It never changes anything in your shop without
          your approval, and it measures afterwards, from your own numbers, whether a change
          earned anything. A figure it has not measured is one it withholds rather than estimates.
        </p>
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <a href="/signin" className="btn-primary inline-flex items-center gap-2">
            <Icon name="lock" size={13} /> START THE FREE PILOT
          </a>
          <a href="/pricing" className="btn-quiet">WHAT IT COSTS</a>
          <span className="num" style={{ fontSize: "10.5px", letterSpacing: ".1em", color: "var(--ink-5)" }}>
            NO CARD · SHOPIFY READ-ONLY · CANCEL ANY TIME
          </span>
        </div>
      </section>

      <section className="px-6 pb-20 lg:px-11" style={{ maxWidth: "76ch" }} aria-labelledby="faq">
        <p className="lbl">Questions</p>
        <h2 id="faq" style={{ margin: "12px 0 0", fontSize: "26px", fontWeight: 600, letterSpacing: "-.02em" }}>
          Before you trust a verdict
        </h2>
        <dl style={{ margin: "20px 0 0", display: "grid", gap: "18px" }}>
          {FAQ.map(([q, a]) => (
            <div key={q}>
              <dt style={{ fontSize: "15px", fontWeight: 600 }}>{q}</dt>
              <dd style={{ margin: "6px 0 0", fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-2)" }}>{a}</dd>
            </div>
          ))}
        </dl>
      </section>
    </>
  );
}
