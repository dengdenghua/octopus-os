/** Workspace URLs identify content; the desktop owns their presentation. */
export function normalizeWorkspaceRoute(value: string | null): string | null {
  if (!value || !/^\/workspace(?:[/?#]|$)/.test(value)) return null;
  const url = new URL(value, "https://echo.invalid");
  if (
    url.origin !== "https://echo.invalid" ||
    !/^\/workspace(?:\/|$)/.test(url.pathname)
  )
    return null;
  if (url.searchParams.get("shell") === "workbench") {
    url.searchParams.delete("shell");
  }
  return `${url.pathname}${url.search}${url.hash}`;
}

export function workbenchRoute(route: string): string {
  const normalized =
    normalizeWorkspaceRoute(route) || "/workspace/realtime/new";
  const url = new URL(normalized, "https://echo.invalid");
  url.searchParams.set("presentation", "workbench");
  return `${url.pathname}${url.search}${url.hash}`;
}

/** Preserve the explicit workbench host while following an internal route. */
export function preserveWorkbenchPresentation(
  route: string,
  currentSearch = "",
): string {
  if (new URLSearchParams(currentSearch).get("presentation") !== "workbench") {
    return route;
  }
  try {
    const url = new URL(route, "https://echo.invalid");
    if (
      url.origin !== "https://echo.invalid" ||
      !/^\/workspace(?:\/|$)/.test(url.pathname)
    ) {
      return route;
    }
    if (!url.searchParams.has("presentation")) {
      url.searchParams.set("presentation", "workbench");
    }
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return route;
  }
}

export function desktopWorkspaceRoute(route: string): string {
  const workspace = normalizeWorkspaceRoute(route);
  return workspace
    ? `/desktop?workspace=${encodeURIComponent(workspace)}`
    : "/desktop";
}
