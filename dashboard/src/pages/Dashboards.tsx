import { useCallback, useEffect, useState } from "react";
import { api, type CorrelateResult, type SavedView } from "../api/client";
import { CorrelationChart } from "../components/CorrelationChart";
import { Markdown } from "../components/Markdown";
import { button, buttonSecondary, card, errorText, input, label, muted, pageTitle } from "../ui";

// Standing correlation questions. Each view keeps its latest finished run, so the page shows answers
// without running anything; "Run now" starts a background run and the page refreshes when it finishes.

const SCHEDULES: { hours: number | null; text: string }[] = [
  { hours: null, text: "Only when I run it" },
  { hours: 24, text: "Every day" },
  { hours: 24 * 7, text: "Every week" },
];

const POLL_MS = 2000;

function formatWhen(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "never run";
}

function scheduleText(hours: number | null): string {
  return SCHEDULES.find((s) => s.hours === hours)?.text ?? `Every ${hours} hours`;
}

export function Dashboards() {
  const [views, setViews] = useState<SavedView[] | null>(null);
  const [folders, setFolders] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState<Record<number, string>>({});

  const load = useCallback(async () => {
    try {
      const data = await api.listViews();
      setViews(data.views);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load views");
    }
  }, []);

  useEffect(() => {
    void load();
    api.listFolders().then((d) => setFolders(d.folders.map((f) => f.name))).catch(() => setFolders([]));
  }, [load]);

  // Poll every running job until it finishes, then refresh the list so the new result shows.
  useEffect(() => {
    const ids = Object.entries(running);
    if (ids.length === 0) return;
    const timer = window.setInterval(async () => {
      for (const [viewId, jobId] of ids) {
        try {
          const job = await api.correlateJob(jobId);
          if (job.state === "SUCCESS" || job.state === "FAILURE") {
            setRunning((prev) => {
              const next = { ...prev };
              delete next[Number(viewId)];
              return next;
            });
            if (job.state === "FAILURE") setError(job.error ?? "A view run failed");
            void load();
          }
        } catch {
          // a transient poll failure is retried on the next tick
        }
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, load]);

  async function runNow(view: SavedView) {
    setError(null);
    try {
      const started = await api.runView(view.id);
      setRunning((prev) => ({ ...prev, [view.id]: started.job_id }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the run");
    }
  }

  async function remove(view: SavedView) {
    if (!window.confirm(`Delete the view "${view.name}"?`)) return;
    try {
      await api.deleteView(view.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the view");
    }
  }

  return (
    <div className="mx-auto max-w-4xl px-4 pb-16 pt-6">
      <div className="flex items-baseline justify-between gap-4">
        <h1 className={pageTitle}>Dashboards</h1>
        <p className={`${muted} text-[13px]`}>Saved correlation questions, shared with everyone who can sign in.</p>
      </div>

      <NewViewForm folders={folders} onCreated={load} onError={setError} />

      {error && <p className={`${errorText} mt-4`}>{error}</p>}

      {views === null && <p className={`${muted} mt-6 text-[14px]`}>Loading…</p>}
      {views !== null && views.length === 0 && (
        <p className={`${muted} mt-6 text-[14px]`}>No views yet. Create one above, for example "how do the vendor and budget files relate?".</p>
      )}

      <div className="mt-6 flex flex-col gap-5">
        {views?.map((view) => (
          <ViewCard
            key={view.id}
            view={view}
            busy={view.id in running}
            onRun={() => void runNow(view)}
            onDelete={() => void remove(view)}
          />
        ))}
      </div>
    </div>
  );
}

function NewViewForm({
  folders,
  onCreated,
  onError,
}: {
  folders: string[];
  onCreated: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const [name, setName] = useState("");
  const [question, setQuestion] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [hours, setHours] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !question.trim()) return;
    setSaving(true);
    try {
      await api.createView({ name, question, folders: chosen, interval_hours: hours });
      setName("");
      setQuestion("");
      setChosen([]);
      setHours(null);
      await onCreated();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not save the view");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className={`${card} mt-6 flex flex-col gap-3 p-4`}>
      <p className={label}>New view</p>
      <input className={input} placeholder="Name, e.g. Vendor and budget links" value={name} onChange={(e) => setName(e.target.value)} />
      <textarea
        className={`${input} min-h-[64px]`}
        placeholder="The question to answer, e.g. How do the vendor shortlist and the budget numbers relate?"
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
      />
      <div className="flex flex-wrap gap-2">
        {folders.map((f) => {
          const on = chosen.includes(f);
          return (
            <button
              key={f}
              type="button"
              onClick={() => setChosen(on ? chosen.filter((x) => x !== f) : [...chosen, f])}
              className={on ? button : buttonSecondary}
            >
              {f}
            </button>
          );
        })}
        {folders.length === 0 && <span className={`${muted} text-[13px]`}>No folders yet. The view reads every document.</span>}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <label className={`${muted} text-[13px]`}>
          Runs{" "}
          <select
            className={input}
            value={hours === null ? "" : String(hours)}
            onChange={(e) => setHours(e.target.value === "" ? null : Number(e.target.value))}
          >
            {SCHEDULES.map((s) => (
              <option key={s.text} value={s.hours === null ? "" : String(s.hours)}>
                {s.text}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={saving || !name.trim() || !question.trim()} className={button}>
          {saving ? "Saving…" : "Save view"}
        </button>
      </div>
    </form>
  );
}

function ViewCard({
  view,
  busy,
  onRun,
  onDelete,
}: {
  view: SavedView;
  busy: boolean;
  onRun: () => void;
  onDelete: () => void;
}) {
  const result: CorrelateResult | null = view.last_result;
  return (
    <section className={`${card} p-5`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[16px] font-semibold">{view.name}</h2>
          <p className={`${muted} mt-1 text-[14px]`}>{view.question}</p>
          <p className={`${muted} mt-1 font-mono text-[11px]`}>
            {view.folders.length ? `Folders: ${view.folders.join(", ")}` : "All folders"} · {scheduleText(view.interval_hours)} · last run{" "}
            {formatWhen(view.last_run_at)}
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={onRun} disabled={busy} className={button}>
            {busy ? "Running…" : "Run now"}
          </button>
          <button type="button" onClick={onDelete} className={buttonSecondary}>
            Delete
          </button>
        </div>
      </div>

      {!result && !busy && <p className={`${muted} mt-4 text-[14px]`}>Not run yet. Press Run now.</p>}

      {result && (
        <div className="mt-4 flex flex-col gap-4 border-t border-[var(--line)] pt-4">
          {result.cross_doc && (
            <div>
              <p className={label}>Cross-document findings</p>
              <div className="mt-2">
                <Markdown>{result.cross_doc.answer}</Markdown>
              </div>
              {result.cross_doc.sources.length > 0 && (
                <p className={`${muted} mt-2 font-mono text-[11px]`}>
                  Cites {result.cross_doc.sources.length} document(s): {result.cross_doc.sources.map((s) => s.split("/").pop()).join(", ")}
                </p>
              )}
            </div>
          )}
          {result.statistical && (
            <div>
              <p className={label}>Statistical findings</p>
              <div className="mt-2">
                <Markdown>{result.statistical.answer}</Markdown>
              </div>
              {result.statistical.pairs && result.statistical.pairs.length > 0 && (
                <div className="mt-3">
                  <CorrelationChart pairs={result.statistical.pairs} />
                </div>
              )}
            </div>
          )}
          {!result.cross_doc && !result.statistical && (
            <p className={`${muted} text-[14px]`}>The last run found no notable relationships or numeric links.</p>
          )}
        </div>
      )}
    </section>
  );
}
