import { LogOut, Menu, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { card, glass } from "../ui";
import { SIDEBAR_WIDTH, useSidebar } from "../sidebarState";
import { ModelControl } from "./ModelControl";
import { ThemeToggle } from "./ThemeToggle";

const NAV = [
  { to: "/ask", label: "Ask" },
  { to: "/documents", label: "Documents" },
  { to: "/history", label: "History" },
  { to: "/progress", label: "Progress" },
  { to: "/dashboards", label: "Dashboards" },
];

/** Height of this bar — Layout (App.tsx) and Sidebar offset by it, so keep
 * the number in one place. */
export const HEADER_HEIGHT_CLASS = "h-12";

const tabClasses = ({ isActive }: { isActive: boolean }) =>
  `relative flex h-full shrink-0 items-center px-3 text-[14px] transition-colors ${
    isActive
      ? "font-medium text-[var(--index)] after:absolute after:inset-x-3 after:bottom-0 after:h-[2px] after:bg-[var(--index)]"
      : "text-[var(--ink-soft)] hover:text-[var(--ink)]"
  }`;

const menuLinkClasses = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-2 text-[14px] transition-colors hover:bg-[var(--paper)] ${
    isActive ? "font-medium text-[var(--index)]" : "text-[var(--ink-soft)] hover:text-[var(--ink)]"
  }`;

// Below md the tabs and controls move into a hamburger menu; from md up they sit in the bar as before.
export function Header() {
  const { logout } = useAuth();
  const { collapsed } = useSidebar();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close the menu on a click outside it or on Escape.
  useEffect(() => {
    if (!menuOpen) return;
    const onPointer = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  return (
    <header
      className={`fixed inset-x-0 top-0 z-20 flex ${HEADER_HEIGHT_CLASS} items-stretch border-b border-[var(--line)] ${glass}`}
    >
      {/* Same width as the Ask sidebar (220px — keep in step with
          Sidebar.tsx and App.tsx's lg:pl-[220px]) with the same right
          border, so the logo cell and the sidebar read as one column, and
          the tabs start exactly where the main section does. */}
      <div
        className={`flex shrink-0 items-center overflow-hidden transition-[width,padding] duration-200 ease-in-out motion-reduce:transition-none ${collapsed ? "px-2" : "px-4"} lg:justify-center lg:border-r lg:border-[var(--line)] ${
          collapsed ? SIDEBAR_WIDTH.closed : SIDEBAR_WIDTH.open
        }`}
      >
        {/* "/" redirects to /ask (see App.tsx), so the logo is always "home". */}
        <Link
          to="/"
          aria-label="Docent — go to Ask"
          className="font-display text-base font-semibold tracking-[0.16em] text-[var(--edit)] no-underline transition-opacity hover:opacity-75 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--index)] lg:text-lg"
        >
          {collapsed ? "D" : (
            <>
              DOC<span className="text-[var(--locator)]">·</span>ENT
            </>
          )}
        </Link>
      </div>

      <nav className="hidden min-w-0 flex-1 overflow-x-auto md:flex">
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} className={tabClasses}>
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="flex-1 md:hidden" />

      <div className="hidden md:flex">
        <ModelControl />
      </div>

      <div className="hidden md:flex">
        <ThemeToggle />
      </div>

      <button
        onClick={logout}
        className="font-mono hidden shrink-0 cursor-pointer items-center gap-1.5 self-center px-4 text-[11px] uppercase tracking-wider text-[var(--ink-soft)] hover:text-[var(--ink)] md:flex sm:px-6"
      >
        <LogOut size={13} /> Log out
      </button>

      <div ref={menuRef} className="relative flex items-center md:hidden">
        <button
          type="button"
          onClick={() => setMenuOpen((o) => !o)}
          aria-label={menuOpen ? "Close menu" : "Open menu"}
          aria-expanded={menuOpen}
          className="flex h-full cursor-pointer items-center px-4 text-[var(--ink-soft)] hover:text-[var(--ink)]"
        >
          {menuOpen ? <X size={18} /> : <Menu size={18} />}
        </button>

        {menuOpen && (
          <div
            role="menu"
            className={`${card} fixed right-3 top-14 z-30 flex w-[min(92vw,300px)] flex-col gap-1 p-2`}
          >
            {NAV.map((item) => (
              <NavLink key={item.to} to={item.to} className={menuLinkClasses} onClick={() => setMenuOpen(false)}>
                {item.label}
              </NavLink>
            ))}

            <div className="my-1 border-t border-[var(--line)]" />

            <div className="px-1">
              <ModelControl />
            </div>

            <div className="flex items-center justify-between px-3 py-1.5">
              <span className="font-mono text-[11px] uppercase tracking-wider text-[var(--ink-soft)]">Theme</span>
              <ThemeToggle />
            </div>

            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);
                logout();
              }}
              className="font-mono flex items-center gap-2 rounded-md px-3 py-2 text-left text-[11px] uppercase tracking-wider text-[var(--ink-soft)] hover:bg-[var(--paper)] hover:text-[var(--ink)]"
            >
              <LogOut size={13} /> Log out
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
