import { Loader2 } from "lucide-react";
import type { IngestionProgress } from "../api/client";
import { card, muted } from "../ui";

/** "Scanning… 12,000 files": a scan of a big folder reads every file, and the files show up
 * below as it goes. Nothing is shown when no scan is running. */
export function ScanNotice({ scan }: { scan: IngestionProgress["scan"] }) {
  if (!scan?.active) return null;
  return (
    <div role="status" className={`${card} mt-4 flex items-center gap-3 !py-2.5`}>
      <Loader2 size={14} className="shrink-0 animate-spin text-[var(--locator)]" aria-hidden="true" />
      <p className="text-sm">
        <span className="font-medium">Scanning folders…</span>{" "}
        <span className={muted}>
          {scan.files_seen.toLocaleString()} {scan.files_seen === 1 ? "file" : "files"} found so far. They appear below as
          they are found.
        </span>
      </p>
    </div>
  );
}
