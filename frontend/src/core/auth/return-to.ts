export const DEFAULT_AUTH_RETURN_TO = "/workspace";

/** Keep redirects inside this app and preserve the full path, query, and hash. */
export function sanitizeAuthReturnTo(
  value: string | null | undefined,
  fallback = DEFAULT_AUTH_RETURN_TO,
): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return fallback;
  }

  try {
    const base = "https://echo.invalid";
    const parsed = new URL(value, base);
    if (parsed.origin !== base) return fallback;
    if (parsed.pathname === "/login" || parsed.pathname === "/register") {
      return fallback;
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return fallback;
  }
}

export function authReturnToFromSearch(search: string): string {
  return sanitizeAuthReturnTo(new URLSearchParams(search).get("returnTo"));
}

function authPathWithReturnTo(path: "/login" | "/register", returnTo: string) {
  const safeReturnTo = sanitizeAuthReturnTo(returnTo);
  return `${path}?returnTo=${encodeURIComponent(safeReturnTo)}`;
}

/** Route an expired workspace session back through the OS login screen. */
export function desktopLoginPathWithReturnTo(returnTo: string): string {
  const safeReturnTo = sanitizeAuthReturnTo(returnTo);
  return `/desktop?returnTo=${encodeURIComponent(safeReturnTo)}`;
}

export function loginPathWithReturnTo(returnTo: string): string {
  return authPathWithReturnTo("/login", returnTo);
}

export function registerPathWithReturnTo(returnTo: string): string {
  return authPathWithReturnTo("/register", returnTo);
}
