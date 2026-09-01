import { describe, expect, it, vi } from "vitest";

import {
  getNASBaseURL,
  NASRequestError,
  isNASAuthenticationError,
  startNASService,
} from "./api";

describe("Storage API errors", () => {
  it("keeps the storage HTTP status so the UI can recover an expired token", () => {
    const error = new NASRequestError("/v1/manifest", 401, "missing bearer token");
    expect(isNASAuthenticationError(error)).toBe(true);
    expect(error.message).toContain("401");
  });

  it("does not misclassify an unavailable service as an authentication error", () => {
    const error = new NASRequestError("/v1/manifest", 503, "unavailable");
    expect(isNASAuthenticationError(error)).toBe(false);
    expect(isNASAuthenticationError(new Error("network"))).toBe(false);
  });

  it("uses the agent same-origin storage gateway", () => {
    expect(getNASBaseURL()).toBe("/api/storage");
  });

  it("starts storage without persisting its private token in the browser", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          status: "already_running",
          base_url: "/api/storage",
          auth_token: null,
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await startNASService();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/local-brain/storage/start",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: {},
      }),
    );
    expect(sessionStorage.getItem("echo.storage.auth-token")).toBeNull();
  });
});
