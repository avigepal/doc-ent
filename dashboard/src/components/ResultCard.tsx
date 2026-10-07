import { card, label, muted } from "../ui";

export interface ResultCardData {
  question: string;
  answer: string;
  sources: string[];
  grounded: boolean;
  cross_doc: { answer: string; sources: string[] } | null;
  statistical: { answer: string; correlation_summary: string } | null;
}

/** Strips everything up to and including "/raw/" so citations show a
 * readable "contracts/q2_update.txt" instead of the full container path. */
export function shortenSource(path: string): string {
  const marker = "/raw/";
  const idx = path.indexOf(marker);
  return idx === -1 ? path : path.slice(idx + marker.length);
}

/** The signature element: clipped-corner index tabs for citations. Kept
 * from the original archive identity because citations are the heart of
 * a RAG result. */
function SourceTabs({ sources }: { sources: string[] }) {
  if (sources.length === 0) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {sources.map((source, i) => (
        <span
          key={source}
          className="font-mono border border-[var(--line)] bg-[var(--locator-soft)] px-2 py-0.5 text-[11px] text-[var(--ink)]"
          style={{ clipPath: "polygon(0 0, calc(100% - 7px) 0, 100% 7px, 100% 100%, 0 100%)" }}
        >
          [{i + 1}] {shortenSource(source)}
        </span>
      ))}
    </div>
  );
}

export function ResultCard({
  result,
  actions,
}: {
  result: ResultCardData;
  actions?: React.ReactNode;
}) {
  return (
    <div className={card}>
      <p className={label}>Answer</p>
      {!result.grounded && (
        <p className={`font-mono mt-1 text-[11px] ${muted}`}>
          Not grounded — no corpus source matched closely enough.
        </p>
      )}
      <p className="mt-2 whitespace-pre-wrap">{result.answer}</p>
      <SourceTabs sources={result.sources} />

      {result.cross_doc && (
        <div className="mt-5 border-t border-[var(--line)] pt-4">
          <p className={label}>Cross-document findings</p>
          <p className="mt-2 whitespace-pre-wrap">{result.cross_doc.answer}</p>
          <SourceTabs sources={result.cross_doc.sources} />
        </div>
      )}

      {result.statistical && (
        <div className="mt-5 border-t border-[var(--line)] pt-4">
          <p className={label}>Statistical findings</p>
          <p className="mt-2 whitespace-pre-wrap">{result.statistical.answer}</p>
          <pre className="font-mono mt-2 whitespace-pre-wrap rounded border border-[var(--line)] bg-[var(--locator-soft)] p-2.5 text-[11px]">
            {result.statistical.correlation_summary}
          </pre>
        </div>
      )}

      {actions && (
        <div className="mt-5 flex items-center gap-2 border-t border-[var(--line)] pt-4">{actions}</div>
      )}
    </div>
  );
}
