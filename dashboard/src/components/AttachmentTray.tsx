import { AlertCircle, Check, ChevronDown, FileText, Loader2, Trash2, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { UploadStage } from "../api/client";
import { formatElapsed, useNow } from "../time";
import { muted } from "../ui";

/** A file attached to the current chat (the upload button). Questions in the
 * chat search only these; a new chat clears them. */
export type Attachment = {
  id: number;
  name: string;
  stage: UploadStage;
  detail: string | null;
  /** When the file was attached (ms since epoch), for the elapsed-time readout. */
  addedAt: number;
};

const PENDING_STAGES: UploadStage[] = ["queued", "converting", "indexing"];
export const isPending = (a: Attachment) => PENDING_STAGES.includes(a.stage);

export const STAGE_LABEL: Record<UploadStage, string> = {
  queued: "Queued",
  converting: "Converting",
  indexing: "Indexing",
  ready: "Ready",
  failed: "Failed",
  unsupported: "Unsupported",
};

function AttachmentChip({ attachment, onRemove }: { attachment: Attachment; onRemove: () => void }) {
  const pending = isPending(attachment);
  const bad = attachment.stage === "failed" || attachment.stage === "unsupported";
  const now = useNow(pending);
  return (
    <span
      title={bad ? `${attachment.name} — ${attachment.detail ?? STAGE_LABEL[attachment.stage]}` : attachment.name}
      className={`inline-flex max-w-[280px] items-center gap-1.5 rounded border px-2 py-1 text-xs ${
        bad ? "border-[var(--danger)] text-[var(--danger)]" : "border-[var(--line)] text-[var(--ink)]"
      }`}
    >
      <FileText size={13} className="shrink-0" />
      <span className="truncate">{attachment.name}</span>
      {pending && (
        <span className={`font-mono flex shrink-0 items-center gap-1 text-[10px] ${muted}`}>
          <Loader2 size={11} className="animate-spin" /> {STAGE_LABEL[attachment.stage]} ·{" "}
          {formatElapsed(now - attachment.addedAt)}
        </span>
      )}
      {attachment.stage === "ready" && <Check size={12} className="shrink-0 text-[var(--signal)]" />}
      {bad && <AlertCircle size={12} className="shrink-0" />}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${attachment.name}`}
        className="shrink-0 cursor-pointer text-[var(--ink-soft)] hover:text-[var(--ink)]"
      >
        <X size={12} />
      </button>
    </span>
  );
}

/** The attached files. A few show as chips; many collapse into one summary
 * line (count, progress, problems) with the full list behind a toggle, so 15
 * uploads don't push the composer up the screen. */
const INLINE_CHIP_LIMIT = 4;

export function AttachmentTray({
  attachments,
  onRemove,
  onClearAll,
  trailing,
}: {
  attachments: Attachment[];
  onRemove: (id: number) => void;
  onClearAll: () => void;
  trailing: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const total = attachments.length;
  const pending = attachments.filter(isPending).length;
  const bad = attachments.filter((a) => a.stage === "failed" || a.stage === "unsupported").length;
  const ready = attachments.filter((a) => a.stage === "ready").length;
  const collapsed = total > INLINE_CHIP_LIMIT;

  const clearAll = (
    <button
      type="button"
      onClick={onClearAll}
      className="inline-flex cursor-pointer items-center gap-1 rounded border border-[var(--line)] px-2 py-1 text-xs text-[var(--ink-soft)] transition-colors hover:border-[var(--danger)] hover:text-[var(--danger)]"
    >
      <Trash2 size={12} /> Clear all
    </button>
  );

  if (!collapsed) {
    return (
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        {attachments.map((a) => (
          <AttachmentChip key={a.id} attachment={a} onRemove={() => onRemove(a.id)} />
        ))}
        {clearAll}
        <span className={`text-[11px] ${muted}`}>Questions search only these files · a new chat clears them</span>
        <span className="ml-auto">{trailing}</span>
      </div>
    );
  }

  return (
    <div className="mb-2">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="inline-flex cursor-pointer items-center gap-2 rounded border border-[var(--line)] px-2.5 py-1 text-xs text-[var(--ink)] transition-colors hover:border-[var(--index)]"
        >
          <FileText size={13} className="shrink-0" />
          <span className="font-medium">{total} files</span>
          {pending > 0 ? (
            <span className={`font-mono flex items-center gap-1 text-[10px] ${muted}`}>
              <Loader2 size={11} className="animate-spin" /> {ready}/{total} ready
            </span>
          ) : (
            bad === 0 && <Check size={12} className="text-[var(--signal)]" />
          )}
          {bad > 0 && (
            <span className="flex items-center gap-1 text-[var(--danger)]">
              <AlertCircle size={12} /> {bad} failed
            </span>
          )}
          <ChevronDown size={13} className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
        {clearAll}
        <span className={`text-[11px] ${muted}`}>Questions search only these files</span>
        <span className="ml-auto">{trailing}</span>
      </div>
      {pending > 0 && (
        <div className="mt-1.5 h-0.5 overflow-hidden rounded bg-[var(--line)]">
          <div
            className="h-full bg-[var(--index)] transition-all"
            style={{ width: `${Math.round(((total - pending) / total) * 100)}%` }}
          />
        </div>
      )}
      {open && (
        <div className="mt-2 flex max-h-32 flex-wrap gap-1.5 overflow-y-auto pr-1">
          {attachments.map((a) => (
            <AttachmentChip key={a.id} attachment={a} onRemove={() => onRemove(a.id)} />
          ))}
        </div>
      )}
    </div>
  );
}
