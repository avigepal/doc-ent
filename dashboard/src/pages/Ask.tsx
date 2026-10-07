import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, downloadExport, type FolderStatus, type QueryResult } from "../api/client";
import { button, buttonSecondary, card, errorText, input, label, muted } from "../ui";

const FOLDER_POLL_MS = 4000;

/** Strips everything up to and including "/raw/" so citations show a
 * readable "contracts/q2_update.txt" instead of the full container path
 * "/data/pipeline/raw/contracts/q2_update.txt". */
function shortenSource(path: string): string {
  const marker = "/raw/";
  const idx = path.indexOf(marker);
  return idx === -1 ? path : path.slice(idx + marker.length);
}

function yamlEscape(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

/** Builds the exported report's markdown source. A YAML front-matter
 * block (title/subtitle/date) drives the "Doc/Index" styled title
 * block in both the PDF (report.latex) and docx (reference.docx)
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
    md += `\n\`\`\`\n${result.statistical.correlation_summary}\n\`\`\`\n`;
  }
  return md;
}

function slugForFilename(question: string): string {
  return question.trim().slice(0, 60) || "query-result";
}

type DerivedState = "processing" | "attention" | "ready" | "pending" | "empty";

function deriveState(f: FolderStatus): DerivedState {
  if (f.processing) return "processing";
  if (f.has_failures) return "attention";
  if (f.total_files === 0) return "empty";
  if (f.summarized === f.total_files) return "ready";
  return "pending";
}

const STATE_META: Record<DerivedState, { dot: string; text: string; pulse?: boolean }> = {
  processing: { dot: "bg-[var(--index)]", text: "Processing", pulse: true },
  attention: { dot: "bg-[var(--danger)]", text: "Needs attention" },
  ready: { dot: "bg-[var(--signal)]", text: "Ready" },
  pending: { dot: "bg-[var(--locator)]", text: "Pending" },
  empty: { dot: "bg-[var(--line)]", text: "Empty" },
};

function FolderSidebar({
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
    <aside className="w-full shrink-0 lg:w-56">
      <p className={label}>Folders</p>

      {folders.length === 0 ? (
        <p className={`mt-2 text-sm ${muted}`}>
          No folders yet — create one below, or drop a folder of files into{" "}
          <code className="font-mono">raw/</code> on the server (SSH/SFTP/FileZilla work directly
          — it's just a directory).
        </p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1">
          {folders.map((f) => {
            const state = deriveState(f);
            const meta = STATE_META[state];
            const active = selected.includes(f.name);
            return (
              <li key={f.name}>
                <button
                  type="button"
                  onClick={() => onToggle(f.name)}
                  className={`flex w-full items-center gap-2 rounded-md border-l-2 px-2.5 py-2 text-left text-sm transition-colors ${
                    active
                      ? "border-[var(--index)] bg-[var(--index-soft)]"
                      : "border-transparent hover:bg-[var(--index-soft)]/40"
                  }`}
                >
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${meta.dot} ${meta.pulse ? "animate-pulse" : ""}`}
                    title={meta.text}
                  />
                  <span className="flex-1 truncate">{f.name}</span>
                  <span className="font-mono shrink-0 text-xs text-[var(--ink-soft)]">
                    {f.summarized}/{f.total_files}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {folders.length > 0 && selected.length === 0 && (
        <p className={`mt-2 text-xs ${muted}`}>whole corpus</p>
      )}

      <form onSubmit={handleCreate} className="mt-4 flex gap-1.5 border-t border-[var(--line)] pt-3">
        <input
          type="text"
          placeholder="new folder"
          value={newFolderName}
          onChange={(e) => setNewFolderName(e.target.value)}
          className={`${input} min-w-0 flex-1 text-sm`}
        />
        <button type="submit" disabled={!newFolderName.trim() || creating} className={buttonSecondary}>
          {creating ? "…" : "+"}
        </button>
      </form>
      {createError && <p className={`mt-1 ${errorText}`}>{createError}</p>}
      <p className={`mt-1 text-xs ${muted}`}>
        Creates raw/&lt;name&gt; on the server — put files in it via SSH/SFTP or the + below.
      </p>
    </aside>
  );
}

/** Signature element: sources rendered as index-tab stubs — a locator
 * number, a clipped corner (literal "cut card" shape), and a colored
 * edge keyed to evidence type. This is the one visual idea the whole
 * app repeats, because grounded citations are the actual product. */
function SourceTabs({ sources, accent = "index" }: { sources: string[]; accent?: "index" | "locator" }) {
  const edgeColor = accent === "index" ? "var(--index)" : "var(--locator)";
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {sources.map((s, i) => (
        <div
          key={i}
          title={s}
          className="group flex items-center gap-2 border-y border-r border-[var(--line)] bg-[var(--paper)] py-1.5 pr-3 pl-2.5 text-sm transition-transform hover:-translate-y-0.5 hover:shadow-[0_2px_0_var(--line)]"
          style={{
            borderLeft: `3px solid ${edgeColor}`,
            clipPath: "polygon(0 0, calc(100% - 8px) 0, 100% 8px, 100% 100%, 0 100%)",
          }}
        >
          <span className="font-mono text-xs" style={{ color: edgeColor }}>
            {String(i + 1).padStart(2, "0")}
          </span>
          <span className="max-w-[16rem] truncate">{shortenSource(s)}</span>
        </div>
      ))}
    </div>
  );
}

export function Ask() {
  const [question, setQuestion] = useState("");
  const [folders, setFolders] = useState<FolderStatus[]>([]);
  const [selectedFolders, setSelectedFolders] = useState<string[]>([]);
  const [authorFilter, setAuthorFilter] = useState("");
  const [titleFilter, setTitleFilter] = useState("");
  const [showFilters, setShowFilters] = useState(false);
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
          : await api.query(question, {
              folders: selectedFolders,
              author: authorFilter.trim() || undefined,
              title: titleFilter.trim() || undefined,
            });
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
      await downloadExport(buildMarkdown(result), slugForFilename(result.question), fmt);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
    } finally {
      setDownloading(null);
    }
  };

  return (
    <div>
      <p className={label}>Query</p>
      <h1 className="font-display mt-1 text-2xl font-semibold tracking-tight">Ask</h1>
      <p className={`mt-1 ${muted}`}>
        A summary, a direct question, or how things relate across documents — answered from
        retrieved sources with citations, plus cross-document and statistical findings when
        they apply. Attach files with + to ask about them directly instead of the ingested
        corpus. Needs a live llama-server.
      </p>

      <div className="mt-6 flex flex-col gap-8 lg:flex-row">
        <FolderSidebar
          folders={folders}
          selected={selectedFolders}
          onToggle={toggleFolder}
          onCreated={fetchFolders}
        />

        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={() => setShowFilters((v) => !v)}
            className={`mb-2 font-mono text-xs ${muted} hover:text-[var(--ink)]`}
          >
            {showFilters ? "− hide filters" : "+ filter by author / title"}
          </button>
          {showFilters && (
            <div className="mb-3 flex gap-2">
              <input
                type="text"
                placeholder="author contains…"
                value={authorFilter}
                onChange={(e) => setAuthorFilter(e.target.value)}
                className={`${input} flex-1 text-sm`}
              />
              <input
                type="text"
                placeholder="title contains…"
                value={titleFilter}
                onChange={(e) => setTitleFilter(e.target.value)}
                className={`${input} flex-1 text-sm`}
              />
            </div>
          )}

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
                className={`${input} w-full`}
              />
              {loading && (
                <div className="pointer-events-none absolute right-0 bottom-0 left-0 h-[2px] overflow-hidden rounded-b-md">
                  <div className="scan-line h-full w-1/3 bg-[var(--index)]" />
                </div>
              )}
            </div>
            <button type="submit" disabled={!question || loading} className={button}>
              {loading ? "Asking…" : "Ask"}
            </button>
          </form>

          {error && <p className={`mt-3 ${errorText}`}>{error}</p>}

          {result && (
            <div className={card}>
              {!result.grounded && (
                <p className={`mb-2 font-mono text-xs ${muted}`}>not grounded — nothing matched closely enough</p>
              )}
              <p>{result.answer}</p>
              {result.sources.length > 0 && (
                <>
                  <p className={`mt-4 ${label}`}>Sources</p>
                  <SourceTabs sources={result.sources} accent="index" />
                </>
              )}

              {result.cross_doc && (
                <div className="mt-6 border-t border-[var(--line)] pt-5">
                  <p className={label}>Cross-document findings</p>
                  <p className="mt-2">{result.cross_doc.answer}</p>
                  <SourceTabs sources={result.cross_doc.sources} accent="index" />
                </div>
              )}

              {result.statistical && (
                <div className="mt-6 border-t border-[var(--line)] pt-5">
                  <p className={label}>Statistical findings</p>
                  <p className="mt-2">{result.statistical.answer}</p>
                  <pre className="font-mono mt-2 whitespace-pre-wrap rounded border border-[var(--line)] bg-[var(--locator-soft)] p-3 text-xs">
                    {result.statistical.correlation_summary}
                  </pre>
                </div>
              )}

              <div className="mt-6 flex items-center gap-2 border-t border-[var(--line)] pt-5">
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
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
