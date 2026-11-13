import type { CorrelationPair } from "../api/client";

// Correlation coefficients as bars: each bar runs right for a positive r and left for a negative one,
// from a centre line at 0. Values come from the backend (pandas computes r; the model only narrates it).

const LABEL_WIDTH = 230;
const BAR_WIDTH = 220;
const ROW = 26;
const PAD = 8;

function label(p: CorrelationPair): string {
  return `${p.first} vs ${p.second}`;
}

export function CorrelationChart({ pairs }: { pairs: CorrelationPair[] }) {
  if (pairs.length === 0) return null;
  const height = pairs.length * ROW + PAD * 2;
  const width = LABEL_WIDTH + BAR_WIDTH + 60;
  const centre = LABEL_WIDTH + BAR_WIDTH / 2;

  return (
    <figure className="m-0">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        role="img"
        aria-label="Correlation coefficients between numeric columns"
        className="max-w-full"
      >
        <line x1={centre} x2={centre} y1={0} y2={height} stroke="var(--line)" strokeWidth={1} />
        {pairs.map((p, i) => {
          const y = PAD + i * ROW;
          const half = (BAR_WIDTH / 2) * Math.min(Math.abs(p.r), 1);
          const x = p.r >= 0 ? centre : centre - half;
          const fill = p.r >= 0 ? "var(--xlsx)" : "var(--pdf)";
          return (
            <g key={`${p.table}-${p.first}-${p.second}`}>
              <text x={LABEL_WIDTH - 8} y={y + ROW / 2 + 4} textAnchor="end" fontSize={11} fill="var(--ink)">
                <title>{`${p.table} · ${p.rows} rows`}</title>
                {label(p)}
              </text>
              <rect x={x} y={y + 5} width={Math.max(half, 1)} height={ROW - 10} rx={2} fill={fill} opacity={0.85} />
              <text x={width - 4} y={y + ROW / 2 + 4} textAnchor="end" fontSize={11} fill="var(--ink-soft)">
                {p.r.toFixed(4)}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-1 text-[11px] text-[var(--ink-soft)]">
        Bars to the right are positive correlations, to the left negative. Each pair is computed within one
        table, using the rows both columns have.
      </figcaption>
    </figure>
  );
}
