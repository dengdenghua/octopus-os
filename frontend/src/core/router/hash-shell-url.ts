export function toHashRouterShellUrl(route: string) {
  if (!route) return "/#/";
  if (route.startsWith("/#/")) return route;
  if (route.startsWith("#/")) return `/${route}`;
  const normalized = route.startsWith("/") ? route : `/${route}`;
  return `/#${canonicalWorkspaceHashRoute(normalized).slice(1)}`;
}

function splitRouteSearch(route: string): { pathname: string; search: string } {
  const queryIndex = route.indexOf("?");
  if (queryIndex === -1) return { pathname: route, search: "" };
  return {
    pathname: route.slice(0, queryIndex) || "/",
    search: route.slice(queryIndex),
  };
}

function canonicalWorkspaceHashRoute(route: string): string {
  const normalized = route.startsWith("/") ? route : `/${route}`;
  const { pathname, search } = splitRouteSearch(normalized);

  if (
    pathname === "/workspace" ||
    pathname === "/workspace/" ||
    pathname === "/workspace/realtime" ||
    pathname === "/workspace/realtime/"
  ) {
    return `#/workspace/realtime/new${search}`;
  }

  return `#${normalized}`;
}

function normalizeLegacyHashRoute(hash: string): string {
  const route = hash.startsWith("#") ? hash.slice(1) : hash;
  const normalized = route.startsWith("/") ? route : `/${route}`;
  const canonical = canonicalWorkspaceHashRoute(normalized);
  if (canonical !== `#${normalized}`) return canonical;
  return hash;
}

const BUILT_WEBUI_PREFIX = "/ui";

function isBuiltWebUiPath(pathname: string): boolean {
  return (
    pathname === BUILT_WEBUI_PREFIX ||
    pathname === `${BUILT_WEBUI_PREFIX}/` ||
    pathname.startsWith(`${BUILT_WEBUI_PREFIX}/`)
  );
}

function currentShellPrefix(): string {
  if (typeof window === "undefined") return "";
  return isBuiltWebUiPath(window.location.pathname) ? BUILT_WEBUI_PREFIX : "";
}

function onCurrentShell(rootHashUrl: string): string {
  const prefix = currentShellPrefix();
  return prefix ? `${prefix}${rootHashUrl}` : rootHashUrl;
}

function normalizeHistoryUrl(
  url: string | URL | null | undefined,
): string | URL | null | undefined {
  if (typeof url !== "string") return url;
  if (url.startsWith(`${BUILT_WEBUI_PREFIX}/#/`)) return url;
  if (url.startsWith("/#/")) return onCurrentShell(url);
  if (url.startsWith("#/")) return onCurrentShell(`/${url}`);
  if (!url.startsWith("/")) return url;
  const route = isBuiltWebUiPath(url)
    ? url.slice(BUILT_WEBUI_PREFIX.length) || "/"
    : url;
  return onCurrentShell(toHashRouterShellUrl(route));
}

export function normalizeHashRouterShellUrl() {
  if (typeof window === "undefined") return;
  const { pathname, search, hash } = window.location;
  if (!hash.startsWith("#/")) {
    // `/ui/` is the production shell mounted by FastAPI. It is not an SPA
    // route and must survive URL normalisation so relative assets and shared
    // hash links keep working after reload.
    if (pathname === BUILT_WEBUI_PREFIX) {
      window.history.replaceState(
        window.history.state,
        "",
        `${BUILT_WEBUI_PREFIX}/${search}`,
      );
      return;
    }
    if (pathname === `${BUILT_WEBUI_PREFIX}/`) return;
    if (pathname.startsWith(`${BUILT_WEBUI_PREFIX}/`)) {
      const route = pathname.slice(BUILT_WEBUI_PREFIX.length) || "/";
      window.history.replaceState(
        window.history.state,
        "",
        `${BUILT_WEBUI_PREFIX}${toHashRouterShellUrl(`${route}${search}`)}`,
      );
      return;
    }
    if (pathname === "/" || pathname === "") return;
    window.history.replaceState(
      window.history.state,
      "",
      toHashRouterShellUrl(`${pathname}${search}`),
    );
    return;
  }
  const normalizedHash = normalizeLegacyHashRoute(hash);
  const shellPrefix = isBuiltWebUiPath(pathname) ? BUILT_WEBUI_PREFIX : "";
  const shellPath = `${shellPrefix}/${search}${normalizedHash}`;
  if (
    (pathname === `${shellPrefix}/` ||
      (!shellPrefix && (pathname === "/" || pathname === ""))) &&
    normalizedHash === hash
  ) {
    return;
  }
  window.history.replaceState(window.history.state, "", shellPath);
}

export function installHashRouterShellUrlNormalizer() {
  normalizeHashRouterShellUrl();
  if (typeof window === "undefined") return;
  const win = window as Window & {
    __echoHashRouterPatched?: boolean;
  };
  if (!win.__echoHashRouterPatched) {
    const originalPushState = window.history.pushState.bind(window.history);
    const originalReplaceState = window.history.replaceState.bind(
      window.history,
    );
    window.history.pushState = function patchedPushState(data, unused, url) {
      return originalPushState(data, unused, normalizeHistoryUrl(url));
    } as typeof window.history.pushState;
    window.history.replaceState = function patchedReplaceState(
      data,
      unused,
      url,
    ) {
      return originalReplaceState(data, unused, normalizeHistoryUrl(url));
    } as typeof window.history.replaceState;
    win.__echoHashRouterPatched = true;
  }
  window.addEventListener("hashchange", normalizeHashRouterShellUrl);
}
