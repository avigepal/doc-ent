import type { LucideIcon } from "lucide-react";

// Written out in full (not built from the tone name) so Tailwind can see every class.
const TONE = {
  edit: {
    on: "border-[var(--edit)] bg-[var(--edit-soft)] font-medium text-[var(--edit)]",
    off: "border-[var(--edit)]/40 text-[var(--edit)] hover:border-[var(--edit)] hover:bg-[var(--edit-soft)]",
  },
  report: {
    on: "border-[var(--report)] bg-[var(--report-soft)] font-medium text-[var(--report)]",
    off: "border-[var(--report)]/40 text-[var(--report)] hover:border-[var(--report)] hover:bg-[var(--report-soft)]",
  },
  sheet: {
    on: "border-[var(--xlsx)] bg-[var(--xlsx-soft)] font-medium text-[var(--xlsx)]",
    off: "border-[var(--xlsx)]/40 text-[var(--xlsx)] hover:border-[var(--xlsx)] hover:bg-[var(--xlsx-soft)]",
  },
} as const;

/** A mode switch for the next message (Edit file, Full report), colored by mode.
 * Unavailable when there is nothing for the mode to work on. */
export function ModeToggle({
  label,
  Icon,
  tone,
  help,
  on,
  disabled,
  disabledReason,
  onToggle,
}: {
  label: string;
  Icon: LucideIcon;
  tone: keyof typeof TONE;
  help: string;
  on: boolean;
  disabled: boolean;
  disabledReason: string;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      aria-pressed={on && !disabled}
      aria-label={label}
      title={disabled ? disabledReason : help}
      className={`inline-flex shrink-0 cursor-pointer items-center gap-1.5 rounded border px-2.5 py-1 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        on && !disabled ? TONE[tone].on : TONE[tone].off
      }`}
    >
      <Icon size={13} aria-hidden="true" /> {label}
    </button>
  );
}
