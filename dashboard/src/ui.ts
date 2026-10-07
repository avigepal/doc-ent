// Shared class strings for the "archive / index-card" identity — see
// index.css for the token definitions these arbitrary-value classes
// reference. Centralized here so the look doesn't drift between pages.

export const input =
  "rounded-md border border-[var(--line)] bg-[var(--paper)] px-3 py-2.5 text-[var(--ink)] placeholder:text-[var(--ink-soft)] focus:border-[var(--index)] focus:outline-none transition-colors";

export const button =
  "rounded-md bg-[var(--index)] px-4 py-2.5 font-medium text-[var(--paper)] hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 transition-opacity";

export const buttonSecondary =
  "rounded-md border border-[var(--line)] px-3 py-1.5 text-sm text-[var(--ink)] hover:border-[var(--index)] hover:text-[var(--index)] disabled:opacity-40 transition-colors";

export const card =
  "mt-4 rounded-lg border border-[var(--line)] bg-[var(--paper)] p-6 shadow-[0_1px_0_var(--line)]";

export const muted = "text-[var(--ink-soft)]";

export const errorText = "font-mono text-sm text-[var(--danger)]";

export const label = "font-mono text-xs uppercase tracking-wider text-[var(--ink-soft)]";
