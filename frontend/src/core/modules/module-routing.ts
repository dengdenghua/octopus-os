/**
 * Pure routing helpers for pluggable modules — no React, no storage.
 *
 * Two layers matter: the sidebar hides an entry, AND the router refuses the
 * route. Hiding alone is not enough — bookmarks and history would still reach
 * a module the user removed.
 */
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

/**
 * Does `descriptor` own this location?
 *
 * Storage libraries all share `/workspace/storage`, so a bare path match would
 * make every library claim every storage URL — the `library` param has to
 * match too. A storage URL with no `library` param belongs to no single
 * module (it's the shared overview) and matches nothing.
 */
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
    search || (pathname.includes("?") ? pathname.slice(pathname.indexOf("?")) : ""),
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

/**
 * Should this location be blocked? True only when a *known* module owns it and
 * that module is disabled. Unknown routes always pass.
 */
export function isLocationBlocked(
  pathname: string,
  search: string,
  enabledIds: readonly string[],
): boolean {
  const descriptor = moduleForLocation(pathname, search);
  if (!descriptor) return false;
  return !enabledIds.includes(descriptor.id);
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
