import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

// Dev-style backend base (relative /api through the proxy).
vi.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));

import { installAuthFetchInterceptor } from "./fetch-interceptor";

const calls: Array<{
  url: string;
  headers: Headers;
  credentials?: RequestCredentials;
}> = [];
let nextStatus = 200;
let nextAuthExpired = false;
const mockFetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
  const url =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.href
        : input.url;
  calls.push({
    url,
    headers: new Headers(init?.headers),
    credentials: init?.credentials,
  });
  const status = nextStatus;
  const authExpired = nextAuthExpired;
  nextStatus = 200;
  nextAuthExpired = false;
  return Promise.resolve(
    new Response(null, {
      status,
      headers: authExpired ? { "X-Echo-Auth-Expired": "1" } : {},
    }),
  );
});

beforeAll(() => {
  // The interceptor captures whatever window.fetch is at install time as the
  // "original", so stub first, then install once (it's idempotent).
  window.fetch = mockFetch as typeof window.fetch;
  installAuthFetchInterceptor();
});

afterEach(() => {
  calls.length = 0;
  mockFetch.mockClear();
  localStorage.clear();
  sessionStorage.clear();
  nextStatus = 200;
  nextAuthExpired = false;
});

const authOf = (i = 0): string | null =>
  calls[i]?.headers.get("Authorization") ?? null;

describe("installAuthFetchInterceptor", () => {
  it("attaches the bearer token to backend /api requests", async () => {
    sessionStorage.setItem("echo_auth_token", "tok123");
    await window.fetch("/api/apps");
    expect(authOf()).toBe("Bearer tok123");
  });

  it("does not leak the token to third-party URLs", async () => {
    sessionStorage.setItem("echo_auth_token", "tok123");
    await window.fetch("https://evil.example.com/api/steal");
    expect(authOf()).toBeNull();
  });

  it("never overrides an Authorization header the caller set", async () => {
    sessionStorage.setItem("echo_auth_token", "tok123");
    await window.fetch("/api/x", {
      headers: { Authorization: "Bearer caller-set" },
    });
    expect(authOf()).toBe("Bearer caller-set");
  });

  it("leaves requests untouched when there is no token", async () => {
    await window.fetch("/api/x");
    expect(authOf()).toBeNull();
    expect(calls[0]?.credentials).toBe("include");
  });

  it("ignores the legacy guest sentinel", async () => {
    sessionStorage.setItem("echo_auth_token", "__guest__");
    await window.fetch("/api/x");
    expect(authOf()).toBeNull();
  });

  it("falls back to a legacy localStorage token until migration scrubs it", async () => {
    localStorage.setItem("echo_auth_token", "legacy");
    await window.fetch("/api/x");
    expect(authOf()).toBe("Bearer legacy");
  });

  it("announces an expired authenticated backend session without reloading", async () => {
    const expired = vi.fn();
    window.addEventListener("echo:auth-expired", expired);
    nextStatus = 401;
    nextAuthExpired = true;

    await window.fetch("/api/capabilities");

    expect(expired).toHaveBeenCalledTimes(1);
    window.removeEventListener("echo:auth-expired", expired);
  });

  it("does not clear the host session for a downstream service 401", async () => {
    const expired = vi.fn();
    window.addEventListener("echo:auth-expired", expired);
    nextStatus = 401;

    await window.fetch("/api/account/oct/refresh", { method: "POST" });

    expect(expired).not.toHaveBeenCalled();
    window.removeEventListener("echo:auth-expired", expired);
  });

  it("does not treat an invalid login code as an expired workspace session", async () => {
    const expired = vi.fn();
    window.addEventListener("echo:auth-expired", expired);
    nextStatus = 401;

    await window.fetch("/api/auth/oct/email/login", { method: "POST" });

    expect(expired).not.toHaveBeenCalled();
    window.removeEventListener("echo:auth-expired", expired);
  });
});
