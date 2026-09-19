import type { Metadata } from "next";
import ProductFinder from "../../components/ProductFinder";

export const metadata: Metadata = {
  title: "Is it worth selling? Free product check · AI Commerce Operator",
  description:
    "Type in a product's price, landed cost and expected sales. Get margin, ROI, profit per unit and a BUY / CAUTION / AVOID verdict — free, no account.",
};

// The front door. Somebody arriving from a post lands on a working tool, not on
// a sign-in form, and decides whether to sign up after seeing a verdict.
export default function TryPage() {
  return <ProductFinder mode="public" />;
}
