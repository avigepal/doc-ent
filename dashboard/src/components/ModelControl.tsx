import { useCallback, useEffect, useRef, useState } from "react";
import { Power } from "lucide-react";
import { api, type TextModel } from "../api/client";
import { card, errorText, muted } from "../ui";

// Chat models on the model server, shown in the header. The header button shows the selected model
// and a green dot when it is loaded (red when not). Opening it lists the chat models; each row has a
// power button, green when that model is loaded and red when it is not. Clicking it loads or unloads.
// Embedding and reranker models never show up here; the API leaves them out.

const POLL_MS = 20000;

// Display names only: the model ids are what the server and the API use.
function displayName(id: string): string {
  if (id.startsWith("Qwen3.6")) return "Qwen3.6 (faster)";
  if (id.startsWith("Qwen3.8")) return "Qwen3.8 (slower, more consistent)";
  return id;
}

export function ModelControl() {
  const [models, setModels] = useState<TextModel[] | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const { models: list } = await api.listTextModels();
      setModels(list);
      setError(null);
      setSelected((current) => (list.some((m) => m.id === current) ? current : (list[0]?.id ?? "")));
    } catch (err) {
      setModels([]);
      setError(err instanceof Error ? err.message : "Could not reach the model server");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  // Close the panel on a click outside it or on Escape.
  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The model server refused the request");
    } finally {
      setBusy(false);
      await refresh();
    }
  }

  if (models === null) {
    return <span className={`font-mono self-center px-2 text-[11px] ${muted}`}>Models…</span>;
  }

  if (models.length === 0) {
    return (
      <span className={`font-mono self-center px-2 text-[11px] ${errorText}`} title={error ?? undefined}>
        AI server offline
      </span>
    );
  }

  // The header shows the model that is loaded, not the one picked in the list.
  const loaded = models.find((m) => m.loaded);
  const dot = loaded ? "bg-[var(--signal)]" : "bg-[var(--danger)]";
  const headerText = loaded ? displayName(loaded.id) : "No model loaded";

  return (
    <div ref={wrapRef} className="relative flex shrink-0 items-center px-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={headerText}
        className="font-mono flex max-w-[180px] cursor-pointer items-center gap-1.5 rounded-md border border-[var(--line)] px-2 py-1 text-[11px] text-[var(--ink-soft)] hover:bg-[var(--paper)] hover:text-[var(--ink)]"
      >
        <span className={`h-2 w-2 shrink-0 rounded-full ${dot}`} aria-hidden="true" />
        <span className="truncate">{headerText}</span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Chat models"
          className={`${card} fixed right-3 top-14 z-30 flex w-[min(92vw,340px)] flex-col gap-1 p-2`}
        >
          <p className="font-mono px-2 pt-1 text-[11px] uppercase tracking-wider text-[var(--ink-soft)]">AI model</p>
          {models.map((m) => {
            const current = m.id === selected;
            return (
              <div
                key={m.id}
                className={`flex items-center justify-between gap-3 rounded-md px-2 py-1.5 ${current ? "bg-[var(--paper)]" : ""}`}
              >
                <button
                  type="button"
                  onClick={() => setSelected(m.id)}
                  className={`min-w-0 flex-1 truncate text-left text-[13px] ${current ? "font-medium text-[var(--index)]" : "text-[var(--ink)]"}`}
                >
                  {displayName(m.id)}
                </button>
                <span className={`font-mono shrink-0 text-[11px] ${muted}`}>{m.loaded ? "Loaded" : "Unloaded"}</span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => run(() => (m.loaded ? api.unloadTextModel(m.id) : api.loadTextModel(m.id)))}
                  aria-label={m.loaded ? `Unload ${displayName(m.id)}` : `Load ${displayName(m.id)}`}
                  title={m.loaded ? "Loaded · click to unload" : "Unloaded · click to load (unloads the other chat model first)"}
                  className={`flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md border border-[var(--line)] hover:bg-[var(--paper)] disabled:cursor-not-allowed disabled:opacity-50 ${
                    m.loaded ? "text-[var(--signal)]" : "text-[var(--danger)]"
                  }`}
                >
                  <Power size={14} />
                </button>
              </div>
            );
          })}
          {error && <span className={`${errorText} px-2 pb-1`}>{error}</span>}
        </div>
      )}
    </div>
  );
}
