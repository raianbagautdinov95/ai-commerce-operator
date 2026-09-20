/**
 * The front door.
 *
 * A stranger arriving from a post must land on a working tool: the page renders
 * with no session, the verdict comes from the public endpoint (no token, nothing
 * stored), and only after a verdict does the invitation to sign up appear. The
 * cap on candidates is the boundary between the free check and the product.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import * as api from "../../lib/api";
import { PUBLIC_MAX_CANDIDATES } from "../../components/ProductFinder";
import TryPage from "./page";

const verdict = (name: string): api.Evaluation => ({
  name,
  economics: { referral_fee: 4.05, fba_fee_total: 3.3, profit_per_unit: 10.65, margin: 0.39,
               roi: 1.64, monthly_profit: 6390 } as api.Evaluation["economics"],
  score: 82, subscores: { margin: 1, roi: 1 }, verdict: "BUY", reason: "Healthy margin",
  whatif: { break_even_price: 16.35, price_for_floor: 19.2, price_for_target: 23.4,
            max_cogs_at_floor: 10.6 } as api.Evaluation["whatif"],
  pros: ["Margin above 30%"], risks: [], explanation: "A comfortable margin at this price.",
  inputs: { name, price: 27, cogs: 6.5, fba_fee: 3.3, monthly_sales: 600, ppc_per_unit: 2.5 } as api.ProductRequest,
});

afterEach(() => { vi.restoreAllMocks(); window.sessionStorage.clear(); });

it("counts the visit with the source the link carried, and credits the evaluation to it", async () => {
  window.history.replaceState({}, "", "/try?utm_source=facebook&utm_medium=paid&utm_campaign=try-us");
  const visit = vi.spyOn(api, "recordPublicVisit").mockImplementation(() => {});
  const pub = vi.spyOn(api, "evaluateProductsPublic")
    .mockResolvedValue({ results: [verdict("Silicone baking molds")], weights: { margin: 25 } });

  render(<TryPage />);
  expect(visit).toHaveBeenCalledWith({ source: "facebook", medium: "paid", campaign: "try-us" });

  await userEvent.click(screen.getByRole("button", { name: /evaluate/i }));
  await waitFor(() => expect(pub).toHaveBeenCalled());
  expect(pub.mock.calls[0][2]).toEqual({ source: "facebook", medium: "paid", campaign: "try-us" });
  window.history.replaceState({}, "", "/try");
});

it("scores through the public endpoint and only then invites", async () => {
  const pub = vi.spyOn(api, "evaluateProductsPublic")
    .mockResolvedValue({ results: [verdict("Silicone baking molds")], weights: { margin: 25, roi: 20 } });
  const priv = vi.spyOn(api, "evaluateProducts");
  const history = vi.spyOn(api, "getHistory");

  render(<TryPage />);
  expect(screen.queryByTestId("public-invitation")).toBeNull();
  expect(screen.getByText(/no account/i)).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /evaluate/i }));

  await waitFor(() => expect(screen.getByTestId("public-invitation")).toBeInTheDocument());
  expect(screen.getByRole("link", { name: /start the free pilot/i })).toHaveAttribute("href", "/signin");
  expect(pub).toHaveBeenCalledTimes(1);
  expect(priv).not.toHaveBeenCalled();
  expect(history).not.toHaveBeenCalled();
});

it("shows the server's own refusal instead of a status code", async () => {
  vi.spyOn(api, "evaluateProductsPublic")
    .mockRejectedValue(new Error("That is enough evaluations for one hour from this address."));
  render(<TryPage />);
  await userEvent.click(screen.getByRole("button", { name: /evaluate/i }));
  await waitFor(() => expect(screen.getByText(/enough evaluations for one hour/i)).toBeInTheDocument());
});

it("stops adding candidates at the public cap", async () => {
  render(<TryPage />);
  const add = screen.getByRole("button", { name: /add product/i });
  for (let i = 0; i < PUBLIC_MAX_CANDIDATES + 2; i += 1) await userEvent.click(add);
  expect(screen.getAllByText(/^candidate \d+$/i)).toHaveLength(PUBLIC_MAX_CANDIDATES);
  expect(add).toBeDisabled();
  expect(screen.getByText(/is the limit here/i)).toBeInTheDocument();
});
