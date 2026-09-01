import { getBackendBaseURL } from "@/core/config";
import { authHeaders } from "@/core/auth/api";

export interface CreatedPublicThreadShare {
  token: string;
  /** Opaque owner-side identifier; safe to use in management endpoints. */
  share_id: string;
  share_path: string;
  /** Optional operator-configured canonical URL for cross-device sharing. */
  share_url?: string | null;
  created_at: string;
  expires_at: string;
}

export type PublicThreadShareMessageRole = "user" | "assistant";

export interface PublicThreadShareMessage {
  role: PublicThreadShareMessageRole;
  content: string;
}

export interface PublicThreadShareStats {
  turns: number;
  messages: number;
  artifacts: number;
}

export interface PublicThreadShare {
  schema: string;
  created_at: string;
  expires_at?: string;
  title: string;
  messages: PublicThreadShareMessage[];
  artifacts: string[];
  stats: PublicThreadShareStats;
}

const SHARE_CACHE_PREFIX = "echo:public-thread-share:";

function shareCacheKey(threadId: string): string {
  return `${SHARE_CACHE_PREFIX}${encodeURIComponent(threadId.trim())}`;
}

function validCreatedShare(value: unknown): value is CreatedPublicThreadShare {
  if (!value || typeof value !== "object") return false;
  const share = value as Partial<CreatedPublicThreadShare>;
  return (
    typeof share.token === "string" &&
    !!share.token &&
    typeof share.share_id === "string" &&
    !!share.share_id &&
    typeof share.share_path === "string" &&
    !!share.share_path &&
    typeof share.created_at === "string" &&
    typeof share.expires_at === "string"
  );
}

/** Restore the current tab's owner capability so a refresh can still revoke it. */
export function getCachedPublicThreadShare(
  threadId: string,
): CreatedPublicThreadShare | null {
  const id = threadId.trim();
  if (!id || typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(shareCacheKey(id));
    const share: unknown = raw ? JSON.parse(raw) : null;
    if (!validCreatedShare(share)) return null;
    const expiresAt = Date.parse(share.expires_at);
    if (Number.isFinite(expiresAt) && expiresAt <= Date.now()) {
      window.sessionStorage.removeItem(shareCacheKey(id));
      return null;
    }
    return share;
  } catch {
    return null;
  }
}

export function clearCachedPublicThreadShare(threadId: string): void {
  const id = threadId.trim();
  if (!id || typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(shareCacheKey(id));
  } catch {
    // Storage can be disabled in hardened/private browser sessions.
  }
}

function cachePublicThreadShare(
  threadId: string,
  share: CreatedPublicThreadShare,
): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(
      shareCacheKey(threadId),
      JSON.stringify(share),
    );
  } catch {
    // Sharing still works for this mount; only refresh-time revoke degrades.
  }
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as {
      detail?: unknown;
      error?: unknown;
      message?: unknown;
    };
    for (const value of [body.detail, body.error, body.message]) {
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  } catch {
    // The status-based fallback below is stable for non-JSON gateway errors.
  }
  if (response.status === 404) return "分享内容不存在或已被取消";
  return `分享请求失败（${response.status}）`;
}

async function request<T>(
  path: string,
  init?: RequestInit,
  baseURL = getBackendBaseURL(),
): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  const response = await fetch(`${baseURL}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Capture a privacy-bounded, immutable snapshot of the current thread. */
export function createPublicThreadShare(
  threadId: string,
): Promise<CreatedPublicThreadShare> {
  const id = threadId.trim();
  if (!id) return Promise.reject(new Error("缺少要分享的任务"));
  return request<CreatedPublicThreadShare>(
    `/api/threads/${encodeURIComponent(id)}/shares`,
    { method: "POST", headers: authHeaders() },
  ).then((share) => {
    cachePublicThreadShare(id, share);
    return share;
  });
}

/** Read a public snapshot by capability token. This endpoint needs no login. */
export function getPublicThreadShare(
  token: string,
): Promise<PublicThreadShare> {
  const value = token.trim();
  if (!value) return Promise.reject(new Error("分享链接无效"));
  // A public capability belongs to the origin that served its share page.
  // Ignore desktop/debug backend overrides here so a stale or attacker-set
  // echoBackend value cannot forward the capability to another host.
  return request<PublicThreadShare>(
    "/api/public/thread-shares/resolve",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: value }),
    },
    "",
  );
}

/** Revoke a previously-created public snapshot owned by the current account. */
export function revokePublicThreadShare(shareId: string): Promise<void> {
  const value = shareId.trim();
  if (!value) return Promise.reject(new Error("分享链接无效"));
  return request<void>(
    `/api/thread-shares/by-id/${encodeURIComponent(value)}`,
    {
      method: "DELETE",
      headers: authHeaders(),
    },
  );
}

function absoluteHttpUrl(value: string | null | undefined): URL | null {
  const text = value?.trim();
  if (!text) return null;
  try {
    const parsed = new URL(text);
    if (
      !["http:", "https:"].includes(parsed.protocol) ||
      parsed.username ||
      parsed.password
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function resolvedSharePath(sharePath: string): string {
  const path = sharePath.trim();
  if (!path) return "";
  if (typeof window === "undefined") return path;
  try {
    // The backend's v1 contract used an absolute-root hash path
    // (`/#/share/...`). Treat it as a hash-only route so a mounted SPA such
    // as `/ui/#/...` keeps its shell prefix instead of falling back to `/`.
    const shellRelativePath = path.startsWith("/#/") ? path.slice(1) : path;
    return new URL(shellRelativePath, window.location.href).href;
  } catch {
    return path;
  }
}

/**
 * Resolve a share URL, preferring an operator-provided canonical HTTPS URL.
 * HTTP canonical URLs remain a fallback for explicitly configured intranet
 * deployments, while invalid/custom-scheme values are ignored.
 */
export function resolvePublicThreadShareUrl(
  sharePath: string,
  shareUrl?: string | null,
): string {
  const canonical = absoluteHttpUrl(shareUrl);
  const fallback = resolvedSharePath(sharePath);
  const fallbackHttp = absoluteHttpUrl(fallback);

  if (canonical?.protocol === "https:") return canonical.href;
  if (fallbackHttp?.protocol === "https:") return fallbackHttp.href;
  if (canonical) return canonical.href;
  return fallback;
}

function privateIpv4(hostname: string): boolean {
  const parts = hostname.split(".").map(Number);
  if (
    parts.length !== 4 ||
    parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)
  ) {
    return false;
  }
  return (
    parts[0] === 0 ||
    parts[0] === 10 ||
    parts[0] === 127 ||
    (parts[0] === 169 && parts[1] === 254) ||
    (parts[0] === 172 && parts[1]! >= 16 && parts[1]! <= 31) ||
    (parts[0] === 192 && parts[1] === 168)
  );
}

/** Whether a resolved link is suitable for QR / cross-device channels. */
export function isPublicThreadShareUrl(value: string): boolean {
  const parsed = absoluteHttpUrl(value);
  if (!parsed || parsed.protocol !== "https:") return false;
  const hostname = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  const isIpv6 = hostname.includes(":");
  if (
    !hostname ||
    hostname === "localhost" ||
    hostname.endsWith(".localhost") ||
    hostname.endsWith(".local") ||
    hostname === "::1" ||
    (isIpv6 &&
      (hostname.startsWith("fc") ||
        hostname.startsWith("fd") ||
        hostname.startsWith("fe80:"))) ||
    privateIpv4(hostname)
  ) {
    return false;
  }
  return true;
}
