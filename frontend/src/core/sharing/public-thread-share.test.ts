import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "https://api.example.test",
}));

vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer owner-token" }),
}));

import {
  clearCachedPublicThreadShare,
  createPublicThreadShare,
  getCachedPublicThreadShare,
  getPublicThreadShare,
  isPublicThreadShareUrl,
  resolvePublicThreadShareUrl,
  revokePublicThreadShare,
} from "./public-thread-share";

const fetchMock = vi.fn<typeof fetch>();

const createdShare = {
  token: "public-token",
  share_id: "share-id",
  share_path: "#/share/public-token",
  share_url: "https://share.example.test/ui/#/share/public-token",
  created_at: "2026-08-25T00:00:00Z",
  expires_at: "2099-08-25T00:00:00Z",
};

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("public thread share API", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    window.sessionStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  it("creates a public snapshot through the authenticated thread endpoint", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(createdShare, 201));

    await expect(
      createPublicThreadShare(" thread / 中文 "),
    ).resolves.toMatchObject({ token: "public-token" });

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe(
      "https://api.example.test/api/threads/thread%20%2F%20%E4%B8%AD%E6%96%87/shares",
    );
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Accept")).toBe("application/json");
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      "Bearer owner-token",
    );
    expect(getCachedPublicThreadShare("thread / 中文")).toEqual(createdShare);
  });

  it("reads a public snapshot without adding an auth requirement", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        schema: "echo.thread-share.v1",
        created_at: "2026-08-25T00:00:00Z",
        title: "公开任务",
        messages: [{ role: "assistant", content: "完成" }],
        artifacts: [],
        stats: { turns: 0, messages: 1, artifacts: 0 },
      }),
    );

    await expect(getPublicThreadShare("token/value")).resolves.toMatchObject({
      title: "公开任务",
    });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/public/thread-shares/resolve");
    expect(String(url)).not.toContain("token%2Fvalue");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ token: "token/value" }));
    expect(new Headers(init?.headers).get("Content-Type")).toBe(
      "application/json",
    );
    expect(new Headers(init?.headers).has("Authorization")).toBe(false);
  });

  it("revokes the owner's link and accepts the 204 response", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await expect(revokePublicThreadShare("share-id")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/api/thread-shares/by-id/share-id",
      expect.objectContaining({
        method: "DELETE",
        headers: expect.any(Headers),
      }),
    );
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer owner-token");
  });

  it("surfaces the gateway detail for missing or revoked shares", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "shared task not found or revoked" }, 404),
    );

    await expect(getPublicThreadShare("gone-token")).rejects.toThrow(
      "shared task not found or revoked",
    );
  });

  it("turns the hash-router path into a copyable absolute URL", () => {
    expect(resolvePublicThreadShareUrl("#/share/public-token")).toBe(
      `${window.location.origin}/#/share/public-token`,
    );
    expect(
      resolvePublicThreadShareUrl("https://share.example.test/#/share/token"),
    ).toBe("https://share.example.test/#/share/token");
  });

  it("keeps the mounted /ui shell and prefers a canonical HTTPS URL", () => {
    window.history.replaceState(null, "", "/ui/#/workspace/realtime/task");

    expect(resolvePublicThreadShareUrl("#/share/public-token")).toBe(
      `${window.location.origin}/ui/#/share/public-token`,
    );
    expect(
      resolvePublicThreadShareUrl(
        "#/share/public-token",
        "https://public.example.test/ui/#/share/canonical",
      ),
    ).toBe("https://public.example.test/ui/#/share/canonical");
  });

  it("rejects local, private, custom-scheme and plain HTTP links for QR sharing", () => {
    expect(
      isPublicThreadShareUrl("https://share.example.test/#/share/token"),
    ).toBe(true);
    expect(isPublicThreadShareUrl("http://share.example.test/share")).toBe(
      false,
    );
    expect(isPublicThreadShareUrl("https://localhost:3000/#/share/token")).toBe(
      false,
    );
    expect(isPublicThreadShareUrl("https://192.168.1.4/#/share/token")).toBe(
      false,
    );
    expect(isPublicThreadShareUrl("echo-app://ui/#/share/token")).toBe(
      false,
    );
  });

  it("restores and clears a created share for refresh-time revocation", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(createdShare, 201));
    await createPublicThreadShare("thread-1");

    expect(getCachedPublicThreadShare("thread-1")).toEqual(createdShare);
    clearCachedPublicThreadShare("thread-1");
    expect(getCachedPublicThreadShare("thread-1")).toBeNull();
  });
});
