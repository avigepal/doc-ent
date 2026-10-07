import { useEffect, useState } from "react";
import { api, ApiError, type OverviewStats } from "../api/client";
import { IngestionBar } from "../components/IngestionBar";
import { KpiTile } from "../components/KpiTile";
import { card, errorText, label, muted, pageTitle } from "../ui";

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

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}

export function Overview() {
  const [stats, setStats] = useState<OverviewStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .stats()
      .then(setStats)
      .catch((err) =>
        setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err)),
      );
  }, []);

  if (error) return <p className={errorText}>{error}</p>;
  if (!stats) return <p className={muted}>Loading…</p>;

  return (
    <div>
      <p className={label}>Corpus</p>
      <h1 className={`mt-1 ${pageTitle}`}>Overview</h1>

      <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiTile
          name="Documents"
          value={stats.documents.total}
          sub={`${stats.documents.summarized} summarized`}
        />
        <KpiTile name="Chunks indexed" value={stats.chunks} sub="searchable vectors" />
        <KpiTile name="Storage" value={formatBytes(stats.storage_bytes)} sub={`${stats.folders} folders`} />
        <KpiTile
          name="Queries run"
          value={stats.queries_total}
          sub={`${stats.exports_total} exports`}
        />
      </div>

      <div className={`mt-4 ${card}`}>
        <IngestionBar counts={{ ...stats.documents }} />
      </div>

      <div className={`mt-4 ${card}`}>
        <p className={label}>Recent activity</p>
        {stats.recent_activity.length === 0 ? (
          <p className={`mt-2 ${muted}`}>Nothing yet.</p>
        ) : (
          <ul className="mt-2">
            {stats.recent_activity.map((event, i) => (
              <li
                key={`${event.type}-${event.at}-${i}`}
                className="flex items-baseline gap-3 border-b border-[var(--line)] py-1.5 last:border-b-0"
              >
                <span className="font-mono w-14 shrink-0 text-[11px] uppercase text-[var(--index)]">
                  {event.type}
                </span>
                <span className="flex-1 truncate">{event.label}</span>
                <span className="font-mono shrink-0 text-[11px] text-[var(--ink-soft)]">
                  {formatWhen(event.at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
