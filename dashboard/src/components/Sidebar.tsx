import { NavLink } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/ask", label: "Ask", end: false },
  { to: "/documents", label: "Documents", end: false },
  { to: "/history", label: "History", end: false },
  { to: "/ingestion", label: "Ingestion", end: false },
];

const linkClasses = ({ isActive }: { isActive: boolean }) =>
  `block rounded px-3 py-1.5 text-[13px] transition-colors ${
    isActive
      ? "bg-[var(--index-soft)] font-medium text-[var(--index)]"
      : "text-[var(--ink-soft)] hover:bg-[var(--index-soft)] hover:text-[var(--ink)]"
  }`;

export function Sidebar() {
  const { logout } = useAuth();

  return (
    <aside className="flex shrink-0 flex-col border-b border-[var(--line)] bg-[var(--paper)] px-4 py-4 lg:h-screen lg:w-[220px] lg:border-b-0 lg:border-r">
      <span className="font-display px-3 text-sm font-semibold tracking-tight">
        DOC<span className="text-[var(--index)]">/</span>INDEX
      </span>

      <nav className="mt-5 flex gap-1 lg:mt-6 lg:flex-col">
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={linkClasses}>
            {item.label}
          </NavLink>
        ))}
      </nav>

      <button
        onClick={logout}
        className="font-mono mt-4 px-3 text-left text-[11px] uppercase tracking-wider text-[var(--ink-soft)] hover:text-[var(--ink)] lg:mt-auto"
      >
        Log out
      </button>
    </aside>
  );
}
