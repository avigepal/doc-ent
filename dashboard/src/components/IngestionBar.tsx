import { label } from "../ui";

const SEGMENTS = [
  { key: "summarized", name: "Summarized", color: "var(--signal)" },
  { key: "converted", name: "Converted", color: "var(--index)" },
  { key: "discovered", name: "Discovered", color: "var(--locator)" },
  { key: "failed", name: "Failed", color: "var(--danger)" },
] as const;

export function IngestionBar({
  counts,
}: {
  counts: { discovered: number; converted: number; summarized: number; failed: number; total: number };
}) {
  const total = counts.total || 1;

  return (
    <div>
      <p className={label}>Ingestion status</p>
      <div className="mt-2 flex h-2 w-full overflow-hidden rounded-full bg-[var(--line)]">
        {SEGMENTS.map((segment) => {
          const value = counts[segment.key];
          if (value === 0) return null;
          return (
            <div
              key={segment.key}
              style={{ width: `${(value / total) * 100}%`, background: segment.color }}
              title={`${segment.name}: ${value}`}
            />
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {SEGMENTS.map((segment) => (
          <span key={segment.key} className="flex items-center gap-1.5 text-[11px]">
            <span className="h-2 w-2 rounded-full" style={{ background: segment.color }} />
            <span className="text-[var(--ink-soft)]">{segment.name}</span>
            <span className="font-mono tabular-nums">{counts[segment.key]}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
