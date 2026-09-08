import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { currentActorId } from "@/core/auth/api";
import { threadIdForWorkspaceRoute } from "@/core/router/task-workspace-route";
import type { DesktopWindow } from "./app-window";

type State = {
  windows: DesktopWindow[];
  minimized: Set<string>;
  focusedWin: string | null;
};
type Action =
  | { type: "open"; window: DesktopWindow }
  | { type: "reset" }
  | { type: "close" | "minimize" | "focus"; id: string }
  | { type: "route"; id: string; route: string; url: string };

function reduce(state: State, action: Action): State {
  if (action.type === "reset") {
    return { windows: [], minimized: new Set<string>(), focusedWin: null };
  }
  const minimized = new Set(state.minimized);
  if (action.type === "open") {
    const existing = state.windows.find(
      (entry) =>
        (action.window.workspaceRoute
          ? !action.window.threadId &&
            !entry.threadId &&
            entry.workspaceRoute === action.window.workspaceRoute
          : entry.id === action.window.id) ||
        (action.window.threadId && entry.threadId === action.window.threadId),
    );
    const target = existing ?? action.window;
    const artifact = action.window.workspaceRoute
      ? new URL(
          action.window.workspaceRoute,
          "https://echo.invalid",
        ).searchParams.get("artifact")
      : null;
    const updated =
      existing && artifact
        ? {
            ...existing,
            artifactRequest: {
              path: artifact,
              revision: (existing.artifactRequest?.revision ?? 0) + 1,
            },
          }
        : target;
    minimized.delete(target.id);
    return {
      windows: existing
        ? state.windows.map((entry) =>
            entry.id === existing.id ? updated : entry,
          )
        : [...state.windows, target],
      minimized,
      focusedWin: target.id,
    };
  }
  if (action.type === "route") {
    const threadId = threadIdForWorkspaceRoute(action.route);
    return {
      ...state,
      windows: state.windows.map((entry) =>
        entry.id === action.id
          ? {
              ...entry,
              threadId,
              workspaceRoute: action.route,
              url: action.url,
            }
          : entry,
      ),
    };
  }
  if (action.type === "focus")
    return state.windows.some((entry) => entry.id === action.id)
      ? { ...state, focusedWin: action.id }
      : state;
  const windows =
    action.type === "close"
      ? state.windows.filter((entry) => entry.id !== action.id)
      : state.windows;
  if (action.type === "close") minimized.delete(action.id);
  else minimized.add(action.id);
  const focusedWin =
    state.focusedWin === action.id
      ? ([...windows].reverse().find((entry) => !minimized.has(entry.id))?.id ??
        null)
      : state.focusedWin;
  return { windows, minimized, focusedWin };
}

/** Resolve identity, restore minimization and focus in a single state transition. */
export function useDesktopWindows() {
  const actor = currentActorId();
  const ownerRef = useRef(actor);
  const [owner, setOwner] = useState(actor);
  const [state, dispatch] = useReducer(reduce, {
    windows: [],
    minimized: new Set<string>(),
    focusedWin: null,
  });
  useEffect(() => {
    if (ownerRef.current === actor) return;
    ownerRef.current = actor;
    setOwner(actor);
    dispatch({ type: "reset" });
  }, [actor]);
  const openWindow = useCallback(
    (window: DesktopWindow) => dispatch({ type: "open", window }),
    [],
  );
  const closeWindow = useCallback(
    (id: string) => dispatch({ type: "close", id }),
    [],
  );
  const minimizeWindow = useCallback(
    (id: string) => dispatch({ type: "minimize", id }),
    [],
  );
  const focusWindow = useCallback(
    (id: string) => dispatch({ type: "focus", id }),
    [],
  );
  const reportWindowRoute = useCallback(
    (id: string, route: string, url: string) =>
      dispatch({ type: "route", id, route, url }),
    [],
  );
  return {
    ...(owner === actor
      ? state
      : { windows: [], minimized: new Set<string>(), focusedWin: null }),
    openWindow,
    closeWindow,
    minimizeWindow,
    focusWindow,
    reportWindowRoute,
  };
}
