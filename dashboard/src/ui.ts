// Shared class strings for the dense "console" look — see index.css for
// the token definitions these arbitrary-value classes reference. The
// tokens themselves are unchanged (dark mode is defined entirely through
// them); what changed here is sizing and spacing, tightened from the
// original airy card layout toward an information-dense dashboard.
//
// Centralized so the look doesn't drift between pages: revise here, not
// per-page.

export const input =
  "rounded border border-[var(--line)] bg-[var(--paper)] px-2.5 py-1.5 text-[14px] text-[var(--ink)] placeholder:text-[var(--ink-soft)] focus:border-[var(--index)] focus:outline-none transition-colors";

export const button =
  "rounded bg-[var(--index)] px-3 py-1.5 text-[14px] font-medium text-[var(--paper)] hover:opacity-90 cursor-pointer disabled:cursor-not-allowed disabled:opacity-40 transition-opacity";

export const buttonSecondary =
  "rounded border border-[var(--line)] px-2.5 py-1 text-[13px] text-[var(--ink)] hover:border-[var(--index)] hover:text-[var(--index)] cursor-pointer disabled:cursor-not-allowed disabled:opacity-40 transition-colors";

// Download buttons colored by file format, so PDF and Word are told apart at a glance:
// a tinted fill and outline that turns solid on hover. Written out in full (not built
// from the color name) so Tailwind can see every class.
const formatButtonBase =
  "inline-flex items-center gap-1.5 rounded border px-2.5 py-1 text-[13px] font-medium cursor-pointer disabled:cursor-not-allowed disabled:opacity-40 transition-colors";

export const formatButton = {
  pdf: `${formatButtonBase} border-[var(--pdf)]/45 bg-[var(--pdf-soft)] text-[var(--pdf)] hover:border-[var(--pdf)] hover:bg-[var(--pdf)] hover:text-[var(--paper)]`,
  docx: `${formatButtonBase} border-[var(--docx)]/45 bg-[var(--docx-soft)] text-[var(--docx)] hover:border-[var(--docx)] hover:bg-[var(--docx)] hover:text-[var(--paper)]`,
} as const;

/** The button style for an export's format; the plain secondary button for any other. */
export const formatButtonFor = (fmt: string): string =>
  fmt.toLowerCase() === "pdf" ? formatButton.pdf : fmt.toLowerCase() === "docx" ? formatButton.docx : `${buttonSecondary} inline-flex items-center gap-1.5`;

// For bars that sit over the page while content scrolls under them (the header and
// the question bar): translucent, with a blur, so the page background shows through
// but the text beneath never competes with the text on the bar.
export const glass = "bg-[var(--paper)]/68 backdrop-blur-md";

export const card =
  "rounded border border-[var(--line)] bg-[var(--paper)] p-4";

export const tile =
  "rounded border border-[var(--line)] bg-[var(--paper)] px-4 py-3";

export const tableHeader =
  "font-mono text-[12px] uppercase tracking-wider text-[var(--ink-soft)] text-left font-normal py-2 pr-4 border-b border-[var(--line)]";

export const tableCell = "py-2 pr-4 text-[14px] border-b border-[var(--line)]";

export const muted = "text-[var(--ink-soft)]";

export const errorText = "font-mono text-xs text-[var(--danger)]";

export const label = "font-mono text-[12px] uppercase tracking-wider text-[var(--ink-soft)]";

export const pageTitle = "font-display text-lg font-semibold tracking-tight";
