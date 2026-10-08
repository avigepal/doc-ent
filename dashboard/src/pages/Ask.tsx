import { AlertCircle, Check, Copy, Download, FileText, Loader2, RefreshCw, Trash2, Upload, X } from "lucide-react";
import { useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import {
  api,
  ApiError,
  downloadExport,
  type QueryHistoryDetail,
  type QueryResult,
  type UploadStage,
} from "../api/client";
import { ResultCard, shortenSource } from "../components/ResultCard";
import { button, buttonSecondary, errorText, input, muted } from "../ui";

/** A file attached to the current chat (the upload button). Questions in the
 * chat search only these; a new chat clears them. */
type Attachment = { id: number; name: string; stage: UploadStage; detail: string | null };

const PENDING_STAGES: UploadStage[] = ["queued", "converting", "indexing"];
const isPending = (a: Attachment) => PENDING_STAGES.includes(a.stage);

const STAGE_LABEL: Record<UploadStage, string> = {
  // every in-progress stage reads as "Reading…" — queue/index are internal
  queued: "Reading…",
  converting: "Reading…",
  indexing: "Reading…",
  ready: "Ready",
  failed: "Failed",
  unsupported: "Unsupported",
};

// Attachments are remembered per chat so a refresh (or switching back to the
// chat) still shows them. Only id + name are stored; the live status is
// re-fetched, which also drops files a new chat has since deleted.
const attachmentsKey = (conversationId: string) => `doc:attachments:${conversationId}`;

function loadAttachments(conversationId: string): Attachment[] {
  try {
    const raw = localStorage.getItem(attachmentsKey(conversationId));
    const parsed = raw ? (JSON.parse(raw) as { id: number; name: string }[]) : [];
    return parsed.map((p) => ({ id: p.id, name: p.name, stage: "queued" as const, detail: null }));
  } catch {
    return [];
  }
}

function saveAttachments(conversationId: string, list: Attachment[]) {
  try {
    const key = attachmentsKey(conversationId);
    if (list.length === 0) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(list.map((a) => ({ id: a.id, name: a.name }))));
  } catch {
    // storage unavailable (private mode, quota) — attachments just won't survive a refresh
  }
}

function AttachmentChip({ attachment, onRemove }: { attachment: Attachment; onRemove: () => void }) {
  const pending = isPending(attachment);
  const bad = attachment.stage === "failed" || attachment.stage === "unsupported";
  return (
    <span
      title={bad ? `${attachment.name} — ${attachment.detail ?? STAGE_LABEL[attachment.stage]}` : attachment.name}
      className={`inline-flex max-w-[280px] items-center gap-1.5 rounded border px-2 py-1 text-xs ${
        bad ? "border-[var(--danger)] text-[var(--danger)]" : "border-[var(--line)] text-[var(--ink)]"
      }`}
    >
      <FileText size={13} className="shrink-0" />
      <span className="truncate">{attachment.name}</span>
      {pending && (
        <span className={`font-mono flex shrink-0 items-center gap-1 text-[10px] ${muted}`}>
          <Loader2 size={11} className="animate-spin" /> {STAGE_LABEL[attachment.stage]}
        </span>
      )}
      {attachment.stage === "ready" && <Check size={12} className="shrink-0 text-[var(--signal)]" />}
      {bad && <AlertCircle size={12} className="shrink-0" />}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${attachment.name}`}
        className="shrink-0 cursor-pointer text-[var(--ink-soft)] hover:text-[var(--ink)]"
      >
        <X size={12} />
      </button>
    </span>
  );
}

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
 * block (title/subtitle/date) drives the "Docent" styled title
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
    md += "\n> No matching documents were found for this question — treat the answer as unverified.\n";
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

/** A stored history row has the same fields a fresh QueryResult does,
 * plus history bookkeeping (id, chat_only, filter_folders, ...) — this
 * strips it down to just what the thread needs to redisplay the turn. */
function historyDetailToEntry(d: QueryHistoryDetail): { id: string; result: QueryResult; chatOnly: boolean; folders: string[] } {
  return {
    id: crypto.randomUUID(),
    result: {
      question: d.question,
      answer: d.answer,
      sources: d.sources,
      grounded: d.grounded,
      cross_doc: d.cross_doc,
      statistical: d.statistical,
      history_id: d.id,
    },
    chatOnly: d.chat_only,
    folders: d.filter_folders,
  };
}

export function Ask() {
  const [question, setQuestion] = useState("");
  // Scope (All/folders) and which chat this is live in the URL, not
  // local state — Sidebar.tsx owns the Scope UI and the chat list now
  // (they're siblings of this page under Layout, see App.tsx), so the
  // URL is the shared channel between them instead of a prop/context.
  const [searchParams, setSearchParams] = useSearchParams();
  const conversationId = searchParams.get("c");
  const scopeAll = searchParams.get("all") === "1";
  const selectedFolders = (searchParams.get("folders") ?? "").split(",").filter(Boolean);

  // ChatGPT-style thread: every answered question stays on screen, oldest
  // first, instead of being replaced by the next one — only the question
  // input itself clears after a submit. `folders` is the scope each turn
  // actually ran with, kept per-entry (not read from current state) so
  // Regenerate replays the same scope even if Scope has changed since.
  const [thread, setThread] = useState<{ id: string; result: QueryResult; chatOnly: boolean; folders: string[]; fileIds?: number[] }[]>(
    [],
  );
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState<{ id: string; fmt: "pdf" | "docx" } | null>(null);
  const [regeneratingId, setRegeneratingId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  // Mirror of `attachments` for the status poll, which outlives renders.
  const attachmentsRef = useRef<Attachment[]>([]);
  // Bumped to cancel an in-flight status poll (new upload, chat switch, unmount).
  const uploadPollRef = useRef(0);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const threadEndRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const [composerHeight, setComposerHeight] = useState(0);

  // The composer is position:fixed, so it's taken out of normal document
  // flow — a static padding guess doesn't track its real height, which
  // changes (upload notice, drag-over hint). Measured directly so the
  // thread always gets exactly the clearance it needs above it.
  useEffect(() => {
    const composer = composerRef.current;
    if (!composer) return;
    // offsetHeight, not entry.contentRect — contentRect excludes an
    // element's own padding/border, under-reporting its true rendered
    // height by exactly that much, which previously left a ~30px gap
    // where content was still hidden behind the composer at full scroll.
    const observer = new ResizeObserver(() => setComposerHeight(composer.offsetHeight));
    observer.observe(composer);
    return () => observer.disconnect();
  }, []);

  // A fresh visit to /ask (no ?c=) gets a new conversation id right
  // away — generated client-side, no server round-trip needed to
  // "start" a chat, it just becomes real the moment the first question
  // is recorded under it. Sidebar.tsx's "New chat" link is just a link
  // to bare /ask, which lands here again and repeats this.
  useEffect(() => {
    if (conversationId) return;
    // A new chat starts with no attachments: uploads belong to the chat they
    // were added in, so the uploads folder is emptied here.
    api.clearUploads().catch(() => {});
    const next = new URLSearchParams(searchParams);
    next.set("c", crypto.randomUUID());
    setSearchParams(next, { replace: true });
  }, [conversationId, searchParams, setSearchParams]);

  // Restore this conversation's thread from server-side history —
  // otherwise it's only in-memory and would vanish on every refresh or
  // every switch between chats in the sidebar, even though every turn
  // is already recorded via record_query.
  useEffect(() => {
    if (!conversationId) return;
    setThread([]);
    api.listQueryHistoryDetail(100, conversationId).then(
      (r) => setThread(r.queries.map(historyDetailToEntry).reverse()),
      () => {}, // history is a nice-to-have on load; a failure here shouldn't block Ask
    );
  }, [conversationId]);

  useEffect(() => {
    // "smooth" here would re-trigger on every token while an answer
    // streams in (thread updates once per token — see streamInto below),
    // and overlapping smooth-scroll animations cancel each other out
    // before reaching the bottom, leaving the view stuck mid-answer.
    // Instant scroll has no animation to interrupt, so it reliably lands
    // at the true bottom on every update.
    threadEndRef.current?.scrollIntoView({ behavior: "instant", block: "end" });
  }, [thread]);

  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);

  // Every change goes through here so it is also saved for this chat.
  const updateAttachments = (change: (prev: Attachment[]) => Attachment[]) => {
    setAttachments((prev) => {
      const next = change(prev);
      attachmentsRef.current = next;
      if (conversationId) saveAttachments(conversationId, next);
      return next;
    });
  };

  // Polls the status of ALL of this chat's attachments (queued -> reading ->
  // indexing -> ready) until none are pending. Files that no longer exist on
  // the server (cleared by a new chat) are dropped from the list.
  const followAttachments = async () => {
    const myPoll = ++uploadPollRef.current;
    for (let attempt = 0; attempt < 400; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, attempt === 0 ? 600 : 1500));
      if (uploadPollRef.current !== myPoll) return;
      const ids = attachmentsRef.current.map((a) => a.id);
      if (ids.length === 0) return;
      try {
        const status = await api.uploadStatus(ids);
        if (uploadPollRef.current !== myPoll) return;
        const byId = new Map(status.files.map((f) => [f.id, f]));
        updateAttachments((prev) =>
          prev
            .filter((a) => byId.has(a.id))
            .map((a) => ({ ...a, stage: byId.get(a.id)!.stage, detail: byId.get(a.id)!.detail })),
        );
        if (status.done) return;
      } catch {
        // transient (server restarting, network blip) — keep polling
      }
    }
  };

  // Switching to (or reloading) a chat: restore its attachments and refresh
  // their status. Also stops any poll that belonged to the previous chat.
  useEffect(() => {
    uploadPollRef.current += 1;
    setUploadError(null);
    if (!conversationId) {
      setAttachments([]);
      attachmentsRef.current = [];
      return;
    }
    const stored = loadAttachments(conversationId);
    setAttachments(stored);
    attachmentsRef.current = stored;
    if (stored.length > 0) void followAttachments();
    // followAttachments only reads refs and conversationId (this effect's own dependency)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  useEffect(() => {
    const poll = uploadPollRef;
    return () => {
      poll.current += 1;
    };
  }, []);

  const removeAttachment = (id: number) => {
    updateAttachments((prev) => prev.filter((a) => a.id !== id));
    api.deleteUpload(id).catch(() => {});
  };

  // Removes every file attached to this chat. Deletes exactly this chat's
  // files (not the whole uploads folder), so another open chat keeps its own.
  const clearAttachments = () => {
    const ids = attachmentsRef.current.map((a) => a.id);
    updateAttachments(() => []);
    setUploadError(null);
    void Promise.allSettled(ids.map((id) => api.deleteUpload(id)));
  };

  const handleFilesPicked = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    const files = Array.from(fileList);
    if (fileInputRef.current) fileInputRef.current.value = "";

    setUploading(true);
    setUploadError(null);
    try {
      const r = await api.uploadFiles(files);
      if (r.file_ids.length === 0) {
        setUploadError("None of those files could be added.");
        return;
      }
      // the status call gives each new file's name and first stage
      const status = await api.uploadStatus(r.file_ids);
      updateAttachments((prev) => {
        const known = new Set(prev.map((a) => a.id));
        const added = status.files
          .filter((f) => !known.has(f.id))
          .map((f) => ({ id: f.id, name: f.name, stage: f.stage, detail: f.detail }));
        return [...prev, ...added];
      });
      void followAttachments();
    } catch (err) {
      setUploadError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
    } finally {
      setUploading(false);
    }
  };

  // Shared by both a fresh question and a regenerate: streams tokens
  // into whichever entry already exists at entryId, updating it in
  // place as meta/token/extra/done events arrive (see client.ts's
  // queryStream and backend app/main.py:query_stream_endpoint).
  const streamInto = async (
    entryId: string,
    question: string,
    folders: string[],
    chatOnly: boolean,
    fileIds: number[] = [],
  ) => {
    await api.queryStream(
      question,
      { folders, chatOnly, fileIds, conversationId: conversationId ?? undefined },
      {
        onMeta: (meta) =>
          setThread((prev) =>
            prev.map((t) =>
              t.id === entryId ? { ...t, result: { ...t.result, sources: meta.sources, grounded: meta.grounded } } : t,
            ),
          ),
        onToken: (text) =>
          setThread((prev) =>
            prev.map((t) => (t.id === entryId ? { ...t, result: { ...t.result, answer: t.result.answer + text } } : t)),
          ),
        onExtra: (extra) =>
          setThread((prev) =>
            prev.map((t) =>
              t.id === entryId
                ? { ...t, result: { ...t.result, cross_doc: extra.cross_doc, statistical: extra.statistical } }
                : t,
            ),
          ),
        onDone: (historyId) =>
          setThread((prev) =>
            prev.map((t) => (t.id === entryId ? { ...t, result: { ...t.result, history_id: historyId } } : t)),
          ),
      },
    );
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (attachments.some(isPending)) {
      setError("Your files are still being processed — ask again in a moment.");
      return;
    }
    const askedQuestion = question;
    // With files attached, the question searches exactly those files.
    const fileIds = attachments.filter((a) => a.stage === "ready").map((a) => a.id);
    const chatOnly = fileIds.length === 0 && !scopeAll && selectedFolders.length === 0;
    const entryId = crypto.randomUUID();

    setLoading(true);
    setError(null);
    setQuestion("");
    setThread((prev) => [
      ...prev,
      {
        id: entryId,
        result: { question: askedQuestion, answer: "", sources: [], grounded: false, cross_doc: null, statistical: null },
        chatOnly,
        folders: selectedFolders,
        fileIds,
      },
    ]);

    try {
      await streamInto(entryId, askedQuestion, selectedFolders, chatOnly, fileIds);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
    } finally {
      setLoading(false);
    }
  };

  const handleRegenerate = async (entryId: string) => {
    const entry = thread.find((t) => t.id === entryId);
    if (!entry) return;
    setRegeneratingId(entryId);
    setError(null);
    setThread((prev) =>
      prev.map((t) =>
        t.id === entryId
          ? { ...t, result: { ...t.result, answer: "", sources: [], grounded: false, cross_doc: null, statistical: null } }
          : t,
      ),
    );
    try {
      // entries restored from history don't remember their files: if they were
      // asked against the chat's attachments (no folders, not chat-only), use
      // the current ones
      const fileIds =
        entry.fileIds ??
        (entry.chatOnly || entry.folders.length > 0
          ? []
          : attachments.filter((a) => a.stage === "ready").map((a) => a.id));
      await streamInto(entryId, entry.result.question, entry.folders, entry.chatOnly, fileIds);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
    } finally {
      setRegeneratingId(null);
    }
  };

  const handleCopy = async (entryId: string, answer: string) => {
    try {
      await navigator.clipboard.writeText(answer);
      setCopiedId(entryId);
      setTimeout(() => setCopiedId((id) => (id === entryId ? null : id)), 1500);
    } catch {
      // clipboard permission denied or unavailable — not worth surfacing an error for
    }
  };

  const handleDragOver = (e: DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.types.includes("Files")) setDragging(true);
  };

  const handleDragLeave = (e: DragEvent) => {
    e.preventDefault();
    if (e.currentTarget === e.target) setDragging(false);
  };

  const handleDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    handleFilesPicked(e.dataTransfer.files);
  };

  const handleDownload = async (entryId: string, result: QueryResult, fmt: "pdf" | "docx") => {
    setDownloading({ id: entryId, fmt });
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
    <>
      <div style={{ paddingBottom: composerHeight + 24 }}>
        <div className="min-w-0 space-y-5">
          {error && <p className={errorText}>{error}</p>}

          {thread.map((entry, index) => (
            // The question sits on the right as a chat bubble; the answer card below stays on the left
            // (its width is capped by the [&>:last-child] rule so the two sides read as different speakers).
            <div key={entry.id} className="space-y-3 [&>:last-child]:max-w-4xl">
              <div className="flex justify-end">
                <p className="max-w-[85%] rounded-2xl rounded-br-md bg-[var(--index-soft)] px-4 py-2.5 text-[14px] leading-relaxed break-words whitespace-pre-wrap text-[var(--ink)] sm:max-w-[70%]">
                  {entry.result.question}
                </p>
              </div>
              <ResultCard
                result={entry.result}
                chatOnly={entry.chatOnly}
                pending={(loading && index === thread.length - 1) || regeneratingId === entry.id}
                actions={
                  <>
                    <button
                      title="Copy answer"
                      onClick={() => handleCopy(entry.id, entry.result.answer)}
                      className={buttonSecondary}
                    >
                      {copiedId === entry.id ? <Check size={12} /> : <Copy size={12} />}
                    </button>
                    <button
                      title="Regenerate"
                      onClick={() => handleRegenerate(entry.id)}
                      disabled={regeneratingId !== null}
                      className={buttonSecondary}
                    >
                      {regeneratingId === entry.id ? (
                        <Loader2 size={12} className="animate-spin" />
                      ) : (
                        <RefreshCw size={12} />
                      )}
                    </button>
                    {(["pdf", "docx"] as const).map((fmt) => (
                      <button
                        key={fmt}
                        title={`Download as ${fmt.toUpperCase()}`}
                        onClick={() => handleDownload(entry.id, entry.result, fmt)}
                        disabled={downloading !== null}
                        className={`${buttonSecondary} inline-flex items-center gap-1.5`}
                      >
                        {downloading?.id === entry.id && downloading.fmt === fmt ? (
                          <Loader2 size={12} className="animate-spin" />
                        ) : (
                          <Download size={12} />
                        )}
                        {fmt.toUpperCase()}
                      </button>
                    ))}
                  </>
                }
              />
            </div>
          ))}
          <div ref={threadEndRef} style={{ scrollMarginBottom: composerHeight + 24 }} />
        </div>
      </div>

      {/* Composer pinned to the bottom of the viewport, ChatGPT/Gemini-style
          — everything above (the answer) scrolls normally in the page;
          this bar stays put so you can always ask the next question
          without scrolling back down. Offset matches Sidebar's
          lg:w-[220px] desktop column so it doesn't run under it. */}
      <div
        ref={composerRef}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`fixed right-0 bottom-0 left-0 border-t bg-[var(--paper)] py-4 transition-colors lg:left-[220px] ${
          dragging ? "border-[var(--index)] bg-[var(--index-soft)]" : "border-[var(--line)]"
        }`}
      >
        {/* px-6 inside mx-auto max-w-7xl, same reasoning as the header above. */}
        <div className="mx-auto max-w-7xl px-6">
          {dragging && (
            <p className={`mb-2 flex items-center gap-1.5 text-xs font-medium text-[var(--index)]`}>
              <Upload size={13} /> Drop to attach to this chat
            </p>
          )}
          {attachments.length > 0 && !dragging && (
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              {attachments.map((a) => (
                <AttachmentChip key={a.id} attachment={a} onRemove={() => removeAttachment(a.id)} />
              ))}
              <button
                type="button"
                onClick={clearAttachments}
                className="inline-flex cursor-pointer items-center gap-1 rounded border border-[var(--line)] px-2 py-1 text-xs text-[var(--ink-soft)] transition-colors hover:border-[var(--danger)] hover:text-[var(--danger)]"
              >
                <Trash2 size={12} /> Clear all
              </button>
              <span className={`text-[11px] ${muted}`}>
                Questions search only these files · a new chat clears them
              </span>
            </div>
          )}
          {uploadError && !dragging && <p className={`mb-2 text-xs ${errorText}`}>{uploadError}</p>}

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
              disabled={uploading}
              title="Attach files to this chat — they're cleared when you start a new chat"
              className="flex h-[42px] w-[42px] shrink-0 cursor-pointer items-center justify-center rounded-md border border-[var(--line)] hover:border-[var(--index)] hover:text-[var(--index)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {uploading ? <Loader2 size={18} className="animate-spin" /> : <Upload size={18} />}
            </button>
            <div className="ask-glow relative flex-1">
              <input
                type="text"
                placeholder={
                  attachments.length > 0
                    ? "Ask about your attached files…"
                    : "e.g. Summarize the Q1 contracts, or how do these numbers relate?"
                }
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                autoFocus
                className={`${input} h-[42px] w-full`}
              />
              {loading && (
                <svg className="ask-glow-container">
                  <rect pathLength="100" className="ask-glow-blur" />
                  <rect pathLength="100" className="ask-glow-line" />
                </svg>
              )}
            </div>
            <button
              type="submit"
              disabled={!question || loading}
              className={`${button} flex h-[42px] shrink-0 items-center justify-center gap-1.5`}
            >
              {loading && <Loader2 size={14} className="animate-spin" />}
              {loading ? "Asking…" : "Ask"}
            </button>
          </form>
        </div>
      </div>
    </>
  );
}
