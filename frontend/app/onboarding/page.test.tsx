/**
 * Tests for the onboarding screen.
 *
 * Each fixture is a response shape the backend actually produces — the step
 * keys, statuses and action names come from `app/onboarding.py`, not from
 * anything invented here. What is being checked is the half this screen owns:
 * that a broken step cannot be mistaken for a working one, that an unhelpful
 * error never reaches the customer, and that pressing a button twice does not
 * ask the server twice.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../../lib/api";
import OnboardingPage from "./page";

type Step = api.OnboardingStep;

const step = (over: Partial<Step> & Pick<Step, "key" | "status">): Step => ({
  title: over.key, detail: "", action: null, action_label: null, help_url: null,
  ...over,
} as Step);

/** The eight steps as the backend orders them, all complete unless overridden. */
function onboarding(over: Partial<Step>[] = [], rest: Partial<api.Onboarding> = {}): api.Onboarding {
  const base = ["account", "permissions", "oauth", "token", "notifications",
                "delivery", "sync", "analysis"];
  const steps = base.map((key) => {
    const patch = over.find((o) => o.key === key);
    return step({ key, status: "complete", title: key, detail: "", ...patch } as never);
  });
  return {
    steps, complete: steps.every((s) => s.status === "complete"),
    support_email: "help@example.test",
    support_response_time: "same business day",
    support_url: null, ...rest,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function mockOnboarding(data: api.Onboarding) {
  return vi.spyOn(api, "getOnboarding").mockResolvedValue(data);
}

describe("a new customer with no store", () => {
  it("says what to do rather than showing an empty screen", async () => {
    mockOnboarding(onboarding([
      { key: "oauth", status: "not_started", title: "Connect Shopify",
        detail: "Not connected yet.", action: "connect_shopify",
        action_label: "CONNECT SHOPIFY" },
      { key: "token", status: "not_started" },
      { key: "notifications", status: "not_started" },
      { key: "delivery", status: "not_started" },
      { key: "sync", status: "not_started" },
      { key: "analysis", status: "not_started" },
    ]));
    render(<OnboardingPage />);
    expect(await screen.findByText("Let's connect your store")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /CONNECT SHOPIFY/ })).toBeInTheDocument();
  });

  it("states the read-only promise before anything is connected", async () => {
    mockOnboarding(onboarding([
      { key: "permissions", status: "complete", title: "What the Operator may do",
        detail: "Read-only. It holds no permission to change anything in your shop." },
      { key: "oauth", status: "not_started" },
    ]));
    render(<OnboardingPage />);
    expect(await screen.findByText(/no permission to change anything/)).toBeInTheDocument();
  });
});

describe("connected but not working", () => {
  it("does not let a completed OAuth step hide a missing token", async () => {
    mockOnboarding(onboarding([
      { key: "oauth", status: "complete", title: "Connect Shopify" },
      { key: "token", status: "needs_attention", title: "Access token",
        detail: "The shop is linked but its access token is missing.",
        action: "connect_shopify", action_label: "RECONNECT SHOPIFY" },
    ]));
    render(<OnboardingPage />);
    expect(await screen.findByText("Something needs you")).toBeInTheDocument();
    expect(screen.getByText(/access token is missing/)).toBeInTheDocument();
  });

  it("reports refused signatures as needing attention, not as connected", async () => {
    mockOnboarding(onboarding([
      { key: "notifications", status: "needs_attention",
        title: "Order notifications",
        detail: "Shopify is sending order notifications and they are being refused.",
        action: "open_help", action_label: "HOW TO FIX THIS",
        help_url: "/help/failing-signature" },
    ]));
    render(<OnboardingPage />);
    expect(await screen.findByText("Something needs you")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /HOW TO FIX THIS/ })).toBeInTheDocument();
  });

  it("names the state in words, not only in colour", async () => {
    mockOnboarding(onboarding([{ key: "sync", status: "needs_attention" }]));
    render(<OnboardingPage />);
    expect(await screen.findByText("NEEDS YOU")).toBeInTheDocument();
  });
});

describe("sync and analysis are separate", () => {
  it("shows a running sync as working rather than done", async () => {
    mockOnboarding(onboarding([
      { key: "sync", status: "in_progress", title: "First sync",
        detail: "Running now." },
      { key: "analysis", status: "not_started" },
    ]));
    render(<OnboardingPage />);
    expect(await screen.findByText("WORKING")).toBeInTheDocument();
  });

  it("offers a retry after a failed sync", async () => {
    mockOnboarding(onboarding([
      { key: "sync", status: "needs_attention", title: "First sync",
        detail: "The last sync did not finish. Running it again is safe.",
        action: "run_sync", action_label: "TRY AGAIN" },
    ]));
    render(<OnboardingPage />);
    expect(await screen.findByRole("button", { name: /TRY AGAIN/ })).toBeInTheDocument();
    expect(screen.getByText(/again is safe/)).toBeInTheDocument();
  });

  it("offers only the analysis when the sync finished without one", async () => {
    mockOnboarding(onboarding([
      { key: "sync", status: "complete" },
      { key: "analysis", status: "needs_attention", title: "First analysis",
        detail: "Your orders are in, but nothing has read them yet.",
        action: "run_analysis", action_label: "READ MY SALES NOW" },
    ]));
    render(<OnboardingPage />);
    expect(await screen.findByRole("button", { name: /READ MY SALES NOW/ }))
      .toBeInTheDocument();
    // Not sent to reconnect Shopify, which fixes nothing and costs the token.
    expect(screen.queryByRole("button", { name: /RECONNECT/ })).toBeNull();
  });

  it("treats an analysis that found nothing as done, and explains it", async () => {
    mockOnboarding(onboarding([
      { key: "analysis", status: "complete", title: "First analysis",
        detail: "Ran and found nothing worth proposing. That is an answer, not a failure." },
    ]));
    render(<OnboardingPage />);
    expect(await screen.findByText(/not a failure/)).toBeInTheDocument();
    expect(screen.getByText("Set up")).toBeInTheDocument();
  });

  it("points at the proposals when there are some", async () => {
    mockOnboarding(onboarding([
      { key: "analysis", status: "complete", title: "First analysis",
        detail: "2 proposal(s) waiting for you.",
        action: "open_proposals", action_label: "SEE THEM" },
    ]));
    render(<OnboardingPage />);
    expect(await screen.findByRole("button", { name: /SEE THEM/ })).toBeInTheDocument();
  });
});

describe("safety", () => {
  it("does not send two requests when a button is pressed twice", async () => {
    mockOnboarding(onboarding([
      { key: "analysis", status: "needs_attention", action: "run_analysis",
        action_label: "READ MY SALES NOW" },
    ]));
    let resolve: (v: unknown) => void = () => {};
    const scan = vi.spyOn(api, "scanCommerce").mockImplementation(
      () => new Promise((r) => { resolve = r as never; }) as never);

    render(<OnboardingPage />);
    const button = await screen.findByRole("button", { name: /READ MY SALES NOW/ });
    await userEvent.click(button);
    await userEvent.click(button);
    resolve({ days: 30, observed_days: 10, findings: 0, opened: 0, already_open: 0 });

    await waitFor(() => expect(scan).toHaveBeenCalledTimes(1));
  });

  it("shows no identifier, token or trace from the API", async () => {
    mockOnboarding(onboarding([
      { key: "oauth", status: "complete",
        detail: "Connected to a-shop.myshopify.com." },
    ]));
    const { container } = render(<OnboardingPage />);
    await screen.findByText(/Connected to a-shop/);
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/store_id|tenant|shpat_|shpss_|Traceback|code=/);
  });

  it("explains an unreachable service instead of showing Failed to fetch", async () => {
    vi.spyOn(api, "getOnboarding").mockRejectedValue(new TypeError("Failed to fetch"));
    render(<OnboardingPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not reach the service/i);
    expect(alert).toHaveTextContent(/nothing has been lost/i);
    expect(alert.textContent).not.toMatch(/Failed to fetch/);
  });

  it("offers support when something goes wrong", async () => {
    vi.spyOn(api, "getOnboarding").mockRejectedValue(new Error("API error 500"));
    render(<OnboardingPage />);
    expect(await screen.findByText("help@example.test")).toBeInTheDocument();
  });
});

describe("finished", () => {
  it("says so only when every step is complete", async () => {
    mockOnboarding(onboarding());
    render(<OnboardingPage />);
    expect(await screen.findByText("Set up")).toBeInTheDocument();
    expect(screen.queryByText("Something needs you")).toBeNull();
  });

  it("shows no percentage anywhere", async () => {
    mockOnboarding(onboarding([{ key: "sync", status: "in_progress" }]));
    const { container } = render(<OnboardingPage />);
    await screen.findByText("Nearly there");
    expect(container.textContent ?? "").not.toMatch(/\d+\s?%/);
  });

  it("gives every step a heading and every action a real button", async () => {
    mockOnboarding(onboarding([
      { key: "sync", status: "needs_attention", action: "run_sync",
        action_label: "TRY AGAIN" },
    ]));
    render(<OnboardingPage />);
    await screen.findByText("Something needs you");
    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(8);
    expect(screen.getByRole("button", { name: /TRY AGAIN/ })).toBeEnabled();
  });
});
