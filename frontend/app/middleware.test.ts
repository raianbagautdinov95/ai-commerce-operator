/**
 * One build, two hosts. The bare domain is a landing page and nothing else;
 * everything with a path belongs to the app host; www is not a second address.
 */
import { describe, expect, it } from "vitest";

import { route } from "../middleware";

const at = (u: string) => new URL(u);

describe("the bare domain", () => {
  it("shows the landing page at / without changing the address", () => {
    const r = route(at("https://aicommerceoperator.com/?utm_source=google"), "aicommerceoperator.com");
    expect(r.headers.get("x-middleware-rewrite")).toContain("/home?utm_source=google");
  });
  it("sends any other path to the app host, keeping path and query", () => {
    const r = route(at("https://aicommerceoperator.com/pricing?x=1"), "aicommerceoperator.com");
    expect(r.status).toBe(307);
    expect(r.headers.get("location")).toBe("https://app.aicommerceoperator.com/pricing?x=1");
  });
  it("folds www into the bare domain permanently", () => {
    const r = route(at("https://www.aicommerceoperator.com/"), "www.aicommerceoperator.com");
    expect(r.status).toBe(308);
    expect(r.headers.get("location")).toBe("https://aicommerceoperator.com/");
  });
});

describe("the app host", () => {
  it("is left alone", () => {
    const r = route(at("https://app.aicommerceoperator.com/dashboard"), "app.aicommerceoperator.com:443");
    expect(r.status).toBe(200);
    expect(r.headers.get("location")).toBeNull();
    expect(r.headers.get("x-middleware-rewrite")).toBeNull();
  });
});
