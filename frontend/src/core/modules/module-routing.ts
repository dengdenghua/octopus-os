/** Routing and sidebar placement for workspace applications. */
import { MODULE_CATALOG } from "./catalog";
import type { ModuleDescriptor } from "./types";

/** Path portion of a route spec, dropping `?query` / `#hash`. */
function routePath(to: string): string {
  return to.split(/[?#]/)[0] || to;
}

/** The `library` query param, when present. */
function routeLibrary(to: string): string | null {
  const index = to.indexOf("?");
  if (index === -1) return null;
  return new URLSearchParams(to.slice(index)).get("library");
}

/** Match application routes, including optional category-specific destinations. */
export function moduleMatchesLocation(
  descriptor: ModuleDescriptor,
  pathname: string,
  search: string,
): boolean {
  const targetPath = routePath(descriptor.to);
  const currentPath = routePath(pathname);
  if (currentPath !== targetPath && !currentPath.startsWith(`${targetPath}/`)) {
    return false;
  }

  const targetLibrary = routeLibrary(descriptor.to);
  if (targetLibrary === null) return true;

  const currentLibrary = new URLSearchParams(
    search ||
      (pathname.includes("?") ? pathname.slice(pathname.indexOf("?")) : ""),
  ).get("library");
  return currentLibrary === targetLibrary;
}

/**
 * The module owning this location, if any. Unlisted routes (chat threads,
 * settings, login…) return undefined and are never gated.
 */
export function moduleForLocation(
  pathname: string,
  search: string,
): ModuleDescriptor | undefined {
  return MODULE_CATALOG.find((m) => moduleMatchesLocation(m, pathname, search));
}

/** Filter route specs down to enabled modules, preserving input order. */
export function filterRoutesByEnabled<T extends { to: string }>(
  routes: T[],
  enabledIds: readonly string[],
): T[] {
  const enabled = new Set(enabledIds);
  return routes.filter((route) => {
    const descriptor = MODULE_CATALOG.find((m) => m.to === route.to);
    // Routes outside the catalog are structural, not pluggable — keep them.
    if (!descriptor) return true;
    if (!descriptor.removable) return true;
    return enabled.has(descriptor.id);
  });
}
