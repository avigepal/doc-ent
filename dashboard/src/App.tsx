import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Header } from "./components/Header";
import { ViewerProvider } from "./components/DocumentViewer";
import { Sidebar } from "./components/Sidebar";
import { useAuth } from "./auth/AuthContext";
import { Ask } from "./pages/Ask";
import { Documents } from "./pages/Documents";
import { History } from "./pages/History";
import { Login } from "./pages/Login";
import { Progress } from "./pages/Progress";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { token } = useAuth();
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function Layout({ children }: { children: React.ReactNode }) {
  // The tabs live in the fixed Header (pt-12 below reserves its height).
  // The Sidebar — chats and Scope — only exists for Ask, so every other
  // page gets the full width. It is lg:fixed (see Sidebar.tsx) so it stays
  // put while Ask's growing chat thread scrolls underneath; lg:pl-[220px]
  // reserves the space it takes out of flow, as padding (not margin) so
  // `main` itself still spans the full remaining width — letting the
  // inner mx-auto max-w-7xl center within THAT, the same way Ask's fixed
  // composer bar centers its own content. A margin would just shift the
  // max-w-7xl block rightward without giving it room to center.
  const onAsk = useLocation().pathname === "/ask";

  return (
    <div className="min-h-screen bg-[var(--paper)] pt-12 text-[14px] text-[var(--ink)]">
      <Header />
      {onAsk && <Sidebar />}
      <main className={onAsk ? "lg:pl-[220px]" : undefined}>
        <div className="mx-auto max-w-7xl px-6 py-7">{children}</div>
      </main>
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
    <ViewerProvider>
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/ask" element={guarded(<Ask />)} />
      <Route path="/documents" element={guarded(<Documents />)} />
      <Route path="/history" element={guarded(<History />)} />
      <Route path="/progress" element={guarded(<Progress />)} />
      {/* Old bookmarks (/status, /ingestion, the removed Overview page) fall through to "*" below. */}
      <Route path="/" element={<Navigate to="/ask" replace />} />
      <Route path="*" element={<Navigate to="/ask" replace />} />
    </Routes>
    </ViewerProvider>
  );
}

export default App;
