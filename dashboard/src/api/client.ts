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
  // retrieval to just those top-level folders under raw/. `author`/
  // `title`: optional case-insensitive substring filters on the
  // document-intrinsic metadata extracted at convert time, e.g. "files
  // written by this author".
  query: (question: string, options: { folders?: string[]; author?: string; title?: string; k?: number } = {}) =>
    request<QueryResult>("/query", {
      method: "POST",
      body: JSON.stringify({
        question,
        k: options.k ?? 8,
        folders: options.folders ?? [],
        author: options.author || null,
        title: options.title || null,
      }),
    }),

  // ChatGPT/OpenWebUI-style "+" attach: converts the given files on the
  // spot and answers strictly from them, bypassing the corpus/pgvector
  // entirely. Multipart, not JSON, since it carries binary files.
  queryUpload: async (question: string, files: File[]): Promise<QueryResult> => {
    const token = getToken();
    const form = new FormData();
    form.append("question", question);
    for (const f of files) form.append("files", f);

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

  stats: () => request<OverviewStats>("/stats"),

  listDocuments: (opts: { folder?: string; status?: string; q?: string } = {}) => {
    const params = new URLSearchParams();
    if (opts.folder) params.set("folder", opts.folder);
    if (opts.status) params.set("status", opts.status);
    if (opts.q) params.set("q", opts.q);
    const qs = params.toString();
    return request<{ documents: DocumentRow[]; total: number }>(
      qs ? `/documents?${qs}` : "/documents",
    );
  },

  listQueryHistory: () => request<{ queries: QueryHistorySummary[] }>("/history/queries"),

  getQueryHistory: (id: number) => request<QueryHistoryDetail>(`/history/queries/${id}`),

  deleteQueryHistory: (id: number) =>
    request<{ deleted: number }>(`/history/queries/${id}`, { method: "DELETE" }),

  listExportHistory: () => request<{ exports: ExportHistoryRow[] }>("/history/exports"),
};

export interface FolderStatus {
  name: string;
  total_files: number;
  discovered: number;
  converted: number;
  summarized: number;
  processing: boolean;
  has_failures: boolean;
}

export interface QueryResult {
  question: string;
  answer: string;
  sources: string[];
  grounded: boolean;
  cross_doc: { answer: string; sources: string[] } | null;
  statistical: { answer: string; correlation_summary: string } | null;
  history_id?: number;
}

export interface OverviewStats {
  documents: {
    total: number;
    discovered: number;
    converted: number;
    summarized: number;
    failed: number;
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
  created_at: string | null;
}

export interface QueryHistoryDetail extends QueryHistorySummary {
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
