import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, downloadExport, type FolderStatus, type QueryResult } from "../api/client";
import { ResultCard, shortenSource } from "../components/ResultCard";
import { button, buttonSecondary, errorText, input, label, muted } from "../ui";

const FOLDER_POLL_MS = 4000;

function yamlEscape(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

/** The backend formats correlation findings as lines like "A vs B: r=0.92"
 * (see backend/app/search/correlate.py:format_correlation_matrix). Parses
 * those into a real markdown table — which pandoc/weasyprint render as a
 * styled <table> in the exported report — instead of dumping the raw text
 * into a code block. Falls back to the original text unchanged if nothing
 * matches (e.g. the "No strong correlations found" message). */
function correlationTable(summary: string): string {
  const rowPattern = /^(.+?) vs (.+?): r=(-?\d+\.\d+)$/;
  const rows = summary
    .split("\n")
    .map((line) => line.match(rowPattern))
    .filter((m): m is RegExpMatchArray => m !== null);

  if (rows.length === 0) return `\n${summary}\n`;

  let table = "\n| Variable A | Variable B | r |\n|---|---|---|\n";
  table += rows.map((m) => `| ${m[1]} | ${m[2]} | ${m[3]} |`).join("\n");
  return `${table}\n`;
}

/** Builds the exported report's markdown source. A YAML front-matter
 * block (title/subtitle/date) drives the "Doc/Index" styled title
 * block in both the PDF (report.html) and docx (reference.docx)
 * templates — see backend/app/export/templates/ — instead of a plain
 * H1, so the exported file reads as a generated report rather than a
 * raw markdown dump. */
function buildMarkdown(result: QueryResult): string {
  const modes = ["Grounded answer"];
  if (result.cross_doc) modes.push("Cross-document correlation");
  if (result.statistical) modes.push("Statistical findings");

  const generated = new Date().toLocaleString(undefined, { dateStyle: "long", timeStyle: "short" });

  let md = "---\n";
  md += `title: "${yamlEscape(result.question)}"\n`;
  md += `subtitle: "${yamlEscape(modes.join(" · "))}"\n`;
  md += `date: "${yamlEscape(generated)}"\n`;
  md += "---\n\n";

  md += `## Answer\n\n${result.answer}\n`;
  if (!result.grounded) {
    md += "\n> No corpus sources matched closely enough to ground this answer — treat it as unverified.\n";
  }

  if (result.sources.length > 0) {
    md += `\n### Sources\n\n${result.sources.map((s, i) => `${i + 1}. ${shortenSource(s)}`).join("\n")}\n`;
  }
  if (result.cross_doc) {
    md += `\n## Cross-document findings\n\n${result.cross_doc.answer}\n`;
    md += `\n### Sources\n\n${result.cross_doc.sources.map((s, i) => `${i + 1}. ${shortenSource(s)}`).join("\n")}\n`;
  }
  if (result.statistical) {
    md += `\n## Statistical findings\n\n${result.statistical.answer}\n`;
    md += correlationTable(result.statistical.correlation_summary);
  }
  return md;
}

function slugForFilename(question: string): string {
  return question.trim().slice(0, 60) || "query-result";
}

/** Horizontal scope strip replacing the old folder sidebar column now that
 * the app has a real sidebar (see Sidebar.tsx) — folder scoping is a
 * per-query filter, not navigation, so it lives above the question input
 * instead of beside it. Same folders/selected/onToggle/onCreated state and
 * polling as before; only the layout changed. */
function ScopeBar({
  folders,
  selected,
  onToggle,
  onCreated,
}: {
  folders: FolderStatus[];
  selected: string[];
  onToggle: (name: string) => void;
  onCreated: () => void;
}) {
  const [newFolderName, setNewFolderName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (!newFolderName.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      await api.createFolder(newFolderName.trim());
      setNewFolderName("");
      onCreated();
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="mb-6">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-[var(--line)] pb-3">
        <span className={`${label} mr-1`}>Scope</span>
        {folders.map((f) => {
          const isSelected = selected.includes(f.name);
          return (
            <button
              key={f.name}
              type="button"
              onClick={() => onToggle(f.name)}
              className={`rounded border px-2 py-0.5 text-xs transition-colors ${
                isSelected
                  ? "border-[var(--index)] bg-[var(--index-soft)] text-[var(--index)]"
                  : "border-[var(--line)] text-[var(--ink-soft)] hover:text-[var(--ink)]"
              }`}
            >
              {f.name}
              {f.processing && <span className="ml-1 text-[var(--locator)]">●</span>}
              {f.has_failures && <span className="ml-1 text-[var(--danger)]">●</span>}
            </button>
          );
        })}
        {folders.length === 0 && <span className={muted}>No folders yet</span>}

        <form onSubmit={handleCreate} className="ml-auto flex items-center gap-1.5">
          <input
            type="text"
            placeholder="new folder"
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            className={`${input} w-32 text-sm`}
          />
          <button type="submit" disabled={!newFolderName.trim() || creating} className={buttonSecondary}>
            {creating ? "…" : "+"}
          </button>
        </form>
      </div>
      {createError && <p className={`mt-1 ${errorText}`}>{createError}</p>}
      {folders.length > 0 && selected.length === 0 && (
        <p className={`mt-1 text-xs ${muted}`}>whole corpus</p>
      )}
    </div>
  );
}

export function Ask() {
  const [question, setQuestion] = useState("");
  const [folders, setFolders] = useState<FolderStatus[]>([]);
  const [selectedFolders, setSelectedFolders] = useState<string[]>([]);
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState<"pdf" | "docx" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchFolders = () => {
    api.listFolders().then(
      (r) => setFolders(r.folders),
      () => setFolders([]), // folder list is a nice-to-have; a failure here shouldn't block Ask
    );
  };

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      api.listFolders().then(
        (r) => {
          if (!cancelled) setFolders(r.folders);
        },
        () => {
          if (!cancelled) setFolders([]);
        },
      );
    };
    poll();
    const interval = setInterval(poll, FOLDER_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const toggleFolder = (folder: string) => {
    setSelectedFolders((prev) =>
      prev.includes(folder) ? prev.filter((f) => f !== folder) : [...prev, folder],
    );
  };

  const handleFilesPicked = (fileList: FileList | null) => {
    if (!fileList) return;
    setAttachedFiles((prev) => [...prev, ...Array.from(fileList)]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const removeAttachment = (index: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const r =
        attachedFiles.length > 0
          ? await api.queryUpload(question, attachedFiles)
          : await api.query(question, { folders: selectedFolders });
      setResult(r);
      setAttachedFiles([]);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = async (fmt: "pdf" | "docx") => {
    if (!result) return;
    setDownloading(fmt);
    setError(null);
    try {
      await downloadExport(buildMarkdown(result), slugForFilename(result.question), fmt, result.history_id);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
    } finally {
      setDownloading(null);
    }
  };

  return (
    <div className="pb-36">
      <p className={label}>Query</p>
      <h1 className="font-display mt-1 text-2xl font-semibold tracking-tight">Ask</h1>
      <p className={`mt-1 ${muted}`}>
        A summary, a direct question, or how things relate across documents — answered from
        retrieved sources with citations, plus cross-document and statistical findings when
        they apply. Attach files with + to ask about them directly instead of the ingested
        corpus. Needs a live llama-server.
      </p>

      <div className="mt-6">
        <ScopeBar
          folders={folders}
          selected={selectedFolders}
          onToggle={toggleFolder}
          onCreated={fetchFolders}
        />

        <div className="min-w-0">
          {error && <p className={`mb-3 ${errorText}`}>{error}</p>}

          {result && (
            <ResultCard
              result={result}
              actions={
                <>
                  <span className={`font-mono text-xs ${muted}`}>Download</span>
                  {(["pdf", "docx"] as const).map((fmt) => (
                    <button
                      key={fmt}
                      onClick={() => handleDownload(fmt)}
                      disabled={downloading !== null}
                      className={buttonSecondary}
                    >
                      {downloading === fmt ? "…" : fmt.toUpperCase()}
                    </button>
                  ))}
                </>
              }
            />
          )}
        </div>
      </div>

      {/* Composer pinned to the bottom of the viewport, ChatGPT/Gemini-style
          — everything above (scope, filters, the answer) scrolls normally in
          the page; this bar stays put so you can always ask the next
          question without scrolling back down. Offset matches Sidebar's
          lg:w-[220px] desktop column so it doesn't run under it. */}
      <div className="fixed right-0 bottom-0 left-0 border-t border-[var(--line)] bg-[var(--paper)] px-6 py-4 lg:left-[220px]">
        <div className="mx-auto max-w-7xl">
          {attachedFiles.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {attachedFiles.map((f, i) => (
                <span
                  key={i}
                  className="flex items-center gap-1.5 rounded-full border border-[var(--line)] bg-[var(--index-soft)] py-1 pr-1.5 pl-3 text-xs"
                >
                  {f.name}
                  <button
                    type="button"
                    onClick={() => removeAttachment(i)}
                    className="rounded-full px-1.5 text-[var(--ink-soft)] hover:bg-[var(--line)] hover:text-[var(--ink)]"
                    aria-label={`Remove ${f.name}`}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}

          <form onSubmit={handleSubmit} className="relative flex gap-3">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => handleFilesPicked(e.target.files)}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              title="Attach files to ask about directly"
              className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-md border border-[var(--line)] text-lg hover:border-[var(--index)] hover:text-[var(--index)]"
            >
              +
            </button>
            <div className="relative flex-1">
              <input
                type="text"
                placeholder={
                  attachedFiles.length > 0
                    ? "Ask about the attached file(s)…"
                    : "e.g. Summarize the Q1 contracts, or how do these numbers relate?"
                }
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                autoFocus
                className={`${input} h-[42px] w-full`}
              />
              {loading && (
                <div className="pointer-events-none absolute right-0 bottom-0 left-0 h-[2px] overflow-hidden rounded-b-md">
                  <div className="scan-line h-full w-1/3 bg-[var(--index)]" />
                </div>
              )}
            </div>
            <button
              type="submit"
              disabled={!question || loading}
              className={`${button} flex h-[42px] shrink-0 items-center justify-center`}
            >
              {loading ? "Asking…" : "Ask"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
