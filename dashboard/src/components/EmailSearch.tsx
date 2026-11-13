import { ChevronLeft, ChevronRight, Loader2, Mail } from "lucide-react";
import { useEffect, useState, type ChangeEvent } from "react";
import { api, ApiError, type EmailMessage } from "../api/client";
import { useViewer } from "./DocumentViewer";
import { buttonSecondary, card, errorText, input, label, muted } from "../ui";

const FILTER_DELAY_MS = 300;
const PAGE_SIZE = 50;

interface Filters {
  sender: string;
  recipient: string;
  after: string;
  before: string;
}

const EMPTY: Filters = { sender: "", recipient: "", after: "", before: "" };

function formatSent(iso: string | null): string {
  if (!iso) return "no date";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** The Emails view on the Documents page: find messages by who sent them, who received them,
 * and when. Rows come from the stored email records, so this needs no model. Clicking a row
 * opens the email file in the document viewer. */
export function EmailSearch({ folder }: { folder: string }) {
  const viewer = useViewer();
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [page, setPage] = useState(0);
  const [messages, setMessages] = useState<EmailMessage[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // a new filter starts again from the first page
  useEffect(() => {
    setPage(0);
  }, [filters, folder]);

  // wait a moment after the last keystroke before asking the server
  useEffect(() => {
    let cancelled = false;
    const handle = setTimeout(() => {
      setLoading(true);
      api
        .listEmails({ ...filters, folder, offset: page * PAGE_SIZE, limit: PAGE_SIZE })
        .then((r) => {
          if (cancelled) return;
          setMessages(r.messages);
          setTotal(r.total);
          setError(null);
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof ApiError ? err.message : "Couldn't search the emails.");
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, FILTER_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [filters, folder, page]);

  const set = (key: keyof Filters) => (e: ChangeEvent<HTMLInputElement>) =>
    setFilters((prev) => ({ ...prev, [key]: e.target.value }));

  const hasFilter = Object.values(filters).some((v) => v !== "");

  return (
    <div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <label className="flex flex-col gap-1">
          <span className={label}>From (sender)</span>
          <input className={input} placeholder="name or address" value={filters.sender} onChange={set("sender")} />
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>To (recipient, To or Cc)</span>
          <input className={input} placeholder="name or address" value={filters.recipient} onChange={set("recipient")} />
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>Sent on or after</span>
          <input className={input} type="date" value={filters.after} onChange={set("after")} />
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>Sent on or before</span>
          <input className={input} type="date" value={filters.before} onChange={set("before")} />
        </label>
      </div>

      <div className="mt-2 flex items-center justify-between text-xs">
        <span className={muted}>
          {folder ? `Emails in “${folder}” and its subfolders` : "Emails in all folders"}
          {loading && <Loader2 size={11} className="ml-2 inline animate-spin" />}
        </span>
        {hasFilter && (
          <button type="button" onClick={() => setFilters(EMPTY)} className={`${buttonSecondary} !py-0.5`}>
            Clear filters
          </button>
        )}
      </div>

      {error && <p className={`mt-3 ${errorText}`}>{error}</p>}

      {!error && !loading && messages.length === 0 && (
        <p className={`mt-4 ${muted}`}>{hasFilter ? "No emails match these filters." : "No emails found yet."}</p>
      )}

      {messages.length > 0 && (
        <div className={`${card} mt-3 !p-0`}>
          {messages.map((m, i) => (
            <button
              key={`${m.file_id}-${i}-${m.sent_at ?? ""}`}
              type="button"
              onClick={() => viewer?.open({ fileId: m.file_id })}
              className={`flex w-full cursor-pointer items-start gap-3 px-4 py-2.5 text-left hover:bg-[var(--index-soft)]/40 ${
                i > 0 ? "border-t border-[var(--line)]" : ""
              }`}
            >
              <Mail size={14} className={`mt-0.5 shrink-0 ${muted}`} aria-hidden="true" />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{m.subject || "(no subject)"}</span>
                <span className={`block truncate text-xs ${muted}`}>
                  {m.sender.name || m.sender.addr || "unknown sender"} → {m.recipients.join(", ") || "no recipients"}
                </span>
              </span>
              <span className="shrink-0 text-right text-xs">
                <span className={`block tabular-nums ${muted}`}>{formatSent(m.sent_at)}</span>
                <span className={`block truncate ${muted}`} title={m.folder ? `${m.folder}/${m.file}` : m.file}>
                  {m.file}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}

      {total > PAGE_SIZE && (
        <div className="mt-3 flex items-center justify-between gap-3 text-xs">
          <span className={`tabular-nums ${muted}`}>
            {(page * PAGE_SIZE + 1).toLocaleString()}–{Math.min(total, (page + 1) * PAGE_SIZE).toLocaleString()} of{" "}
            {total.toLocaleString()}
          </span>
          <div className="flex gap-1.5">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0 || loading}
              aria-label="Previous page"
              className={`${buttonSecondary} inline-flex items-center !px-2 disabled:cursor-not-allowed disabled:opacity-40`}
            >
              <ChevronLeft size={13} />
            </button>
            <button
              type="button"
              onClick={() => setPage((p) => p + 1)}
              disabled={(page + 1) * PAGE_SIZE >= total || loading}
              aria-label="Next page"
              className={`${buttonSecondary} inline-flex items-center !px-2 disabled:cursor-not-allowed disabled:opacity-40`}
            >
              <ChevronRight size={13} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
