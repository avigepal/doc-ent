import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  File,
  FileArchive,
  FileCode,
  FileImage,
  FileSpreadsheet,
  FileText,
  Loader2,
  Mail,
  Presentation,
  RefreshCw,
  Trash2,
  UploadCloud,
  X,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { api, ApiError, type DocumentRow, type FolderStatus } from "../api/client";
import { button, buttonSecondary, errorText, input, label, muted, pageTitle, tableCell, tableHeader } from "../ui";

const STATUSES = ["discovered", "converted", "summarized", "failed", "unsupported"];

/** The list endpoint caps a page at 500 rows; the table sorts what it loaded. */
const PAGE_LIMIT = 500;
/** While any file is still being converted, the list refreshes on this interval. */
const REFRESH_MS = 4000;

type SortKey = "path" | "title" | "author" | "page_count" | "size_bytes" | "status" | "discovered_at";

const STATUS_CHIP: Record<string, { text: string; tone: string }> = {
  discovered: { text: "Pending", tone: "border-[var(--locator)] bg-[var(--locator-soft)] text-[var(--locator)]" },
  converted: { text: "Converted", tone: "border-[var(--index)] bg-[var(--index-soft)] text-[var(--index)]" },
  summarized: { text: "Summarized", tone: "border-[var(--signal)] text-[var(--signal)]" },
  failed: { text: "Failed", tone: "border-[var(--danger)] text-[var(--danger)]" },
  unsupported: { text: "Unsupported", tone: "border-[var(--line)] text-[var(--ink-soft)]" },
};

function StatusChip({ status }: { status: string }) {
  const chip = STATUS_CHIP[status] ?? { text: status, tone: "border-[var(--line)] text-[var(--ink-soft)]" };
  return (
    <span
      title={status}
      className={`font-mono inline-block rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${chip.tone}`}
    >
      {chip.text}
    </span>
  );
}

const ICON_BY_EXTENSION: Record<string, LucideIcon> = {
  pdf: FileText,
  doc: FileText,
  docx: FileText,
  txt: FileText,
  md: FileText,
  rtf: FileText,
  odt: FileText,
  xls: FileSpreadsheet,
  xlsx: FileSpreadsheet,
  csv: FileSpreadsheet,
  ods: FileSpreadsheet,
  ppt: Presentation,
  pptx: Presentation,
  png: FileImage,
  jpg: FileImage,
  jpeg: FileImage,
  gif: FileImage,
  webp: FileImage,
  bmp: FileImage,
  tif: FileImage,
  tiff: FileImage,
  eml: Mail,
  msg: Mail,
  mbox: Mail,
  zip: FileArchive,
  tar: FileArchive,
  gz: FileArchive,
  json: FileCode,
  xml: FileCode,
  html: FileCode,
  py: FileCode,
  js: FileCode,
  ts: FileCode,
};

function FileIcon({ path }: { path: string }) {
  const dot = path.lastIndexOf(".");
  const Icon = (dot === -1 ? undefined : ICON_BY_EXTENSION[path.slice(dot + 1).toLowerCase()]) ?? File;
  return <Icon size={15} className="shrink-0 text-[var(--ink-soft)]" aria-hidden="true" />;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = -1;
  do {
    value /= 1024;
    unitIndex++;
  } while (value >= 1024 && unitIndex < units.length - 1);
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

function formatAdded(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** Rows with no value for the sorted column always go last, in either direction. */
function compareRows(a: DocumentRow, b: DocumentRow, key: SortKey, dir: 1 | -1): number {
  const av = a[key];
  const bv = b[key];
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;
  if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
  return String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: "base" }) * dir;
}

function SortHeader({
  name,
  sortKey,
  active,
  dir,
  onSort,
  className = "",
}: {
  name: string;
  sortKey: SortKey;
  active: SortKey;
  dir: 1 | -1;
  onSort: (key: SortKey) => void;
  className?: string;
}) {
  const isActive = active === sortKey;
  const Arrow = !isActive ? ArrowUpDown : dir === 1 ? ArrowUp : ArrowDown;
  return (
    <th className={`${tableHeader} ${className}`} aria-sort={isActive ? (dir === 1 ? "ascending" : "descending") : "none"}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`font-mono inline-flex cursor-pointer items-center gap-1 uppercase tracking-wider hover:text-[var(--ink)] ${
          isActive ? "text-[var(--ink)]" : ""
        }`}
      >
        {name}
        <Arrow size={11} className={isActive ? "" : "opacity-50"} />
      </button>
    </th>
  );
}

export function Documents() {
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [total, setTotal] = useState(0);
  const [folders, setFolders] = useState<FolderStatus[]>([]);
  const [folder, setFolder] = useState("");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [sortKey, setSortKey] = useState<SortKey>("discovered_at");
  const [sortDir, setSortDir] = useState<1 | -1>(-1);

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [busy, setBusy] = useState(false);

  const [targetFolder, setTargetFolder] = useState("");
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadFolders = useCallback(() => {
    api.listFolders().then((r) => setFolders(r.folders)).catch(() => setFolders([]));
  }, []);

  useEffect(() => {
    loadFolders();
  }, [loadFolders]);

  const load = useCallback(() => {
    return api
      .listDocuments({ folder: folder || undefined, status: status || undefined, q: search || undefined, limit: PAGE_LIMIT })
      .then((r) => {
        setDocuments(r.documents);
        setTotal(r.total);
        setError(null);
      })
      .catch((err) => setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err)));
  }, [folder, status, search]);

  useEffect(() => {
    const handle = setTimeout(() => void load(), 250);
    return () => clearTimeout(handle);
  }, [load]);

  // Keep the table live while files are still being processed.
  const hasPending = documents.some((d) => d.status === "discovered");
  useEffect(() => {
    if (!hasPending) return;
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [hasPending, load]);

  const rows = useMemo(
    () => [...documents].sort((a, b) => compareRows(a, b, sortKey, sortDir)),
    [documents, sortKey, sortDir],
  );

  // Selection only counts rows that are still listed (a filter change or a delete drops the rest).
  const selectedIds = useMemo(() => documents.filter((d) => selected.has(d.id)).map((d) => d.id), [documents, selected]);
  const allSelected = rows.length > 0 && selectedIds.length === rows.length;
  const someSelected = selectedIds.length > 0 && !allSelected;

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) setSortDir((d) => (d === 1 ? -1 : 1));
    else {
      setSortKey(key);
      // numbers and dates read best biggest/newest first; text from A
      setSortDir(key === "path" || key === "title" || key === "author" || key === "status" ? 1 : -1);
    }
  };

  const toggleOne = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(rows.map((d) => d.id)));

  const reportError = (err: unknown) =>
    setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));

  const handleReprocess = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const r = await api.reprocessDocuments(selectedIds);
      setNotice(`Reprocessing ${r.reprocessed} ${r.reprocessed === 1 ? "file" : "files"}.`);
      setSelected(new Set());
      await load();
    } catch (err) {
      reportError(err);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const r = await api.deleteDocuments(selectedIds);
      setNotice(`Deleted ${r.deleted} ${r.deleted === 1 ? "file" : "files"}.`);
      setSelected(new Set());
      setConfirmingDelete(false);
      await load();
      loadFolders();
    } catch (err) {
      reportError(err);
    } finally {
      setBusy(false);
    }
  };

  const handleClearFolder = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const r = await api.clearFolder(folder);
      setNotice(`Cleared “${folder}”: ${r.deleted} ${r.deleted === 1 ? "file" : "files"} deleted.`);
      setSelected(new Set());
      setConfirmingClear(false);
      await load();
      loadFolders();
    } catch (err) {
      reportError(err);
    } finally {
      setBusy(false);
    }
  };

  const changeFolder = (name: string) => {
    setFolder(name);
    setConfirmingClear(false);
  };

  // how many files the selected folder holds, for the confirmation
  const folderFileCount = folders.find((f) => f.name === folder)?.total_files;

  const uploadTarget = (targetFolder || folder || folders[0]?.name || "").trim();

  const handleFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    const files = Array.from(fileList);
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (!uploadTarget) {
      setError("Choose a folder (or type a new folder name) to put the files in first.");
      return;
    }
    setUploading(true);
    setError(null);
    setNotice(null);
    try {
      const r = await api.uploadDocuments(uploadTarget, files);
      setNotice(
        `Added ${r.uploaded.length} ${r.uploaded.length === 1 ? "file" : "files"} to “${r.folder}”. They are being processed.`,
      );
      await load();
      loadFolders();
    } catch (err) {
      reportError(err);
    } finally {
      setUploading(false);
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
    void handleFiles(e.dataTransfer.files);
  };

  return (
    <div>
      <p className={label}>Corpus</p>
      <h1 className={`mt-1 ${pageTitle}`}>Documents</h1>
      <p className={`mt-1 ${muted}`}>
        Every ingested file and the metadata extracted from it at convert time.
      </p>

      {/* Drop zone: adds files to a corpus folder (not a chat's attachments). */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`mt-5 flex flex-wrap items-center gap-3 rounded border border-dashed px-4 py-3 transition-colors ${
          dragging ? "border-[var(--index)] bg-[var(--index-soft)]" : "border-[var(--line)]"
        }`}
      >
        <UploadCloud size={20} className={dragging ? "text-[var(--index)]" : muted} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-medium">{dragging ? "Drop to add these files" : "Drop files here to add them"}</p>
          <p className={`text-xs ${muted}`}>
            They are saved in the folder on the right, then converted and indexed.
          </p>
        </div>
        <input
          className={`${input} w-48`}
          list="document-folders"
          placeholder={folders[0]?.name ?? "Folder name"}
          value={targetFolder}
          onChange={(e) => setTargetFolder(e.target.value)}
          aria-label="Folder to add files to"
        />
        <datalist id="document-folders">
          {folders.map((f) => (
            <option key={f.name} value={f.name} />
          ))}
        </datalist>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => void handleFiles(e.target.files)}
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
          className={`${button} inline-flex items-center gap-1.5`}
        >
          {uploading ? <Loader2 size={13} className="animate-spin" /> : <UploadCloud size={13} />} Choose files
        </button>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <input
          className={`${input} w-60`}
          placeholder="Search path, title or author"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className={input} value={folder} onChange={(e) => changeFolder(e.target.value)}>
          <option value="">All folders</option>
          {folders.map((f) => (
            <option key={f.name} value={f.name}>
              {f.name}
            </option>
          ))}
        </select>
        <select className={input} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATUS_CHIP[s]?.text ?? s}
            </option>
          ))}
        </select>
        {folder && !confirmingClear && (
          <button
            type="button"
            onClick={() => setConfirmingClear(true)}
            disabled={busy}
            title={`Delete every file in “${folder}”`}
            className={`${buttonSecondary} inline-flex items-center gap-1.5 hover:!border-[var(--danger)] hover:!text-[var(--danger)]`}
          >
            <Trash2 size={12} /> Clear folder
          </button>
        )}
        <span className={`font-mono ml-auto text-[11px] ${muted}`}>
          {total > documents.length ? `${documents.length} of ${total} files` : `${total} files`}
        </span>
      </div>

      {/* Confirmation for emptying a whole folder. */}
      {folder && confirmingClear && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded border border-[var(--danger)] px-3 py-2">
          <span className="text-[var(--danger)]">
            Delete {folderFileCount === undefined ? "everything" : `all ${folderFileCount} ${folderFileCount === 1 ? "file" : "files"}`} in
            “{folder}” from disk and from search? The folder stays, empty. This can’t be undone.
          </span>
          <button
            type="button"
            onClick={() => void handleClearFolder()}
            disabled={busy}
            className="inline-flex cursor-pointer items-center gap-1.5 rounded bg-[var(--danger)] px-3 py-1 text-xs font-medium text-[var(--paper)] hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />} Clear folder
          </button>
          <button type="button" onClick={() => setConfirmingClear(false)} disabled={busy} className={buttonSecondary}>
            Cancel
          </button>
        </div>
      )}

      {/* Bulk actions, shown once rows are ticked. */}
      {selectedIds.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded border border-[var(--index)] bg-[var(--index-soft)] px-3 py-2">
          <span className="font-medium">{selectedIds.length} selected</span>
          {confirmingDelete ? (
            <>
              <span className="text-[var(--danger)]">
                Delete {selectedIds.length === 1 ? "this file" : `these ${selectedIds.length} files`} from disk and from
                search? This can’t be undone.
              </span>
              <button
                type="button"
                onClick={() => void handleDelete()}
                disabled={busy}
                className="inline-flex cursor-pointer items-center gap-1.5 rounded bg-[var(--danger)] px-3 py-1 text-xs font-medium text-[var(--paper)] hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />} Delete
              </button>
              <button type="button" onClick={() => setConfirmingDelete(false)} disabled={busy} className={buttonSecondary}>
                Cancel
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => void handleReprocess()}
                disabled={busy}
                title="Run these files through conversion and indexing again"
                className={`${buttonSecondary} inline-flex items-center gap-1.5`}
              >
                {busy ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} Reprocess
              </button>
              <button
                type="button"
                onClick={() => setConfirmingDelete(true)}
                disabled={busy}
                className={`${buttonSecondary} inline-flex items-center gap-1.5 hover:!border-[var(--danger)] hover:!text-[var(--danger)]`}
              >
                <Trash2 size={12} /> Delete
              </button>
              <button
                type="button"
                onClick={() => setSelected(new Set())}
                className={`${buttonSecondary} ml-auto inline-flex items-center gap-1.5`}
              >
                <X size={12} /> Clear
              </button>
            </>
          )}
        </div>
      )}

      {notice && (
        <p role="status" className={`mt-3 text-xs ${muted}`}>
          {notice}
        </p>
      )}
      {error && <p className={`mt-3 ${errorText}`}>{error}</p>}

      <div className="mt-4 overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className={`${tableHeader} w-8`}>
                <input
                  type="checkbox"
                  aria-label="Select all files"
                  checked={allSelected}
                  ref={(el) => {
                    if (el) el.indeterminate = someSelected;
                  }}
                  onChange={toggleAll}
                  disabled={rows.length === 0}
                  className="cursor-pointer accent-[var(--index)]"
                />
              </th>
              <SortHeader name="Path" sortKey="path" active={sortKey} dir={sortDir} onSort={toggleSort} />
              <SortHeader name="Title" sortKey="title" active={sortKey} dir={sortDir} onSort={toggleSort} />
              <SortHeader name="Author" sortKey="author" active={sortKey} dir={sortDir} onSort={toggleSort} />
              <SortHeader name="Pages" sortKey="page_count" active={sortKey} dir={sortDir} onSort={toggleSort} />
              <SortHeader name="Size" sortKey="size_bytes" active={sortKey} dir={sortDir} onSort={toggleSort} />
              <SortHeader name="Status" sortKey="status" active={sortKey} dir={sortDir} onSort={toggleSort} />
              <SortHeader name="Added" sortKey="discovered_at" active={sortKey} dir={sortDir} onSort={toggleSort} />
            </tr>
          </thead>
          <tbody>
            {rows.map((doc) => {
              const isSelected = selected.has(doc.id);
              return (
                <tr key={doc.id} className={isSelected ? "bg-[var(--index-soft)]" : "hover:bg-[var(--index-soft)]/40"}>
                  <td className={tableCell}>
                    <input
                      type="checkbox"
                      aria-label={`Select ${doc.path}`}
                      checked={isSelected}
                      onChange={() => toggleOne(doc.id)}
                      className="cursor-pointer accent-[var(--index)]"
                    />
                  </td>
                  <td className={tableCell}>
                    <span className="flex items-center gap-2">
                      <FileIcon path={doc.path} />
                      <span className="font-mono break-all" title={doc.path}>
                        {doc.path}
                      </span>
                    </span>
                  </td>
                  <td className={tableCell}>{doc.title ?? <span className={muted}>—</span>}</td>
                  <td className={tableCell}>{doc.author ?? <span className={muted}>—</span>}</td>
                  <td className={`${tableCell} tabular-nums`}>{doc.page_count ?? ""}</td>
                  <td className={`${tableCell} tabular-nums whitespace-nowrap`}>{formatBytes(doc.size_bytes)}</td>
                  <td className={tableCell}>
                    <StatusChip status={doc.status} />
                  </td>
                  <td className={`${tableCell} whitespace-nowrap ${muted}`}>{formatAdded(doc.discovered_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {documents.length === 0 && !error && <p className={`mt-4 ${muted}`}>No documents match.</p>}
    </div>
  );
}
