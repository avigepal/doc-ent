import { muted } from "../ui";

export interface TabItem<K extends string> {
  key: K;
  label: string;
  /** Number shown in the tab's badge. */
  count?: number;
  /** Colors the badge red when the count is above zero (failures). */
  alert?: boolean;
}

/** A small tab strip. The caller shows the panel that matches `value`. */
export function Tabs<K extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: TabItem<K>[];
  value: K;
  onChange: (key: K) => void;
  /** Accessible name for the strip. */
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex items-center gap-1 rounded-md border border-[var(--line)] p-0.5">
      {tabs.map((t) => {
        const active = t.key === value;
        const alert = t.alert && (t.count ?? 0) > 0;
        return (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(t.key)}
            className={`inline-flex cursor-pointer items-center gap-1.5 rounded px-2.5 py-1 text-xs transition-colors ${
              active
                ? "bg-[var(--index-soft)] font-medium text-[var(--index)]"
                : `${muted} hover:text-[var(--ink)]`
            }`}
          >
            {t.label}
            {t.count !== undefined && (
              <span
                className={`font-mono rounded px-1 text-[10px] tabular-nums ${
                  alert ? "bg-[var(--danger)] text-[var(--paper)]" : "border border-[var(--line)]"
                }`}
              >
                {t.count.toLocaleString()}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
