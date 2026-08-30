import { useEffect, useState } from "react";

const RECENT_WORKDIRS_KEY = "echo:recentWorkdirs";

function isAbsolutePath(value: string) {
  return value.startsWith("/") || /^[A-Za-z]:[\\/]/.test(value);
}

function readActiveProjectRoot(): string | null {
  if (typeof window === "undefined") return null;
  const hashQuery = window.location.hash.includes("?")
    ? window.location.hash.slice(window.location.hash.indexOf("?") + 1)
    : "";
  const routePath = new URLSearchParams(
    window.location.search || hashQuery,
  ).get("workspace_path");
  if (routePath && isAbsolutePath(routePath)) return routePath;
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(RECENT_WORKDIRS_KEY) ?? "[]",
    ) as unknown;
    if (Array.isArray(parsed)) {
      const first = parsed.find(
        (item): item is string =>
          typeof item === "string" && isAbsolutePath(item),
      );
      return first ?? null;
    }
  } catch {
    // A malformed recent-project entry should not break the knowledge page.
  }
  return null;
}

export function activateProjectRoot(path: string) {
  if (typeof window === "undefined" || !isAbsolutePath(path)) return;
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(RECENT_WORKDIRS_KEY) ?? "[]",
    ) as unknown;
    const recent = Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string")
      : [];
    const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "");
    const next = [
      path,
      ...recent.filter(
        (item) => item.replace(/\\/g, "/").replace(/\/+$/, "") !== normalized,
      ),
    ].slice(0, 6);
    window.localStorage.setItem(RECENT_WORKDIRS_KEY, JSON.stringify(next));
  } catch {
    window.localStorage.setItem(RECENT_WORKDIRS_KEY, JSON.stringify([path]));
  }
  window.dispatchEvent(
    new CustomEvent("echo:workdir-selected", {
      detail: { path, source: "wiki" },
    }),
  );
}

export function useActiveProjectRoot() {
  const [root, setRoot] = useState<string | null>(readActiveProjectRoot);

  useEffect(() => {
    const onWorkDirSelected = (event: Event) => {
      const path = (event as CustomEvent<{ path?: unknown }>).detail?.path;
      if (typeof path === "string" && isAbsolutePath(path)) setRoot(path);
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === RECENT_WORKDIRS_KEY) setRoot(readActiveProjectRoot());
    };
    window.addEventListener("echo:workdir-selected", onWorkDirSelected);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("echo:workdir-selected", onWorkDirSelected);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  return root;
}
