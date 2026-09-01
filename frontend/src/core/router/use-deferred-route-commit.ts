import { useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";

/**
 * Stage a route while work is live, then commit it at a terminal boundary.
 *
 * Hash-router navigation remounts the realtime page and closes its WebSocket.
 * New tasks therefore keep `/new` mounted while the server owns an active
 * turn; the sidebar may follow the staged route independently.
 */
export function useDeferredRouteCommit() {
  const navigate = useNavigate();
  const pendingRouteRef = useRef<string | null>(null);

  const stageRoute = useCallback((path: string) => {
    pendingRouteRef.current = path;
  }, []);

  const commitRoute = useCallback(() => {
    const path = pendingRouteRef.current;
    pendingRouteRef.current = null;
    if (path) navigate(path, { replace: true });
  }, [navigate]);

  return { stageRoute, commitRoute };
}
