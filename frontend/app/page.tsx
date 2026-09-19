import ProductFinder from "../components/ProductFinder";

// The signed-in Hunter. The screen itself lives in components/ProductFinder so
// the public /try page can show the very same thing to somebody with no account.
export default function ProductHunterPage() {
  return <ProductFinder />;
}
