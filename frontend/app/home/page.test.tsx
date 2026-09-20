/**
 * The front door for a search: the calculator is on the page, the verdict's
 * thresholds are stated as the engine has them, and the page carries what a
 * search engine needs to know it answers the query.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import * as api from "../../lib/api";
import HomePage, { metadata } from "./page";

afterEach(() => vi.restoreAllMocks());

it("puts the calculator on the page under a search-facing headline", () => {
  vi.spyOn(api, "recordPublicVisit").mockImplementation(() => {});
  render(<HomePage />);
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/fba profit calculator/i);
  // The Hunter itself, not a picture of it: its evaluate button is here.
  expect(screen.getByRole("button", { name: /evaluate/i })).toBeInTheDocument();
  // And it does not bring a second h1 with it.
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
});

it("states the engine's thresholds, not a paraphrase of them", () => {
  vi.spyOn(api, "recordPublicVisit").mockImplementation(() => {});
  render(<HomePage />);
  // Said twice on purpose: once where the criteria are, once in the FAQ.
  expect(screen.getAllByText(/70 or more is/i).length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText(/margin under 15%/i).length).toBeGreaterThanOrEqual(1);
  expect(screen.getByText("25")).toBeInTheDocument(); // margin weight
});

it("carries a canonical, a description and FAQ structured data", () => {
  vi.spyOn(api, "recordPublicVisit").mockImplementation(() => {});
  expect(metadata.alternates?.canonical).toBe("https://aicommerceoperator.com/");
  expect(String(metadata.title)).toMatch(/profit calculator/i);
  const { container } = render(<HomePage />);
  const ld = Array.from(container.querySelectorAll('script[type="application/ld+json"]'))
    .map((s) => JSON.parse(s.textContent || "{}"));
  expect(ld.some((d) => d["@type"] === "FAQPage")).toBe(true);
});
