(() => {
  "use strict";

  const STATE_PATTERN = /^[A-Za-z0-9._~-]{20,512}$/;
  const ERROR_PATTERN = /^[A-Za-z0-9._~-]{1,128}$/;
  const SOURCE_ORIGIN = "https://auth.tdx.com.cn";
  const SOURCE_PATH_PREFIX = "/tdx-oauth/";

  function parseURL(value) {
    try {
      return new URL(value);
    } catch {
      return null;
    }
  }

  function readParam(url, name) {
    const direct = url.searchParams.get(name);
    if (direct !== null) return direct;
    const hash = String(url.hash || "").replace(/^#/, "");
    const queryIndex = hash.indexOf("?");
    return new URLSearchParams(
      queryIndex >= 0 ? hash.slice(queryIndex + 1) : hash,
    ).get(name);
  }

  function loopbackOrigin(rawURL) {
    const url = parseURL(rawURL);
    if (!url) return null;
    const loopback = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);
    if (
      (url.protocol !== "http:" && url.protocol !== "https:") ||
      !loopback.has(url.hostname.toLowerCase()) ||
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

  function buildCallbackURL({ sourceURL, deepLinkURL, backendBaseURL }) {
    const source = parseURL(sourceURL);
    const deepLink = parseURL(deepLinkURL);
    const backend = loopbackOrigin(backendBaseURL);
    if (
      !source ||
      source.origin !== SOURCE_ORIGIN ||
      !source.pathname.startsWith(SOURCE_PATH_PREFIX) ||
      !deepLink ||
      deepLink.protocol !== "workbuddy:" ||
      !backend
    ) {
      return null;
    }
    const state = readParam(deepLink, "state") || "";
    const code = readParam(deepLink, "code") || "";
    const error = readParam(deepLink, "error") || "";
    if (!STATE_PATTERN.test(state) || (!code && !error) || (code && error)) {
      return null;
    }
    if (code && (code.length > 4096 || /[\u0000-\u001f\u007f]/.test(code))) {
      return null;
    }
    if (error && !ERROR_PATTERN.test(error)) return null;

    const callback = new URL("/api/mcp/oauth/callback", backend);
    callback.searchParams.set("state", state);
    if (code) callback.searchParams.set("code", code);
    if (error) callback.searchParams.set("error", error);
    return callback.toString();
  }

  Object.defineProperty(globalThis, "EchoMcpOAuthDeepLink", {
    value: Object.freeze({ buildCallbackURL }),
    configurable: false,
    enumerable: false,
    writable: false,
  });
})();

