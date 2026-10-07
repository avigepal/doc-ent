import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  downloadHistoryExport,
  type ExportHistoryRow,
  type QueryHistoryDetail,
  type QueryHistorySummary,
} from "../api/client";
import { ResultCard } from "../components/ResultCard";
import { buttonSecondary, errorText, label, muted, pageTitle, tableCell, tableHeader } from "../ui";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = -1;
  do {
    value /= 1024;
    unitIndex++;
  } while (value >= 1024 && unitIndex < units.length - 1);
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}

function scopeLabel(entry: QueryHistorySummary): string {
  if (entry.attached_filenames.length > 0) return `${entry.attached_filenames.length} attached file(s)`;
  const parts: string[] = [];
  if (entry.filter_folders.length > 0) parts.push(entry.filter_folders.join(", "));
  if (entry.filter_author) parts.push(`author: ${entry.filter_author}`);
  if (entry.filter_title) parts.push(`title: ${entry.filter_title}`);
  return parts.length > 0 ? parts.join(" · ") : "whole corpus";
}

export function History() {
  const [queries, setQueries] = useState<QueryHistorySummary[]>([]);
  const [exports, setExports] = useState<ExportHistoryRow[]>([]);
  const [open, setOpen] = useState<QueryHistoryDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    api.listQueryHistory().then((r) => setQueries(r.queries)).catch(reportError);
    api.listExportHistory().then((r) => setExports(r.exports)).catch(reportError);
  };

  function reportError(err: unknown) {
    setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
  }

  useEffect(refresh, []);

  const openEntry = async (id: number) => {
    setError(null);
    try {
      setOpen(await api.getQueryHistory(id));
    } catch (err) {
      reportError(err);
    }
  };

  const remove = async (id: number) => {
    setError(null);
    try {
      await api.deleteQueryHistory(id);
      if (open?.id === id) setOpen(null);
      refresh();
    } catch (err) {
      reportError(err);
    }
  };

  const download = async (row: ExportHistoryRow) => {
    setError(null);
    try {
      await downloadHistoryExport(row);
    } catch (err) {
      reportError(err);
    }
  };

  return (
    <div>
      <p className={label}>Activity</p>
      <h1 className={`mt-1 ${pageTitle}`}>History</h1>
      <p className={`mt-1 ${muted}`}>
        Every question asked and every file exported. Opening a past search restores its stored
        answer without re-running the model.
      </p>

      {error && <p className={`mt-4 ${errorText}`}>{error}</p>}

      <p className={`mt-6 ${label}`}>Searches</p>
      <table className="mt-2 w-full border-collapse">
        <thead>
          <tr>
            <th className={tableHeader}>Question</th>
            <th className={tableHeader}>Scope</th>
            <th className={tableHeader}>Sources</th>
            <th className={tableHeader}>When</th>
            <th className={tableHeader}></th>
          </tr>
        </thead>
        <tbody>
          {queries.map((entry) => (
            <tr key={entry.id}>
              <td className={tableCell}>
                <button className="text-left hover:text-[var(--index)]" onClick={() => openEntry(entry.id)}>
                  {entry.question}
                </button>
              </td>
              <td className={`${tableCell} text-[11px] ${muted}`}>{scopeLabel(entry)}</td>
              <td className={`${tableCell} tabular-nums`}>{entry.source_count}</td>
              <td className={`font-mono ${tableCell} text-[11px]`}>{formatWhen(entry.created_at)}</td>
              <td className={tableCell}>
                <button className={buttonSecondary} onClick={() => remove(entry.id)}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {queries.length === 0 && <p className={`mt-3 ${muted}`}>No searches yet.</p>}

      {open && (
        <div className="mt-4">
          <div className="flex items-center justify-between">
            <p className={label}>{open.question}</p>
            <button className={buttonSecondary} onClick={() => setOpen(null)}>
              Close
            </button>
          </div>
          <div className="mt-2">
            <ResultCard result={open} />
          </div>
        </div>
      )}

      <p className={`mt-8 ${label}`}>Exports</p>
      <table className="mt-2 w-full border-collapse">
        <thead>
          <tr>
            <th className={tableHeader}>File</th>
            <th className={tableHeader}>Format</th>
            <th className={tableHeader}>Size</th>
            <th className={tableHeader}>When</th>
            <th className={tableHeader}></th>
          </tr>
        </thead>
        <tbody>
          {exports.map((row) => (
            <tr key={row.id}>
              <td className={`font-mono ${tableCell}`}>{row.filename}</td>
              <td className={`font-mono ${tableCell} text-[11px] uppercase`}>{row.fmt}</td>
              <td className={`${tableCell} tabular-nums`}>{formatBytes(row.size_bytes)}</td>
              <td className={`font-mono ${tableCell} text-[11px]`}>{formatWhen(row.created_at)}</td>
              <td className={tableCell}>
                <button className={buttonSecondary} onClick={() => download(row)}>
                  Download
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {exports.length === 0 && <p className={`mt-3 ${muted}`}>No exports yet.</p>}
    </div>
  );
}
