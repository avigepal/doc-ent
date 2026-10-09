// Thin fetch wrapper for the FastAPI backend. Same-origin by default
// since the built dashboard is served as static files from the API
// itself (see backend README) — set VITE_API_BASE_URL only for local
// dev against a backend on a different port.

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export function getToken(): string | null {
  try {
    return localStorage.getItem("bearer_token");
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem("bearer_token", token);
  } catch {
    // ignore — private browsing / blocked storage; token just won't persist
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem("bearer_token");
  } catch {
    // ignore
  }
}

/** True for the error a cancelled fetch (AbortController) rejects with. */
export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${BASE_URL}${path}`, { ...options, headers });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // response wasn't JSON; keep statusText
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  health: () => request<{ status: string }>("/health"),

  ingestScan: () =>
    request<{
      raw_dir: string;
      total_files: number;
      total_bytes: number;
      by_queue: Record<string, number>;
      by_mime: Record<string, number>;
    }>("/ingest/scan", { method: "POST" }),

  ingestConvert: () =>
    request<{ enqueued: Record<string, number> }>("/ingest/convert", { method: "POST" }),

  ingestSummarize: () =>
    request<{ enqueued: { summarize: number } }>("/ingest/summarize", { method: "POST" }),

  // Top-level folders under raw/ with per-folder ingestion status, for
  // the Ask page's sidebar. Polled while the page is open so "processing"
  // reflects auto-ingest's live progress.
  listFolders: () => request<{ folders: FolderStatus[] }>("/folders"),

  // Creates a new top-level folder under raw/ — lets someone organize the
  // corpus from the dashboard instead of only via SSH/SFTP (e.g. FileZilla)
  // straight into HOST_DATA_DIR/raw on the server.
  createFolder: (name: string) =>
    request<{ created: string }>("/folders", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  // Unified entry point: one question, everything the corpus can say
  // about it — grounded answer plus cross-document/statistical
  // correlation, whichever apply. This is the only query call the
  // dashboard makes; /ask and /correlate still exist on the backend for
  // direct API use but the UI doesn't have separate pages for them.
  // `folders`: empty searches the whole corpus; non-empty scopes
  // retrieval to just those top-level folders under raw/. `chatOnly`:
  // skips retrieval entirely and talks to the model directly — set when
  // the Scope bar has nothing selected (neither "All" nor a folder).
  // `author`/`title`: optional case-insensitive substring filters on the
  // document-intrinsic metadata extracted at convert time, e.g. "files
  // written by this author". `conversationId`: which chat thread this
  // question belongs to (see Ask.tsx) — stored alongside the answer so
  // the sidebar's chat list and thread-restore-on-reload can group by it.
  query: (
    question: string,
    options: {
      folders?: string[];
      author?: string;
      title?: string;
      k?: number;
      chatOnly?: boolean;
      conversationId?: string;
      fileIds?: number[];
    } = {},
  ) =>
    request<QueryResult>("/query", {
      method: "POST",
      body: JSON.stringify({
        question,
        k: options.k,
        folders: options.folders ?? [],
        author: options.author || null,
        title: options.title || null,
        chat_only: options.chatOnly ?? false,
        conversation_id: options.conversationId ?? "",
        file_ids: options.fileIds?.length ? options.fileIds : null,
      }),
    }),

  // Streaming counterpart to query() — same semantics (folders/chatOnly/
  // author/title), but the main answer arrives token-by-token through
  // Server-Sent Events instead of as one blocking JSON response. fetch +
  // a manual ReadableStream read, not EventSource, since EventSource
  // can't send a POST body. Resolves once the stream's "done" event
  // arrives; each piece is delivered through the handlers as it comes
  // in. See backend/app/main.py:query_stream_endpoint for the event
  // shapes (meta/token/extra/done/error).
  queryStream: async (
    question: string,
    options: {
      folders?: string[];
      author?: string;
      title?: string;
      k?: number;
      chatOnly?: boolean;
      conversationId?: string;
      fileIds?: number[];
      // regenerating an earlier reply: edits continue from what came before it
      beforeHistoryId?: number;
      // "report": read every document in scope and write a full report, whatever the message says
      mode?: "report";
    } = {},
    handlers: {
      onMeta?: (meta: { sources: string[]; grounded: boolean }) => void;
      onToken?: (text: string) => void;
      onExtra?: (extra: {
        cross_doc: { answer: string; sources: string[] } | null;
        statistical: { answer: string; correlation_summary: string } | null;
      }) => void;
      onDone?: (historyId: number) => void;
      // what the app decided to do with the message, and progress while it works
      onRoute?: (route: { action: string; label: string }) => void;
      onStatus?: (text: string) => void;
      onFile?: (file: GeneratedFile) => void;
    } = {},
    // aborting stops reading, and the server stops generating when the connection closes
    signal?: AbortSignal,
  ): Promise<void> => {
    const token = getToken();
    const headers = new Headers();
    headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const response = await fetch(`${BASE_URL}/query/stream`, {
      method: "POST",
      headers,
      signal,
      body: JSON.stringify({
        question,
        k: options.k,
        folders: options.folders ?? [],
        author: options.author || null,
        title: options.title || null,
        chat_only: options.chatOnly ?? false,
        conversation_id: options.conversationId ?? "",
        file_ids: options.fileIds?.length ? options.fileIds : null,
        before_history_id: options.beforeHistoryId ?? null,
        mode: options.mode ?? null,
      }),
    });

    if (!response.ok || !response.body) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        detail = body.detail ?? detail;
      } catch {
        // not JSON; keep statusText
      }
      throw new ApiError(response.status, detail);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sepIndex: number;
      while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
        const rawEvent = buffer.slice(0, sepIndex);
        buffer = buffer.slice(sepIndex + 2);
        const lines = rawEvent.split("\n");
        const eventLine = lines.find((l) => l.startsWith("event: "));
        const dataLine = lines.find((l) => l.startsWith("data: "));
        if (!eventLine || !dataLine) continue;

        const eventName = eventLine.slice("event: ".length);
        const data = JSON.parse(dataLine.slice("data: ".length));

        if (eventName === "meta") handlers.onMeta?.(data);
        else if (eventName === "token") handlers.onToken?.(data.text);
        else if (eventName === "extra") handlers.onExtra?.(data);
        else if (eventName === "route") handlers.onRoute?.(data);
        else if (eventName === "status") handlers.onStatus?.(data.text);
        else if (eventName === "file") handlers.onFile?.(data);
        else if (eventName === "done") handlers.onDone?.(data.history_id);
        else if (eventName === "error") throw new ApiError(500, data.message ?? "stream error");
      }
    }
  },

  // ChatGPT/OpenWebUI-style "+" attach: converts the given files on the
  // spot and answers strictly from them, bypassing the corpus/pgvector
  // entirely. Multipart, not JSON, since it carries binary files.
  queryUpload: async (
    question: string,
    files: File[],
    options: { author?: string; title?: string } = {},
  ): Promise<QueryResult> => {
    const token = getToken();
    const form = new FormData();
    form.append("question", question);
    for (const f of files) form.append("files", f);
    if (options.author) form.append("author", options.author);
    if (options.title) form.append("title", options.title);

    const headers = new Headers();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    // Content-Type intentionally NOT set here — the browser fills in the
    // correct multipart boundary itself only when left unset.

    const response = await fetch(`${BASE_URL}/query/upload`, {
      method: "POST",
      headers,
      body: form,
    });

    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        detail = body.detail ?? detail;
      } catch {
        // not JSON; keep statusText
      }
      throw new ApiError(response.status, detail);
    }

    return (await response.json()) as QueryResult;
  },

  // The Ask page's "+" button: persists the picked file(s) into a fixed
  // raw/uploads/ folder — never into whatever folder is selected in
  // Scope — and kicks off the normal convert -> summarize pipeline right
  // away. Multipart, not JSON, since it carries binary files. Distinct
  // from queryUpload above, which never saves anything.
  uploadFiles: async (
    files: File[],
  ): Promise<{
    folder: string;
    uploaded: string[];
    file_ids: number[];
    registered: number;
    convert_enqueued: Record<string, number>;
  }> => {
    const token = getToken();
    const form = new FormData();
    for (const f of files) form.append("files", f);

    const headers = new Headers();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    // Content-Type intentionally NOT set here — same reason as queryUpload.

    const response = await fetch(`${BASE_URL}/ingest/upload`, {
      method: "POST",
      headers,
      body: form,
    });

    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        detail = body.detail ?? detail;
      } catch {
        // not JSON; keep statusText
      }
      throw new ApiError(response.status, detail);
    }

    return await response.json();
  },

  // Live pipeline state for the Progress page — polled while it's open.
  ingestionProgress: () => request<IngestionProgress>("/ingestion/progress"),

  stats: () => request<OverviewStats>("/stats"),

  // Drop zone on the Documents page: adds files to a corpus folder (created if
  // new). Multipart, so no JSON Content-Type — the browser sets the boundary.
  uploadDocuments: async (
    folder: string,
    files: File[],
  ): Promise<{ folder: string; uploaded: string[]; file_ids: number[] }> => {
    const token = getToken();
    const form = new FormData();
    form.append("folder", folder);
    for (const f of files) form.append("files", f);

    const headers = new Headers();
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const response = await fetch(`${BASE_URL}/documents/upload`, { method: "POST", headers, body: form });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        detail = body.detail ?? detail;
      } catch {
        // not JSON; keep statusText
      }
      throw new ApiError(response.status, detail);
    }
    return response.json();
  },

  deleteDocuments: (ids: number[]) =>
    request<{ deleted: number }>("/documents/delete", { method: "POST", body: JSON.stringify({ ids }) }),

  // "Compare across documents": runs the cross-document analysis for a finished answer.
  // cross_doc is null when fewer than two documents match well enough to compare.
  compareAcrossDocuments: (
    question: string,
    options: { folders?: string[]; fileIds?: number[]; historyId?: number } = {},
  ) =>
    request<{ cross_doc: { answer: string; sources: string[] } | null }>("/query/compare", {
      method: "POST",
      body: JSON.stringify({
        question,
        folders: options.folders ?? [],
        file_ids: options.fileIds?.length ? options.fileIds : null,
        history_id: options.historyId ?? null,
      }),
    }),

  // Related questions to offer under a finished answer. Never throws on the model's side:
  // the server answers with an empty list when it couldn't think of any.
  suggestFollowUps: (question: string, answer: string, sources: string[], historyId?: number) =>
    request<{ suggestions: string[] }>("/query/suggest", {
      method: "POST",
      body: JSON.stringify({ question, answer, sources, history_id: historyId ?? null }),
    }),

  // Deletes every file in one folder (the folder itself stays).
  clearFolder: (name: string) =>
    request<{ deleted: number }>(`/folders/${encodeURIComponent(name)}/clear`, { method: "POST" }),

  reprocessDocuments: (ids: number[]) =>
    request<{ reprocessed: number }>("/documents/reprocess", { method: "POST", body: JSON.stringify({ ids }) }),

  listDocuments: (opts: { folder?: string; status?: string; q?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (opts.limit) params.set("limit", String(opts.limit));
    if (opts.folder) params.set("folder", opts.folder);
    if (opts.status) params.set("status", opts.status);
    if (opts.q) params.set("q", opts.q);
    const qs = params.toString();
    return request<{ documents: DocumentRow[]; total: number }>(
      qs ? `/documents/list?${qs}` : "/documents/list",
    );
  },

  listQueryHistory: () => request<{ queries: QueryHistorySummary[] }>("/history/queries"),

  // detail=true returns every answer body in one call, not just
  // metadata — used by the Ask page to restore one chat thread
  // (conversationId) on reload without fetching each entry individually.
  listQueryHistoryDetail: (limit = 30, conversationId?: string) => {
    const params = new URLSearchParams({ limit: String(limit), detail: "true" });
    if (conversationId) params.set("conversation_id", conversationId);
    return request<{ queries: QueryHistoryDetail[] }>(`/history/queries?${params}`);
  },

  // The sidebar's chat list — one row per conversation, titled by its
  // opening question, newest-active first. See backend app/history/
  // store.py:list_conversations.
  listConversations: (limit = 50) =>
    request<{ conversations: ConversationSummary[] }>(`/history/conversations?limit=${limit}`),

  setConversationPinned: (conversationId: string, pinned: boolean) =>
    request<{ conversation_id: string; pinned: boolean }>(
      `/history/conversations/${encodeURIComponent(conversationId)}/pin`,
      { method: "PUT", body: JSON.stringify({ pinned }) },
    ),

  deleteConversation: (conversationId: string) =>
    request<{ deleted: string; messages: number }>(
      `/history/conversations/${encodeURIComponent(conversationId)}`,
      { method: "DELETE" },
    ),

  // A window of one document's sections for the viewer: around a section, or around
  // the one that matches `q` best. See backend/app/search/viewer.py.
  getFileSections: (o: { fileId?: number; path?: string; around?: number; span?: number; q?: string; phrase?: boolean }) => {
    const params = new URLSearchParams();
    if (o.fileId !== undefined) params.set("file_id", String(o.fileId));
    if (o.path) params.set("path", o.path);
    if (o.around !== undefined) params.set("around", String(o.around));
    if (o.span !== undefined) params.set("span", String(o.span));
    if (o.q) params.set("q", o.q);
    if (o.phrase) params.set("phrase", "true");
    return request<FileSections>(`/files/sections?${params}`);
  },

  getQueryHistory: (id: number) => request<QueryHistoryDetail>(`/history/queries/${id}`),

  deleteQueryHistory: (id: number) =>
    request<{ deleted: number }>(`/history/queries/${id}`, { method: "DELETE" }),

  // Where freshly uploaded files are on their way to being searchable; the
  // ids come from uploadFiles' response. See backend upload_status.py.
  uploadStatus: (ids: number[]) =>
    request<{ files: UploadStatusFile[]; done: boolean }>(`/ingest/upload/status?ids=${ids.join(",")}`),

  // Uploads are the current chat's attachments: a new chat clears them all,
  // and a chip's X removes one. Only the uploads folder is ever touched.
  clearUploads: () => request<{ deleted: number }>("/ingest/uploads", { method: "DELETE" }),
  deleteUpload: (id: number) => request<{ deleted: number }>(`/ingest/uploads/${id}`, { method: "DELETE" }),

  listExportHistory: () => request<{ exports: ExportHistoryRow[] }>("/history/exports"),
};

export type UploadStage = "queued" | "converting" | "indexing" | "ready" | "failed" | "unsupported";

export interface UploadStatusFile {
  id: number;
  name: string;
  stage: UploadStage;
  detail: string | null;
}

export interface FolderStatus {
  name: string;
  total_files: number;
  discovered: number;
  converted: number;
  summarized: number;
  unsupported: number;
  processing: boolean;
  has_failures: boolean;
}

/** A file the assistant created while answering (the edit feature). */
export interface GeneratedFile {
  id: number;
  name: string;
  fmt: string;
  size_bytes: number;
  source: string;
  download_path: string;
}

export interface QueryResult {
  question: string;
  answer: string;
  sources: string[];
  grounded: boolean;
  cross_doc: { answer: string; sources: string[] } | null;
  statistical: { answer: string; correlation_summary: string } | null;
  files?: GeneratedFile[];
  history_id?: number;
  /** Related questions offered under the answer. */
  suggestions?: string[];
}

export interface IngestionProgress {
  summaries_enabled: boolean;
  totals: { files: number; unsupported: number; discovered: number; converted: number; summarized: number; running: number; failed: number };
  folders: { name: string; total: number; converted: number; summarized: number; running: number; failed: number }[];
  active: { file: string; folder: string; stage: string; size_bytes: number; elapsed_seconds: number }[];
  failures: { file: string; folder: string; stage: string; error: string | null; retries: number; at: string | null }[];
  recent: { file: string; folder: string; stage: string; finished_at: string | null }[];
  /** Files still moving through the pipeline, one by one, plus per-stage counts. */
  pipeline?: {
    queue: { queued: number; converting: number; indexing: number };
    files: PipelineFile[];
    /** All unfinished files; `files` stops at the first 100. */
    files_total: number;
    /** Files whose indexing finished in the last couple of minutes (drives the "ready" toast). */
    recently_ready: { id: number; file: string; folder: string; finished_at: string | null }[];
  };
}

export type PipelineStage = "queued" | "converting" | "indexing";

export interface PipelineFile {
  id: number;
  file: string;
  folder: string;
  stage: PipelineStage;
  size_bytes: number;
  /** When the file entered its current stage. */
  since: string | null;
  elapsed_seconds: number;
}

export interface OverviewStats {
  documents: {
    total: number;
    discovered: number;
    converted: number;
    summarized: number;
    failed: number;
    unsupported: number;
  };
  chunks: number;
  storage_bytes: number;
  folders: number;
  queries_total: number;
  exports_total: number;
  recent_activity: { type: string; label: string; at: string }[];
}

export interface DocumentRow {
  id: number;
  path: string;
  title: string | null;
  author: string | null;
  mime_type: string;
  size_bytes: number;
  page_count: number | null;
  status: string;
  queue: string;
  discovered_at: string | null;
  doc_created_at: string | null;
}

export interface QueryHistorySummary {
  id: number;
  question: string;
  grounded: boolean;
  source_count: number;
  filter_folders: string[];
  filter_author: string | null;
  filter_title: string | null;
  attached_filenames: string[];
  chat_only: boolean;
  conversation_id: string;
  /** How the reply was made: "keyword", "search", "chat", "edit", "catalog"; "" for older rows. */
  route?: string;
  created_at: string | null;
}

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  last_at: string | null;
  message_count: number;
  pinned: boolean;
}

export interface FileSection {
  index: number;
  heading: string;
  text: string;
}

export interface FileSections {
  file: { id: number; name: string; path: string };
  total: number;
  anchor: number;
  has_before: boolean;
  has_after: boolean;
  terms: string[];
  phrase: boolean;
  sections: FileSection[];
}

export interface QueryHistoryDetail extends QueryHistorySummary {
  files?: GeneratedFile[];
  suggestions?: string[];
  answer: string;
  sources: string[];
  cross_doc: { answer: string; sources: string[] } | null;
  statistical: { answer: string; correlation_summary: string } | null;
}

export interface ExportHistoryRow {
  id: number;
  query_history_id: number | null;
  filename: string;
  fmt: string;
  size_bytes: number;
  created_at: string | null;
}

/** Downloads a query result as a file (PDF by default) — POSTs the
 * content to /export, which streams the converted file straight back,
 * and triggers the browser's normal save-file flow. Not a separate
 * "export" page/step: this is called directly from the Ask page's
 * result card. */
export async function downloadExport(
  content: string,
  filename: string,
  fmt: "pdf" | "docx" = "pdf",
  historyId?: number,
): Promise<void> {
  const token = getToken();
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${BASE_URL}/export`, {
    method: "POST",
    headers,
    body: JSON.stringify({ content, filename, fmt, history_id: historyId ?? null }),
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // not JSON; keep statusText
    }
    throw new ApiError(response.status, detail);
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${filename}.${fmt}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** Re-downloads a previously generated export straight from the server
 * rather than re-rendering it — the file is still on disk under
 * exports/. Returns an error message if the server no longer has it. */
export async function downloadHistoryExport(row: ExportHistoryRow): Promise<void> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${BASE_URL}/history/exports/${row.id}/download`, { headers });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // not JSON; keep statusText
    }
    throw new ApiError(response.status, detail);
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${row.filename}.${row.fmt}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** Saves a file the assistant generated, straight from the server (it is kept
 * under outputs/, and also listed in History). The request needs the auth
 * header, so this fetches it and hands the browser a blob to save rather than
 * linking to the URL. */
export async function downloadGeneratedFile(file: GeneratedFile): Promise<void> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${BASE_URL}${file.download_path}`, { headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // not JSON; keep statusText
    }
    throw new ApiError(response.status, detail);
  }

  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = file.name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
