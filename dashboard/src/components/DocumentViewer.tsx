import { Loader2, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { ApiError, api } from "../api/client";
import type { FileSection } from "../api/client";
import { label, muted } from "../ui";

/** What to open: a file (by id or path), optionally at a section, with words to highlight. */
export interface ViewTarget {
  fileId?: number;
  path?: string;
  chunk?: number;
  q?: string;
  phrase?: boolean;
}

const SPAN = 12;

const ViewerContext = createContext<{ open: (target: ViewTarget) => void } | null>(null);

/** Opens the viewer from anywhere (null outside the provider). */
export function useViewer() {
  return useContext(ViewerContext);
}

/** "#view?file=12&chunk=3&q=mac" -- the links search results carry. */
export function parseViewHref(href: string | undefined): ViewTarget | null {
  if (!href || !href.startsWith("#view?")) return null;
  const params = new URLSearchParams(href.slice("#view?".length));
  const file = params.get("file");
  const chunk = params.get("chunk");
  const path = params.get("path");
  if (!file && !path) return null;
  return {
    fileId: file ? Number(file) : undefined,
    path: path ?? undefined,
    chunk: chunk ? Number(chunk) : undefined,
    q: params.get("q") ?? undefined,
    phrase: params.get("phrase") === "1",
  };
}

const escapeRegex = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Matches the words the search matched, including their other endings
 * (compare / comparing), as one hit for a phrase. */
function highlightRegex(terms: string[], phrase: boolean): RegExp | null {
  if (terms.length === 0) return null;
  const word = (w: string) => `(?<![\\p{L}\\p{N}_])${escapeRegex(w.length < 5 ? w : w.slice(0, -1))}[\\p{L}\\p{N}_]*`;
  const source = phrase ? terms.map(word).join("[^\\p{L}\\p{N}_]+") : terms.map(word).join("|");
  return new RegExp(source, "giu");
}

function Highlighted({ text, pattern }: { text: string; pattern: RegExp | null }) {
  if (!pattern) return <>{text}</>;
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > last) parts.push(text.slice(last, start));
    parts.push(
      <mark key={start} className="rounded-sm bg-[var(--index)]/25 px-0.5 text-inherit">
        {match[0]}
      </mark>,
    );
    last = start + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

interface Loaded {
  name: string;
  path: string;
  total: number;
  anchor: number;
  terms: string[];
  phrase: boolean;
  sections: FileSection[];
  hasBefore: boolean;
  hasAfter: boolean;
}

function Viewer({ target, onClose }: { target: ViewTarget; onClose: () => void }) {
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState<"before" | "after" | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  // where to put the scroll position after the next render: the opening section, or unchanged when earlier sections are added above
  const scrollPlan = useRef<{ kind: "anchor" } | { kind: "keep"; height: number; top: number } | null>({ kind: "anchor" });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    scrollPlan.current = { kind: "anchor" };
    api
      .getFileSections({ fileId: target.fileId, path: target.path, around: target.chunk, span: SPAN, q: target.q, phrase: target.phrase })
      .then((r) => {
        if (cancelled) return;
        setData({
          name: r.file.name,
          path: r.file.path,
          total: r.total,
          anchor: r.anchor,
          terms: r.terms,
          phrase: r.phrase,
          sections: r.sections,
          hasBefore: r.has_before,
          hasAfter: r.has_after,
        });
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Couldn't open this document.");
      });
    return () => {
      cancelled = true;
    };
  }, [target]);

  useLayoutEffect(() => {
    const box = scroller.current;
    const plan = scrollPlan.current;
    if (!box || !data || !plan) return;
    if (plan.kind === "anchor") {
      box.querySelector('[data-anchor="true"]')?.scrollIntoView({ block: "center" });
    } else {
      box.scrollTop = plan.top + (box.scrollHeight - plan.height);
    }
    scrollPlan.current = null;
  }, [data]);

  const loadMore = async (direction: "before" | "after") => {
    if (!data || loadingMore) return;
    const box = scroller.current;
    const indexes = data.sections.map((s) => s.index);
    const around = direction === "before" ? Math.min(...indexes) - SPAN - 1 : Math.max(...indexes) + SPAN + 1;
    setLoadingMore(direction);
    try {
      const r = await api.getFileSections({ fileId: target.fileId, path: target.path, around, span: SPAN, q: target.q, phrase: target.phrase });
      if (direction === "before" && box) scrollPlan.current = { kind: "keep", height: box.scrollHeight, top: box.scrollTop };
      setData((prev) => {
        if (!prev) return prev;
        const byIndex = new Map(prev.sections.map((s) => [s.index, s]));
        for (const s of r.sections) byIndex.set(s.index, s);
        return {
          ...prev,
          sections: [...byIndex.values()].sort((a, b) => a.index - b.index),
          hasBefore: direction === "before" ? r.has_before : prev.hasBefore,
          hasAfter: direction === "after" ? r.has_after : prev.hasAfter,
        };
      });
    } catch {
      setError("Couldn't load more of this document.");
    } finally {
      setLoadingMore(null);
    }
  };

  const pattern = useMemo(() => (data ? highlightRegex(data.terms, data.phrase) : null), [data?.terms, data?.phrase]); // eslint-disable-line react-hooks/exhaustive-deps

  const moreButton = (direction: "before" | "after", text: string) => (
    <button
      type="button"
      onClick={() => loadMore(direction)}
      disabled={loadingMore !== null}
      className="font-mono mx-auto flex cursor-pointer items-center gap-1.5 rounded border border-[var(--line)] px-3 py-1 text-[11px] text-[var(--ink-soft)] transition-colors hover:border-[var(--index)] hover:text-[var(--index)] disabled:cursor-not-allowed disabled:opacity-50"
    >
      {loadingMore === direction && <Loader2 size={12} className="animate-spin" />}
      {text}
    </button>
  );

  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={data ? `Document: ${data.name}` : "Document"}
        onClick={(e) => e.stopPropagation()}
        className="flex h-full w-full max-w-xl flex-col border-l border-[var(--line)] bg-[var(--paper)] shadow-xl"
      >
        <header className="flex items-start justify-between gap-4 border-b border-[var(--line)] px-5 py-3">
          <div className="min-w-0">
            <p className={label}>Document</p>
            <h2 className="font-display truncate text-sm font-semibold tracking-tight" title={data?.path}>
              {data?.name ?? "Opening…"}
            </h2>
            {data && (
              <p className={`text-[11px] ${muted}`}>
                {data.total} section{data.total === 1 ? "" : "s"} · text as it was read for search
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            autoFocus
            className="cursor-pointer rounded p-0.5 text-[var(--ink-soft)] hover:text-[var(--ink)]"
          >
            <X size={16} />
          </button>
        </header>

        <div ref={scroller} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
          {error && <p className="font-mono text-xs text-[var(--danger)]">{error}</p>}
          {!data && !error && (
            <p className={`flex items-center gap-2 text-[13px] ${muted}`}>
              <Loader2 size={14} className="animate-spin" /> Opening the document…
            </p>
          )}
          {data && (
            <>
              {data.hasBefore && moreButton("before", "Load earlier sections")}
              {data.sections.map((section) => {
                const isAnchor = section.index === data.anchor;
                return (
                  <section
                    key={section.index}
                    data-anchor={isAnchor ? "true" : undefined}
                    className={`rounded border-l-2 px-3 py-2 ${
                      isAnchor ? "border-[var(--index)] bg-[var(--index-soft)]" : "border-transparent"
                    }`}
                  >
                    {section.heading && (
                      <p className="font-display mb-1 text-[13px] font-semibold">
                        <Highlighted text={section.heading} pattern={pattern} />
                      </p>
                    )}
                    <p className="text-[13px] leading-relaxed break-words whitespace-pre-wrap">
                      <Highlighted text={section.text} pattern={pattern} />
                    </p>
                  </section>
                );
              })}
              {data.hasAfter && moreButton("after", "Load later sections")}
            </>
          )}
        </div>
      </aside>
    </div>,
    document.body,
  );
}

export function ViewerProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<ViewTarget | null>(null);
  const open = useCallback((next: ViewTarget) => setTarget(next), []);
  const close = useCallback(() => setTarget(null), []);
  const value = useMemo(() => ({ open }), [open]);

  return (
    <ViewerContext.Provider value={value}>
      {children}
      {target && <Viewer target={target} onClose={close} />}
    </ViewerContext.Provider>
  );
}
