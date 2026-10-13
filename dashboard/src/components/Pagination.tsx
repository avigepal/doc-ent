import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { buttonSecondary, muted } from "../ui";

export const PAGE_SIZES = [50, 100, 200] as const;

/** "1–100 of 56,432" with first / previous / next / last and a page-size choice.
 * `page` counts from 0. */
export function Pagination({
  page,
  pageSize,
  total,
  onPage,
  onPageSize,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  onPageSize: (size: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const first = total === 0 ? 0 : page * pageSize + 1;
  const last = Math.min(total, (page + 1) * pageSize);
  const nav = `${buttonSecondary} inline-flex items-center !px-2 disabled:cursor-not-allowed disabled:opacity-40`;

  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs">
      <span className={`tabular-nums ${muted}`}>
        {first.toLocaleString()}–{last.toLocaleString()} of {total.toLocaleString()}
      </span>
      <div className="flex items-center gap-1.5">
        <label className={`mr-2 flex items-center gap-1.5 ${muted}`}>
          Per page
          <select
            value={pageSize}
            onChange={(e) => onPageSize(Number(e.target.value))}
            className="cursor-pointer rounded border border-[var(--line)] bg-transparent px-1.5 py-1 text-[var(--ink)]"
          >
            {PAGE_SIZES.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className={nav} onClick={() => onPage(0)} disabled={page === 0} aria-label="First page">
          <ChevronsLeft size={14} />
        </button>
        <button type="button" className={nav} onClick={() => onPage(page - 1)} disabled={page === 0} aria-label="Previous page">
          <ChevronLeft size={14} />
        </button>
        <span className="min-w-20 text-center tabular-nums">
          Page {(page + 1).toLocaleString()} / {pages.toLocaleString()}
        </span>
        <button type="button" className={nav} onClick={() => onPage(page + 1)} disabled={page >= pages - 1} aria-label="Next page">
          <ChevronRight size={14} />
        </button>
        <button type="button" className={nav} onClick={() => onPage(pages - 1)} disabled={page >= pages - 1} aria-label="Last page">
          <ChevronsRight size={14} />
        </button>
      </div>
    </div>
  );
}
