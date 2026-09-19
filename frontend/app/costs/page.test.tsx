/**
 * Tests for the product costs screen.
 *
 * The claim this screen exists to make possible is a margin, and the failure it
 * exists to prevent is an absent cost read as a zero. A missing figure must
 * look missing — never as nothing paid, which would make the whole sale price
 * come out as profit.
 *
 * The second thing checked is provenance. Shopify's own number and a number
 * somebody typed are both usable and only one of them is evidence, so the
 * screen says which it is looking at rather than presenting them alike.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import * as api from "../../lib/api";
import ProductCostsPage from "./page";

const entry = (over: Partial<api.ProductCostEntry> = {}): api.ProductCostEntry => ({
  product_id: "gid://shopify/Product/1",
  product_title: "The Complete Snowboard",
  variant_id: "gid://shopify/ProductVariant/10",
  variant_title: "Small",
  units_sold: 14, last_sold: "2026-09-22", sale_currency: "USD",
  unit_cost: null, cost_currency: null, effective_from: null,
  source: null, verification: null,
  applies_to_every_variant: false, currency_matches: true,
  attributable_to_variant: true, waiting_measurements: [],
  ...over,
});

const costs = (over: Partial<api.ProductCosts> = {}): api.ProductCosts => ({
  entries: [entry()], without_cost: 1, blocking: 0, currency: "USD", ...over,
});

function mock(data: api.ProductCosts) {
  return vi.spyOn(api, "getProductCosts").mockResolvedValue(data);
}

beforeEach(() => { vi.restoreAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

it("shows a missing cost as missing and never as nothing paid", async () => {
  mock(costs());
  const { container } = render(<ProductCostsPage />);
  await screen.findByText(/The Complete Snowboard/);
  expect(screen.getByText("NOT KNOWN")).toBeInTheDocument();
  expect(container.textContent ?? "").not.toMatch(/\$0\.00/);
});

it("says when a figure came from the shop rather than from a person", async () => {
  mock(costs({ entries: [entry({ unit_cost: 20, cost_currency: "USD",
                                 source: "shopify", verification: "confirmed",
                                 effective_from: "2026-09-08" })] }));
  render(<ProductCostsPage />);
  expect(await screen.findByText(/READ FROM SHOPIFY/)).toBeInTheDocument();
});

it("says when a figure is one the seller reported", async () => {
  mock(costs({ entries: [entry({ unit_cost: 20, cost_currency: "USD",
                                 source: "manual", verification: "reported",
                                 effective_from: "2026-09-08" })] }));
  render(<ProductCostsPage />);
  expect(await screen.findByText(/REPORTED BY YOU/)).toBeInTheDocument();
});

it("names the measurement that is waiting on this cost", async () => {
  mock(costs({
    blocking: 1,
    entries: [entry({ waiting_measurements: ["The Complete Snowboard"] })],
  }));
  const { container } = render(<ProductCostsPage />);
  await screen.findByText(/Waiting on this/);
  expect(container.textContent ?? "").toMatch(/HOLDING UP A MEASUREMENT/);
});

it("refuses to convert a cost recorded in another currency", async () => {
  mock(costs({
    entries: [entry({ unit_cost: 18, cost_currency: "EUR", sale_currency: "USD",
                      currency_matches: false, source: "manual",
                      verification: "reported" })],
  }));
  const { container } = render(<ProductCostsPage />);
  await screen.findByText(/The Complete Snowboard/);
  expect(container.textContent ?? "").toMatch(/Nothing here converts between them/);
});

it("does not offer a cost box for units whose variant is unknown", async () => {
  /** No cost can honestly be attached to a unit we cannot identify. */
  mock(costs({ entries: [entry({ attributable_to_variant: false, variant_id: null,
                                 variant_title: null })] }));
  render(<ProductCostsPage />);
  await screen.findByText(/did not say which variant/);
  expect(screen.queryByRole("button", { name: /RECORD THIS COST/ })).toBeNull();
});

it("sends what was typed, split into purchase and everything else", async () => {
  mock(costs());
  const write = vi.spyOn(api, "setProductCost").mockResolvedValue({} as api.ProductCost);
  render(<ProductCostsPage />);
  await screen.findByText(/The Complete Snowboard/);

  await userEvent.type(screen.getByLabelText(/PAID PER UNIT/), "18.5");
  await userEvent.type(screen.getByLabelText(/EVERYTHING ELSE PER UNIT/), "1.5");
  await userEvent.click(screen.getByRole("button", { name: /RECORD THIS COST/ }));

  await waitFor(() => expect(write).toHaveBeenCalled());
  expect(write.mock.calls[0][0]).toMatchObject({
    purchase_amount: 18.5, extra_amount: 1.5, currency: "USD",
  });
});

it("records the cost in the currency the units sold in", async () => {
  mock(costs({ currency: "USD", entries: [entry({ sale_currency: "GBP" })] }));
  const write = vi.spyOn(api, "setProductCost").mockResolvedValue({} as api.ProductCost);
  render(<ProductCostsPage />);
  await screen.findByText(/The Complete Snowboard/);

  await userEvent.type(screen.getByLabelText(/PAID PER UNIT/), "20");
  await userEvent.click(screen.getByRole("button", { name: /RECORD THIS COST/ }));

  await waitFor(() => expect(write).toHaveBeenCalled());
  expect(write.mock.calls[0][0]).toMatchObject({ currency: "GBP" });
});

it("puts what is blocking a measurement above what is not", async () => {
  mock(costs({
    entries: [
      entry({ variant_title: "Quiet", units_sold: 99, unit_cost: 20,
              cost_currency: "USD", source: "shopify", verification: "confirmed" }),
      entry({ variant_id: "gid://shopify/ProductVariant/11", variant_title: "Blocking", units_sold: 2,
              waiting_measurements: ["The Complete Snowboard"] }),
    ],
    blocking: 1,
  }));
  const { container } = render(<ProductCostsPage />);
  await screen.findByText(/Blocking/);
  const text = container.textContent ?? "";
  expect(text.indexOf("Blocking")).toBeLessThan(text.indexOf("Quiet"));
});
