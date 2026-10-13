import { AlertCircle, ChevronRight, Folder, FolderOpen, Loader2, Library } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, type DocumentsTreeLevel } from "../api/client";
import { muted } from "../ui";

/** The Documents page's folder tree. One level is fetched when a folder is opened, so a
 * corpus with thousands of subfolders is never loaded whole. Counts include subfolders;
 * a red mark means failed files inside. */
export function FolderTree({
  selected,
  onSelect,
  refreshKey,
}: {
  /** The chosen folder path ("" = all documents). */
  selected: string;
  onSelect: (path: string) => void;
  /** Changes whenever the files may have changed (an upload, a delete): the open levels reload. */
  refreshKey: number;
}) {
  const [levels, setLevels] = useState<Record<string, DocumentsTreeLevel>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState<Set<string>>(new Set());
  const [failed, setFailed] = useState(false);

  const load = useCallback(async (parent: string) => {
    setLoading((prev) => new Set(prev).add(parent));
    try {
      const level = await api.documentsTree(parent);
      setLevels((prev) => ({ ...prev, [parent]: level }));
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setLoading((prev) => {
        const next = new Set(prev);
        next.delete(parent);
        return next;
      });
    }
  }, []);

  // first load, and again whenever the files changed: the top level plus every open folder
  useEffect(() => {
    void load("");
    expanded.forEach((path) => void load(path));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey, load]);

  // a folder chosen from elsewhere (or a refresh) should be visible: open the folders above it
  useEffect(() => {
    const parts = selected.split("/").filter(Boolean);
    const ancestors = parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join("/"));
    const missing = ancestors.filter((p) => !expanded.has(p));
    if (missing.length === 0) return;
    setExpanded((prev) => new Set([...prev, ...missing]));
    missing.forEach((p) => {
      if (!levels[p]) void load(p);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const toggle = (path: string) => {
    const open = expanded.has(path);
    setExpanded((prev) => {
      const next = new Set(prev);
      if (open) next.delete(path);
      else next.add(path);
      return next;
    });
    if (!open && !levels[path]) void load(path);
  };

  const root = levels[""];
  const allCount = root ? root.folders.reduce((n, f) => n + f.total, 0) + root.files_here : null;
  const row = "flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-sm";
  const tone = (isSelected: boolean) =>
    isSelected
      ? "bg-[var(--index-soft)] font-medium text-[var(--index)]"
      : "text-[var(--ink)] hover:bg-[var(--index-soft)]/50";

  const renderLevel = (parent: string, depth: number) => {
    const level = levels[parent];
    if (!level) {
      return loading.has(parent) ? (
        <div className={`flex items-center gap-1.5 px-2 py-1 text-xs ${muted}`} style={{ paddingLeft: 8 + depth * 14 }}>
          <Loader2 size={11} className="animate-spin" /> Loading…
        </div>
      ) : null;
    }
    return level.folders.map((f) => {
      const open = expanded.has(f.path);
      const isSelected = selected === f.path;
      return (
        <div key={f.path} role="treeitem" aria-expanded={f.has_subfolders ? open : undefined} aria-selected={isSelected}>
          <div className="flex items-center" style={{ paddingLeft: depth * 14 }}>
            {f.has_subfolders ? (
              <button
                type="button"
                onClick={() => toggle(f.path)}
                aria-label={`${open ? "Collapse" : "Expand"} ${f.name}`}
                className="flex h-6 w-5 shrink-0 cursor-pointer items-center justify-center text-[var(--ink-soft)] hover:text-[var(--ink)]"
              >
                <ChevronRight size={13} className={`transition-transform ${open ? "rotate-90" : ""}`} />
              </button>
            ) : (
              <span className="w-5 shrink-0" aria-hidden="true" />
            )}
            <button
              type="button"
              onClick={() => onSelect(f.path)}
              title={f.path}
              className={`${row} min-w-0 cursor-pointer ${tone(isSelected)}`}
            >
              {open ? <FolderOpen size={14} className="shrink-0" /> : <Folder size={14} className="shrink-0" />}
              <span className="min-w-0 flex-1 truncate">{f.name}</span>
              {f.failed > 0 && (
                <AlertCircle size={12} className="shrink-0 text-[var(--danger)]" aria-label={`${f.failed} failed`} />
              )}
              <span className={`font-mono shrink-0 text-[11px] tabular-nums ${isSelected ? "" : muted}`}>
                {f.total.toLocaleString()}
              </span>
            </button>
          </div>
          {open && f.has_subfolders && <div role="group">{renderLevel(f.path, depth + 1)}</div>}
        </div>
      );
    });
  };

  return (
    <nav aria-label="Folders" className="rounded border border-[var(--line)] p-2">
      <div role="tree">
        <button
          type="button"
          onClick={() => onSelect("")}
          aria-selected={selected === ""}
          role="treeitem"
          className={`${row} cursor-pointer ${tone(selected === "")}`}
        >
          <Library size={14} className="shrink-0" />
          <span className="min-w-0 flex-1 truncate">All documents</span>
          {allCount !== null && (
            <span className={`font-mono shrink-0 text-[11px] tabular-nums ${selected === "" ? "" : muted}`}>
              {allCount.toLocaleString()}
            </span>
          )}
        </button>
        <div className="mt-1">{renderLevel("", 0)}</div>
      </div>
      {root && root.folders.length === 0 && <p className={`px-2 py-1 text-xs ${muted}`}>No folders yet.</p>}
      {failed && <p className="px-2 py-1 text-xs text-[var(--danger)]">Couldn’t load the folders.</p>}
    </nav>
  );
}
