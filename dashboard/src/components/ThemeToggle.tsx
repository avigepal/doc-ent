import { Monitor, Moon, Sun, type LucideIcon } from "lucide-react";
import { useState } from "react";

type Theme = "system" | "light" | "dark";

const STORAGE_KEY = "doc:theme";
const ORDER: Theme[] = ["system", "light", "dark"];
const META: Record<Theme, { icon: LucideIcon; label: string }> = {
  system: { icon: Monitor, label: "Match system" },
  light: { icon: Sun, label: "Light" },
  dark: { icon: Moon, label: "Dark" },
};

function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

function applyTheme(theme: Theme) {
  // "system" removes the pin so the OS preference decides (see index.css)
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  try {
    if (theme === "system") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // storage unavailable: the choice just won't survive a reload
  }
}

/** Cycles system -> light -> dark. index.html applies the saved choice before
 * first paint; this only changes it. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];
  const { icon: Icon, label } = META[theme];

  return (
    <button
      type="button"
      onClick={() => {
        applyTheme(next);
        setTheme(next);
      }}
      title={`Theme: ${label} — click for ${META[next].label.toLowerCase()}`}
      aria-label={`Theme: ${label}. Switch to ${META[next].label.toLowerCase()}`}
      className="flex shrink-0 cursor-pointer items-center gap-1.5 self-center px-3 text-[var(--ink-soft)] hover:text-[var(--ink)]"
    >
      <Icon size={15} aria-hidden="true" />
      <span className="font-mono hidden text-[11px] uppercase tracking-wider sm:inline">{label}</span>
    </button>
  );
}
