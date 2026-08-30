export interface DetectedLocalService {
  port: number;
  name: string;
  type: "frontend" | "backend" | "other";
  url: string;
}

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

/** Distinguish a local app preview from an ordinary web page without relying
 * on a particular development port. */
export function isLocalPreviewUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      (url.protocol === "http:" || url.protocol === "https:") &&
      LOOPBACK_HOSTS.has(url.hostname)
    );
  } catch {
    return false;
  }
}

export function localPreviewPort(value: string): string {
  try {
    const url = new URL(value);
    return url.port || (url.protocol === "https:" ? "443" : "80");
  } catch {
    return "";
  }
}

const COMMON_DEV_PORTS = [
  { port: 5173, name: "Vite", type: "frontend" as const },
  { port: 5174, name: "Vite", type: "frontend" as const },
  { port: 3000, name: "React / Next.js", type: "frontend" as const },
  { port: 3001, name: "React", type: "frontend" as const },
  { port: 4000, name: "Remix / Svelte", type: "frontend" as const },
  { port: 4200, name: "Angular", type: "frontend" as const },
  { port: 4321, name: "Astro", type: "frontend" as const },
  { port: 8080, name: "HTTP Server", type: "other" as const },
  { port: 8000, name: "FastAPI / Django", type: "backend" as const },
  { port: 8001, name: "FastAPI", type: "backend" as const },
  { port: 8888, name: "Jupyter", type: "backend" as const },
  { port: 5000, name: "Flask", type: "backend" as const },
] as const;

export async function detectLocalServices({
  excludePorts = [],
  timeoutMs = 3000,
}: {
  excludePorts?: readonly number[];
  timeoutMs?: number;
} = {}): Promise<DetectedLocalService[]> {
  const excluded = new Set(excludePorts);
  const results: DetectedLocalService[] = [];
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);

  await Promise.allSettled(
    COMMON_DEV_PORTS.filter(({ port }) => !excluded.has(port)).map(
      async ({ port, name, type }) => {
        try {
          const response = await fetch(`http://localhost:${port}/`, {
            method: "HEAD",
            signal: controller.signal,
            mode: "no-cors",
          });
          if (response.type === "opaque" || response.ok) {
            results.push({
              port,
              name,
              type,
              url: `http://localhost:${port}`,
            });
          }
        } catch {
          // A failed probe means the port is not serving HTTP.
        }
      },
    ),
  );

  window.clearTimeout(timeout);
  return results.sort((left, right) => {
    if (left.type === right.type) return left.port - right.port;
    if (left.type === "frontend") return -1;
    if (right.type === "frontend") return 1;
    return left.port - right.port;
  });
}
