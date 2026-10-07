import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { Login } from "./pages/Login";
import { Status } from "./pages/Status";
import { Ask } from "./pages/Ask";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { token } = useAuth();
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

const navLinkClasses = ({ isActive }: { isActive: boolean }) =>
  `font-mono text-xs uppercase tracking-wider pb-3 border-b-2 transition-colors ${
    isActive
      ? "border-[var(--index)] text-[var(--ink)]"
      : "border-transparent text-[var(--ink-soft)] hover:text-[var(--ink)]"
  }`;

function Layout({ children }: { children: React.ReactNode }) {
  const { logout } = useAuth();
  return (
    <div className="min-h-screen bg-[var(--paper)] text-[var(--ink)]">
      <header className="border-b border-[var(--line)]">
        <div className="mx-auto flex max-w-5xl items-end gap-8 px-6 pt-5">
          <span className="font-display pb-3 text-base font-semibold tracking-tight">
            DOC<span className="text-[var(--index)]">/</span>INDEX
          </span>
          <nav className="flex gap-6">
            <NavLink to="/status" className={navLinkClasses}>
              Status
            </NavLink>
            <NavLink to="/ask" className={navLinkClasses}>
              Ask
            </NavLink>
          </nav>
          <button
            onClick={logout}
            className="font-mono ml-auto pb-3 text-xs uppercase tracking-wider text-[var(--ink-soft)] hover:text-[var(--ink)]"
          >
            Log out
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-10">{children}</main>
    </div>
  );
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/status"
        element={
          <RequireAuth>
            <Layout>
              <Status />
            </Layout>
          </RequireAuth>
        }
      />
      <Route
        path="/ask"
        element={
          <RequireAuth>
            <Layout>
              <Ask />
            </Layout>
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/status" replace />} />
    </Routes>
  );
}

export default App;
