"use client";

import { useState } from "react";
import { parseDelimited } from "../lib/csv";

/**
 * Collapsible paste-area for CSV/TSV import. Parses pasted text into rows and
 * hands them to `onRows`; the parent maps columns to its own fields.
 */
export default function CsvImport({
  hint,
  onRows,
}: {
  hint: string;
  onRows: (rows: Record<string, string>[]) => number;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  function load() {
    const rows = parseDelimited(text);
    if (rows.length === 0) {
      setMsg("No rows found — include a header line and at least one data row.");
      return;
    }
    const used = onRows(rows);
    setMsg(used > 0 ? `Imported ${used} row${used === 1 ? "" : "s"}.` : "No recognizable rows — check the column headers.");
  }

  return (
    <div className="mt-4">
      <button
        onClick={() => setOpen((o) => !o)}
        className="text-sm font-medium text-blue-600 hover:underline"
      >
        {open ? "▾ Import from CSV/Excel" : "▸ Import from CSV/Excel"}
      </button>
      {open && (
        <div className="mt-2 rounded-lg border border-slate-200 p-3">
          <p className="text-xs text-slate-500">{hint}</p>
          <textarea
            className="mt-2 h-28 w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
            placeholder="Paste rows here (tab- or comma-separated, with a header line)…"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="mt-2 flex items-center gap-3">
            <button
              onClick={load}
              className="rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
            >
              Load
            </button>
            {msg && <span className="text-xs text-slate-500">{msg}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
