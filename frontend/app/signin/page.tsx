"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Icon, IconPlate } from "../icons";
import SessionsPanel from "../sessions-panel";
import {
  endSession,
  fetchAuthConfig,
  getToken,
  requestLoginCode,
  setToken,
  signInWithGoogle,
  startSession,
  verifyLoginCode,
  type AuthConfig,
  type Session,
  SignInError,
} from "../../lib/api";

const GSI_SRC = "https://accounts.google.com/gsi/client";

/** Load Google's script once, and only on a deployment that has a client ID. */
function useGoogleScript(clientId: string | null) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!clientId) return;
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${GSI_SRC}"]`);
    if (existing) {
      if ((window as any).google?.accounts?.id) setReady(true);
      else existing.addEventListener("load", () => setReady(true), { once: true });
      return;
    }
    const script = document.createElement("script");
    script.src = GSI_SRC;
    script.async = true;
    script.defer = true;
    script.onload = () => setReady(true);
    document.head.appendChild(script);
  }, [clientId]);
  return ready;
}

export default function SignIn() {
  const router = useRouter();
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [hasToken, setHasToken] = useState(false);
  const [manual, setManual] = useState("");
  const googleButton = useRef<HTMLDivElement | null>(null);
  const scriptReady = useGoogleScript(config?.google_client_id ?? null);

  useEffect(() => {
    setHasToken(Boolean(getToken()));
    fetchAuthConfig()
      .then(setConfig)
      .catch((e) =>
        setConfigError(e instanceof SignInError ? e.message : "Could not reach the server."),
      );
  }, []);

  const arrive = useCallback(
    (session: Session) => {
      startSession(session);
      setHasToken(true);
      router.push("/dashboard");
    },
    [router],
  );

  // Render Google's own button: its look and its consent text are not ours to
  // reimplement, and a hand-drawn copy is what gets an app taken down.
  useEffect(() => {
    if (!scriptReady || !config?.google_client_id || !googleButton.current) return;
    const google = (window as any).google;
    if (!google?.accounts?.id) return;
    google.accounts.id.initialize({
      client_id: config.google_client_id,
      callback: async (response: { credential?: string }) => {
        if (!response.credential) {
          setError("Google did not return a sign-in token. Try again.");
          return;
        }
        setError(null);
        setBusy(true);
        try {
          arrive(await signInWithGoogle(response.credential));
        } catch (e) {
          setError(e instanceof SignInError ? e.message : "Could not sign in with Google.");
        } finally {
          setBusy(false);
        }
      },
    });
    google.accounts.id.renderButton(googleButton.current, {
      theme: "outline",
      size: "large",
      width: 320,
      text: "continue_with",
      shape: "rectangular",
    });
  }, [scriptReady, config?.google_client_id, arrive]);

  async function sendCode() {
    setError(null);
    setBusy(true);
    try {
      await requestLoginCode(email.trim());
      setCodeSent(true);
    } catch (e) {
      setError(e instanceof SignInError ? e.message : "Could not send the code.");
    } finally {
      setBusy(false);
    }
  }

  async function submitCode() {
    setError(null);
    setBusy(true);
    try {
      arrive(await verifyLoginCode(email.trim(), code.trim()));
    } catch (e) {
      setError(e instanceof SignInError ? e.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  }

  async function forget() {
    // Clearing the browser was never signing out — the token stayed valid for
    // the rest of its life. This ends the session on the server first.
    await endSession();
    setHasToken(false);
    setCodeSent(false);
    setCode("");
  }

  const googleOffered = Boolean(config?.google_client_id);
  const emailOffered = Boolean(config?.email_login);
  const nothingOffered = config !== null && !googleOffered && !emailOffered;

  return (
    <main className="mx-auto px-6 py-16" style={{ maxWidth: "560px" }}>
      <div className="flex items-center gap-3">
        <IconPlate name="lock" />
        <p className="lbl">Access</p>
      </div>
      <h1 style={{ margin: "16px 0 0", fontSize: "30px", fontWeight: 600, letterSpacing: "-.025em" }}>
        Sign in
      </h1>
      <p style={{ margin: "14px 0 0", fontSize: "14px", lineHeight: 1.7, color: "var(--ink-2)" }}>
        Your session is kept in this browser and sent to this API only. A new account starts in
        approve-only mode: the Operator reads and proposes, and changes nothing until you raise
        the limit yourself.
      </p>

      {hasToken && <SessionsPanel onSignOut={forget} />}

      {configError && <Notice tone="waiting">{configError}</Notice>}

      {nothingOffered && (
        <Notice tone="waiting">
          No sign-in method is configured on this server yet. Whoever runs it can set{" "}
          <span className="num">GOOGLE_CLIENT_ID</span>, or <span className="num">RESEND_API_KEY</span>{" "}
          and <span className="num">LOGIN_EMAIL_FROM</span> for codes by email. Until then, use a
          token issued on the server below.
        </Notice>
      )}

      <div className="card mt-8 p-6 sm:p-7">
        {googleOffered && (
          <div className="flex flex-col items-center gap-3">
            <div ref={googleButton} aria-label="Sign in with Google" />
            {!scriptReady && (
              <p className="num" style={{ fontSize: "11px", letterSpacing: ".1em", color: "var(--ink-5)" }}>
                LOADING GOOGLE SIGN-IN…
              </p>
            )}
          </div>
        )}

        {googleOffered && emailOffered && (
          <div className="my-7 flex items-center gap-4">
            <span style={{ height: "1px", flex: 1, background: "var(--line)" }} />
            <span className="num" style={{ fontSize: "10.5px", letterSpacing: ".18em", color: "var(--ink-5)" }}>OR</span>
            <span style={{ height: "1px", flex: 1, background: "var(--line)" }} />
          </div>
        )}

        {emailOffered && (
          <div>
            <label className="lbl" htmlFor="email">Email address</label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              value={email}
              disabled={codeSent}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@yourstore.com"
              className="field"
              style={{ marginTop: "8px", opacity: codeSent ? 0.5 : 1 }}
            />

            {!codeSent ? (
              <button
                onClick={sendCode}
                disabled={busy || !email.includes("@")}
                className="btn-primary mt-4 w-full">
                {busy ? "SENDING…" : "EMAIL ME A CODE"}
              </button>
            ) : (
              <div className="mt-6">
                <label className="lbl" htmlFor="code">The six digits we sent you</label>
                <input
                  id="code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                  placeholder="000000"
                  className="field num"
                  style={{ marginTop: "8px", textAlign: "center", fontSize: "20px",
                           letterSpacing: ".4em", padding: "14px" }}
                />
                <button
                  onClick={submitCode}
                  disabled={busy || code.length !== 6}
                  className="btn-primary mt-4 w-full">
                  {busy ? "SIGNING IN…" : "SIGN IN"}
                </button>
                <button
                  onClick={() => { setCodeSent(false); setCode(""); setError(null); }}
                  className="num mt-3 w-full py-2"
                  style={{ fontSize: "11px", letterSpacing: ".1em", color: "var(--ink-5)" }}>
                  USE A DIFFERENT ADDRESS
                </button>
                <p style={{ margin: "14px 0 0", fontSize: "12px", lineHeight: 1.6, color: "var(--ink-5)" }}>
                  The code expires in 10 minutes and works once. Asking for another one cancels it.
                </p>
              </div>
            )}
          </div>
        )}

        {error && (
          <p role="alert" className="card-unproven mt-5 flex items-start gap-3 px-4 py-3"
             style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                      fontSize: "13px", lineHeight: 1.6, color: "var(--unproven)" }}>
            <Icon name="alert" size={16} className="mt-0.5 shrink-0" /> {error}
          </p>
        )}
      </div>

      <details className="card mt-5 p-6" style={{ background: "var(--raised)" }}>
        <summary className="num cursor-pointer"
                 style={{ fontSize: "11.5px", letterSpacing: ".1em", color: "var(--ink-2)" }}>
          I WAS GIVEN A TOKEN INSTEAD
        </summary>
        <p style={{ margin: "16px 0 0", fontSize: "13px", lineHeight: 1.7, color: "var(--ink-2)" }}>
          Whoever runs the server can mint one directly. This stays as the way in when email
          delivery is down, or before either sign-in method is configured.
        </p>
        <pre className="mt-4 overflow-x-auto p-4"
             style={{ background: "#05070A", border: "1px solid var(--line-faint)",
                      borderRadius: "var(--r)", fontSize: "11.5px", lineHeight: 1.7,
                      color: "var(--ink-3)", fontFamily: "IBM Plex Mono, ui-monospace, monospace" }}>
{`docker compose exec api python -m app.issue_token \
  --email you@example.com --role owner --days 30`}
        </pre>
        <textarea
          value={manual}
          onChange={(e) => setManual(e.target.value)}
          rows={3}
          placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…"
          className="field num"
          style={{ marginTop: "14px", resize: "vertical", fontSize: "11.5px" }}
        />
        <button
          onClick={() => {
            setToken(manual.trim() || null);
            setManual("");
            setHasToken(Boolean(manual.trim()));
            if (manual.trim()) router.push("/dashboard");
          }}
          disabled={!manual.trim()}
          className="btn-quiet mt-4">
          USE THIS TOKEN
        </button>
        <p style={{ margin: "16px 0 0", fontSize: "12px", lineHeight: 1.7, color: "var(--ink-4)" }}>
          A token issued this way is listed and ended like any other session. Rotating{" "}
          <span className="num">JWT_SECRET</span> remains the blunt instrument: it invalidates
          every token that exists, everywhere.
        </p>
      </details>
    </main>
  );
}

function Notice({ tone, children }: { tone: "waiting" | "unproven"; children: React.ReactNode }) {
  return (
    <p className={`card-${tone} mt-6 flex items-start gap-3 px-4 py-3`}
       style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                fontSize: "13px", lineHeight: 1.65, color: "var(--ink-2)" }}>
      <Icon name={tone === "waiting" ? "clock" : "alert"} size={16}
            stroke={`var(--${tone})`} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </p>
  );
}
