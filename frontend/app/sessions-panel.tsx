"use client";

import { useCallback, useEffect, useState } from "react";
import { listSessions, revokeOtherSessions, type TokenSession } from "../lib/api";
import { Icon } from "./icons";

/* Every way into this account that currently works.
   The point of showing them is recognising one you did not open — a phone you
   no longer have, a laptop left somewhere — and ending it. So each row carries
   dates and how it was opened, and never a token or any part of one. */

const METHOD: Record<string, string> = {
  google: "GOOGLE",
  email: "EMAIL CODE",
  cli: "ISSUED ON THE SERVER",
};

function ago(iso: string) {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 90) return "just now";
  const minutes = seconds / 60;
  if (minutes < 90) return `${Math.round(minutes)} min ago`;
  const hours = minutes / 60;
  if (hours < 36) return `${Math.round(hours)} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

function until(iso: string) {
  const days = (new Date(iso).getTime() - Date.now()) / 86_400_000;
  if (days < 1) return "expires today";
  return `expires in ${Math.round(days)} d`;
}

export default function SessionsPanel({ onSignOut }: { onSignOut: () => void }) {
  const [rows, setRows] = useState<TokenSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ended, setEnded] = useState<number | null>(null);

  const load = useCallback(() => {
    listSessions()
      .then((next) => { setRows(next); setError(null); })
      .catch(() => setError("Could not read your sessions from the server."));
  }, []);

  useEffect(load, [load]);

  async function revokeOthers() {
    setBusy(true);
    setError(null);
    try {
      setEnded(await revokeOtherSessions());
      load();
    } catch {
      setError("Could not end the other sessions. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }

  const others = (rows ?? []).filter((row) => !row.current).length;

  return (
    <div className="card mt-6 p-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Icon name="shield" size={18} stroke="var(--proven)" />
          <div>
            <p className="lbl">Signed in</p>
            <p style={{ margin: "5px 0 0", fontSize: "14px", color: "var(--ink-2)" }}>
              {rows === null ? "Reading your sessions…"
                : `${rows.length} live session${rows.length === 1 ? "" : "s"} on this account`}
            </p>
          </div>
        </div>
        <button onClick={onSignOut} className="btn-quiet inline-flex items-center gap-2">
          <Icon name="lock" size={13} /> SIGN OUT
        </button>
      </div>

      {error && (
        <p className="card-unproven mt-5 px-4 py-3"
           style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                    fontSize: "13px", color: "var(--unproven)" }}>
          {error}
        </p>
      )}

      {rows !== null && rows.length > 0 && (
        <div className="mt-6">
          {rows.map((row, i) => (
            <div key={row.id} className="flex flex-wrap items-center justify-between gap-4 py-3.5"
                 style={{ borderTop: i === 0 ? "none" : "1px solid var(--line-faint)" }}>
              <div className="flex items-center gap-3">
                <span className={`dot ${row.current ? "dot-proven" : "dot-waiting"}`} />
                <div>
                  <p className="num" style={{ margin: 0, fontSize: "12px", letterSpacing: ".1em" }}>
                    {METHOD[row.method] ?? row.method.toUpperCase()}
                    {row.current && <span style={{ color: "var(--proven)" }}> · THIS DEVICE</span>}
                  </p>
                  <p style={{ margin: "5px 0 0", fontSize: "12px", color: "var(--ink-5)" }}>
                    Last used {ago(row.last_seen_at)} · {until(row.expires_at)}
                  </p>
                </div>
              </div>
              <p className="num" style={{ margin: 0, fontSize: "11px", color: "var(--ink-6)" }}>
                {new Date(row.issued_at).toLocaleDateString()}
              </p>
            </div>
          ))}
        </div>
      )}

      {others > 0 && (
        <div className="mt-6 flex flex-wrap items-center justify-between gap-4 pt-5"
             style={{ borderTop: "1px solid var(--line-faint)" }}>
          <p style={{ margin: 0, maxWidth: "56ch", fontSize: "13px", lineHeight: 1.65, color: "var(--ink-2)" }}>
            Lost a laptop, or signed in somewhere you should not have? Ending the
            other {others === 1 ? "session" : "sessions"} takes effect on their
            very next request. This device stays signed in.
          </p>
          <button onClick={revokeOthers} disabled={busy} className="btn-quiet shrink-0">
            {busy ? "ENDING…" : `SIGN OUT ${others} OTHER${others === 1 ? "" : "S"}`}
          </button>
        </div>
      )}

      {ended !== null && (
        <p className="num mt-4" style={{ fontSize: "11.5px", color: "var(--proven)" }}>
          {ended === 0 ? "NOTHING ELSE WAS SIGNED IN" : `ENDED ${ended} SESSION${ended === 1 ? "" : "S"}`}
        </p>
      )}
    </div>
  );
}
