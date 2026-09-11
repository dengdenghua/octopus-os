import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { eventBus } from "@/core/events/event-bus";
import { parseDesktopAction, type DesktopAction } from "./desktop-actions";

export function useDesktopAction(
  ready: boolean,
  onAction: (action: DesktopAction) => void,
) {
  const location = useLocation();
  const navigate = useNavigate();
  const consumed = useRef<string | null>(null);
  useEffect(() => {
    if (!ready) return;
    return eventBus.on("desktop:photo-search", ({ query }) => {
      const action = parseDesktopAction(
        new URLSearchParams({
          desktopAction: "photos.search",
          query,
        }).toString(),
      );
      if (action) onAction(action);
    });
  }, [ready, onAction]);
  useEffect(() => {
    if (!ready || location.pathname !== "/desktop") return;
    const params = new URLSearchParams(location.search);
    if (!params.has("desktopAction")) return;
    const key = location.key + location.search;
    if (consumed.current === key) return;
    consumed.current = key;
    const action = parseDesktopAction(location.search);
    if (action) onAction(action);
    for (const name of ["desktopAction", "query", "path"]) params.delete(name);
    navigate(
      {
        pathname: location.pathname,
        search: params.toString(),
        hash: location.hash,
      },
      { replace: true, state: location.state },
    );
  }, [ready, location, navigate, onAction]);
}
