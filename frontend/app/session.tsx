"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { endSession, fetchMe, NotAuthenticated, setToken, type Principal } from "../lib/api";

/* ---------------------------------------------------------------------------
   Who is looking at this, and what happens when the answer is "nobody".

   Before this existed the API refused every call with a 401 and each screen
   drew its own empty state, so a signed-out visitor saw a dashboard full of
   dashes and a banner blaming the server. Two different problems that look
   identical are one problem too many, so the two are separated here:

   * the server REFUSED the token  -> sign in again
   * the server did not answer     -> leave the app up and let it say so

   A deployment with authentication switched off answers /api/auth/me happily,
   so it lands in the first branch's success case and nothing redirects.
   --------------------------------------------------------------------------- */

/** Pages a person must be able to reach without being signed in. */
const PUBLIC = new Set(["/signin", "/privacy", "/terms", "/privacy-center", "/pricing"]);

type SessionState = {
  principal: Principal | null;
  /** False only while the very first /me call is still in flight. */
  checked: boolean;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<SessionState>({
  principal: null,
  checked: false,
  signOut: async () => {},
});

export const useSession = () => useContext(SessionContext);

export default function SessionGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [checked, setChecked] = useState(false);

  const isPublic = PUBLIC.has(pathname);

  const signOut = useCallback(async () => {
    // The server call is what ends the session; clearing the browser only hides
    // it. `endSession` drops the local token either way, so a failure here
    // still gets somebody out of the app — the access screen is where they can
    // see whether the session is really gone.
    await endSession();
    setPrincipal(null);
    router.push("/signin");
  }, [router]);

  useEffect(() => {
    let alive = true;
    fetchMe()
      .then((me) => {
        if (!alive) return;
        setPrincipal(me);
        setChecked(true);
      })
      .catch((error) => {
        if (!alive) return;
        setChecked(true);
        // Only a refusal means signed out. A network failure leaves the app
        // standing so the screen can report what actually happened.
        if (error instanceof NotAuthenticated && !isPublic) {
          setToken(null);
          router.replace("/signin");
        }
      });
    return () => { alive = false; };
    // Deliberately keyed on the public/private boundary rather than on the
    // path: re-asking who you are on every page change is a request per click
    // for an answer that has not moved. A token that dies mid-visit is caught
    // by the listener below instead.
  }, [isPublic, router]);

  // A token that expires mid-visit surfaces as a 401 on some ordinary call.
  useEffect(() => {
    if (isPublic) return;
    const expired = () => {
      setToken(null);
      setPrincipal(null);
      router.replace("/signin");
    };
    window.addEventListener("aco:unauthenticated", expired);
    return () => window.removeEventListener("aco:unauthenticated", expired);
  }, [isPublic, router]);

  // Rendering a screen we are about to navigate away from would show a page
  // full of failures for a moment and teach the wrong thing about the product.
  if (!isPublic && !checked) {
    return (
      <div className="flex items-center justify-center" style={{ minHeight: "60vh" }}>
        <p className="num" style={{ fontSize: "11.5px", letterSpacing: ".14em", color: "var(--ink-5)" }}>
          CHECKING YOUR SESSION…
        </p>
      </div>
    );
  }

  return (
    <SessionContext.Provider value={{ principal, checked, signOut }}>
      {children}
    </SessionContext.Provider>
  );
}
