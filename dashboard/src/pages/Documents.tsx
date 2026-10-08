import { useEffect, useState } from "react";
import { api, ApiError, type DocumentRow, type FolderStatus } from "../api/client";
import { errorText, input, label, muted, pageTitle, tableCell, tableHeader } from "../ui";

const STATUSES = ["discovered", "converted", "summarized", "failed", "unsupported"];

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

export function Documents() {
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [total, setTotal] = useState(0);
  const [folders, setFolders] = useState<FolderStatus[]>([]);
  const [folder, setFolder] = useState("");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listFolders().then((r) => setFolders(r.folders)).catch(() => setFolders([]));
  }, []);

  useEffect(() => {
    const handle = setTimeout(() => {
      api
        .listDocuments({ folder: folder || undefined, status: status || undefined, q: search || undefined })
        .then((r) => {
          setDocuments(r.documents);
          setTotal(r.total);
          setError(null);
        })
        .catch((err) =>
          setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err)),
        );
    }, 250);
    return () => clearTimeout(handle);
  }, [folder, status, search]);

  return (
    <div>
      <p className={label}>Corpus</p>
      <h1 className={`mt-1 ${pageTitle}`}>Documents</h1>
      <p className={`mt-1 ${muted}`}>
        Every ingested file and the metadata extracted from it at convert time.
      </p>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <input
          className={`${input} w-60`}
          placeholder="Search path, title or author"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className={input} value={folder} onChange={(e) => setFolder(e.target.value)}>
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
              {s}
            </option>
          ))}
        </select>
        <span className={`font-mono ml-auto text-[11px] ${muted}`}>{total} files</span>
      </div>

      {error && <p className={`mt-4 ${errorText}`}>{error}</p>}

      <table className="mt-4 w-full border-collapse">
        <thead>
          <tr>
            <th className={tableHeader}>Path</th>
            <th className={tableHeader}>Title</th>
            <th className={tableHeader}>Author</th>
            <th className={tableHeader}>Pages</th>
            <th className={tableHeader}>Size</th>
            <th className={tableHeader}>Status</th>
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => (
            <tr key={doc.id}>
              <td className={`font-mono ${tableCell}`}>{doc.path}</td>
              <td className={tableCell}>{doc.title ?? <span className={muted}>—</span>}</td>
              <td className={tableCell}>{doc.author ?? <span className={muted}>—</span>}</td>
              <td className={`${tableCell} tabular-nums`}>{doc.page_count ?? ""}</td>
              <td className={`${tableCell} tabular-nums`}>{formatBytes(doc.size_bytes)}</td>
              <td className={`font-mono ${tableCell} text-[11px]`}>{doc.status}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {documents.length === 0 && !error && <p className={`mt-4 ${muted}`}>No documents match.</p>}
    </div>
  );
}
