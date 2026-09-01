import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const authMocks = vi.hoisted(() => ({
  headers: {} as Record<string, string>,
}));

vi.mock("@/core/auth/api", () => ({
  authHeaders: () => authMocks.headers,
  getToken: () => null,
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "https://backend.example.test",
  getEchoBaseURL: () => "/api",
}));

import { accountApi } from "./api";

describe("accountApi.uploadAvatar", () => {
  const originalFetch = globalThis.fetch;
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    authMocks.headers = {};
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ success: true, avatar_url: "/avatars/me.png" }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    globalThis.fetch = fetchMock;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("uses the shared auth headers for the current session token", async () => {
    authMocks.headers = { Authorization: "Bearer session-token" };
    const file = new File(["avatar"], "avatar.png", { type: "image/png" });

    await accountApi.uploadAvatar(file);

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("https://backend.example.test/api/account/avatar");
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      "Bearer session-token",
    );
    expect(init?.body).toBeInstanceOf(FormData);
    expect((init?.body as FormData).get("file")).toBe(file);
  });

  it("never sends an empty Authorization header without a session", async () => {
    const file = new File(["avatar"], "avatar.png", { type: "image/png" });

    await accountApi.uploadAvatar(file);

    const [, init] = fetchMock.mock.calls[0]!;
    expect(new Headers(init?.headers).has("Authorization")).toBe(false);
  });
});
