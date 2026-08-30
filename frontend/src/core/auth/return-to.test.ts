import { describe, expect, it } from "vitest";

import {
  authReturnToFromSearch,
  loginPathWithReturnTo,
  sanitizeAuthReturnTo,
} from "./return-to";

describe("auth returnTo", () => {
  it("preserves an invite path, token query and hash", () => {
    const target = "/workspace/team/join?token=secret-token#details";
    const loginPath = loginPathWithReturnTo(target);

    expect(loginPath).toBe(
      "/login?returnTo=%2Fworkspace%2Fteam%2Fjoin%3Ftoken%3Dsecret-token%23details",
    );
    expect(
      authReturnToFromSearch(loginPath.slice(loginPath.indexOf("?"))),
    ).toBe(target);
  });

  it("rejects cross-origin and auth-loop redirects", () => {
    expect(sanitizeAuthReturnTo("https://evil.example/path")).toBe(
      "/workspace",
    );
    expect(sanitizeAuthReturnTo("//evil.example/path")).toBe("/workspace");
    expect(sanitizeAuthReturnTo("/login?returnTo=/workspace")).toBe(
      "/workspace",
    );
  });
});
