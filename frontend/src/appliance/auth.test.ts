import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applianceLogin,
  authHeader,
  fetchApplianceAuthStatus,
  hasDeviceOperatorAccess,
} from "./auth";
import { _clearTokens, getToken } from "@/core/auth/api";

vi.mock("@/core/auth/api", () => ({
  _clearTokens: vi.fn(),
  getToken: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("appliance browser authentication", () => {
  it("keeps the new session in HttpOnly cookie and removes readable old JWTs", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          success: true,
          access_token: "must-not-enter-local-storage",
          actor_id: "local:admin",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await applianceLogin("alice", "device-password");

    expect(_clearTokens).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/local/login",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          username: "alice",
          password: "device-password",
        }),
      }),
    );
  });

  it("retains Bearer compatibility only when an existing non-appliance token exists", () => {
    vi.mocked(getToken).mockReturnValue("legacy-cli-token");
    expect(authHeader()).toEqual({ Authorization: "Bearer legacy-cli-token" });
    vi.mocked(getToken).mockReturnValue(null);
    expect(authHeader()).toEqual({});
  });

  it("projects the authenticated family role used by the desktop", async () => {
    vi.mocked(getToken).mockReturnValue(null);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            authRequired: true,
            authenticated: true,
            role: "member",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(fetchApplianceAuthStatus()).resolves.toEqual({
      authRequired: true,
      authenticated: true,
      role: "member",
    });
  });

  it("keeps settings available when appliance authentication is disabled", () => {
    expect(hasDeviceOperatorAccess(false, true, null)).toBe(true);
  });

  it("only grants device settings to operators when authentication is enabled", () => {
    expect(hasDeviceOperatorAccess(true, true, "operator")).toBe(true);
    expect(hasDeviceOperatorAccess(true, true, "member")).toBe(false);
    expect(hasDeviceOperatorAccess(true, false, null)).toBe(false);
    expect(hasDeviceOperatorAccess(null, null, null)).toBe(false);
  });
});
