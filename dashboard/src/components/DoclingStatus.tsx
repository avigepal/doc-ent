import type { IngestionProgress } from "../api/client";
import { muted } from "../ui";

/** Whether the Docling server is answering. A dead one otherwise shows up only as failed files. */
export function DoclingStatus({ docling }: { docling: IngestionProgress["docling"] }) {
  const checking = docling.online === null;
  const online = docling.online === true;

  const tone = checking ? muted : online ? "text-[var(--signal)]" : "text-[var(--danger)]";
  const dot = checking ? "bg-[var(--ink-soft)]" : online ? "bg-[var(--signal)]" : "bg-[var(--danger)]";
  const text = checking
    ? "Docling: checking…"
    : online
      ? `Docling online${docling.latency_ms !== null ? ` · ${docling.latency_ms} ms` : ""}`
      : `Docling offline${docling.error ? ` — ${docling.error}` : ""}`;

  return (
    <span
      role="status"
      title={`${docling.url}${docling.checked_at ? ` · checked ${new Date(docling.checked_at).toLocaleTimeString()}` : ""}`}
      className={`inline-flex items-center gap-1.5 text-xs ${tone}`}
    >
      <span className={`h-2 w-2 shrink-0 rounded-full ${dot}`} aria-hidden="true" />
      {text}
    </span>
  );
}
