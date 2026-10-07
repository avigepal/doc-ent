import { label, tile } from "../ui";

export function KpiTile({
  name,
  value,
  sub,
}: {
  name: string;
  value: string | number;
  sub?: string;
}) {
  return (
    <div className={tile}>
      <p className={label}>{name}</p>
      <p className="font-display mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="mt-0.5 text-[11px] text-[var(--ink-soft)]">{sub}</p>}
    </div>
  );
}
