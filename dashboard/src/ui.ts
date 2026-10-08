// Shared class strings for the dense "console" look — see index.css for
// the token definitions these arbitrary-value classes reference. The
// tokens themselves are unchanged (dark mode is defined entirely through
// them); what changed here is sizing and spacing, tightened from the
// original airy card layout toward an information-dense dashboard.
//
// Centralized so the look doesn't drift between pages: revise here, not
// per-page.

export const input =
  "rounded border border-[var(--line)] bg-[var(--paper)] px-2.5 py-1.5 text-[13px] text-[var(--ink)] placeholder:text-[var(--ink-soft)] focus:border-[var(--index)] focus:outline-none transition-colors";

export const button =
  "rounded bg-[var(--index)] px-3 py-1.5 text-[13px] font-medium text-[var(--paper)] hover:opacity-90 cursor-pointer disabled:cursor-not-allowed disabled:opacity-40 transition-opacity";

export const buttonSecondary =
  "rounded border border-[var(--line)] px-2.5 py-1 text-xs text-[var(--ink)] hover:border-[var(--index)] hover:text-[var(--index)] cursor-pointer disabled:cursor-not-allowed disabled:opacity-40 transition-colors";

export const card =
  "rounded border border-[var(--line)] bg-[var(--paper)] p-4";

export const tile =
  "rounded border border-[var(--line)] bg-[var(--paper)] px-4 py-3";

export const tableHeader =
  "font-mono text-[11px] uppercase tracking-wider text-[var(--ink-soft)] text-left font-normal py-2 pr-4 border-b border-[var(--line)]";

export const tableCell = "py-2 pr-4 text-[13px] border-b border-[var(--line)]";

export const muted = "text-[var(--ink-soft)]";

export const errorText = "font-mono text-xs text-[var(--danger)]";

export const label = "font-mono text-[11px] uppercase tracking-wider text-[var(--ink-soft)]";

export const pageTitle = "font-display text-lg font-semibold tracking-tight";
