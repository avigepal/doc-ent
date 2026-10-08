import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type IngestionProgress } from "../api/client";
import { card, errorText, label, muted, pageTitle, tile } from "../ui";

const POLL_MS = 3000;

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

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      api.ingestionProgress().then(
        (r) => {
          if (cancelled) return;
          setData(r);
          setFailedToLoad(false);
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
    </div>
  );
}
