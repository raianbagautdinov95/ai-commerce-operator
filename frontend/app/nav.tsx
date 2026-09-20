"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { getOnboarding, type OnboardingStatus } from "../lib/api";
import { Icon, type IconName } from "./icons";
import { useSession } from "./session";

const PRIMARY: { href: string; label: string; icon: IconName }[] = [
  { href: "/dashboard", label: "TODAY", icon: "revenue" },
  { href: "/proposals", label: "PROPOSALS", icon: "spark" },
  { href: "/autopilot", label: "AUTOPILOT", icon: "bolt" },
  { href: "/payroll", label: "PAYROLL", icon: "profit" },
  { href: "/integrations", label: "CHANNELS", icon: "plug" },
];

const SECONDARY = [
  { href: "/operator", label: "LAUNCH PLAN" }, { href: "/report", label: "REPORT" },
  { href: "/discover", label: "DISCOVER" }, { href: "/", label: "HUNTER" },
  { href: "/ppc", label: "PPC" }, { href: "/inventory", label: "INVENTORY" },
  { href: "/creative", label: "CREATIVE" }, { href: "/suppliers", label: "SUPPLIERS" },
  { href: "/costs", label: "PRODUCT COSTS" }, { href: "/billing", label: "BILLING" },
  { href: "/setup", label: "SETUP" },
];

/* What a visitor with no account can actually open. Showing them the whole
   product's menu is a list of doors that all lead to the sign-in form. */
const GUEST: { href: string; label: string; icon: IconName }[] = [
  { href: "/try", label: "FREE PRODUCT CHECK", icon: "gem" },
  { href: "/pricing", label: "PRICING", icon: "profit" },
];

const SETUP_TONE: Record<OnboardingStatus, { colour: string; word: string }> = {
  complete: { colour: "var(--proven)", word: "SET UP" },
  needs_attention: { colour: "var(--unproven)", word: "SETUP NEEDS YOU" },
  in_progress: { colour: "var(--ink-3)", word: "FINISH SETUP" },
  not_started: { colour: "var(--ink-3)", word: "FINISH SETUP" },
};

export default function Nav() {
  const pathname = usePathname();
  const { principal, checked, signOut } = useSession();
  // Nobody signed in, and the server has said so (not merely not answered yet).
  const guest = checked && principal == null;
  const authOff = principal != null && principal.user_id === "";
  const [setup, setSetup] = useState<OnboardingStatus | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(window.localStorage.getItem("operator-nav-collapsed") === "true");
  }, []);
  useEffect(() => {
    if (principal == null) return;
    let cancelled = false;
    getOnboarding().then((data) => {
      if (cancelled) return;
      const statuses = data.steps.map((s) => s.status);
      setSetup(statuses.includes("needs_attention") ? "needs_attention"
        : data.complete ? "complete"
        : statuses.some((s) => s !== "not_started") ? "in_progress" : "not_started");
    }).catch(() => { /* Navigation must remain available when status is unavailable. */ });
    return () => { cancelled = true; };
  }, [principal]);

  const toggle = () => setCollapsed((current) => {
    const next = !current;
    window.localStorage.setItem("operator-nav-collapsed", String(next));
    return next;
  });
  const linkStyle = (active: boolean) => ({ color: active ? "var(--ink)" : "var(--ink-3)" });

  // The landing page is a document, not a screen in the product: no sidebar.
  // It is /home in the app, and "/" when the bare domain rewrites to it — the
  // rewrite is invisible to the browser, so the host is what tells them apart.
  const onLanding = pathname === "/home" ||
    (pathname === "/" && typeof window !== "undefined" &&
     /^(www\.)?aicommerceoperator\.com$/.test(window.location.hostname));
  if (onLanding) return null;

  return (
    <nav className={`operator-nav ${collapsed ? "is-collapsed" : ""}`} aria-label="Main navigation">
      <div className="operator-nav-head">
        <a href={guest ? "/try" : "/dashboard"} className="operator-brand" title="OPERATOR">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--proven)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect x="3" y="3" width="18" height="18" rx="2" /><path d="M8 12.5l2.5 2.5L16 9" />
          </svg>
          <span className="operator-nav-label num">OPERATOR</span>
        </a>
        <button className="operator-nav-toggle" type="button" onClick={toggle}
                aria-label={collapsed ? "Show navigation" : "Hide navigation"}
                title={collapsed ? "Show navigation" : "Hide navigation"}>
          <Icon name="list" size={18} />
        </button>
      </div>

      <div className="operator-nav-scroll">
        {guest ? (
          <div className="operator-nav-group">
            {GUEST.map((item) => {
              const active = pathname === item.href;
              return <a key={item.href} href={item.href} title={item.label}
                className={`operator-nav-link num ${active ? "is-active" : ""}`} style={linkStyle(active)}>
                <Icon name={item.icon} size={17} /><span className="operator-nav-label">{item.label}</span>
              </a>;
            })}
          </div>
        ) : (<>
        <div className="operator-nav-group">
          {PRIMARY.map((item) => {
            const active = pathname === item.href;
            return <a key={item.href} href={item.href} title={item.label}
              className={`operator-nav-link num ${active ? "is-active" : ""}`} style={linkStyle(active)}>
              <Icon name={item.icon} size={17} /><span className="operator-nav-label">{item.label}</span>
            </a>;
          })}
        </div>
        <div className="operator-nav-divider" />
        <div className="operator-nav-group operator-nav-tools">
          {SECONDARY.map((item) => {
            const active = pathname === item.href;
            return <a key={item.href + item.label} href={item.href} title={item.label}
              className={`operator-nav-link num ${active ? "is-active" : ""}`} style={linkStyle(active)}>
              <Icon name="list" size={16} /><span className="operator-nav-label">{item.label}</span>
            </a>;
          })}
        </div>
        </>)}
      </div>

      <div className="operator-nav-account num">
        {setup && setup !== "complete" && <a href="/onboarding" title={SETUP_TONE[setup].word}
          className="operator-nav-link" style={{ color: SETUP_TONE[setup].colour }}>
          <Icon name={setup === "needs_attention" ? "alert" : "plug"} size={16} />
          <span className="operator-nav-label">{SETUP_TONE[setup].word}</span>
        </a>}
        {authOff && <span className="operator-nav-link" style={{ color: "var(--waiting)" }} title="Authentication is disabled"><Icon name="alert" size={16} /><span className="operator-nav-label">AUTH OFF</span></span>}
        {principal && !authOff && <>
          <span className="operator-user operator-nav-label" title={principal.email}>{principal.email}</span>
          <button onClick={signOut} className="operator-nav-link operator-sign-out" title="SIGN OUT"><Icon name="lock" size={16} /><span className="operator-nav-label">SIGN OUT</span></button>
        </>}
        {!principal && <a href="/signin" className="operator-nav-link" title="SIGN IN"><Icon name="lock" size={16} /><span className="operator-nav-label">SIGN IN</span></a>}
      </div>
    </nav>
  );
}
