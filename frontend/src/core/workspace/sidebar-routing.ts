/**
 * Sidebar routing helpers — extracted from `workspace-sidebar.tsx`
 * (P3 decomposition). Pure functions, no React; independently testable.
 */
export const PRIMARY_WORKSPACE_ROUTE = "/workspace/realtime/new";
export const BROWSER_WORKSPACE_ROUTE = "/browser";

/**
 * Resolve where the EchoAI side of the workspace switch should return.
 *
 * The complete browser mode lives outside the workspace route tree. Keep the
 * last workspace location so switching back does not discard the active
 * conversation, project, or settings context.
 */
export function workspaceAgentReturnRoute(
  pathname: string,
  search = "",
  rememberedRoute?: string | null,
): string {
  const currentRoute = `${pathname}${search}`;
  if (
    pathname === BROWSER_WORKSPACE_ROUTE ||
    pathname.startsWith(`${BROWSER_WORKSPACE_ROUTE}/`)
  ) {
    if (
      rememberedRoute === "/workspace" ||
      (rememberedRoute?.startsWith("/workspace/") &&
        !rememberedRoute.startsWith(BROWSER_WORKSPACE_ROUTE))
    ) {
      return rememberedRoute;
    }
    return PRIMARY_WORKSPACE_ROUTE;
  }
  if (pathname === "/workspace" || pathname.startsWith("/workspace/")) {
    return currentRoute;
  }
  if (
    rememberedRoute === "/workspace" ||
    rememberedRoute?.startsWith("/workspace/")
  ) {
    return rememberedRoute;
  }
  return PRIMARY_WORKSPACE_ROUTE;
}

export function routePath(to: string): string {
  return to.split(/[?#]/)[0] || to;
}

export function routeSearch(to: string): string {
  const index = to.indexOf("?");
  return index === -1 ? "" : to.slice(index);
}

export function routeSearchFromLocation(
  pathname: string,
  search: string,
): string {
  if (search) return search;
  const index = pathname.indexOf("?");
  return index === -1 ? "" : pathname.slice(index);
}

export function libraryFromLocation(pathname: string, search: string): string {
  const searchValue = routeSearchFromLocation(pathname, search);
  return new URLSearchParams(searchValue).get("library") || "overview";
}

export function isStorageRouteActive(pathname: string) {
  const path = routePath(pathname);
  return (
    path === "/workspace/storage" ||
    path.startsWith("/workspace/storage/") ||
    path === "/workspace/nas" ||
    path.startsWith("/workspace/nas/") ||
    path === "/workspace/database" ||
    path.startsWith("/workspace/database/") ||
    path === "/workspace/knowledge" ||
    path.startsWith("/workspace/knowledge/")
  );
}

export function isStorageLibraryRouteActive(
  pathname: string,
  search: string,
  to: string,
) {
  const targetPath = routePath(to);
  // Knowledge graph has its own route, not a ?library= param.
  if (targetPath === "/workspace/knowledge") {
    const path = routePath(pathname);
    return (
      path === "/workspace/knowledge" ||
      path.startsWith("/workspace/knowledge/")
    );
  }
  if (!isStorageRouteActive(pathname)) return false;
  const targetLibrary = new URLSearchParams(routeSearch(to)).get("library");
  if (!targetLibrary) return false;
  return libraryFromLocation(pathname, search) === targetLibrary;
}

export function isNavRouteActive(pathname: string, to: string) {
  const path = routePath(to);
  if (path === PRIMARY_WORKSPACE_ROUTE) {
    return isChatSurfaceRoute(pathname);
  }
  return pathname === path || pathname.startsWith(`${path}/`);
}

export function isChatSurfaceRoute(pathname: string) {
  return (
    pathname === "/workspace/realtime" || pathname === "/workspace/realtime/new"
  );
}

export function isCompanySurfaceRoute(pathname: string) {
  return (
    pathname === "/workspace/agents" ||
    pathname.startsWith("/workspace/agents/") ||
    pathname === "/workspace/intelligence" ||
    pathname.startsWith("/workspace/intelligence/") ||
    pathname === "/workspace/storage" ||
    pathname.startsWith("/workspace/storage/") ||
    pathname === "/workspace/nas" ||
    pathname.startsWith("/workspace/nas/") ||
    pathname === "/workspace/database" ||
    pathname.startsWith("/workspace/database/") ||
    pathname === "/workspace/evolution" ||
    pathname.startsWith("/workspace/evolution/") ||
    pathname === "/workspace/knowledge" ||
    pathname.startsWith("/workspace/knowledge/")
  );
}

export function isCompanySurfaceActive(pathname: string, search = "") {
  if (isAgentSurfaceActive(pathname, search)) return false;
  const surfaceParam = new URLSearchParams(search).get("surface");
  return (
    surfaceParam === "company" ||
    (surfaceParam !== "chat" && isCompanySurfaceRoute(pathname))
  );
}

/**
 * Link to the agent HUD.
 *
 * `agentName` targets the HUD at one specific role — used by the per-row HUD
 * buttons in the bottom-left agent switcher, so each row can open the panel on
 * its own role rather than on whichever agent happens to be active.
 */
export function agentHudHref(options: {
  surface: "chat" | "company";
  tab?: string;
  agentName?: string;
}): string {
  const params = new URLSearchParams({ hud: "1", surface: options.surface });
  if (options.tab) params.set("tab", options.tab);
  const agentName = options.agentName?.trim();
  if (agentName) params.set("agent", agentName);
  return `/workspace/agents?${params.toString()}`;
}

export function isAgentSurfaceActive(pathname: string, search = "") {
  const params = new URLSearchParams(search);
  return (
    (pathname === "/workspace/agents" &&
      (params.get("surface") === "chat" ||
        params.get("hud") === "1" ||
        params.get("return") === "hud")) ||
    (pathname.startsWith("/workspace/agents/") &&
      (params.get("surface") === "chat" ||
        params.get("hud") === "1" ||
        params.get("return") === "hud"))
  );
}
