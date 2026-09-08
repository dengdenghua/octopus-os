import { useEffect, useState } from "react";
import { currentActorId } from "@/core/auth/api";
import {
  readRecentWorkdirs,
  recentWorkdirsStorageKey,
  writeRecentWorkdirs,
} from "./recent-workdirs";

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
    const first = readRecentWorkdirs().find((item) => isAbsolutePath(item));
    if (first) {
      return first;
    }
  } catch {
    // A malformed recent-project entry should not break the knowledge page.
  }
  return null;
}

export function activateProjectRoot(path: string) {
  if (typeof window === "undefined" || !isAbsolutePath(path)) return;
  try {
    const recent = readRecentWorkdirs();
    const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "");
    const next = [
      path,
      ...recent.filter(
        (item) => item.replace(/\\/g, "/").replace(/\/+$/, "") !== normalized,
      ),
    ].slice(0, 6);
    writeRecentWorkdirs(next);
  } catch {
    writeRecentWorkdirs([path]);
  }
  window.dispatchEvent(
    new CustomEvent("echo:workdir-selected", {
      detail: { path, source: "wiki" },
    }),
  );
}

export function useActiveProjectRoot() {
  const actor = currentActorId();
  const [root, setRoot] = useState<string | null>(readActiveProjectRoot);
  const [sessionActor, setSessionActor] = useState(actor);

  useEffect(() => {
    if (sessionActor === actor) return;
    setSessionActor(actor);
    setRoot(readActiveProjectRoot());
  }, [actor, sessionActor]);

  useEffect(() => {
    const onWorkDirSelected = (event: Event) => {
      const path = (event as CustomEvent<{ path?: unknown }>).detail?.path;
      if (typeof path === "string" && isAbsolutePath(path)) setRoot(path);
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === recentWorkdirsStorageKey())
        setRoot(readActiveProjectRoot());
    };
    window.addEventListener("echo:workdir-selected", onWorkDirSelected);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("echo:workdir-selected", onWorkDirSelected);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  return sessionActor === actor ? root : null;
}
