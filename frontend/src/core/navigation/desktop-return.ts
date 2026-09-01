export type DesktopReturnContext = {
  currentUrl: string;
  configuredUrl?: string | null;
  referrer?: string | null;
};

function isLoopbackHostname(hostname: string): boolean {
  return (
    hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]"
  );
}

function parseUrl(value: string, base: URL): URL | null {
  try {
    return new URL(value, base);
  } catch {
    return null;
  }
}

function withDesktopHash(url: URL): string {
  url.search = "";
  url.hash = "/desktop";
  if (!url.pathname.endsWith("/")) url.pathname = `${url.pathname}/`;
  return url.toString();
}

/**
 * Resolve the Echo OS desktop independently from the Agent router.
 *
 * Local development deliberately runs the shell on :3000 and the current
 * Agent UI on :3001. Deployments can provide an explicit desktop URL; a
 * loopback referrer is also accepted so an OS-hosted Agent keeps the shell's
 * actual hostname and port.
 */
export function resolveEchoOsDesktopUrl({
  currentUrl,
  configuredUrl,
  referrer,
}: DesktopReturnContext): string {
  const current = new URL(currentUrl);
  const configured = configuredUrl?.trim();
  if (configured) {
    const configuredDesktop = parseUrl(configured, current);
    if (configuredDesktop) return withDesktopHash(configuredDesktop);
  }

  const source = referrer?.trim();
  if (source) {
    const referringUrl = parseUrl(source, current);
    if (
      referringUrl &&
      isLoopbackHostname(current.hostname) &&
      isLoopbackHostname(referringUrl.hostname) &&
      referringUrl.origin !== current.origin
    ) {
      return withDesktopHash(referringUrl);
    }
  }

  if (isLoopbackHostname(current.hostname) && current.port === "3001") {
    current.hostname = "localhost";
    current.port = "3000";
    current.pathname = "/";
    return withDesktopHash(current);
  }

  current.pathname = "/";
  return withDesktopHash(current);
}

export function navigateToEchoOsDesktop(configuredUrl?: string | null): void {
  if (typeof window === "undefined") return;
  window.location.assign(
    resolveEchoOsDesktopUrl({
      currentUrl: window.location.href,
      configuredUrl,
      referrer: document.referrer,
    }),
  );
}
