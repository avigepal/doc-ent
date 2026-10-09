import { AlertCircle, Check, CheckCircle2, Loader2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type IngestionProgress, type PipelineFile, type PipelineStage } from "../api/client";
import { formatElapsed, useNow } from "../time";
import { card, errorText, label, muted, pageTitle, tile } from "../ui";

const POLL_MS = 3000;
const TOAST_MS = 7000;

const STAGES: { key: PipelineStage | "ready"; label: string }[] = [
  { key: "queued", label: "Queued" },
  { key: "converting", label: "Converting" },
  { key: "indexing", label: "Indexing" },
  { key: "ready", label: "Ready" },
];

/** queued -> converting -> indexing -> ready, with the file's current stage lit
 * and the earlier ones ticked. A file in this list is never "ready" yet. */
function StageTrail({ stage }: { stage: PipelineStage }) {
  const current = STAGES.findIndex((s) => s.key === stage);
  return (
    <ol className="flex shrink-0 items-center gap-1 text-[11px]" aria-label={`Stage: ${stage}`}>
      {STAGES.map((s, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <li key={s.key} className="flex items-center gap-1">
            {i > 0 && <span className={`h-px w-3 ${done || active ? "bg-[var(--index)]" : "bg-[var(--line)]"}`} aria-hidden="true" />}
            <span
              className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 ${
                active
                  ? "border-[var(--index)] bg-[var(--index-soft)] font-medium text-[var(--index)]"
                  : done
                    ? "border-[var(--line)] text-[var(--ink)]"
                    : "border-[var(--line)] text-[var(--ink-soft)] opacity-60"
              }`}
            >
              {done && <Check size={10} aria-hidden="true" />}
              {active && stage !== "queued" && <Loader2 size={10} className="animate-spin" aria-hidden="true" />}
              {s.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function PipelineRow({ file, now, first }: { file: PipelineFile; now: number; first: boolean }) {
  // elapsed is measured from when the file entered this stage, ticking between polls
  const elapsedMs = file.since ? now - new Date(file.since).getTime() : file.elapsed_seconds * 1000;
  return (
    <div className={`flex flex-wrap items-center gap-x-4 gap-y-1.5 px-4 py-2.5 ${first ? "" : "border-t border-[var(--line)]"}`}>
      <span className="min-w-0 flex-1 basis-48 truncate" title={file.file}>
        {file.file}
      </span>
      <span className={`shrink-0 text-xs ${muted}`}>{file.folder}</span>
      <StageTrail stage={file.stage} />
      <span className={`w-14 shrink-0 text-right text-xs tabular-nums ${muted}`} title="Time in this stage">
        {formatElapsed(elapsedMs)}
      </span>
    </div>
  );
}

type Toast = { id: number; text: string };

function formatAgo(iso: string | null): string {
  if (!iso) return "";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

/** Stacked bar: fully processed, converted-but-not-summarized, failed. */
function ProgressBar({ total, summarized, converted, failed }: { total: number; summarized: number; converted: number; failed: number }) {
  if (total === 0) return <div className="h-2 rounded bg-[var(--line)]" />;
  const pct = (n: number) => `${(n / total) * 100}%`;
  const convertedOnly = Math.max(0, converted - summarized);
  return (
    <div className="flex h-2 overflow-hidden rounded bg-[var(--line)]">
      <div className="bg-[var(--index)] transition-all duration-500" style={{ width: pct(summarized) }} />
      <div className="bg-[var(--index)] opacity-40 transition-all duration-500" style={{ width: pct(convertedOnly) }} />
      <div className="bg-[var(--danger)] transition-all duration-500" style={{ width: pct(failed) }} />
    </div>
  );
}

export function Progress() {
  const [data, setData] = useState<IngestionProgress | null>(null);
  const [failedToLoad, setFailedToLoad] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  // Files already announced. The first poll only fills it, so opening the page
  // doesn't toast everything that finished a moment ago.
  const announced = useRef<Set<number> | null>(null);

  const dismissToast = (id: number) => setToasts((prev) => prev.filter((t) => t.id !== id));

  const announceReady = (r: IngestionProgress) => {
    const ready = r.pipeline?.recently_ready ?? [];
    if (announced.current === null) {
      announced.current = new Set(ready.map((f) => f.id));
      return;
    }
    const seen = announced.current;
    const fresh = ready.filter((f) => !seen.has(f.id));
    if (fresh.length === 0) return;
    fresh.forEach((f) => seen.add(f.id));
    const added = fresh.map((f) => ({ id: f.id, text: `${f.file} is ready` }));
    setToasts((prev) => [...prev, ...added].slice(-4));
    added.forEach((t) => setTimeout(() => dismissToast(t.id), TOAST_MS));
  };

  const pipeline = data?.pipeline;
  const now = useNow((pipeline?.files.length ?? 0) > 0);

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      api.ingestionProgress().then(
        (r) => {
          if (cancelled) return;
          setData(r);
          setFailedToLoad(false);
          announceReady(r);
        },
        // keep the last good snapshot on a transient failure instead of blanking the page
        () => {
          if (!cancelled) setFailedToLoad(true);
        },
      );
    };
    poll();
    const interval = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (!data) {
    return (
      <div>
        <h1 className={pageTitle}>Progress</h1>
        <p className={`mt-3 ${failedToLoad ? errorText : muted}`}>
          {failedToLoad ? "Couldn't reach the server." : "Loading…"}
        </p>
      </div>
    );
  }

  const { totals } = data;
  const busy = totals.running > 0;
  // With auto-summarize off, a file is "done" once converted (and indexed).
  const doneOf = (x: { converted: number; summarized: number }) =>
    data.summaries_enabled ? x.summarized : x.converted;
  const stats = [
    { name: "Files", value: totals.files },
    { name: "Converted", value: totals.converted },
    { name: "Summarized", value: totals.summarized },
    { name: "Running", value: totals.running },
    { name: "Failed", value: totals.failed },
  ];

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className={pageTitle}>Progress</h1>
        <span className={`flex items-center gap-1.5 text-xs ${failedToLoad ? "text-[var(--danger)]" : muted}`}>
          {failedToLoad ? (
            "Connection lost — showing last update"
          ) : busy ? (
            <>
              <Loader2 size={12} className="animate-spin text-[var(--locator)]" /> Running · live
            </>
          ) : (
            <>
              <CheckCircle2 size={12} className="text-[var(--signal)]" /> Idle · live
            </>
          )}
        </span>
      </div>

      <div className={`${card} mt-4`}>
        <div className="flex items-baseline justify-between">
          <p className={label}>Overall</p>
          <p className={`text-xs ${muted}`}>
            {doneOf(totals)} of {totals.files} {data.summaries_enabled ? "fully processed" : "converted and indexed"}
          </p>
        </div>
        <div className="mt-2">
          <ProgressBar
            total={totals.files}
            summarized={doneOf(totals)}
            converted={totals.converted}
            failed={totals.failed}
          />
        </div>
        <p className={`mt-2 text-[11px] ${muted}`}>
          {data.summaries_enabled
            ? "Solid = converted and summarized · faded = converted, awaiting summary · red = failed"
            : "Solid = converted and indexed (summaries off) · red = failed"}
        </p>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {stats.map((s) => (
          <div key={s.name} className={tile}>
            <p className={label}>{s.name}</p>
            <p className="font-display mt-1 text-xl font-semibold tabular-nums">{s.value}</p>
          </div>
        ))}
      </div>

      {pipeline && (
        <>
          <div className="mt-6 flex items-baseline justify-between">
            <p className={label}>In the pipeline</p>
            <p className={`text-xs ${muted}`}>
              {pipeline.files_total === 0
                ? "Nothing waiting"
                : `${pipeline.files_total} ${pipeline.files_total === 1 ? "file" : "files"} unfinished`}
            </p>
          </div>

          <div className="mt-2 grid grid-cols-3 gap-3">
            {(
              [
                { name: "Queued", hint: "waiting for a worker", value: pipeline.queue.queued },
                { name: "Converting", hint: "being read", value: pipeline.queue.converting },
                { name: "Indexing", hint: "being made searchable", value: pipeline.queue.indexing },
              ] as const
            ).map((s) => (
              <div key={s.name} className={tile}>
                <p className={label}>{s.name}</p>
                <p className="font-display mt-1 text-xl font-semibold tabular-nums">{s.value}</p>
                <p className={`mt-0.5 text-[11px] ${muted}`}>{s.hint}</p>
              </div>
            ))}
          </div>

          {pipeline.files.length > 0 && (
            <div className={`${card} mt-3 !p-0`}>
              {pipeline.files.map((f, i) => (
                <PipelineRow key={f.id} file={f} now={now} first={i === 0} />
              ))}
              {pipeline.files_total > pipeline.files.length && (
                <p className={`border-t border-[var(--line)] px-4 py-2 text-xs ${muted}`}>
                  + {pipeline.files_total - pipeline.files.length} more not shown
                </p>
              )}
            </div>
          )}
        </>
      )}

      <p className={`mt-6 ${label}`}>By folder</p>
      <div className={`${card} mt-2 space-y-4`}>
        {data.folders.length === 0 && <p className={muted}>No folders yet.</p>}
        {data.folders.map((f) => (
          <div key={f.name}>
            <div className="flex items-baseline justify-between text-xs">
              <span className="flex items-center gap-1.5 font-medium">
                {f.name}
                {f.running > 0 && <Loader2 size={11} className="animate-spin text-[var(--locator)]" />}
                {f.failed > 0 && <AlertCircle size={11} className="text-[var(--danger)]" />}
              </span>
              <span className={`tabular-nums ${muted}`}>
                {doneOf(f)}/{f.total} done{f.failed > 0 ? ` · ${f.failed} failed` : ""}
              </span>
            </div>
            <div className="mt-1.5">
              <ProgressBar total={f.total} summarized={doneOf(f)} converted={f.converted} failed={f.failed} />
            </div>
          </div>
        ))}
      </div>

      {data.failures.length > 0 && (
        <>
          <p className={`mt-6 ${label}`}>Failures</p>
          <div className={`${card} mt-2 !p-0`}>
            {data.failures.map((f, i) => (
              <div
                key={`${f.file}-${f.stage}`}
                className={`px-4 py-2.5 ${i > 0 ? "border-t border-[var(--line)]" : ""}`}
              >
                <div className="flex items-center gap-2">
                  <AlertCircle size={14} className="shrink-0 text-[var(--danger)]" />
                  <span className="min-w-0 flex-1 truncate" title={f.file}>
                    {f.file}
                  </span>
                  <span className={`shrink-0 text-xs ${muted}`}>
                    {f.folder} · {f.stage} · {f.retries} {f.retries === 1 ? "retry" : "retries"} · {formatAgo(f.at)}
                  </span>
                </div>
                {f.error && <p className={`mt-1 pl-6 ${errorText}`}>{f.error}</p>}
              </div>
            ))}
          </div>
        </>
      )}

      <p className={`mt-6 ${label}`}>Recently finished</p>
      <div className={`${card} mt-2 !p-0`}>
        {data.recent.length === 0 ? (
          <p className={`p-4 ${muted}`}>Nothing finished yet.</p>
        ) : (
          data.recent.map((r, i) => (
            <div
              key={`${r.file}-${r.stage}-${r.finished_at}`}
              className={`flex items-center gap-3 px-4 py-2 ${i > 0 ? "border-t border-[var(--line)]" : ""}`}
            >
              <CheckCircle2 size={14} className="shrink-0 text-[var(--signal)]" />
              <span className="min-w-0 flex-1 truncate" title={r.file}>
                {r.file}
              </span>
              <span className={`shrink-0 text-xs ${muted}`}>{r.folder}</span>
              <span className={`shrink-0 text-xs ${muted}`}>{r.stage}</span>
              <span className={`w-16 shrink-0 text-right text-xs tabular-nums ${muted}`}>{formatAgo(r.finished_at)}</span>
            </div>
          ))
        )}
      </div>

      {/* A file just became searchable. Announced politely for screen readers too. */}
      <div role="status" aria-live="polite" className="fixed right-4 bottom-4 z-30 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className="flex items-center gap-2 rounded border border-[var(--signal)] bg-[var(--paper)] px-3 py-2.5 shadow-lg"
          >
            <CheckCircle2 size={16} className="shrink-0 text-[var(--signal)]" aria-hidden="true" />
            <span className="min-w-0 flex-1 truncate" title={t.text}>
              {t.text}
            </span>
            <button
              type="button"
              onClick={() => dismissToast(t.id)}
              aria-label="Dismiss"
              className="shrink-0 cursor-pointer text-[var(--ink-soft)] hover:text-[var(--ink)]"
            >
              <X size={13} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
