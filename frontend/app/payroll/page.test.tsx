/**
 * Tests for what the payroll screen says while a result is still being waited on.
 *
 * The failure this guards is the one that already happened. Somebody confirmed
 * a restock, Shopify agreed the shelf had been refilled, and the screen showed
 * nothing at all — the count of what was "in effect, waiting to be priced" was
 * taken after the list had been filtered down to proven money, and an action
 * waiting for its first measurement is by definition not proven yet.
 *
 * The second thing checked here is the difference between waiting and being
 * stuck. A window still running is the calendar doing its job. A window that
 * closed while nothing was syncing is a fault, and telling somebody to be
 * patient about it is wrong advice.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import * as api from "../../lib/api";
import PayrollPage from "./page";

const waiting = (over: Partial<api.AwaitingMeasurement> = {}): api.AwaitingMeasurement => ({
  action_id: "a1", action_type: "RESTOCK_PRODUCT", target: "The Complete Snowboard",
  applied_at: "2026-09-08T13:00:00Z",
  stock_before: 1, verified_on_hand: 30,
  window_first: "2026-09-09", window_last: "2026-09-22", measure_on: "2026-09-23",
  days_remaining: 9, state: "window_open", data_state: "collecting",
  reason: "9 full day(s) to go; measuring on 2026-09-23.",
  ...over,
});

const payroll = (over: Partial<api.Payroll> = {}): api.Payroll => ({
  period_days: 30, settled_impact: 0, provisional_impact: 0, operator_cost: 0,
  net: 0, paid_for_itself: false, awaiting_measurement: 1, counts: { applied: 1 },
  currency: "USD", evidence_mode: "real", excluded_unverified: 1, excluded_demo: 0,
  awaiting: [waiting()], explanation: null,
  ...over,
});

function mock(data: api.Payroll) {
  vi.spyOn(api, "getPayroll").mockResolvedValue(data);
  vi.spyOn(api, "listActions").mockResolvedValue([]);
  vi.spyOn(api, "getGuardrails").mockResolvedValue({
    enabled: true, max_actions_per_day: 20, max_change_pct: 0.25,
    auto_apply_below: 0, protected_spend_per_day: 0,
    applied_today: 0, remaining_today: 20,
  });
}

beforeEach(() => { vi.restoreAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

it("shows an applied action that has not been priced yet", async () => {
  mock(payroll());
  render(<PayrollPage />);
  expect(await screen.findByText("The Complete Snowboard")).toBeInTheDocument();
});

it("says what Shopify confirmed, not merely that something was applied", async () => {
  mock(payroll());
  const { container } = render(<PayrollPage />);
  await screen.findByText("The Complete Snowboard");
  expect(container.textContent ?? "").toMatch(/SHOPIFY CONFIRMED 30 IN STOCK, UP FROM 1/);
});

it("says how long is left and when the answer arrives", async () => {
  mock(payroll());
  const { container } = render(<PayrollPage />);
  await screen.findByText("The Complete Snowboard");
  expect(container.textContent ?? "").toMatch(/9 full day\(s\) to go/);
  expect(container.textContent ?? "").toMatch(/measured on/);
});

it("does not tell somebody to wait when the hold-up is a broken sync", async () => {
  mock(payroll({
    awaiting: [waiting({
      state: "awaiting_data", data_state: "incomplete", days_remaining: 0,
      reason: "The window closed, but this shop has never completed a sync.",
    })],
  }));
  const { container } = render(<PayrollPage />);
  await screen.findByText("The Complete Snowboard");
  expect(container.textContent ?? "").toMatch(/never completed a sync/);
  expect(container.textContent ?? "").not.toMatch(/full day\(s\) to go/);
});

it("points a cost-blocked result at the screen that unblocks it", async () => {
  /** A window nobody can price is not a wait. It is a blank somebody can fill
      in, and telling them to be patient instead is wrong advice. */
  mock(payroll({
    awaiting: [waiting({
      state: "awaiting_cost", data_state: "awaiting_cost", days_remaining: 0,
      reason: "No unit cost is on record for Small.",
    })],
  }));
  render(<PayrollPage />);
  await screen.findByText("The Complete Snowboard");
  const link = screen.getByRole("link", { name: /Add the unit cost/ });
  expect(link).toHaveAttribute("href", "/costs");
});

it("shows nothing about waiting when nothing is waiting", async () => {
  mock(payroll({ awaiting: [], awaiting_measurement: 0, excluded_unverified: 0 }));
  const { container } = render(<PayrollPage />);
  // The headline still renders; the waiting block must not.
  await screen.findByText(/PAID FOR ITSELF|NOT YET PROVEN/);
  expect(container.textContent ?? "").not.toMatch(/waiting to be priced/);
});

it("never presents a projection as a measured result", async () => {
  /** Provisional money sits beside the headline, never inside it. */
  mock(payroll({ provisional_impact: 120, settled_impact: 0 }));
  const { container } = render(<PayrollPage />);
  await screen.findByText("The Complete Snowboard");
  expect(container.textContent ?? "").toMatch(/not counted above/i);
});
