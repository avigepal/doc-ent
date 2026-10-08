import { Loader2 } from "lucide-react";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import { buttonSecondary, card, errorText, label, muted, pageTitle, tableCell, tableHeader } from "../ui";

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

interface Step {
  n: string;
  label: string;
  busyLabel: string;
  run: () => Promise<string | void>;
}

export function Status() {
  const [scanResult, setScanResult] = useState<Awaited<ReturnType<typeof api.ingestScan>> | null>(null);
  const [log, setLog] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const steps: Step[] = [
    {
      n: "01",
      label: "Scan raw/",
      busyLabel: "Scanning",
      run: async () => {
        const r = await api.ingestScan();
        setScanResult(r);
      },
    },
    {
      n: "02",
      label: "Convert",
      busyLabel: "Enqueuing",
      run: async () => {
        const r = await api.ingestConvert();
        return `Enqueued convert: ${JSON.stringify(r.enqueued)}`;
      },
    },
    {
      n: "03",
      label: "Summarize",
      busyLabel: "Enqueuing",
      run: async () => {
        const r = await api.ingestSummarize();
        return `Enqueued summarize: ${r.enqueued.summarize} file(s)`;
      },
    },
  ];

  const run = async (step: Step) => {
    setBusy(step.n);
    setError(null);
    try {
      const result = await step.run();
      if (result) setLog(result);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <p className={label}>Ingestion</p>
      <h1 className={`mt-1 ${pageTitle}`}>Ingestion</h1>
      <p className={`mt-1 ${muted}`}>
        Each step calls the matching endpoint directly. Queue depth and worker health for
        running jobs live on{" "}
        <a href="http://localhost:5555" target="_blank" rel="noreferrer" className="underline">
          Flower
        </a>
        .
      </p>

      <ol className="mt-6 flex flex-col gap-0">
        {steps.map((step, i) => (
          <li key={step.n} className="flex items-center gap-4 border-[var(--line)] py-2" style={i > 0 ? { borderTopWidth: 1 } : undefined}>
            <span className="font-mono w-7 shrink-0 text-sm text-[var(--index)]">{step.n}</span>
            <span className="flex-1 font-medium">{step.label}</span>
            <button onClick={() => run(step)} disabled={busy !== null} className={`${buttonSecondary} flex items-center gap-1.5`}>
              {busy === step.n && <Loader2 size={12} className="animate-spin" />}
              {busy === step.n ? step.busyLabel : "Run"}
            </button>
          </li>
        ))}
      </ol>

      {error && <p className={`mt-4 ${errorText}`}>{error}</p>}
      {log && <p className={`mt-3 font-mono text-sm ${muted}`}>{log}</p>}

      {scanResult && (
        <div className={card}>
          <p className={label}>Last scan</p>
          <p className="font-mono mt-2 text-sm">{scanResult.raw_dir}</p>
          <p className="mt-1">
            <span className="font-display text-2xl font-semibold">{scanResult.total_files}</span>
            <span className={`ml-2 text-sm ${muted}`}>files · {formatBytes(scanResult.total_bytes)}</span>
          </p>

          <div className="mt-5 grid grid-cols-2 gap-6">
            <div>
              <p className={label}>By queue</p>
              <table className="mt-2 w-full border-collapse">
                <thead>
                  <tr>
                    <th className={tableHeader}>Queue</th>
                    <th className={tableHeader}>Count</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(scanResult.by_queue).map(([queue, count]) => (
                    <tr key={queue}>
                      <td className={`font-mono ${tableCell}`}>{queue}</td>
                      <td className={`${tableCell} text-right tabular-nums`}>{count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <p className={label}>By mime type</p>
              <table className="mt-2 w-full border-collapse">
                <thead>
                  <tr>
                    <th className={tableHeader}>Mime type</th>
                    <th className={tableHeader}>Count</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(scanResult.by_mime).map(([mime, count]) => (
                    <tr key={mime}>
                      <td className={`font-mono truncate ${tableCell}`}>{mime}</td>
                      <td className={`${tableCell} text-right tabular-nums`}>{count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
