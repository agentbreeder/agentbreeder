/**
 * Utilities for exporting list data as JSON or CSV files.
 */

/** Trigger a browser download of the given content. */
function download(content: string, filename: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/** Export data as a pretty-printed JSON file. */
export function exportAsJson<T>(data: T[], filename: string) {
  const json = JSON.stringify(data, null, 2);
  download(json, `${filename}.json`, "application/json");
}

/** Export data as a CSV file. Handles quoting/escaping. */
export function exportAsCsv<T extends Record<string, unknown>>(
  data: T[],
  filename: string
) {
  if (data.length === 0) return;

  const headers = Object.keys(data[0]);

  const escapeCell = (value: unknown): string => {
    if (value === null || value === undefined) return "";
    const str = typeof value === "object" ? JSON.stringify(value) : String(value);
    // Quote if contains comma, newline, or double quote
    if (str.includes(",") || str.includes("\n") || str.includes('"')) {
      return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
  };

  const rows = [
    headers.join(","),
    ...data.map((row) => headers.map((h) => escapeCell(row[h])).join(",")),
  ];

  download(rows.join("\n"), `${filename}.csv`, "text/csv");
}
