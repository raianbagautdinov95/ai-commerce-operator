// Tiny CSV/TSV parser for pasted spreadsheet data (Excel paste = tab-delimited,
// downloaded reports = comma-delimited). Returns rows keyed by lower-cased header.

function parseLine(line: string, delim: string): string[] {
  if (delim === "\t") return line.split("\t");
  const out: string[] = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQuotes) {
      if (c === '"') {
        if (line[i + 1] === '"') {
          cur += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        cur += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      out.push(cur);
      cur = "";
    } else {
      cur += c;
    }
  }
  out.push(cur);
  return out;
}

export function parseDelimited(text: string): Record<string, string>[] {
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length < 2) return [];
  const delim = lines[0].includes("\t") ? "\t" : ",";
  const headers = parseLine(lines[0], delim).map((h) => h.trim().toLowerCase());
  return lines.slice(1).map((line) => {
    const cells = parseLine(line, delim);
    const row: Record<string, string> = {};
    headers.forEach((h, i) => {
      row[h] = (cells[i] ?? "").trim();
    });
    return row;
  });
}

/** Strip $, %, thousands separators and parse a number; fall back to 0. */
export function num(row: Record<string, string>, aliases: string[]): number {
  for (const a of aliases) {
    const v = row[a];
    if (v != null && v !== "") {
      const n = Number(v.replace(/[$,%\s]/g, ""));
      if (!Number.isNaN(n)) return n;
    }
  }
  return 0;
}

/** First non-empty string value among aliases. */
export function str(row: Record<string, string>, aliases: string[]): string {
  for (const a of aliases) {
    if (row[a]) return row[a];
  }
  return "";
}

/** Build a CSV from rows and trigger a browser download. */
export function downloadCsv(filename: string, rows: Record<string, string | number | null>[]): void {
  if (rows.length === 0) return;
  const headers = Object.keys(rows[0]);
  const esc = (v: string | number | null) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [headers.join(","), ...rows.map((r) => headers.map((h) => esc(r[h])).join(","))].join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8;" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
