"use strict";

// Some MCP providers reuse a vendor-specific desktop OAuth page even when a
// dynamically registered client supplied an ordinary loopback redirect URI.
// TongDaXin currently returns the authorization result through a
// `workbuddy://...` launch URL.  The browser must consume that URL before the
// operating system tries to open another client and hand the code to Echo'
// existing loopback callback instead.

const TRUSTED_DEEP_LINK_SOURCES = Object.freeze({
  "workbuddy:": Object.freeze([
    Object.freeze({
      origin: "https://auth.tdx.com.cn",
      pathPrefix: "/tdx-oauth/",
    }),
  ]),
});

const CALLBACK_PATH = "/api/mcp/oauth/callback";
const STATE_PATTERN = /^[A-Za-z0-9._~-]{20,512}$/;
const ERROR_PATTERN = /^[A-Za-z0-9._~-]{1,128}$/;

function parseURL(rawURL) {
  try {
    return new URL(rawURL);
  } catch {
    return null;
  }
}

function isTrustedSource(sourceURL, protocol) {
  const source = parseURL(sourceURL);
  const rules = TRUSTED_DEEP_LINK_SOURCES[protocol] || [];
  if (!source || source.username || source.password) return false;
  return rules.some(
    ({ origin, pathPrefix }) =>
      source.origin === origin && source.pathname.startsWith(pathPrefix),
  );
}

function isSafeOAuthAuthorizeURL(rawURL) {
  const url = parseURL(rawURL);
  return Boolean(
    url &&
      url.protocol === "https:" &&
      !url.username &&
      !url.password &&
      url.hostname,
  );
}

function paramsFromHash(hash) {
  const raw = String(hash || "").replace(/^#/, "");
  if (!raw) return new URLSearchParams();
  const queryIndex = raw.indexOf("?");
  return new URLSearchParams(queryIndex >= 0 ? raw.slice(queryIndex + 1) : raw);
}

function readOAuthParam(url, name) {
  const direct = url.searchParams.get(name);
  if (direct !== null) return direct;
  return paramsFromHash(url.hash).get(name);
}

function hasUnsafeCharacters(value) {
  return /[\u0000-\u001f\u007f]/.test(value);
}

function normalizeLoopbackBackendBaseURL(rawURL) {
  const url = parseURL(rawURL);
  if (!url) return null;
  const host = url.hostname.toLowerCase();
  if (
    (url.protocol !== "http:" && url.protocol !== "https:") ||
    !new Set(["127.0.0.1", "localhost", "::1", "[::1]"]).has(host) ||
    url.username ||
    url.password ||
    (url.pathname && url.pathname !== "/") ||
    url.search ||
    url.hash
  ) {
    return null;
  }
  return url.origin;
}

function buildMcpOAuthCallbackURL({
  sourceURL,
  deepLinkURL,
  backendBaseURL,
}) {
  const deepLink = parseURL(deepLinkURL);
  const backend = normalizeLoopbackBackendBaseURL(backendBaseURL);
  if (!deepLink || !backend) return null;
  if (!Object.hasOwn(TRUSTED_DEEP_LINK_SOURCES, deepLink.protocol)) {
    return null;
  }
  if (!isTrustedSource(sourceURL, deepLink.protocol)) return null;

  const state = readOAuthParam(deepLink, "state") || "";
  const code = readOAuthParam(deepLink, "code") || "";
  const error = readOAuthParam(deepLink, "error") || "";
  if (!STATE_PATTERN.test(state)) return null;
  if ((!code && !error) || (code && error)) return null;
  if (code && (code.length > 4096 || hasUnsafeCharacters(code))) return null;
  if (error && !ERROR_PATTERN.test(error)) return null;

  const callback = new URL(CALLBACK_PATH, backend);
  callback.searchParams.set("state", state);
  if (code) callback.searchParams.set("code", code);
  if (error) callback.searchParams.set("error", error);
  return callback.toString();
}

function attachMcpOAuthDeepLinkBridge(contents, options) {
  const backendBaseURL = options?.backendBaseURL;
  const onNavigationError = options?.onNavigationError;
  let callbackLoading = false;

  const callbackURLFor = (deepLinkURL) =>
    buildMcpOAuthCallbackURL({
      sourceURL: contents.getURL(),
      deepLinkURL,
      backendBaseURL:
        typeof backendBaseURL === "function"
          ? backendBaseURL()
          : backendBaseURL,
    });

  const loadCallback = (callbackURL) => {
    if (callbackLoading) return;
    callbackLoading = true;
    // Promise.resolve also supports small WebContents-compatible test doubles
    // whose loadURL implementation is synchronous.
    Promise.resolve(contents.loadURL(callbackURL))
      .catch((error) => onNavigationError?.(error))
      .finally(() => {
        callbackLoading = false;
      });
  };

  const interceptNavigation = (event, deepLinkURL) => {
    const callbackURL = callbackURLFor(deepLinkURL);
    if (!callbackURL) return false;
    event?.preventDefault?.();
    loadCallback(callbackURL);
    return true;
  };

  contents.on("will-navigate", interceptNavigation);
  contents.on("will-redirect", interceptNavigation);
  contents.on("will-frame-navigate", (event, details) => {
    const deepLinkURL =
      typeof details === "string" ? details : String(details?.url || "");
    interceptNavigation(event, deepLinkURL);
  });

  return Object.freeze({
    handleWindowOpen(deepLinkURL) {
      const callbackURL = callbackURLFor(deepLinkURL);
      if (!callbackURL) return false;
      loadCallback(callbackURL);
      return true;
    },
  });
}

module.exports = {
  CALLBACK_PATH,
  TRUSTED_DEEP_LINK_SOURCES,
  attachMcpOAuthDeepLinkBridge,
  buildMcpOAuthCallbackURL,
  isSafeOAuthAuthorizeURL,
  normalizeLoopbackBackendBaseURL,
};
