import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

// Whether the sidebar is collapsed. The header's logo cell has to follow it, so the state lives in one
// place that both read. It is remembered per browser; storage can be blocked, so every access is guarded.

const STORAGE_KEY = "docent.sidebarCollapsed";

type SidebarState = { collapsed: boolean; toggle: () => void };

const SidebarContext = createContext<SidebarState>({ collapsed: false, toggle: () => {} });

function readStored(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function SidebarProvider({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState<boolean>(readStored);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
    } catch {
      // not remembered this time; the toggle still works
    }
  }, [collapsed]);

  return (
    <SidebarContext.Provider value={{ collapsed, toggle: () => setCollapsed((c) => !c) }}>
      {children}
    </SidebarContext.Provider>
  );
}

export function useSidebar(): SidebarState {
  return useContext(SidebarContext);
}

// Tailwind only sees class names written out in full, so every width is spelled out here.
export const SIDEBAR_WIDTH = { open: "lg:w-[220px]", closed: "lg:w-[56px]" } as const;
export const SIDEBAR_PAD = { open: "lg:pl-[220px]", closed: "lg:pl-[56px]" } as const;
export const SIDEBAR_LEFT = { open: "lg:left-[220px]", closed: "lg:left-[56px]" } as const;

