import { Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  ApiError,
  downloadHistoryExport,
  type ExportHistoryRow,
  type QueryHistoryDetail,
  type QueryHistorySummary,
} from "../api/client";
import { AnswerBadge, ResultCard, answerKind } from "../components/ResultCard";
import { buttonSecondary, errorText, input, label, muted, pageTitle, tableCell, tableHeader } from "../ui";

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

const kindOf = (entry: QueryHistorySummary) => answerKind(entry.route, entry.chat_only, entry.grounded);

/** What a question was asked against. A direct chat searched nothing, so it must
 * not read "whole corpus"; a chat with files attached searched just those. */
function scopeLabel(entry: QueryHistorySummary): string {
  if (entry.attached_filenames.length > 0) {
    const [first, ...rest] = entry.attached_filenames;
    return rest.length === 0 ? `Attached: ${first}` : `Attached: ${first} +${rest.length} more`;
  }
  if (entry.chat_only || entry.route === "chat") return "No documents · direct chat";
  const parts: string[] = [];
  if (entry.filter_folders.length > 0) parts.push(entry.filter_folders.join(", "));
  if (entry.filter_author) parts.push(`author: ${entry.filter_author}`);
  if (entry.filter_title) parts.push(`title: ${entry.filter_title}`);
  return parts.length > 0 ? parts.join(" · ") : "Whole corpus";
}

const TYPE_FILTERS: { value: string; text: string }[] = [
  { value: "", text: "All types" },
  { value: "keyword", text: "Keyword matches" },
  { value: "search", text: "AI answers" },
  { value: "report", text: "Full reports" },
  { value: "chat", text: "Direct chats" },
  { value: "edit", text: "File edits" },
  { value: "catalog", text: "Library lookups" },
];

export function History() {
  const [queries, setQueries] = useState<QueryHistorySummary[]>([]);
  const [exports, setExports] = useState<ExportHistoryRow[]>([]);
  const [open, setOpen] = useState<QueryHistoryDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [search, setSearch] = useState("");
  const [type, setType] = useState("");
  const openRef = useRef<HTMLDivElement>(null);

  function reportError(err: unknown) {
    setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
  }

  const refresh = () => {
    api
      .listQueryHistory()
      .then((r) => setQueries(r.queries))
      .catch(reportError)
      .finally(() => setLoaded(true));
    api.listExportHistory().then((r) => setExports(r.exports)).catch(reportError);
  };

  useEffect(refresh, []);

  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return queries.filter(
      (q) => (!type || kindOf(q) === type) && (!needle || q.question.toLowerCase().includes(needle)),
    );
  }, [queries, search, type]);

  const openEntry = async (id: number) => {
    setError(null);
    try {
      setOpen(await api.getQueryHistory(id));
      // the answer opens below the table: bring it into view
      requestAnimationFrame(() => openRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
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

  const filtering = search.trim() !== "" || type !== "";

  return (
    <div>
      <p className={label}>Activity</p>
      <h1 className={`mt-1 ${pageTitle}`}>History</h1>
      <p className={`mt-1 ${muted}`}>
        Every question asked and every file exported. Opening a past search restores its stored
        answer without re-running the model.
      </p>

      {error && <p className={`mt-4 ${errorText}`}>{error}</p>}

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <p className={label}>Searches</p>
        <input
          className={`${input} ml-2 w-60`}
          placeholder="Search your questions"
          aria-label="Search your questions"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className={input} value={type} onChange={(e) => setType(e.target.value)} aria-label="Filter by type">
          {TYPE_FILTERS.map((t) => (
            <option key={t.value} value={t.value}>
              {t.text}
            </option>
          ))}
        </select>
        <span className={`font-mono ml-auto text-[11px] ${muted}`}>
          {filtering ? `${shown.length} of ${queries.length}` : queries.length} {queries.length === 1 ? "search" : "searches"}
        </span>
      </div>

      <div className="mt-2 overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className={tableHeader}>Question</th>
              <th className={tableHeader}>Type</th>
              <th className={tableHeader}>Scope</th>
              <th className={tableHeader}>Sources</th>
              <th className={tableHeader}>When</th>
              <th className={tableHeader}></th>
            </tr>
          </thead>
          <tbody>
            {shown.map((entry) => (
              <tr key={entry.id} className={open?.id === entry.id ? "bg-[var(--index-soft)]" : "hover:bg-[var(--index-soft)]/40"}>
                <td className={tableCell}>
                  <button
                    className="max-w-xl cursor-pointer text-left hover:text-[var(--index)]"
                    onClick={() => openEntry(entry.id)}
                  >
                    {entry.question}
                  </button>
                </td>
                <td className={`${tableCell} whitespace-nowrap`}>
                  <AnswerBadge
                    route={entry.route}
                    chatOnly={entry.chat_only}
                    grounded={entry.grounded}
                    sourceCount={0}
                  />
                </td>
                <td className={`${tableCell} text-xs ${muted}`}>{scopeLabel(entry)}</td>
                <td className={`${tableCell} tabular-nums`}>
                  {entry.source_count > 0 ? entry.source_count : <span className={muted}>—</span>}
                </td>
                <td className={`font-mono ${tableCell} whitespace-nowrap text-[11px]`}>{formatWhen(entry.created_at)}</td>
                <td className={tableCell}>
                  <button
                    className={`${buttonSecondary} inline-flex items-center gap-1.5 hover:!border-[var(--danger)] hover:!text-[var(--danger)]`}
                    onClick={() => remove(entry.id)}
                    aria-label={`Delete “${entry.question}”`}
                  >
                    <Trash2 size={12} /> Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {loaded && queries.length === 0 && <p className={`mt-3 ${muted}`}>No searches yet.</p>}
      {queries.length > 0 && shown.length === 0 && <p className={`mt-3 ${muted}`}>No searches match.</p>}

      {open && (
        <div ref={openRef} className="mt-4 scroll-mt-16">
          <div className="flex items-center justify-between gap-3">
            <p className={label}>{open.question}</p>
            <button className={buttonSecondary} onClick={() => setOpen(null)}>
              Close
            </button>
          </div>
          <div className="mt-2 max-w-4xl">
            <ResultCard result={open} route={open.route} chatOnly={open.chat_only} />
          </div>
        </div>
      )}

      <p className={`mt-8 ${label}`}>Exports</p>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full border-collapse">
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
                <td className={`font-mono ${tableCell} whitespace-nowrap text-[11px]`}>{formatWhen(row.created_at)}</td>
                <td className={tableCell}>
                  <button className={buttonSecondary} onClick={() => download(row)}>
                    Download
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {exports.length === 0 && <p className={`mt-3 ${muted}`}>No exports yet.</p>}
    </div>
  );
}
