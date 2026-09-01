/**
 * Echo API client - direct fetch with zero framework dependencies.
 */

import { swallow } from "@/core/utils/log";
import type { Thread, ThreadState } from "./types";

const warnedStubResponses = new Set<string>();

export const STUB_RESPONSE_EVENT = "echo:api-stub-response";

export interface StubResponseDetail {
  method: string;
  path: string;
}

function isStubResponse(value: unknown): boolean {
  return (
    !!value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (value as { _stub?: unknown })._stub === true
  );
}

function warnOnStubResponse(method: string, path: string, value: unknown) {
  if (!isStubResponse(value)) return;
  const key = `${method} ${path}`;
  if (warnedStubResponses.has(key)) return;
  warnedStubResponses.add(key);
  console.warn(
    `[Echo API] ${key} returned a stub response. This endpoint is using simulated compatibility data.`,
  );
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent<StubResponseDetail>(STUB_RESPONSE_EVENT, {
        detail: { method, path },
      }),
    );
  }
}

export class EchoClient {
  private baseUrl: string;
  private getToken?: () => string | null;

  constructor(config: { apiUrl: string; getToken?: () => string | null }) {
    this.baseUrl = config.apiUrl.replace(/\/+$/, "");
    this.getToken = config.getToken;
  }

  private _authHeaders(): Record<string, string> {
    const token = this.getToken?.();
    if (!token) return {};
    return { Authorization: `Bearer ${token}` };
  }

  private _jsonHeaders(): Record<string, string> {
    return { "Content-Type": "application/json", ...this._authHeaders() };
  }

  private _csrfHeaders(): Record<string, string> {
    if (typeof document === "undefined") return {};
    const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]*)/);
    const csrfToken = match?.[1] ? decodeURIComponent(match[1]) : "";
    if (!csrfToken) return {};
    return { "X-CSRF-Token": csrfToken };
  }

  private _safeHeaders(hasBody: boolean): Record<string, string> {
    const base = hasBody ? this._jsonHeaders() : this._authHeaders();
    return { ...base, ...this._csrfHeaders() };
  }

  // ---------------------------------------------------------------------
  // Generic verbs
  //
  // Account / billing / privacy modules call ``apiClient.get`` /
  // ``apiClient.post`` / ``apiClient.patch`` / ``apiClient.delete``
  // directly with absolute paths like ``/api/account/profile``. Without
  // these methods on the client, those calls threw ``apiClient.get is not
  // a function`` and React Query did its 1s+2s+4s retry backoff before
  // settling; that was the ~8s "skeleton stuck" symptom in the settings
  // dialog. We resolve the URL against ``baseUrl`` and forward auth.
  //
  // The expected response shape across these endpoints is the gateway's
  // ``{success, data, error, meta}`` envelope; we return ``data`` to keep
  // call sites compatible with the existing typings.
  // ---------------------------------------------------------------------

  private _resolveUrl(path: string): string {
    // Absolute URL: pass through unchanged.
    if (/^https?:\/\//i.test(path)) return path;
    // ``baseUrl`` is the runtime API root (e.g. ``/api`` or an
    // absolute desktop URL like ``http://127.0.0.1:4105/api``). Gateway-style
    // paths already start with ``/api/``. In dev, returning ``/api/...`` lets
    // Vite proxy handle the request. In packaged Electron, however, the page
    // runs from ``file://`` and a bare ``/api/...`` fetch fails; route it to
    // the backend origin selected by Electron instead.
    if (path.startsWith("/api/")) {
      try {
        if (/^https?:\/\//i.test(this.baseUrl)) {
          return `${new URL(this.baseUrl).origin}${path}`;
        }
      } catch (e) {
        swallow(e);
      }
      return path;
    }
    return `${this.baseUrl}${path.startsWith("/") ? "" : "/"}${path}`;
  }

  private async _request<T>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    const init: RequestInit = {
      method,
      headers: this._safeHeaders(body !== undefined),
    };
    if (body !== undefined) {
      init.body = JSON.stringify(body);
    }
    const resp = await fetch(this._resolveUrl(path), init);
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(
        `${method} ${path} failed: ${resp.status}${text ? ` - ${text}` : ""}`,
      );
    }
    if (resp.status === 204) return undefined as T;
    // Return the raw JSON unmodified; call sites (e.g. the account hooks)
    // already handle the gateway's ``{success, data, error}`` envelope and
    // expect to read the wrapper directly.
    const payload = (await resp.json()) as T;
    warnOnStubResponse(method, path, payload);
    return payload;
  }

  get<T>(path: string): Promise<T> {
    return this._request<T>("GET", path);
  }
  post<T>(path: string, body?: unknown): Promise<T> {
    return this._request<T>("POST", path, body ?? {});
  }
  patch<T>(path: string, body?: unknown): Promise<T> {
    return this._request<T>("PATCH", path, body ?? {});
  }
  put<T>(path: string, body?: unknown): Promise<T> {
    return this._request<T>("PUT", path, body ?? {});
  }
  delete<T>(path: string): Promise<T> {
    return this._request<T>("DELETE", path);
  }

  // -- Threads API --

  threads = {
    create: (body?: Record<string, unknown>): Promise<Thread> =>
      this.post<Thread>("/threads", body),

    get: (threadId: string): Promise<Thread> =>
      this.get<Thread>(`/threads/${threadId}`),

    search: (params?: Record<string, unknown>): Promise<Thread[]> =>
      this.post<Thread[]>("/threads/search", params),

    delete: (threadId: string): Promise<void> =>
      this.delete<void>(`/threads/${threadId}`),

    getState: <T = Record<string, unknown>>(
      threadId: string,
    ): Promise<ThreadState<T>> =>
      this.get<ThreadState<T>>(`/threads/${threadId}/state`),

    updateState: (
      threadId: string,
      body: Record<string, unknown>,
    ): Promise<ThreadState> =>
      this.post<ThreadState>(`/threads/${threadId}/state`, body),

    getHistory: (
      threadId: string,
      params?: { limit?: number },
    ): Promise<ThreadState[]> =>
      this.post<ThreadState[]>(`/threads/${threadId}/history`, params),

    renameTitle: (
      threadId: string,
      title: string,
    ): Promise<SessionTitleSnapshot> =>
      this.post<SessionTitleSnapshot>(`/threads/${threadId}/title/rename`, {
        title,
      }),

    refreshTitle: (
      threadId: string,
      body?: { provider?: string; force?: boolean },
    ): Promise<SessionTitleSnapshot> =>
      this.post<SessionTitleSnapshot>(`/threads/${threadId}/title/refresh`, body ?? {}),

    forkThread: (
      threadId: string,
      atMessageIndex?: number,
    ): Promise<{ thread_id: string; seeded_messages: number }> =>
      this.post<{ thread_id: string; seeded_messages: number }>(
        `/threads/${threadId}/fork`,
        atMessageIndex != null ? { at_message_index: atMessageIndex } : {},
      ),
  };
}

export interface SessionTitleSnapshot {
  title: string;
  source: "fallback" | "provider" | "user";
  pinned: boolean;
  provider?: string | null;
  model?: string | null;
  updated_at?: string;
}
