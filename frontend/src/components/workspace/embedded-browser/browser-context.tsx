/* Implementation note. */

import { createContext, useContext, useState, type ReactNode } from "react";

export type DevicePreset = "desktop" | "tablet" | "mobile";

interface BrowserContextType {
  url: string;
  mode: DevicePreset;
  renderMode: "iframe" | "webview";
  setUrl: (url: string) => void;
  setMode: (mode: DevicePreset) => void;
  setRenderMode: (mode: "iframe" | "webview") => void;
  toggle: () => void;
  isOpen: boolean;
}

const BrowserContext = createContext<BrowserContextType | null>(null);

export function BrowserProvider({ children }: { children: ReactNode }) {
  const [url, setUrl] = useState("");
  const [mode, setMode] = useState<DevicePreset>("desktop");
  const [renderMode, setRenderMode] = useState<"iframe" | "webview">("iframe");
  const [isOpen, setIsOpen] = useState(false);

  const toggle = () => setIsOpen((prev) => !prev);

  return (
    <BrowserContext.Provider
      value={{
        url,
        mode,
        renderMode,
        setUrl,
        setMode,
        setRenderMode,
        toggle,
        isOpen,
      }}
    >
      {children}
    </BrowserContext.Provider>
  );
}

export function useBrowserPanel() {
  const context = useContext(BrowserContext);
  if (!context) {
    throw new Error("useBrowserPanel must be used within BrowserProvider");
  }
  return context;
}
