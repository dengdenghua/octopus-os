/**
 * Global auth-header injection for the app's own backend requests.
 *
 * The app has no single fetch client — many modules call raw `fetch()` and only
 * some remember to attach the bearer token. Once auth is enabled (guest mode was
 * removed), every token-less call to a `require_auth` endpoint 401s. A 401 from a
 * *throwing* react-query then trips the auth-failure handler in `main.tsx`, which
 * clears the token and reloads — bouncing a perfectly valid session to /login.
 * That is the "I just logged in but some pages kick me back to login" symptom.
 *
 * Rather than chase every raw fetch, patch `window.fetch` once so every request
 * to our backend's `/api/*` carries the stored token. Guarantees:
 *  - Only the app's own backend is touched (relative `/api/...` or the configured
 *    backend origin) — third-party URLs never receive the token.
 *  - An Authorization header the caller set on purpose is never overwritten.
 *  - No token (or the legacy guest sentinel) ⇒ the request is left untouched.
 */
import { getBackendBaseURL } from "@/core/config";

const TOKEN_KEY = "echo_auth_token";
const GUEST_SENTINEL = "__guest__";
export const AUTH_EXPIRED_EVENT = "echo:auth-expired";

let installed = false;

function isBackendApiRequest(rawUrl: string): boolean {
  if (!rawUrl) return false;
  // Relative path (dev proxy + same-origin prod): "/api/..." or "/api?...".
  if (rawUrl.startsWith("/api/") || rawUrl.startsWith("/api?")) return true;
  try {
    const origin =
      typeof window !== "undefined" ? window.location.origin : undefined;
    const resolved = new URL(rawUrl, origin);
    if (!resolved.pathname.startsWith("/api/")) return false;
    // Same-origin /api/* (proxied backend).
    if (origin && resolved.origin === origin) return true;
    // The explicitly configured backend origin (prod / Electron, cross-origin).
    const base = getBackendBaseURL();
    if (base) {
      const baseUrl = new URL(base, origin);
      if (resolved.origin === baseUrl.origin) return true;
    }
  } catch {
    // Unparseable URL — never attach the token.
  }
  return false;
}

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url; // Request
}

function isInteractiveLoginRequest(rawUrl: string): boolean {
  try {
    const origin =
      typeof window !== "undefined" ? window.location.origin : undefined;
    const pathname = new URL(rawUrl, origin).pathname;
    return (
      pathname === "/api/auth/oct/email/login" ||
      pathname === "/api/auth/oct/email/send" ||
      pathname === "/api/auth/local/login" ||
      pathname === "/api/auth/status" ||
      pathname === "/api/auth/providers" ||
      pathname === "/api/auth/me" ||
      pathname === "/api/auth/logout"
    );
  } catch {
    return false;
  }
}

function observeAuthResponse(
  response: Promise<Response>,
  rawUrl: string,
): Promise<Response> {
  return response.then((res) => {
    if (
      res.status === 401 &&
      res.headers.get("X-Echo-Auth-Expired") === "1" &&
      !isInteractiveLoginRequest(rawUrl) &&
      typeof window !== "undefined"
    ) {
      window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
    }
    return res;
  });
}

/**
 * Patch `window.fetch` to attach the bearer token to backend `/api` requests.
 * Idempotent: safe to call more than once. No-op outside the browser.
 */
export function installAuthFetchInterceptor(): void {
  if (installed) return;
  if (typeof window === "undefined" || typeof window.fetch !== "function") {
    return;
  }
  installed = true;
  const originalFetch = window.fetch.bind(window);

  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const rawUrl = urlOf(input);
    if (!isBackendApiRequest(rawUrl)) {
      return originalFetch(input, init);
    }
    // Browser restarts recover through an HttpOnly session cookie.  Same-origin
    // requests include it by default; Electron and configured cross-origin
    // backends need an explicit credentials policy.
    const backendInit: RequestInit = {
      ...init,
      credentials: init?.credentials ?? "include",
    };
    try {
      // Audit S-07: the token lives in sessionStorage. The localStorage
      // fallback only covers a legacy session before the one-time migration
      // (core/auth/api) has scrubbed it.
      const token =
        window.sessionStorage.getItem(TOKEN_KEY) ||
        window.localStorage.getItem(TOKEN_KEY);
      if (token && token !== GUEST_SENTINEL) {
        // Merge the Request's own headers (if any) with init's, so passing a
        // fresh `headers` to fetch doesn't drop headers the caller set.
        const headers = new Headers(
          input instanceof Request ? input.headers : undefined,
        );
        if (init?.headers) {
          new Headers(init.headers).forEach((value, key) =>
            headers.set(key, value),
          );
        }
        if (!headers.has("Authorization")) {
          headers.set("Authorization", `Bearer ${token}`);
          return observeAuthResponse(
            originalFetch(input, { ...backendInit, headers }),
            rawUrl,
          );
        }
      }
    } catch {
      // Any unexpected error ⇒ fall through to the untouched fetch.
    }
    return observeAuthResponse(originalFetch(input, backendInit), rawUrl);
  };
}
