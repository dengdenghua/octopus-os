import { swallow } from "@/core/utils/log";
function getBaseOrigin() {
  if (typeof window !== "undefined") {
    return window.location.origin;
  }
  return "http://localhost:3000";
}

const RUNTIME_BACKEND_PARAM = "echoBackend";
const DESKTOP_APP_PROTOCOL = "echo-app:";

function normalizeBaseURL(value: string) {
  return value.replace(/\/+$/, "");
}

function normalizeBackendBaseURL(value: string | undefined | null) {
  if (!value) {
    return "";
  }
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return "";
    }
    return normalizeBaseURL(url.toString());
  } catch (e) {
    swallow(e);
    return "";
  }
}

function getRuntimeBackendParamFromURL() {
  const shellParam = new URLSearchParams(window.location.search).get(
    RUNTIME_BACKEND_PARAM,
  );
  if (shellParam) return shellParam;

  const hash = window.location.hash || "";
  const queryStart = hash.indexOf("?");
  if (queryStart < 0) return "";
  return new URLSearchParams(hash.slice(queryStart + 1)).get(
    RUNTIME_BACKEND_PARAM,
  );
}

function getRuntimeBackendBaseURL() {
  if (typeof window === "undefined") {
    return "";
  }

  try {
    const fromURL = getRuntimeBackendParamFromURL();
    if (fromURL) {
      const normalized = normalizeBackendBaseURL(fromURL);
      if (!normalized) {
        return "";
      }
      window.sessionStorage?.setItem(RUNTIME_BACKEND_PARAM, normalized);
      return normalized;
    }
    const fromElectron = normalizeBackendBaseURL(
      window.echo?.backendBaseURL,
    );
    if (fromElectron) {
      window.sessionStorage?.setItem(RUNTIME_BACKEND_PARAM, fromElectron);
      return fromElectron;
    }
    if (window.echo?.isElectron || window.location.protocol === "file:") {
      window.sessionStorage?.removeItem(RUNTIME_BACKEND_PARAM);
      return "";
    }
    return window.sessionStorage?.getItem(RUNTIME_BACKEND_PARAM) || "";
  } catch (e) {
    swallow(e);
    return "";
  }
}

function isPackagedDesktopRenderer() {
  return (
    typeof window !== "undefined" &&
    window.echo?.isElectron === true &&
    window.location.protocol === DESKTOP_APP_PROTOCOL
  );
}

export function getBackendBaseURL() {
  // The packaged shell owns a secure, standard application origin. HTTP
  // requests stay relative so /api, /api/plugins, and backend media all pass
  // through the main process's fixed loopback proxy without browser CORS.
  if (isPackagedDesktopRenderer()) {
    return "";
  }

  const runtimeBackend = getRuntimeBackendBaseURL();
  if (runtimeBackend) {
    return runtimeBackend;
  }

  // In dev mode, always use relative paths so requests go through Vite proxy.
  // This prevents CORS issues (e.g. localhost:3001 -> localhost:8001).
  // Runtime overrides still win so Electron and ?echoBackend=... debug
  // sessions can point at the backend that actually owns their session.
  if (import.meta.env.DEV) {
    return "";
  }

  const val = import.meta.env.VITE_BACKEND_BASE_URL;
  if (val) {
    try {
      return normalizeBaseURL(new URL(val, getBaseOrigin()).toString());
    } catch {
      console.warn("[config] Invalid BACKEND_BASE_URL:", val);
    }
  }

  // Last-resort Electron fallback for old desktop shells that do not inject a runtime URL.
  if (typeof window !== "undefined" && window.location.protocol === "file:") {
    return "http://127.0.0.1:8000";
  }

  return "";
}

/**
 * Optional dedicated read-only quote plane.  Only first-party production
 * origins are accepted: a build-time typo or injected runtime URL must never
 * become a destination for the user's Bearer token.  Empty means the plugin
 * keeps using its current backend origin.
 */
export function getQuoteHubBaseURL() {
  const raw = import.meta.env.VITE_QUOTE_HUB_BASE_URL;
  if (!raw) return "";
  try {
    const origin = new URL(raw).origin;
    if (
      origin === "https://quotes.echo-age.com" ||
      origin === "https://api.echo-age.com"
    ) {
      return origin;
    }
  } catch (e) {
    swallow(e);
  }
  return "";
}

/**
 * Control-plane requests carry the user's authenticated session, so they must
 * stay on the configured backend or the current browser origin. Even though
 * localhost and 127.0.0.1 reach the same process, browsers isolate their
 * cookies by hostname; swapping the loopback alias silently logs these
 * requests out after a browser restart.
 */
export function getControlPlaneBaseURL() {
  return getBackendBaseURL();
}

/**
 * Raw loopback transport used only by browser WebSocket clients. Electron's
 * custom protocol handles fetch/iframe traffic, but WebSocket upgrades must
 * connect to the backend listener directly. The value comes from the trusted
 * preload bridge, never from a renderer-controlled query parameter.
 */
export function getBackendTransportBaseURL() {
  if (isPackagedDesktopRenderer()) {
    return normalizeBackendBaseURL(window.echo?.backendBaseURL);
  }
  return getBackendBaseURL();
}

export function getBackendWebSocketBaseURL() {
  const transport = getBackendTransportBaseURL();
  if (transport.startsWith("https://")) {
    return `wss://${transport.slice(8)}`;
  }
  if (transport.startsWith("http://")) {
    return `ws://${transport.slice(7)}`;
  }
  if (typeof window === "undefined") {
    return "";
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}`;
}

export function getEchoBaseURL(_isMock?: boolean) {
  if (isPackagedDesktopRenderer()) {
    return "/api";
  }

  const runtimeBackend = getRuntimeBackendBaseURL();
  if (runtimeBackend) {
    return `${runtimeBackend}/api`;
  }

  if (import.meta.env.DEV) {
    return "/api";
  }

  const val = import.meta.env.VITE_ECHO_BASE_URL;
  if (val) {
    try {
      return normalizeBaseURL(new URL(val, getBaseOrigin()).toString());
    } catch {
      console.warn("[config] Invalid ECHO_BASE_URL:", val);
    }
  }

  const backend = getBackendBaseURL();
  if (backend) {
    return `${backend}/api`;
  }

  return "/api";
}

/** Resolve a file from Vite's configured public base in every renderer. */
export function getPublicAssetURL(pathname: string) {
  const base = (import.meta.env.BASE_URL || "/").replace(/\/?$/, "/");
  return `${base}${pathname.replace(/^\/+/, "")}`;
}

// Public-facing URLs
//
// Canonical GitHub repo URL. Previously hard-coded in several places as the
// placeholder `https://github.com/echo` (which points at a random account,
// not this project). Centralize here and override via `VITE_GITHUB_URL` in
// deployments that fork to a different URL.
export const GITHUB_URL: string =
  (import.meta.env.VITE_GITHUB_URL as string | undefined) ??
  "https://github.com/dengdenghua/echo-os";
