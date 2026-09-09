/**
 * Tests for the billing screen.
 *
 * The one that matters most is the last: this screen must not report a
 * subscription as active because somebody arrived back on it from Stripe.
 * Checkout redirects on payment *submitted*; only the webhook afterwards means
 * it succeeded, and a screen that confuses the two will tell somebody they are
 * paying thirty seconds before their card is declined.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../../lib/api";
import BillingPage from "./page";

const base: api.Entitlement = {
  status: "trialing", access: true, period_ends_at: null,
  trial_days_remaining: 9, action_required: false, action: "checkout",
  explanation: "You are on the free trial.", plan_name: "Operator",
  price_per_month: 49, currency: "USD", checkout_available: true,
  features: ["Connects to your Shopify store, read-only"],
  support_email: "help@example.test", support_response_time: "same business day",
};

const state = (over: Partial<api.Entitlement> = {}): api.Entitlement => ({ ...base, ...over });

beforeEach(() => { vi.restoreAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

function mock(data: api.Entitlement) {
  return vi.spyOn(api, "getEntitlement").mockResolvedValue(data);
}

describe("the trial", () => {
  it("shows the days left and the price without a card", async () => {
    mock(state({ trial_days_remaining: 9 }));
    const { container } = render(<BillingPage />);
    expect(await screen.findByText(/9 day\(s\) of trial left/)).toBeInTheDocument();
    // The amount and the period are separate elements, so read the rendered
    // text. The currency symbol is left to the runtime's locale rather than
    // asserted, because pinning it here would test Intl instead of this screen.
    expect(container.textContent ?? "").toMatch(/49/);
    expect(container.textContent ?? "").toMatch(/per month/);
    expect(screen.getByRole("button", { name: /SUBSCRIBE/ })).toBeInTheDocument();
  });

  it("explains rather than offering a button when payment is not set up", async () => {
    mock(state({ checkout_available: false }));
    render(<BillingPage />);
    expect(await screen.findByText(/Payment is not set up/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /SUBSCRIBE/ })).toBeNull();
  });
});

describe("paying, and not paying", () => {
  it("sends an active subscriber to the portal, not to a second checkout", async () => {
    mock(state({ status: "active", action: "portal", access: true,
                 explanation: "Your subscription is active." }));
    render(<BillingPage />);
    expect(await screen.findByRole("button", { name: /MANAGE SUBSCRIPTION/ }))
      .toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /SUBSCRIBE/ })).toBeNull();
  });

  it("asks a past-due customer to update their card and says data is safe", async () => {
    mock(state({
      status: "past_due", access: false, action: "portal", action_required: true,
      explanation: "The last payment did not go through. Your data is safe.",
    }));
    render(<BillingPage />);
    expect(await screen.findByText("PAYMENT FAILED")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /UPDATE PAYMENT/ })).toBeInTheDocument();
    expect(screen.getByText(/Your data is safe/)).toBeInTheDocument();
  });

  it("tells a cancelled customer their data stayed", async () => {
    mock(state({
      status: "canceled", access: false, action: "checkout", action_required: true,
      explanation: "Your subscription has ended. Everything you have is still here.",
    }));
    render(<BillingPage />);
    expect(await screen.findByText("CANCELLED")).toBeInTheDocument();
    expect(screen.getByText(/still here/)).toBeInTheDocument();
    expect(screen.getByText(/read it, export it or ask for it to be deleted/))
      .toBeInTheDocument();
  });

  it("says what stops when access has lapsed", async () => {
    mock(state({ status: "expired", access: false, action_required: true,
                 explanation: "Your trial has ended." }));
    render(<BillingPage />);
    expect(await screen.findByText(/stops importing orders/)).toBeInTheDocument();
  });

  it("limits nothing when the deployment has no billing", async () => {
    mock(state({ status: "not_configured", access: true, action: null,
                 action_required: false,
                 explanation: "This deployment has no billing configured." }));
    render(<BillingPage />);
    expect(await screen.findByText("NO BILLING")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("safety", () => {
  it("does not open two checkout sessions on a double click", async () => {
    mock(state());
    let resolve: (v: string) => void = () => {};
    const checkout = vi.spyOn(api, "createBillingCheckout")
      .mockImplementation(() => new Promise<string>((r) => { resolve = r; }));

    render(<BillingPage />);
    const button = await screen.findByRole("button", { name: /SUBSCRIBE/ });
    await userEvent.click(button);
    await userEvent.click(button);
    resolve("https://checkout.stripe.test/session");

    await waitFor(() => expect(checkout).toHaveBeenCalledTimes(1));
  });

  it("shows no Stripe or tenant identifier", async () => {
    mock(state({ status: "active", action: "portal" }));
    const { container } = render(<BillingPage />);
    await screen.findByText("ACTIVE");
    expect(container.textContent ?? "").not.toMatch(/cus_|sub_|store_id|price_/);
  });

  it("explains an unreachable service instead of showing Failed to fetch", async () => {
    vi.spyOn(api, "getEntitlement").mockRejectedValue(new TypeError("Failed to fetch"));
    render(<BillingPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not reach the service/i);
    expect(alert).toHaveTextContent(/nothing has been charged/i);
    expect(alert.textContent).not.toMatch(/Failed to fetch/);
  });

  it("never claims active on the strength of coming back from Stripe", async () => {
    // The customer has just returned from Checkout; the webhook has not landed,
    // so the backend still says trialing. The screen must say trialing too.
    mock(state({ status: "trialing", access: true }));
    render(<BillingPage />);
    expect(await screen.findByText("FREE TRIAL")).toBeInTheDocument();
    expect(screen.queryByText("ACTIVE")).toBeNull();
  });

  it("names the state in words as well as colour", async () => {
    mock(state({ status: "past_due", access: false, action: "portal",
                 action_required: true }));
    render(<BillingPage />);
    expect(await screen.findByText("PAYMENT FAILED")).toBeInTheDocument();
  });
});
