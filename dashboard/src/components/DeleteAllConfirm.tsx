import { Loader2, Trash2 } from "lucide-react";
import { useState } from "react";
import { buttonSecondary } from "../ui";

/** The "are you sure" for Delete all. It says how many files and what goes, and when the
 * whole library is about to be emptied (no filter) it asks for the word DELETE first. */
export function DeleteAllConfirm({
  count,
  scope,
  requireTyping,
  busy,
  onConfirm,
  onCancel,
}: {
  count: number;
  /** What the filters match, in words: `in “pgt”`, `that are Failed`, or `` for everything. */
  scope: string;
  requireTyping: boolean;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const [typed, setTyped] = useState("");
  const ready = !requireTyping || typed.trim() === "DELETE";

  return (
    <div role="alertdialog" aria-label="Confirm delete all" className="mt-3 rounded border border-[var(--danger)] px-3 py-3">
      <p className="text-[var(--danger)]">
        <span className="font-medium">
          Delete all {count.toLocaleString()} {count === 1 ? "file" : "files"}
          {scope ? ` ${scope}` : " in the library"}?
        </span>{" "}
        This removes them from search <span className="font-medium">and deletes the original files from disk</span>, along with their converted text and summaries. This can’t be undone.
        Chat attachments in “uploads” are not touched.
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {requireTyping && (
          <input
            autoFocus
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder="Type DELETE to confirm"
            aria-label="Type DELETE to confirm"
            className="rounded border border-[var(--line)] bg-transparent px-2 py-1 text-xs"
          />
        )}
        <button
          type="button"
          onClick={onConfirm}
          disabled={busy || !ready}
          className="inline-flex cursor-pointer items-center gap-1.5 rounded bg-[var(--danger)] px-3 py-1 text-xs font-medium text-[var(--paper)] hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />} Delete all {count.toLocaleString()}
        </button>
        <button type="button" onClick={onCancel} disabled={busy} className={buttonSecondary}>
          Cancel
        </button>
      </div>
    </div>
  );
}
