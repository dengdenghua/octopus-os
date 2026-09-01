/* Implementation note. */

import { swallow } from "@/core/utils/log";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useState,
  type ReactNode,
} from "react";

import type { DevicePreset } from "../workspace/embedded-browser/browser-context";

const STORAGE_KEY = "echo:browser-state";
const HISTORY_KEY = "echo:browser-history";
const BOOKMARKS_KEY = "echo:browser-bookmarks";
const SETTINGS_KEY = "echo:browser-settings";
export const BROWSER_OPEN_URL_REQUEST_KEY = "echo:browser-open-url-request";
export const BROWSER_OPEN_URL_REQUEST_EVENT =
  "echo:browser-open-url-request";
export const BROWSER_OPEN_URL_ACK_EVENT = "echo:browser-open-url-ack";
export const BROWSER_EDIT_HOME_EVENT = "echo:browser-edit-home";
export const BROWSER_HOME_URL = "echo://home";
const LEGACY_DEFAULT_HOMEPAGE = "https://www.google.com";
const DEFAULT_HOMEPAGE = BROWSER_HOME_URL;

export interface BrowserSettings {
  homepage: string;
  searchEngine: "google" | "bing" | "baidu" | "duckduckgo";
}

const DEFAULT_SETTINGS: BrowserSettings = {
  homepage: DEFAULT_HOMEPAGE,
  searchEngine: "google",
};

export const SEARCH_ENGINE_URLS: Record<
  BrowserSettings["searchEngine"],
  string
> = {
  google: "https://www.google.com/search?q=",
  bing: "https://www.bing.com/search?q=",
  baidu: "https://www.baidu.com/s?wd=",
  duckduckgo: "https://duckduckgo.com/?q=",
};

function loadSettings(): BrowserSettings {
  if (typeof window === "undefined") return DEFAULT_SETTINGS;
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw);
    const next = { ...DEFAULT_SETTINGS, ...parsed };
    if (next.homepage === LEGACY_DEFAULT_HOMEPAGE) {
      next.homepage = BROWSER_HOME_URL;
    }
    return next;
  } catch (e) {
    swallow(e);
    return DEFAULT_SETTINGS;
  }
}

function saveSettings(s: BrowserSettings): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
  } catch (e) {
    swallow(e, "storage");
  }
}
const MAX_HISTORY = 500;
const MAX_BOOKMARKS = 200;
const MAX_CLOSED_TABS = 20;

export interface HistoryEntry {
  url: string;
  title: string;
  favicon?: string;
  visitedAt: number;
}

export interface Bookmark {
  url: string;
  title: string;
  favicon?: string;
  addedAt: number;
}

function loadHistory(): HistoryEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    swallow(e);
    return [];
  }
}

function saveHistory(items: HistoryEntry[]): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(
      HISTORY_KEY,
      JSON.stringify(items.slice(0, MAX_HISTORY)),
    );
  } catch (e) {
    swallow(e, "storage");
  }
}

function loadBookmarks(): Bookmark[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(BOOKMARKS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    swallow(e);
    return [];
  }
}

function saveBookmarks(items: Bookmark[]): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(
      BOOKMARKS_KEY,
      JSON.stringify(items.slice(0, MAX_BOOKMARKS)),
    );
  } catch (e) {
    swallow(e, "storage");
  }
}

export interface BrowserTab {
  id: string;
  url: string;
  title: string;
  favicon?: string;
  isLoading: boolean;
  device: DevicePreset;
  crash?: {
    reason: string;
    exitCode: number;
    occurredAt: number;
    attempts: number;
    autoRecovering?: boolean;
  };
}

export interface ClosedBrowserTab extends BrowserTab {
  closedAt: number;
}

export interface BrowserOpenUrlRequest {
  url: string;
  requestId?: string;
  title?: string;
  device?: DevicePreset;
  source?: string;
  sessionId?: string;
}

export interface BrowserOpenUrlAck {
  requestId: string;
  accepted: boolean;
}

interface BrowserState {
  tabs: BrowserTab[];
  closedTabs: ClosedBrowserTab[];
  activeId: string | null;
  copilotOpen: boolean;
  copilotWidth: number;
  homeSeeded?: boolean;
}

type Action =
  | { type: "OPEN_TAB"; url?: string; patch?: Partial<BrowserTab> }
  | { type: "CLOSE_TAB"; id: string }
  | { type: "ACTIVATE_TAB"; id: string }
  | { type: "REORDER_TAB"; from: number; to: number }
  | { type: "PATCH_TAB"; id: string; patch: Partial<BrowserTab> }
  | { type: "SET_COPILOT_OPEN"; open: boolean }
  | { type: "SET_COPILOT_WIDTH"; width: number }
  | { type: "RESTORE_CLOSED_TAB"; id?: string };

function genId(): string {
  return `tab_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}

function freshTab(url?: string, homepage?: string): BrowserTab {
  const target = url ?? homepage ?? DEFAULT_HOMEPAGE;
  return {
    id: genId(),
    url: target,
    title: target === BROWSER_HOME_URL ? "AI 浏览器桌面" : target,
    isLoading: false,
    device: "desktop",
  };
}

function reducer(state: BrowserState, action: Action): BrowserState {
  switch (action.type) {
    case "OPEN_TAB": {
      // Implementation note.
      // Implementation note.
      // Implementation note.
      // Implementation note.
      const tab = { ...freshTab(action.url), ...action.patch };
      return { ...state, tabs: [...state.tabs, tab], activeId: tab.id };
    }
    case "CLOSE_TAB": {
      const idx = state.tabs.findIndex((t) => t.id === action.id);
      if (idx < 0) return state;
      const closedTab = state.tabs[idx];
      if (!closedTab) return state;
      const next = state.tabs.filter((t) => t.id !== action.id);
      const closedTabs: ClosedBrowserTab[] = [
        { ...closedTab, isLoading: false, closedAt: Date.now() },
        ...state.closedTabs.filter((tab) => tab.id !== action.id),
      ].slice(0, MAX_CLOSED_TABS);
      let activeId = state.activeId;
      if (activeId === action.id) {
        // Implementation note.
        const neighbor = next[idx] ?? next[idx - 1] ?? null;
        activeId = neighbor?.id ?? null;
      }
      // Implementation note.
      if (next.length === 0) {
        const tab = freshTab();
        return { ...state, tabs: [tab], closedTabs, activeId: tab.id };
      }
      return { ...state, tabs: next, closedTabs, activeId };
    }
    case "ACTIVATE_TAB":
      return state.tabs.some((t) => t.id === action.id)
        ? { ...state, activeId: action.id }
        : state;
    case "REORDER_TAB": {
      if (action.from === action.to) return state;
      const next = [...state.tabs];
      const [moved] = next.splice(action.from, 1);
      if (!moved) return state;
      next.splice(action.to, 0, moved);
      return { ...state, tabs: next };
    }
    case "PATCH_TAB": {
      const tabs = state.tabs.map((t) =>
        t.id === action.id ? { ...t, ...action.patch } : t,
      );
      return { ...state, tabs };
    }
    case "SET_COPILOT_OPEN":
      return { ...state, copilotOpen: action.open };
    case "SET_COPILOT_WIDTH":
      return {
        ...state,
        copilotWidth: Math.max(280, Math.min(720, action.width)),
      };
    case "RESTORE_CLOSED_TAB": {
      const index = action.id
        ? state.closedTabs.findIndex((tab) => tab.id === action.id)
        : 0;
      if (index < 0) return state;
      const closed = state.closedTabs[index];
      const remaining = state.closedTabs.filter(
        (_, itemIndex) => itemIndex !== index,
      );
      if (!closed) return state;
      const restored: BrowserTab = {
        ...closed,
        id: genId(),
        isLoading: false,
        crash: undefined,
      };
      return {
        ...state,
        tabs: [...state.tabs, restored],
        closedTabs: remaining,
        activeId: restored.id,
      };
    }
    default:
      return state;
  }
}

function loadInitial(): BrowserState {
  if (typeof window === "undefined") {
    return {
      tabs: [],
      closedTabs: [],
      activeId: null,
      copilotOpen: false,
      copilotWidth: 380,
      homeSeeded: true,
    };
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<BrowserState>;
      const tabs = Array.isArray(parsed.tabs)
        ? parsed.tabs
            .filter(
              (t): t is BrowserTab =>
                !!t && typeof (t as BrowserTab).id === "string",
            )
            .map((t) => ({ ...t, isLoading: false }))
        : [];
      if (tabs.length === 0) tabs.push(freshTab());
      const closedTabs = Array.isArray(parsed.closedTabs)
        ? parsed.closedTabs
            .filter(
              (tab): tab is ClosedBrowserTab =>
                !!tab &&
                typeof (tab as ClosedBrowserTab).id === "string" &&
                typeof (tab as ClosedBrowserTab).closedAt === "number",
            )
            .slice(0, MAX_CLOSED_TABS)
        : [];
      const activeId =
        typeof parsed.activeId === "string" &&
        tabs.some((t) => t.id === parsed.activeId)
          ? parsed.activeId
          : (tabs[0]?.id ?? null);
      if (
        parsed.homeSeeded !== true &&
        !tabs.some((t) => t.url === BROWSER_HOME_URL)
      ) {
        const home = freshTab(BROWSER_HOME_URL);
        return {
          tabs: [home, ...tabs],
          closedTabs,
          activeId: home.id,
          copilotOpen: !!parsed.copilotOpen,
          copilotWidth:
            typeof parsed.copilotWidth === "number" ? parsed.copilotWidth : 380,
          homeSeeded: true,
        };
      }
      return {
        tabs,
        closedTabs,
        activeId,
        copilotOpen: !!parsed.copilotOpen,
        copilotWidth:
          typeof parsed.copilotWidth === "number" ? parsed.copilotWidth : 380,
        homeSeeded: true,
      };
    }
  } catch (e) {
    swallow(e);
  }
  const tab = freshTab();
  return {
    tabs: [tab],
    closedTabs: [],
    activeId: tab.id,
    copilotOpen: false,
    copilotWidth: 380,
    homeSeeded: true,
  };
}

interface BrowserStoreContextType {
  state: BrowserState;
  activeTab: BrowserTab | null;
  openTab: (url?: string, patch?: Partial<BrowserTab>) => void;
  closeTab: (id: string) => void;
  restoreClosedTab: (id?: string) => void;
  activateTab: (id: string) => void;
  reorderTab: (from: number, to: number) => void;
  patchTab: (id: string, patch: Partial<BrowserTab>) => void;
  setCopilotOpen: (open: boolean) => void;
  toggleCopilot: () => void;
  setCopilotWidth: (w: number) => void;
  // Implementation note.
  // Implementation note.
  // Implementation note.
  history: HistoryEntry[];
  bookmarks: Bookmark[];
  recordVisit: (entry: Omit<HistoryEntry, "visitedAt">) => void;
  addBookmark: (b: Omit<Bookmark, "addedAt">) => void;
  removeBookmark: (url: string) => void;
  isBookmarked: (url: string) => boolean;
  clearHistory: () => void;
  settings: BrowserSettings;
  updateSettings: (patch: Partial<BrowserSettings>) => void;
}

const BrowserStoreContext = createContext<BrowserStoreContextType | null>(null);

export function BrowserStoreProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, undefined, loadInitial);
  const [history, setHistory] = useState<HistoryEntry[]>(() => loadHistory());
  const [bookmarks, setBookmarks] = useState<Bookmark[]>(() => loadBookmarks());
  const [settings, setSettings] = useState<BrowserSettings>(() =>
    loadSettings(),
  );

  const updateSettings = useCallback((patch: Partial<BrowserSettings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...patch };
      saveSettings(next);
      return next;
    });
  }, []);
  // Implementation note.
  const [, setReady] = useState(false);
  useEffect(() => {
    setReady(true);
  }, []);

  // Implementation note.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const t = setTimeout(() => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      } catch (e) {
        swallow(e, "storage");
      }
    }, 200);
    return () => clearTimeout(t);
  }, [state]);

  const recordVisit = useCallback((entry: Omit<HistoryEntry, "visitedAt">) => {
    if (!entry.url || entry.url.startsWith("about:")) return;
    setHistory((prev) => {
      // Implementation note.
      const next: HistoryEntry[] = [
        { ...entry, visitedAt: Date.now() },
        ...prev.filter((h) => h.url !== entry.url),
      ].slice(0, MAX_HISTORY);
      saveHistory(next);
      return next;
    });
  }, []);

  const addBookmark = useCallback((b: Omit<Bookmark, "addedAt">) => {
    if (!b.url) return;
    setBookmarks((prev) => {
      if (prev.some((x) => x.url === b.url)) return prev;
      const next: Bookmark[] = [{ ...b, addedAt: Date.now() }, ...prev].slice(
        0,
        MAX_BOOKMARKS,
      );
      saveBookmarks(next);
      return next;
    });
  }, []);

  const removeBookmark = useCallback((url: string) => {
    setBookmarks((prev) => {
      const next = prev.filter((x) => x.url !== url);
      saveBookmarks(next);
      return next;
    });
  }, []);

  const isBookmarked = useCallback(
    (url: string) => bookmarks.some((x) => x.url === url),
    [bookmarks],
  );

  const clearHistory = useCallback(() => {
    setHistory([]);
    saveHistory([]);
  }, []);

  const activeTab = useMemo(
    () => state.tabs.find((t) => t.id === state.activeId) ?? null,
    [state.tabs, state.activeId],
  );

  const value = useMemo<BrowserStoreContextType>(
    () => ({
      state,
      activeTab,
      openTab: (url?: string, patch?: Partial<BrowserTab>) =>
        dispatch({ type: "OPEN_TAB", url: url ?? settings.homepage, patch }),
      closeTab: (id: string) => dispatch({ type: "CLOSE_TAB", id }),
      restoreClosedTab: (id?: string) =>
        dispatch({ type: "RESTORE_CLOSED_TAB", id }),
      activateTab: (id: string) => dispatch({ type: "ACTIVATE_TAB", id }),
      reorderTab: (from: number, to: number) =>
        dispatch({ type: "REORDER_TAB", from, to }),
      patchTab: (id: string, patch: Partial<BrowserTab>) =>
        dispatch({ type: "PATCH_TAB", id, patch }),
      setCopilotOpen: (open: boolean) =>
        dispatch({ type: "SET_COPILOT_OPEN", open }),
      toggleCopilot: () =>
        dispatch({ type: "SET_COPILOT_OPEN", open: !state.copilotOpen }),
      setCopilotWidth: (w: number) =>
        dispatch({ type: "SET_COPILOT_WIDTH", width: w }),
      history,
      bookmarks,
      recordVisit,
      addBookmark,
      removeBookmark,
      isBookmarked,
      clearHistory,
      settings,
      updateSettings,
    }),
    [
      state,
      activeTab,
      history,
      bookmarks,
      recordVisit,
      addBookmark,
      removeBookmark,
      isBookmarked,
      clearHistory,
      settings,
      updateSettings,
    ],
  );

  return (
    <BrowserStoreContext.Provider value={value}>
      {children}
    </BrowserStoreContext.Provider>
  );
}

export function useBrowserStore(): BrowserStoreContextType {
  const ctx = useContext(BrowserStoreContext);
  if (!ctx)
    throw new Error("useBrowserStore must be used within BrowserStoreProvider");
  return ctx;
}

// Implementation note.
const MODE_KEY = "echo:app-mode";
export type AppMode = "workspace" | "browser";

export function getInitialAppMode(): AppMode {
  if (typeof window === "undefined") return "workspace";
  const v = localStorage.getItem(MODE_KEY);
  return v === "browser" ? "browser" : "workspace";
}

export function setAppMode(mode: AppMode): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(MODE_KEY, mode);
}

// Implementation note.
export function useAppMode(): [AppMode, (mode: AppMode) => void] {
  const [mode, setLocal] = useState<AppMode>(() => getInitialAppMode());
  const set = useCallback((next: AppMode) => {
    setLocal(next);
    setAppMode(next);
  }, []);
  return [mode, set];
}
