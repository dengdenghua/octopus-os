"use strict";

const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

const DESKTOP_APP_SCHEME = "echo-app";
const DESKTOP_APP_HOST = "app";
const DESKTOP_APP_ORIGIN = `${DESKTOP_APP_SCHEME}://${DESKTOP_APP_HOST}`;
const DESKTOP_APP_ENTRY_URL = `${DESKTOP_APP_ORIGIN}/index.html`;

// Keep this list aligned with the Vite development proxy. Everything else is
// a renderer asset and must resolve inside the immutable dist directory.
const BACKEND_ROUTE_PREFIXES = Object.freeze([
  "/api",
  "/v1",
  "/media",
  "/.well-known",
  "/.a2a",
  // K8s-style process health endpoints live at the backend root; the
  // bootstrap overlay polls /readyz until the bundled backend is up.
  "/readyz",
  "/livez",
]);

const FORBIDDEN_PROXY_HEADERS = Object.freeze([
  "connection",
  "content-length",
  "host",
  "origin",
  "referer",
  "transfer-encoding",
]);

function parseDesktopAppURL(rawURL) {
  let url;
  try {
    url = new URL(rawURL);
  } catch {
    return null;
  }
  if (
    url.protocol !== `${DESKTOP_APP_SCHEME}:` ||
    url.hostname !== DESKTOP_APP_HOST ||
    url.port ||
    url.username ||
    url.password
  ) {
    return null;
  }
  return url;
}

function isDesktopAppURL(rawURL) {
  return parseDesktopAppURL(rawURL) !== null;
}

function isBackendRoute(pathname) {
  return BACKEND_ROUTE_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

function normalizeLoopbackBackendBaseURL(rawURL) {
  let url;
  try {
    url = new URL(rawURL);
  } catch {
    throw new Error("desktop backend URL must be an absolute URL");
  }

  const hostname = url.hostname.toLowerCase();
  const loopbackHosts = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);
  if (
    (url.protocol !== "http:" && url.protocol !== "https:") ||
    !loopbackHosts.has(hostname) ||
    url.username ||
    url.password ||
    (url.pathname && url.pathname !== "/") ||
    url.search ||
    url.hash
  ) {
    throw new Error(
      "desktop backend URL must be a credential-free loopback HTTP(S) origin",
    );
  }
  return url.origin;
}

function pathIsInside(root, candidate) {
  return candidate === root || candidate.startsWith(`${root}${path.sep}`);
}

function resolveDesktopAssetPath(distRoot, pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  if (
    !decoded.startsWith("/") ||
    decoded.includes("\0") ||
    decoded.includes("\\")
  ) {
    return null;
  }

  const relative = decoded.replace(/^\/+/, "") || "index.html";
  const root = path.resolve(distRoot);
  const candidate = path.resolve(root, relative);
  if (!pathIsInside(root, candidate)) {
    return null;
  }

  try {
    if (!fs.statSync(candidate).isFile()) {
      return null;
    }
    const realRoot = fs.realpathSync(root);
    const realCandidate = fs.realpathSync(candidate);
    return pathIsInside(realRoot, realCandidate) ? realCandidate : null;
  } catch {
    return null;
  }
}

function buildBackendTargetURL(appURL, backendBaseURL) {
  const target = new URL(normalizeLoopbackBackendBaseURL(backendBaseURL));
  // Assigning the fields avoids the network-path-reference trap where a
  // pathname beginning with // could otherwise replace the trusted host.
  target.pathname = appURL.pathname;
  target.search = appURL.search;
  target.hash = "";
  return target.toString();
}

function errorResponse(status, code) {
  return new Response(JSON.stringify({ error: code }), {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}

function proxyHeaders(requestHeaders) {
  const headers = new Headers(requestHeaders);
  for (const name of FORBIDDEN_PROXY_HEADERS) {
    headers.delete(name);
  }
  return headers;
}

function rewriteBackendRedirect(response, backendOrigin) {
  if (response.status < 300 || response.status >= 400) {
    return response;
  }
  const location = response.headers.get("location");
  if (!location) {
    return response;
  }

  let target;
  try {
    target = new URL(location, backendOrigin);
  } catch {
    return response;
  }
  if (target.origin !== backendOrigin) {
    return response;
  }

  const headers = new Headers(response.headers);
  headers.set(
    "location",
    `${DESKTOP_APP_ORIGIN}${target.pathname}${target.search}${target.hash}`,
  );
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

function createDesktopProtocolHandler({
  distRoot,
  backendBaseURL,
  fetchImpl,
  onProxyError,
}) {
  const trustedBackendOrigin = normalizeLoopbackBackendBaseURL(backendBaseURL);
  if (typeof fetchImpl !== "function") {
    throw new TypeError("fetchImpl is required");
  }

  return async function handleDesktopRequest(request) {
    const appURL = parseDesktopAppURL(request.url);
    if (!appURL) {
      return errorResponse(403, "desktop_origin_denied");
    }

    if (isBackendRoute(appURL.pathname)) {
      const method = String(request.method || "GET").toUpperCase();
      const init = {
        method,
        headers: proxyHeaders(request.headers),
        redirect: "manual",
        bypassCustomProtocolHandlers: true,
      };
      if (method !== "GET" && method !== "HEAD" && request.body) {
        init.body = request.body;
        init.duplex = "half";
      }
      try {
        const response = await fetchImpl(
          buildBackendTargetURL(appURL, trustedBackendOrigin),
          init,
        );
        return rewriteBackendRedirect(response, trustedBackendOrigin);
      } catch (error) {
        onProxyError?.(error);
        return errorResponse(502, "desktop_backend_unavailable");
      }
    }

    if (request.method !== "GET" && request.method !== "HEAD") {
      return errorResponse(405, "desktop_asset_method_denied");
    }
    const assetPath = resolveDesktopAssetPath(distRoot, appURL.pathname);
    if (!assetPath) {
      return errorResponse(404, "desktop_asset_not_found");
    }
    try {
      return await fetchImpl(pathToFileURL(assetPath).toString(), {
        method: request.method,
        bypassCustomProtocolHandlers: true,
      });
    } catch {
      return errorResponse(404, "desktop_asset_not_found");
    }
  };
}

module.exports = {
  BACKEND_ROUTE_PREFIXES,
  DESKTOP_APP_ENTRY_URL,
  DESKTOP_APP_HOST,
  DESKTOP_APP_ORIGIN,
  DESKTOP_APP_SCHEME,
  buildBackendTargetURL,
  createDesktopProtocolHandler,
  isBackendRoute,
  isDesktopAppURL,
  normalizeLoopbackBackendBaseURL,
  parseDesktopAppURL,
  resolveDesktopAssetPath,
  rewriteBackendRedirect,
};
