import { Navigate, Route, Routes } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { useAuth } from "./auth/AuthContext";
import { Ask } from "./pages/Ask";
import { Documents } from "./pages/Documents";
import { History } from "./pages/History";
import { Login } from "./pages/Login";
import { Overview } from "./pages/Overview";
import { Status } from "./pages/Status";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { token } = useAuth();
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-[var(--paper)] text-[13px] text-[var(--ink)] lg:flex">
      <Sidebar />
      <main className="w-full max-w-7xl px-6 py-7">{children}</main>
    </div>
  );
}

function guarded(element: React.ReactNode) {
  return (
    <RequireAuth>
      <Layout>{element}</Layout>
    </RequireAuth>
  );
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={guarded(<Overview />)} />
      <Route path="/ask" element={guarded(<Ask />)} />
      <Route path="/documents" element={guarded(<Documents />)} />
      <Route path="/history" element={guarded(<History />)} />
      <Route path="/ingestion" element={guarded(<Status />)} />
      {/* Old bookmarks from the two-page layout. */}
      <Route path="/status" element={<Navigate to="/ingestion" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
