import { LogOut } from "lucide-react";
import { Link, NavLink } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const NAV = [
  { to: "/ask", label: "Ask" },
  { to: "/documents", label: "Documents" },
  { to: "/history", label: "History" },
  { to: "/progress", label: "Progress" },
];

/** Height of this bar — Layout (App.tsx) and Sidebar offset by it, so keep
 * the number in one place. */
export const HEADER_HEIGHT_CLASS = "h-12";

const tabClasses = ({ isActive }: { isActive: boolean }) =>
  `relative flex h-full shrink-0 items-center px-3 text-[13px] transition-colors ${
    isActive
      ? "font-medium text-[var(--index)] after:absolute after:inset-x-3 after:bottom-0 after:h-[2px] after:bg-[var(--index)]"
      : "text-[var(--ink-soft)] hover:text-[var(--ink)]"
  }`;

export function Header() {
  const { logout } = useAuth();

  return (
    <header
      className={`fixed inset-x-0 top-0 z-20 flex ${HEADER_HEIGHT_CLASS} items-stretch border-b border-[var(--line)] bg-[var(--paper)]`}
    >
      {/* Same width as the Ask sidebar (220px — keep in step with
          Sidebar.tsx and App.tsx's lg:pl-[220px]) with the same right
          border, so the logo cell and the sidebar read as one column, and
          the tabs start exactly where the main section does. */}
      <div className="flex shrink-0 items-center px-4 lg:w-[220px] lg:justify-center lg:border-r lg:border-[var(--line)]">
        {/* "/" redirects to /ask (see App.tsx), so the logo is always "home". */}
        <Link
          to="/"
          aria-label="Docent — go to Ask"
          className="font-display text-base font-semibold tracking-[0.16em] text-[var(--ink)] no-underline transition-opacity hover:opacity-75 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--index)] lg:text-lg"
        >
          DOC<span className="text-[var(--index)]">·</span>ENT
        </Link>
      </div>

      <nav className="flex min-w-0 flex-1 overflow-x-auto">
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} className={tabClasses}>
            {item.label}
          </NavLink>
        ))}
      </nav>

      <button
        onClick={logout}
        className="font-mono flex shrink-0 cursor-pointer items-center gap-1.5 self-center px-4 text-[11px] uppercase tracking-wider text-[var(--ink-soft)] hover:text-[var(--ink)] sm:px-6"
      >
        <LogOut size={13} /> Log out
      </button>
    </header>
  );
}
