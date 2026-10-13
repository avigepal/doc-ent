import { Loader2, Play, Square } from "lucide-react";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import { buttonSecondary, card, errorText, muted } from "../ui";

/** Stop / Resume for the whole ingestion pipeline. Stop empties the queues and
 * pauses the timer (files being converted right now finish); Resume queues what
 * is left again. Nothing is deleted either way. */
export function PipelineControl({
  paused,
  canStop,
  onChanged,
}: {
  paused: boolean;
  /** There is something to stop: files are running or waiting. */
  canStop: boolean;
  onChanged: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      setConfirming(false);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (paused) {
    return (
      <div className={`${card} mt-4 flex flex-wrap items-center gap-3 !border-[var(--locator)] !bg-[var(--locator-soft)]`}>
        <Square size={14} className="shrink-0 text-[var(--locator)]" aria-hidden="true" />
        <p className="min-w-0 flex-1 text-sm">
          <span className="font-medium">Processing is stopped.</span>{" "}
          <span className={muted}>New files in the folders are not picked up until you resume. Uploads from Ask and Documents still work.</span>
        </p>
        <button
          type="button"
          onClick={() => run(api.resumePipeline)}
          disabled={busy}
          className={`${buttonSecondary} inline-flex items-center gap-1.5`}
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />} Resume
        </button>
        {error && <p className={`w-full ${errorText}`}>{error}</p>}
      </div>
    );
  }

  if (!canStop) return null;

  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {confirming ? (
        <>
          <span className={`text-xs ${muted}`}>Empty the queues and pause? Running files finish; nothing is deleted.</span>
          <button
            type="button"
            onClick={() => run(api.stopPipeline)}
            disabled={busy}
            className={`${buttonSecondary} inline-flex items-center gap-1.5 !border-[var(--danger)] !text-[var(--danger)]`}
          >
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Square size={12} />} Stop
          </button>
          <button type="button" onClick={() => setConfirming(false)} disabled={busy} className={buttonSecondary}>
            Cancel
          </button>
        </>
      ) : (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className={`${buttonSecondary} inline-flex items-center gap-1.5 hover:!border-[var(--danger)] hover:!text-[var(--danger)]`}
        >
          <Square size={12} /> Stop
        </button>
      )}
      {error && <p className={`w-full text-right ${errorText}`}>{error}</p>}
    </div>
  );
}
