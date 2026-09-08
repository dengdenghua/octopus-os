import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { currentActorId } from "@/core/auth/api";
import { authReturnToFromSearch } from "@/core/auth/return-to";
import { normalizeWorkspaceRoute } from "@/core/router/desktop-workspace-route";

/** Keep pending links in the URL until login succeeds, then consume them once. */
export function useDesktopWorkspaceRequest({
  ready,
  onOpen,
}: {
  ready: boolean;
  onOpen: (route: string, options: { state: unknown }) => void;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const consumed = useRef<string | null>(null);
  const actor = currentActorId();
  useEffect(() => {
    if (!ready || location.pathname !== "/desktop") return;
    const params = new URLSearchParams(location.search);
    if (!params.has("workspace") && !params.has("returnTo")) return;
    // Include the actor so an auth transition cannot inherit the previous
    // account's consumed marker when a pending desktop URL is retained.
    const requestKey = `${actor}:${location.key}:${location.search}`;
    if (consumed.current === requestKey) return;
    consumed.current = requestKey;

    const returnTo = params.has("returnTo")
      ? authReturnToFromSearch(location.search)
      : null;
    const route = normalizeWorkspaceRoute(params.get("workspace") ?? returnTo);
    if (route) onOpen(route, { state: location.state });
    else if (returnTo) {
      navigate(returnTo, { replace: true, state: location.state });
      return;
    }

    params.delete("workspace");
    params.delete("returnTo");
    const query = params.toString();
    navigate(`/desktop${query ? `?${query}` : ""}${location.hash}`, {
      replace: true,
      state: null,
    });
  }, [actor, ready, location, navigate, onOpen]);
}
