import { Download, FileText, FileEdit, Library, MessageSquare, ScrollText, Search, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { buttonSecondary, card, label, muted } from "../ui";
import type { GeneratedFile } from "../api/client";
import { useViewer } from "./DocumentViewer";
import { Markdown } from "./Markdown";

export interface ResultCardData {
  question: string;
  answer: string;
  sources: string[];
  grounded: boolean;
  cross_doc: { answer: string; sources: string[] } | null;
  statistical: { answer: string; correlation_summary: string } | null;
  files?: GeneratedFile[];
}

/** Small badge saying how a reply was made, so a keyword lookup (matched in the
 * index, no model involved) is never mistaken for a generated answer. `route`
 * is what the backend decided: "keyword", "search", "chat", "edit", "catalog";
 * empty for older history rows, where it is inferred from the result. */
export function answerKind(route: string | undefined, chatOnly: boolean, grounded: boolean): string {
  return route || (chatOnly ? "chat" : grounded ? "search" : "");
}

export function AnswerBadge({
  route,
  chatOnly,
  grounded,
  sourceCount,
}: {
  route?: string;
  chatOnly: boolean;
  grounded: boolean;
  sourceCount: number;
}) {
  const kind = answerKind(route, chatOnly, grounded);
  const sources = sourceCount > 0 ? ` · ${sourceCount} ${sourceCount === 1 ? "source" : "sources"}` : "";
  let text: string;
  let title: string;
  let Icon = Sparkles;
  let tone = "border-[var(--index)] bg-[var(--index-soft)] text-[var(--index)]";
  switch (kind) {
    case "keyword":
      text = `Keyword matches${sourceCount > 0 ? ` · ${sourceCount} ${sourceCount === 1 ? "file" : "files"}` : ""}`;
      title = "Exact matches found in your documents' index. No AI model was used.";
      Icon = Search;
      tone = "border-[var(--locator)] bg-[var(--locator-soft)] text-[var(--locator)]";
      break;
    case "search":
      text = `AI answer${sources}`;
      title = "Written by the AI model from the passages found in your documents.";
      break;
    case "report":
      text = `Full report${sources}`;
      title = "Written by the AI model after reading every passage of the selected documents.";
      Icon = ScrollText;
      break;
    case "chat":
      text = "AI chat · no documents searched";
      title = "Answered by the AI model on its own; your documents were not searched.";
      Icon = MessageSquare;
      break;
    case "edit":
      text = "File edit";
      title = "The AI model edited an attached file.";
      Icon = FileEdit;
      break;
    case "catalog":
      text = "From your library";
      title = "Counted and listed from your document library. No AI model was used.";
      Icon = Library;
      tone = "border-[var(--line)] text-[var(--ink-soft)]";
      break;
    default:
      return null;
  }
  return (
    <span
      title={title}
      className={`font-mono inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${tone}`}
    >
      <Icon size={11} className="shrink-0" /> {text}
    </span>
  );
}

/** Strips everything up to and including "/raw/" so citations show a
 * readable "contracts/q2_update.txt" instead of the full container path. */
export function shortenSource(path: string): string {
  const marker = "/raw/";
  const idx = path.indexOf(marker);
  return idx === -1 ? path : path.slice(idx + marker.length);
}

function SourceList({ sources, onOpen }: { sources: string[]; onOpen?: (source: string) => void }) {
  return (
    <ol className="mt-2 space-y-1.5">
      {sources.map((source, i) => {
        const text = (
          <>
            <span className="block break-words text-[14px] font-medium text-[var(--ink)]">
              {shortenSource(source).split("/").pop()}
            </span>
            <span className={`font-mono block break-all text-[11px] ${muted}`}>{shortenSource(source)}</span>
          </>
        );
        return (
          <li key={source} className="flex items-baseline gap-2.5">
            <span className={`font-mono text-[11px] tabular-nums ${muted}`}>[{i + 1}]</span>
            {onOpen ? (
              <button
                type="button"
                onClick={() => onOpen(source)}
                title="Open this document"
                className="min-w-0 cursor-pointer text-left hover:[&_span:first-child]:text-[var(--index)]"
              >
                {text}
              </button>
            ) : (
              <span className="min-w-0">{text}</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** Rendered into document.body (like the Ask help dialog) so it covers the
 * fixed header and isn't clipped by a card's stacking context. */
function SourcesModal({
  result,
  onClose,
  onOpen,
}: {
  result: ResultCardData;
  onClose: () => void;
  onOpen?: (source: string) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const crossDocSources = result.cross_doc?.sources ?? [];

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="sources-title"
        onClick={(e) => e.stopPropagation()}
        className="max-h-[80vh] w-full max-w-lg overflow-y-auto rounded-lg border border-[var(--line)] bg-[var(--paper)] p-5 shadow-xl"
      >
        <div className="flex items-start justify-between gap-4">
          <h2 id="sources-title" className="font-display text-base font-semibold tracking-tight">
            Sources
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            autoFocus
            className="cursor-pointer rounded p-0.5 text-[var(--ink-soft)] hover:text-[var(--ink)]"
          >
            <X size={16} />
          </button>
        </div>
        <p className={`mt-1 text-xs ${muted}`}>Documents this answer was based on. [n] matches the citation numbers. Click one to read it.</p>

        {result.sources.length > 0 && (
          <div className="mt-4">
            <p className={label}>Answer</p>
            <SourceList sources={result.sources} onOpen={onOpen} />
          </div>
        )}

        {crossDocSources.length > 0 && (
          <div className="mt-4 border-t border-[var(--line)] pt-4">
            <p className={label}>Cross-document findings</p>
            <SourceList sources={crossDocSources} onOpen={onOpen} />
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

/** Shown inside the card while the model is still working, so the box is
 * never just an empty frame. Before the sources arrive it is searching; after,
 * the model is reading them (a reasoning model can take a while before its
 * first word). */
function Thinking({ label: text }: { label: string }) {
  return (
    <div role="status" aria-live="polite" className={`mt-3 flex items-center gap-2.5 text-[14px] ${muted}`}>
      <span className="flex items-center gap-1" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="size-1.5 rounded-full bg-[var(--index)] motion-safe:animate-pulse"
            style={{ animationDelay: `${i * 180}ms`, animationDuration: "1s" }}
          />
        ))}
      </span>
      {text}
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A file the assistant made. It is saved automatically as soon as it is
 * ready; the button is only there to save it again (or if the browser
 * blocked the automatic download). */
function GeneratedFileCard({ file, onDownload }: { file: GeneratedFile; onDownload?: (file: GeneratedFile) => void }) {
  return (
    <div className="flex items-center gap-3 rounded border border-[var(--line)] bg-[var(--index-soft)] px-3 py-2.5">
      <FileText size={18} className="shrink-0 text-[var(--index)]" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[14px] font-medium" title={file.name}>
          {file.name}
        </p>
        <p className={`text-[11px] ${muted}`}>
          {formatSize(file.size_bytes)} · saved to your downloads{file.source ? ` · made from ${file.source}` : ""}
        </p>
      </div>
      {onDownload && (
        <button
          type="button"
          onClick={() => onDownload(file)}
          title="Download again"
          aria-label={`Download ${file.name} again`}
          className="inline-flex shrink-0 cursor-pointer items-center rounded border border-[var(--line)] bg-[var(--paper)] p-1.5 text-[var(--ink-soft)] transition-colors hover:border-[var(--index)] hover:text-[var(--index)]"
        >
          <Download size={14} />
        </button>
      )}
    </div>
  );
}

export function ResultCard({
  result,
  actions,
  chatOnly = false,
  pending = false,
  statusText,
  onDownloadFile,
  route,
  stopped = false,
}: {
  result: ResultCardData;
  /** The user stopped this reply before it finished. */
  stopped?: boolean;
  actions?: React.ReactNode;
  /** How the backend produced this reply (see AnswerBadge). */
  route?: string;
  /** What the app is doing right now ("Rewriting part 2 of 5…"); replaces the
   * generic loader text while pending. */
  statusText?: string;
  /** Saves a generated file again. */
  onDownloadFile?: (file: GeneratedFile) => void;
  /** True while this answer is still being generated: shows a loader instead
   * of an empty box and holds back the action buttons until it's done. */
  pending?: boolean;
  /** True when this answer came from a direct model chat with no corpus
   * scope selected — distinguishes "nothing was searched" from the
   * grounded-but-no-match case below, which otherwise look identical
   * (both have grounded: false). */
  chatOnly?: boolean;
}) {
  const [showSources, setShowSources] = useState(false);
  const viewer = useViewer();

  // Opens a document at the part that matches the question best; the answer's
  // [n] markers and the Sources list both use it.
  const openSource = useCallback(
    (source: string) => {
      setShowSources(false);
      viewer?.open({ path: source, q: result.question });
    },
    [viewer, result.question],
  );
  const openCitation = useCallback(
    (n: number) => {
      const source = result.sources[n - 1];
      if (source) openSource(source);
    },
    [openSource, result.sources],
  );

  // hovering a [n] badge previews the cited file; the cross-document findings number their own sources
  const crossSources = result.cross_doc?.sources;
  const citationPreview = useMemo(
    () => ({ sourceFor: (n: number) => result.sources[n - 1], question: result.question }),
    [result.sources, result.question],
  );
  const crossPreview = useMemo(
    () => ({ sourceFor: (n: number) => crossSources?.[n - 1], question: result.question }),
    [crossSources, result.question],
  );
  const openCrossCitation = useCallback(
    (n: number) => {
      const source = crossSources?.[n - 1];
      if (source) openSource(source);
    },
    [openSource, crossSources],
  );

  const sourceCount = new Set([...result.sources, ...(result.cross_doc?.sources ?? [])]).size;

  return (
    <div className={card}>
      <div className="flex flex-wrap items-center gap-2">
        <p className={label}>{route === "keyword" ? "Matches" : "Answer"}</p>
        <AnswerBadge route={route} chatOnly={chatOnly} grounded={result.grounded} sourceCount={sourceCount} />
      </div>
      {!chatOnly && !result.grounded && !pending && (
        <p className={`font-mono mt-1 text-[11px] ${muted}`}>
          No matching document was found for this question.
        </p>
      )}
      {result.answer ? (
        <div className="mt-2">
          <Markdown
            onCitation={viewer && result.sources.length > 0 ? openCitation : undefined}
            preview={result.sources.length > 0 ? citationPreview : undefined}
          >
            {result.answer}
          </Markdown>
        </div>
      ) : pending ? (
        <Thinking
          label={
            statusText ||
            (chatOnly
              ? "Thinking…"
              : result.sources.length === 0
                ? "Searching your documents…"
                : "Reading the sources and writing the answer…")
          }
        />
      ) : (
        <p className={`mt-2 text-[14px] ${muted}`}>
          {stopped ? "Stopped before an answer was written." : "No answer was generated. Try regenerating."}
        </p>
      )}
      {stopped && result.answer && (
        <p className={`font-mono mt-2 text-[11px] ${muted}`}>Stopped — this reply is incomplete and isn’t saved to History.</p>
      )}

      {result.files && result.files.length > 0 && (
        <div className="mt-3 space-y-2">
          {result.files.map((file) => (
            <GeneratedFileCard key={file.id} file={file} onDownload={onDownloadFile} />
          ))}
        </div>
      )}

      {result.cross_doc && (
        <div className="mt-5 border-t border-[var(--line)] pt-4">
          <p className={label}>Cross-document findings</p>
          <div className="mt-2">
            <Markdown onCitation={viewer ? openCrossCitation : undefined} preview={crossPreview}>
              {result.cross_doc.answer}
            </Markdown>
          </div>
        </div>
      )}

      {result.statistical && (
        <div className="mt-5 border-t border-[var(--line)] pt-4">
          <p className={label}>Statistical findings</p>
          <div className="mt-2">
            <Markdown>{result.statistical.answer}</Markdown>
          </div>
          <pre className="font-mono mt-2 whitespace-pre-wrap rounded border border-[var(--line)] bg-[var(--locator-soft)] p-2.5 text-[11px]">
            {result.statistical.correlation_summary}
          </pre>
        </div>
      )}

      {!pending && (actions || sourceCount > 0) && (
        <div className="mt-5 flex items-center gap-2 border-t border-[var(--line)] pt-4">
          {actions}
          {sourceCount > 0 && (
            <button
              type="button"
              onClick={() => setShowSources(true)}
              className={`${buttonSecondary} ml-auto inline-flex items-center gap-1.5`}
            >
              <FileText size={13} /> Sources ({sourceCount})
            </button>
          )}
        </div>
      )}

      {showSources && (
        <SourcesModal result={result} onClose={() => setShowSources(false)} onOpen={viewer ? openSource : undefined} />
      )}
    </div>
  );
}
