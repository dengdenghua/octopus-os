const DEFAULT_CANONICAL_HOST = "localhost";
const LOOPBACK_HOSTS = new Set([
  "localhost",
  "127.0.0.1",
  "0.0.0.0",
  "[::1]",
  "::1",
]);

function canonicalLoopbackHost(): string {
  return (
    import.meta.env.VITE_CANONICAL_LOOPBACK_HOST || DEFAULT_CANONICAL_HOST
  ).trim();
}

function loopbackOriginNormalizationEnabled(): boolean {
  return (
    import.meta.env.VITE_DISABLE_LOOPBACK_ORIGIN_NORMALIZATION !== "true" &&
    canonicalLoopbackHost().toLowerCase() !== "none"
  );
}

function shouldNormalizeLoopbackOrigin(location: {
  hostname: string;
  protocol: string;
}): boolean {
  if (!loopbackOriginNormalizationEnabled()) return false;
  if (location.protocol !== "http:") return false;
  const canonicalHost = canonicalLoopbackHost();
  if (!canonicalHost || location.hostname === canonicalHost) return false;
  return LOOPBACK_HOSTS.has(location.hostname);
}

export function loopbackOriginRedirectURL(href: string): string | null {
  const next = new URL(href);
  if (!shouldNormalizeLoopbackOrigin(next)) return null;
  next.hostname = canonicalLoopbackHost();
  return next.toString();
}

export function normalizeLoopbackOrigin() {
  if (typeof window === "undefined") return false;
  const next = loopbackOriginRedirectURL(window.location.href);
  if (!next) return false;
  window.location.replace(next);
  return true;
}
