import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";

import type {
  AuthStatus,
  LoginRequest,
  LoginResponse,
  RegisterRequest,
  User,
} from "./types";

const TOKEN_KEY = "echo_auth_token";
const USER_KEY = "echo_user";
const TOKEN_TIMESTAMP_KEY = "echo_auth_ts";
const SESSION_TIMEOUT_MS = 7 * 24 * 60 * 60 * 1000;

function canUseBrowserStorage(): boolean {
  return typeof window !== "undefined" && import.meta.env.MODE !== "test";
}

/**
 * Audit S-07: the JWT must not live in localStorage (persistent, readable by
 * any script). Tokens are kept in sessionStorage instead. This migrates a
 * legacy localStorage session over once (so existing users are not logged
 * out), then scrubs localStorage entirely.
 */
function _migrateLegacyLocalStorage(): void {
  if (!canUseBrowserStorage()) return;
  try {
    const legacyToken = window.localStorage.getItem(TOKEN_KEY);
    if (legacyToken && !window.sessionStorage.getItem(TOKEN_KEY)) {
      window.sessionStorage.setItem(TOKEN_KEY, legacyToken);
      const user = window.localStorage.getItem(USER_KEY);
      if (user) window.sessionStorage.setItem(USER_KEY, user);
      const ts = window.localStorage.getItem(TOKEN_TIMESTAMP_KEY);
      if (ts) window.sessionStorage.setItem(TOKEN_TIMESTAMP_KEY, ts);
    }
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
    window.localStorage.removeItem(TOKEN_TIMESTAMP_KEY);
  } catch {
    // best-effort migration must never throw
  }
}

function _isSessionExpired(): boolean {
  if (!canUseBrowserStorage()) return true;
  _migrateLegacyLocalStorage();
  const ts = window.sessionStorage.getItem(TOKEN_TIMESTAMP_KEY);
  if (!ts) return false;
  const elapsed = Date.now() - Number(ts);
  return elapsed > SESSION_TIMEOUT_MS;
}

function _readToken(): string | null {
  if (!canUseBrowserStorage()) return null;
  if (_isSessionExpired()) {
    _clearTokens();
    return null;
  }
  return window.sessionStorage.getItem(TOKEN_KEY);
}

export function _writeToken(token: string, user: unknown): void {
  if (!canUseBrowserStorage()) return;
  // Audit S-07: sessionStorage only — no token in localStorage.
  window.sessionStorage.setItem(TOKEN_KEY, token);
  window.sessionStorage.setItem(USER_KEY, JSON.stringify(user));
  window.sessionStorage.setItem(TOKEN_TIMESTAMP_KEY, String(Date.now()));
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
  window.localStorage.removeItem(TOKEN_TIMESTAMP_KEY);
}

export function _clearTokens(): void {
  if (!canUseBrowserStorage()) return;
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
  window.localStorage.removeItem(TOKEN_TIMESTAMP_KEY);
  window.sessionStorage.removeItem(TOKEN_KEY);
  window.sessionStorage.removeItem(USER_KEY);
  window.sessionStorage.removeItem(TOKEN_TIMESTAMP_KEY);
}

export function authHeaders(): Record<string, string> {
  const token = _readToken();
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

export function jsonAuthHeaders(): Record<string, string> {
  return { "Content-Type": "application/json", ...authHeaders() };
}

/**
 * Best-effort current actor id for cross-thread analytics (mention
 * history ranking, etc.). Reads from the persisted USER_KEY blob; if
 * the user isn't signed in or storage is unavailable, returns
 * "anonymous" so downstream callers always get a stable string.
 */
export function currentActorId(): string {
  if (!canUseBrowserStorage()) return "anonymous";
  _migrateLegacyLocalStorage();
  try {
    const raw = window.sessionStorage.getItem(USER_KEY);
    if (!raw) return "anonymous";
    const parsed = JSON.parse(raw) as Record<string, unknown> | null;
    if (!parsed || typeof parsed !== "object") return "anonymous";
    const candidates = ["user_id", "actor_id", "id", "username", "phone"];
    for (const key of candidates) {
      const value = parsed[key];
      if (typeof value === "string" && value.length > 0) return value;
    }
    return "anonymous";
  } catch {
    return "anonymous";
  }
}

export async function getAuthStatus(): Promise<AuthStatus> {
  const res = await fetch(`${getBackendBaseURL()}/api/auth/status`);
  if (!res.ok) throw new Error(`Failed to get auth status: ${res.statusText}`);
  return (await res.json()) as AuthStatus;
}

export interface AuthProviderInfo {
  id: string;
  label?: string;
  mock_mode?: boolean;
  allow_any_username?: boolean;
  password_required?: boolean;
  endpoint?: string;
  endpoint_send?: string;
  endpoint_verify?: string;
}

/** Backend reports which login providers are wired · empty list means
 * no interactive login providers are configured. Returns empty list on
 * any error so the UI fails closed (show nothing). */
export async function getAuthProviderInfo(): Promise<AuthProviderInfo[]> {
  try {
    const res = await fetch(`${getBackendBaseURL()}/api/auth/providers`);
    if (!res.ok) return [];
    const data = (await res.json()) as {
      providers?: AuthProviderInfo[] | string[];
    };
    if (!data.providers) return [];
    // Backwards-compatible with the old string[] shape.
    if (data.providers.length > 0 && typeof data.providers[0] === "object") {
      return data.providers as AuthProviderInfo[];
    }
    return (data.providers as string[]).map((id) => ({ id }));
  } catch (e) {
    swallow(e);
    return [];
  }
}

export async function getAuthProviders(): Promise<string[]> {
  return (await getAuthProviderInfo()).map((p) => p.id);
}

export async function login(request: LoginRequest): Promise<LoginResponse> {
  const body: Record<string, unknown> = { username: request.username };
  if (request.password) body.password = request.password;
  const res = await fetch(`${getBackendBaseURL()}/api/auth/local/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(err.detail ?? `Login failed: ${res.statusText}`);
  }
  const data = (await res.json()) as LoginResponse & {
    credits?: Record<string, unknown>;
  };
  if (data.access_token && data.user) {
    const fallbackIdentity =
      data.user.mobile || data.user.username || request.username;
    _writeToken(data.access_token, {
      ...data.user,
      user_id: data.user.user_id || data.user.actor_id || fallbackIdentity,
      mobile: data.user.mobile,
      username:
        data.user.username && data.user.username !== "anonymous"
          ? data.user.username
          : fallbackIdentity,
    });
  }
  return data;
}

export async function register(request: RegisterRequest): Promise<User> {
  const res = await fetch(`${getBackendBaseURL()}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(err.detail ?? `Registration failed: ${res.statusText}`);
  }
  return (await res.json()) as User;
}

export async function getMe(): Promise<User> {
  const res = await fetch(`${getBackendBaseURL()}/api/auth/me`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to get user: ${res.statusText}`);
  return (await res.json()) as User;
}

export async function logout(): Promise<void> {
  await fetch(`${getBackendBaseURL()}/api/auth/logout`, {
    method: "POST",
    headers: authHeaders(),
    credentials: "include",
  });
  clearAuth();
}

export async function refreshToken(): Promise<LoginResponse> {
  const res = await fetch(`${getBackendBaseURL()}/api/auth/refresh`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to refresh token: ${res.statusText}`);
  const data = (await res.json()) as LoginResponse;
  if (data.access_token && data.user) {
    _writeToken(data.access_token, data.user);
  }
  return data;
}

export function getToken(): string | null {
  return _readToken();
}

export function getUser(): User | null {
  if (typeof window === "undefined") return null;
  _migrateLegacyLocalStorage();
  const userStr = sessionStorage.getItem(USER_KEY);
  if (!userStr) return null;
  try {
    return JSON.parse(userStr) as User;
  } catch (e) {
    swallow(e);
    return null;
  }
}

export function clearAuth(): void {
  _clearTokens();
}
