"use client";

import { useState } from "react";
import { downloadWorkspaceExport, requestWorkspaceDeletion } from "../../lib/api";
import { IconPlate } from "../icons";

const PHRASE = "DELETE MY WORKSPACE";

export default function PrivacyCenter() {
  const [confirmation, setConfirmation] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"export" | "delete" | null>(null);

  async function exportData() {
    setBusy("export"); setError(null); setMessage(null);
    try {
      await downloadWorkspaceExport();
      setMessage("The export has been downloaded.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function requestDeletion() {
    setBusy("delete"); setError(null); setMessage(null);
    try {
      const id = await requestWorkspaceDeletion(confirmation);
      setMessage(`Request ${id} is pending review.`);
      setConfirmation("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="mx-auto px-6 pb-20 pt-11" style={{ maxWidth: "780px" }}>
      <div className="flex items-center gap-3">
        <IconPlate name="key" />
        <p className="lbl">Your data</p>
      </div>
      <h1 style={{ margin: "16px 0 0", fontSize: "30px", fontWeight: 600, letterSpacing: "-.025em" }}>
        Privacy Center
      </h1>
      <p style={{ margin: "14px 0 0", maxWidth: "66ch", fontSize: "14.5px", lineHeight: 1.7, color: "var(--ink-2)" }}>
        Take a machine-readable copy of everything in this workspace, or ask for it
        to be deleted.
      </p>

      <section className="card mt-8 p-6 sm:p-7">
        <h2 style={{ margin: 0, fontSize: "17px", fontWeight: 600 }}>Export workspace data</h2>
        <p style={{ margin: "10px 0 0", fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-3)" }}>
          Secrets, tokens and internal security records are never included.
        </p>
        <button
          onClick={exportData}
          disabled={busy !== null}
          className="btn-quiet mt-5"
        >
          {busy === "export" ? "Preparing…" : "Download JSON export"}
        </button>
      </section>

      <section className="card-unproven mt-4 p-6 sm:p-7"
               style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)" }}>
        <h2 className="font-semibold text-rose-300">Request workspace deletion</h2>
        <p style={{ margin: "10px 0 0", fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-3)" }}>
          This creates an auditable pending request. It does not instantly erase data,
          which leaves room for identity, billing and legal-retention checks.
        </p>
        <input
          value={confirmation}
          onChange={(e) => setConfirmation(e.target.value)}
          placeholder={`Type ${PHRASE}`}
          className="field num" style={{ marginTop: "16px" }}
        />
        <button
          disabled={confirmation !== PHRASE || busy !== null}
          onClick={requestDeletion}
          className="num mt-4" style={{ border: "1px solid rgba(248,113,113,.4)", borderRadius: "var(--r-sm)",
                   padding: "12px 20px", fontSize: "11.5px", letterSpacing: ".12em",
                   background: "rgba(248,113,113,.07)", color: "var(--unproven)" }}
        >
          {busy === "delete" ? "Submitting…" : "Submit deletion request"}
        </button>
      </section>

      {message && <p className="num mt-5" style={{ fontSize: "12px", letterSpacing: ".06em", color: "var(--proven)" }}>{message}</p>}
      {error && (
        <p className="card-unproven mt-4 px-5 py-4"
           style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)", fontSize: "13.5px", color: "var(--unproven)" }}>
          {error}
        </p>
      )}
    </main>
  );
}
