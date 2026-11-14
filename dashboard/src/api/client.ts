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

/** One email message, as the Emails view lists it (a message is listed once, with all its recipients). */
export interface EmailMessage {
  file_id: number;
  file: string;
  folder: string;
  subject: string;
  sent_at: string | null;
  sender: { name: string; addr: string };
  recipients: string[];
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** fetch for every call that needs a sign-in. A 401 means the session ended (it expires, and a new
 * login password signs everybody out): the saved token is dropped and the login page opens, instead
 * of leaving the person on a page of errors. */
async function authFetch(input: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(input, init);
  if (response.status === 401) {
    clearToken();
    if (window.location.pathname !== "/login") window.location.assign("/login");
  }
  return response;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await authFetch(`${BASE_URL}${path}`, { ...options, headers });

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

/** A chat model the model server knows, and whether it is loaded now. */
export interface TextModel {
  id: string;
  loaded: boolean;
}

export const api = {
  health: () => request<{ status: string }>("/health"),

  listTextModels: () => request<{ models: TextModel[] }>("/llm/text-models"),
  loadTextModel: (id: string) =>
    request<{ loaded: string }>(`/llm/text-models/${encodeURIComponent(id)}/load`, { method: "POST" }),
  unloadTextModel: (id: string) =>
    request<{ unloaded: string }>(`/llm/text-models/${encodeURIComponent(id)}/unload`, { method: "POST" }),

  /** Checks the login password on the server. Returns the session token to send on every later call;
   * throws ApiError (401 wrong password, 429 too many tries) otherwise. */
  login: async (password: string): Promise<string> => {
    const response = await fetch(`${BASE_URL}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        detail = (await response.json()).detail ?? detail;
      } catch {
        // not JSON; keep statusText
      }
      throw new ApiError(response.status, detail);
    }
    return (await response.json()).token as string;
  },

  // Rebuilds an edited Word file with only the accepted changes (indices from its change list).
  updateFileChanges: (fileId: number, accepted: number[]) =>
    request<GeneratedFile>(`/history/exports/${fileId}/changes`, {
      method: "POST",
      body: JSON.stringify({ accepted }),
    }),

  // Stops a full report that is running in a worker (the Stop button).
  cancelReportJob: (jobId: string) =>
    request<{ stopping: boolean }>(`/report-jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }),

  // folder "" = all of raw/; otherwise that folder and its subfolders only
  ingestScan: (folder = "") =>
    request<{
      raw_dir: string;
      folder: string;
      total_files: number;
      total_bytes: number;
      by_queue: Record<string, number>;
      by_mime: Record<string, number>;
    }>(`/ingest/scan?folder=${encodeURIComponent(folder)}`, { method: "POST" }),

  ingestConvert: (folder = "") =>
    request<{ enqueued: Record<string, number> }>(`/ingest/convert?folder=${encodeURIComponent(folder)}`, {
      method: "POST",
    }),

  ingestSummarize: () =>
    request<{ enqueued: { summarize: number } }>("/ingest/summarize", { method: "POST" }),

  // Top-level folders under raw/ with per-folder ingestion status, for
  // the Ask page's sidebar. Polled while the page is open so "processing"
  // reflects auto-ingest's live progress.
  listFolders: () => request<{ folders: FolderStatus[] }>("/folders"),

  // One level of the Documents page's folder tree: the folders directly under `parent` ("" = top).
  documentsTree: (parent = "") =>
    request<DocumentsTreeLevel>(`/documents/tree?parent=${encodeURIComponent(parent)}`),

  // Creates a new top-level folder under raw/ — lets someone organize the
  // corpus from the dashboard instead of only via SSH/SFTP (e.g. FileZilla)
  // straight into HOST_DATA_DIR/raw on the server.
  createFolder: (name: string) =>
    request<{ created: string }>("/folders", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  // Saved correlation views (the Dashboards page). A view's last_result is its most recent finished run.
  listViews: () => request<{ views: SavedView[] }>("/views"),
  createView: (view: { name: string; question: string; folders: string[]; interval_hours: number | null }) =>
    request<SavedView>("/views", { method: "POST", body: JSON.stringify(view) }),
  deleteView: (id: number) => request<{ deleted: number }>(`/views/${id}`, { method: "DELETE" }),
  runView: (id: number) => request<{ job_id: string; state: string }>(`/views/${id}/run`, { method: "POST" }),
  // Background correlation: start, then poll until state is SUCCESS or FAILURE.
  startCorrelateJob: (question: string, folders: string[]) =>
    request<{ job_id: string; state: string }>("/correlate/jobs", {
      method: "POST",
      body: JSON.stringify({ question, folders }),
    }),
  correlateJob: (jobId: string) =>
    request<{ job_id: string; state: string; result?: CorrelateResult; error?: string }>(`/correlate/jobs/${jobId}`),

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
      // "report_confirmed": a report over a big folder after the user saw the plan and pressed "Read them all"
      // "spreadsheet": read every document in scope for the rows the message asks for and build an .xlsx
      mode?: "report" | "edit" | "spreadsheet" | "correlate" | "report_confirmed";
    } = {},
    handlers: {
      onMeta?: (meta: { sources: string[]; grounded: boolean }) => void;
      onToken?: (text: string) => void;
      onExtra?: (extra: {
        cross_doc: CrossDocFindings | null;
        statistical: StatisticalFindings | null;
        graph?: RelationGraph | null;
      }) => void;
      onDone?: (historyId: number) => void;
      // what the app decided to do with the message, and progress while it works
      onRoute?: (route: { action: string; label: string }) => void;
      onStatus?: (text: string) => void;
      onFile?: (file: GeneratedFile) => void;
      // a report over a big folder is running in a worker; it keeps going if the tab is closed
      onJob?: (jobId: string) => void;
    } = {},
    // aborting stops reading, and the server stops generating when the connection closes
    signal?: AbortSignal,
  ): Promise<void> => {
    const token = getToken();
    const headers = new Headers();
    headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const response = await authFetch(`${BASE_URL}/query/stream`, {
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
        mode: options.mode === "report_confirmed" ? "report" : (options.mode ?? null),
        confirm_report: options.mode === "report_confirmed",
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
        else if (eventName === "job") handlers.onJob?.(data.job_id);
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

    const response = await authFetch(`${BASE_URL}/query/upload`, {
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

    const response = await authFetch(`${BASE_URL}/ingest/upload`, {
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
  // folder "" = all of raw/; otherwise only that folder and its subfolders
  ingestionProgress: (pipelineLimit = 100, folder = "") =>
    request<IngestionProgress>(
      `/ingestion/progress?pipeline_limit=${pipelineLimit}&folder=${encodeURIComponent(folder)}`,
    ),
  stopPipeline: () => request<{ paused: boolean; purged: number }>("/ingest/stop", { method: "POST" }),
  resumePipeline: () => request<{ paused: boolean }>("/ingest/resume", { method: "POST" }),

  stats: () => request<OverviewStats>("/stats"),

  // Drop zone on the Documents page: adds files to a corpus folder (created if
  // new). Multipart, so no JSON Content-Type — the browser sets the boundary.
  // Sends the files as one request and reports how much of it has gone out
  // (0 to 1). XMLHttpRequest is used because fetch can't report upload progress.
  uploadDocuments: (
    folder: string,
    files: File[],
    onProgress?: (fraction: number) => void,
  ): Promise<{ folder: string; uploaded: string[]; file_ids: number[] }> =>
    new Promise((resolve, reject) => {
      const token = getToken();
      const form = new FormData();
      form.append("folder", folder);
      for (const f of files) form.append("files", f);

      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE_URL}/documents/upload`);
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
      };
      xhr.onload = () => {
        if (xhr.status === 401) {
          // same as authFetch: the session ended, so go to the login page
          clearToken();
          if (window.location.pathname !== "/login") window.location.assign("/login");
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText));
          return;
        }
        let detail = xhr.statusText;
        try {
          detail = JSON.parse(xhr.responseText).detail ?? detail;
        } catch {
          // not JSON; keep statusText
        }
        reject(new ApiError(xhr.status, detail));
      };
      xhr.onerror = () => reject(new ApiError(0, "The upload was interrupted. Check the connection and try again."));
      xhr.send(form);
    }),

  deleteDocuments: (ids: number[]) =>
    request<{ deleted: number }>("/documents/delete", { method: "POST", body: JSON.stringify({ ids }) }),

  // "Compare across documents": runs the cross-document analysis for a finished answer.
  // cross_doc is null when fewer than two documents match well enough to compare.
  compareAcrossDocuments: (
    question: string,
    options: { folders?: string[]; fileIds?: number[]; historyId?: number } = {},
  ) =>
    request<{ cross_doc: CrossDocFindings | null }>("/query/compare", {
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
  deleteMatching: (filters: { folder?: string; status?: string; q?: string }) =>
    request<{ deleted: number }>("/documents/delete-matching", { method: "POST", body: JSON.stringify(filters) }),

  clearFolder: (name: string) =>
    request<{ deleted: number }>(`/folders/${encodeURIComponent(name)}/clear`, { method: "POST" }),

  reprocessDocuments: (ids: number[]) =>
    request<{ reprocessed: number }>("/documents/reprocess", { method: "POST", body: JSON.stringify({ ids }) }),

  listDocuments: (
    opts: { folder?: string; status?: string; q?: string; limit?: number; offset?: number; sort?: string; dir?: "asc" | "desc" } = {},
  ) => {
    const params = new URLSearchParams();
    if (opts.limit) params.set("limit", String(opts.limit));
    if (opts.offset) params.set("offset", String(opts.offset));
    if (opts.sort) params.set("sort", opts.sort);
    if (opts.dir) params.set("dir", opts.dir);
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
  // The document's original file (PDF, image, or any other type as a download).
  // Fetched with the auth header, so the viewer gets a blob it can show.
  fileOriginal: async (o: { fileId?: number; path?: string }): Promise<Blob> => {
    const params = new URLSearchParams();
    if (o.fileId !== undefined) params.set("file_id", String(o.fileId));
    if (o.path) params.set("path", o.path);
    const token = getToken();
    const headers = new Headers();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await authFetch(`${BASE_URL}/files/original?${params}`, { headers });
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
    return response.blob();
  },

  // Emails filtered by sender, recipient, sent date (YYYY-MM-DD, both ends inclusive) and folder.
  listEmails: (o: { sender?: string; recipient?: string; after?: string; before?: string; folder?: string; offset?: number; limit?: number }) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(o)) {
      if (value !== undefined && value !== "") params.set(key, String(value));
    }
    return request<{ total: number; messages: EmailMessage[] }>(`/emails/messages?${params}`);
  },

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

export interface TreeFolder {
  name: string;
  /** Path from the top, such as "pgt/reports": what the folder filter takes. */
  path: string;
  /** Files inside, subfolders included. */
  total: number;
  pending: number;
  failed: number;
  has_subfolders: boolean;
}

export interface DocumentsTreeLevel {
  parent: string;
  folders: TreeFolder[];
  /** Files sitting directly in `parent`, not in a subfolder. */
  files_here: number;
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
  /** What an edit changed in a Word file, one entry per change, each of which can be accepted or rejected. */
  changes?: FileChange[];
}

export interface FileChange {
  index: number;
  /** "replaced" | "inserted" | "deleted" | "cell" */
  op: string;
  /** Where it is, e.g. "paragraph 3" or "new heading after block 2". */
  label: string;
  before: string;
  after: string;
  accepted: boolean;
}

/** A saved correlation view; last_result is the latest finished run (null until it has run once). */
export type SavedView = {
  id: number;
  name: string;
  question: string;
  folders: string[];
  interval_hours: number | null;
  last_result: (CorrelateResult & { question: string }) | null;
  last_run_at: string | null;
};

/** What /correlate and a finished view run return. */
export type CorrelateResult = {
  question?: string;
  cross_doc: CrossDocFindings | null;
  statistical: StatisticalFindings | null;
};

/** One Pearson correlation, computed by the backend within a single table. */
export type CorrelationPair = { table: string; first: string; second: string; r: number; rows: number };

export type StatisticalFindings = { answer: string; correlation_summary: string; pairs?: CorrelationPair[] };

/** sources = the documents the answer cites; retrieved = every document that was compared. */
export type CrossDocFindings = { answer: string; sources: string[]; retrieved?: string[] };

/** The people in an email relation chart: nodes are people, edges are the emails between them. */
export interface RelationGraph {
  mode: "between" | "around";
  nodes: { id: string; label: string; person: boolean }[];
  edges: {
    source: string;
    target: string;
    emails: number;
    /** emails from source to target */
    forward: number;
    /** emails from target to source */
    backward: number;
    first: string;
    last: string;
    subjects: string[];
  }[];
  /** named people with no email in the selected documents */
  missing: string[];
}

export interface QueryResult {
  question: string;
  answer: string;
  sources: string[];
  grounded: boolean;
  cross_doc: CrossDocFindings | null;
  statistical: StatisticalFindings | null;
  graph?: RelationGraph | null;
  files?: GeneratedFile[];
  history_id?: number;
  /** Related questions offered under the answer. */
  suggestions?: string[];
}

export interface IngestionProgress {
  summaries_enabled: boolean;
  /** Stop was pressed: nothing new is queued until Resume. */
  paused: boolean;
  /** The file scan: active while it is reading a folder, with how many files it has found so far. */
  scan: { active: boolean; files_seen: number; started_at: string | null; finished_at?: string } | null;
  /** The Docling server's health. `online` is null until the first check has finished. */
  docling: { url: string; online: boolean | null; latency_ms: number | null; error: string | null; checked_at: string | null };
  totals: { files: number; unsupported: number; discovered: number; converted: number; summarized: number; running: number; failed: number };
  folders: { name: string; total: number; converted: number; summarized: number; running: number; failed: number }[];
  active: { file: string; folder: string; stage: string; size_bytes: number; elapsed_seconds: number }[];
  failures: { file: string; folder: string; stage: string; error: string | null; retries: number; at: string | null }[];
  /** All failed jobs; `failures` lists the newest 200 of them. */
  failures_total: number;
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
  cross_doc: CrossDocFindings | null;
  statistical: StatisticalFindings | null;
  graph?: RelationGraph | null;
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
/** "csv" and "xlsx" export the answer's tables (a CSV of several tables arrives as a .zip). */
export type ExportFormat = "pdf" | "docx" | "xlsx" | "csv";

export async function downloadExport(
  content: string,
  filename: string,
  fmt: ExportFormat = "pdf",
  historyId?: number,
): Promise<void> {
  const token = getToken();
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await authFetch(`${BASE_URL}/export`, {
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
  // the server names the file: a CSV of several tables is really a .zip
  const named = /filename="?([^";]+)"?/i.exec(response.headers.get("content-disposition") ?? "");
  link.download = named?.[1] ?? `${filename}.${fmt}`;
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

  const response = await authFetch(`${BASE_URL}/history/exports/${row.id}/download`, { headers });

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

  const response = await authFetch(`${BASE_URL}${file.download_path}`, { headers });
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
