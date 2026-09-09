import type { Metadata } from "next";
import "./globals.css";
import Nav from "./nav";
import SessionGate from "./session";

export const metadata: Metadata = {
  title: "AI Commerce Operator",
  description: "One AI operator for every commerce channel",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@300;400;500;600&display=swap"
        />
      </head>
      <body className="antialiased">
        {/* The spectrum lives outside the panel and means nothing. Inside, only
            green, amber and red are saturated, and each one means something. */}
        <div className="shell">
          <div className="shell-bed" />
          <div className="shell-core" />
          <div className="shell-panel">
            <div className="shell-grid" />
            <div className="relative">
              <SessionGate>
              <Nav />
              {children}
              <footer style={{ borderTop: "1px solid var(--line)" }}>
                <div className="flex flex-wrap items-center gap-6 px-6 py-6 lg:px-11">
                  <a href="/privacy" className="num" style={{ fontSize: "11px", letterSpacing: ".08em", color: "var(--ink-5)" }}>PRIVACY</a>
                  <a href="/terms" className="num" style={{ fontSize: "11px", letterSpacing: ".08em", color: "var(--ink-5)" }}>TERMS</a>
                  <a href="/privacy-center" className="num" style={{ fontSize: "11px", letterSpacing: ".08em", color: "var(--ink-5)" }}>PRIVACY CENTER</a>
                  <span className="num ml-auto" style={{ fontSize: "11px", letterSpacing: ".08em", color: "var(--ink-6)" }}>AI COMMERCE OPERATOR</span>
                </div>
              </footer>
              </SessionGate>
            </div>
          </div>
        </div>
      </body>
    </html>
  );
}
