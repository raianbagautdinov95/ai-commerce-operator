"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { getOnboarding, type OnboardingStatus } from "../lib/api";
import { Icon, type IconName } from "./icons";
import { useSession } from "./session";

/* The order is the operator's cycle, not the alphabet: what it did today, what
   it proposes, what it runs alone, what you owe it, where it reads from.
   Those five carry icons; the tools behind the divider are text, so the cycle
   stays legible at a glance in a row this long. */
const PRIMARY: { href: string; label: string; icon: IconName }[] = [
  { href: "/dashboard", label: "TODAY", icon: "revenue" },
  { href: "/proposals", label: "PROPOSALS", icon: "spark" },
  { href: "/autopilot", label: "AUTOPILOT", icon: "bolt" },
  { href: "/payroll", label: "PAYROLL", icon: "profit" },
  { href: "/integrations", label: "CHANNELS", icon: "plug" },
];

const SECONDARY = [
  { href: "/operator", label: "LAUNCH PLAN" },
  { href: "/report", label: "REPORT" },
  { href: "/discover", label: "DISCOVER" },
  { href: "/", label: "HUNTER" },
  { href: "/ppc", label: "PPC" },
  { href: "/inventory", label: "INVENTORY" },
  { href: "/creative", label: "CREATIVE" },
  { href: "/suppliers", label: "SUPPLIERS" },
  { href: "/costs", label: "PRODUCT COSTS" },
  { href: "/billing", label: "BILLING" },
  { href: "/setup", label: "SETUP" },
];

/* The setup indicator carries a word as well as a colour, because a coloured
   dot alone is a state some people cannot read. Green appears only when the
   backend says every step is complete — never because somebody visited the
   page. */
const SETUP_TONE: Record<OnboardingStatus, { colour: string; word: string }> = {
  complete: { colour: "var(--proven)", word: "SET UP" },
  needs_attention: { colour: "var(--unproven)", word: "SETUP NEEDS YOU" },
  in_progress: { colour: "var(--ink-3)", word: "FINISH SETUP" },
  not_started: { colour: "var(--ink-3)", word: "FINISH SETUP" },
};

export default function Nav() {
  const pathname = usePathname();
  const { principal, signOut } = useSession();
  // A server with authentication switched off answers /me with an empty
  // user id. That is worth seeing in the chrome rather than discovering later.
  const authOff = principal != null && principal.user_id === "";

  // Read once, and never allowed to break the chrome: a nav bar that throws
  // because a status call failed takes the sign-out link with it.
  const [setup, setSetup] = useState<OnboardingStatus | null>(null);
  useEffect(() => {
    if (principal == null) return;
    let cancelled = false;
    getOnboarding()
      .then((data) => {
        if (cancelled) return;
        const statuses = data.steps.map((s) => s.status);
        setSetup(statuses.includes("needs_attention") ? "needs_attention"
          : data.complete ? "complete"
          : statuses.some((s) => s !== "not_started") ? "in_progress" : "not_started");
      })
      .catch(() => { /* the nav is not the place to report this */ });
    return () => { cancelled = true; };
  }, [principal]);

  const style = (active: boolean, dim: boolean) => ({
    fontSize: "11.5px",
    letterSpacing: ".07em",
    padding: "21px 0",
    color: active ? "var(--ink)" : dim ? "var(--ink-5)" : "var(--ink-3)",
    borderBottom: active ? "1px solid var(--proven)" : "1px solid transparent",
  });

  return (
    <nav
      className="flex items-center gap-6 overflow-x-auto px-6 lg:px-11"
      style={{ height: "62px", borderBottom: "1px solid var(--line)" }}
    >
      <a href="/dashboard" className="mr-2 flex shrink-0 items-center gap-3">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--proven)"
             strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <path d="M8 12.5l2.5 2.5L16 9" />
        </svg>
        <span className="num" style={{ fontSize: "13px", fontWeight: 500, letterSpacing: ".1em" }}>
          OPERATOR
        </span>
      </a>

      {PRIMARY.map((item) => {
        const active = pathname === item.href;
        return (
          <a key={item.href} href={item.href}
             className="num flex shrink-0 items-center gap-2 whitespace-nowrap transition-colors"
             style={style(active, false)}>
            <Icon name={item.icon} size={15} />
            {item.label}
          </a>
        );
      })}

      <span style={{ width: "1px", height: "16px", background: "var(--line-soft)", flexShrink: 0 }} />

      {SECONDARY.map((item) => (
        <a key={item.href + item.label} href={item.href}
           className="num shrink-0 whitespace-nowrap transition-colors"
           style={style(pathname === item.href, true)}>
          {item.label}
        </a>
      ))}

      <span className="ml-auto flex shrink-0 items-center gap-4 pl-6">
        {setup && setup !== "complete" && (
          <a href="/onboarding"
             className="num flex shrink-0 items-center gap-2 whitespace-nowrap"
             style={{ fontSize: "11px", letterSpacing: ".1em",
                      color: SETUP_TONE[setup].colour }}>
            <Icon name={setup === "needs_attention" ? "alert" : "plug"} size={14} />
            {SETUP_TONE[setup].word}
          </a>
        )}
        {authOff && (
          <span className="num flex items-center gap-2"
                style={{ fontSize: "11px", letterSpacing: ".1em", color: "var(--waiting)" }}
                title="This server accepts every request without a token.">
            <Icon name="alert" size={14} /> AUTH OFF
          </span>
        )}
        {principal && !authOff && (
          <>
            <span className="num truncate" style={{ maxWidth: "190px", fontSize: "11.5px", color: "var(--ink-3)" }}
                  title={principal.email}>
              {principal.email}
            </span>
            <button onClick={signOut}
                    className="num flex items-center gap-2 whitespace-nowrap transition-colors hover:text-[color:var(--ink)]"
                    style={{ fontSize: "11px", letterSpacing: ".1em", color: "var(--ink-5)" }}>
              <Icon name="lock" size={14} /> SIGN OUT
            </button>
          </>
        )}
        {!principal && (
          <a href="/signin" className="num flex items-center gap-2 whitespace-nowrap"
             style={{ fontSize: "11px", letterSpacing: ".1em", color: "var(--ink-3)" }}>
            <Icon name="lock" size={14} /> SIGN IN
          </a>
        )}
      </span>
    </nav>
  );
}
