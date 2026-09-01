import { describe, expect, it } from "vitest";
import { EventEmitter } from "node:events";
import { vi } from "vitest";

import oauthDeepLink from "./mcp-oauth-deep-link.cjs";

const SOURCE =
  "https://auth.tdx.com.cn/tdx-oauth/page_workbuddy_oauth.html?client_id=cid";
const STATE = "d91XSEUHgszEbCXJ882-uowvVte4FAzMsRGihLGahss";

describe("MCP OAuth desktop deep-link bridge", () => {
  it("allows only credential-free HTTPS authorization pages in the popup", () => {
    expect(
      oauthDeepLink.isSafeOAuthAuthorizeURL(
        "https://auth.example.com/oauth/authorize?client_id=cid",
      ),
    ).toBe(true);
    for (const denied of [
      "http://auth.example.com/oauth",
      "https://user:secret@auth.example.com/oauth",
      "file:///tmp/oauth.html",
      "workbuddy://oauth",
      "not a url",
    ]) {
      expect(oauthDeepLink.isSafeOAuthAuthorizeURL(denied)).toBe(false);
    }
  });

  it("converts a trusted WorkBuddy query callback to the loopback callback", () => {
    const callback = oauthDeepLink.buildMcpOAuthCallbackURL({
      sourceURL: SOURCE,
      deepLinkURL: `workbuddy://mcp/oauth/callback?code=auth-code&state=${STATE}`,
      backendBaseURL: "http://127.0.0.1:8765",
    });

    expect(callback).not.toBeNull();
    const url = new URL(callback);
    expect(url.origin).toBe("http://127.0.0.1:8765");
    expect(url.pathname).toBe("/api/mcp/oauth/callback");
    expect(url.searchParams.get("code")).toBe("auth-code");
    expect(url.searchParams.get("state")).toBe(STATE);
  });

  it("supports callback parameters stored in a hash route", () => {
    const callback = oauthDeepLink.buildMcpOAuthCallbackURL({
      sourceURL: SOURCE,
      deepLinkURL: `workbuddy://oauth/#/done?code=hash-code&state=${STATE}`,
      backendBaseURL: "http://localhost:8000/",
    });

    expect(new URL(callback).searchParams.get("code")).toBe("hash-code");
  });

  it("forwards OAuth errors without accepting an authorization code too", () => {
    const callback = oauthDeepLink.buildMcpOAuthCallbackURL({
      sourceURL: SOURCE,
      deepLinkURL: `workbuddy://oauth/callback?error=access_denied&state=${STATE}`,
      backendBaseURL: "http://127.0.0.1:8000",
    });
    expect(new URL(callback).searchParams.get("error")).toBe("access_denied");

    expect(
      oauthDeepLink.buildMcpOAuthCallbackURL({
        sourceURL: SOURCE,
        deepLinkURL: `workbuddy://oauth/callback?code=C&error=denied&state=${STATE}`,
        backendBaseURL: "http://127.0.0.1:8000",
      }),
    ).toBeNull();
  });

  it("rejects untrusted pages, schemes, state values and remote callbacks", () => {
    const base = {
      sourceURL: SOURCE,
      deepLinkURL: `workbuddy://oauth/callback?code=C&state=${STATE}`,
      backendBaseURL: "http://127.0.0.1:8000",
    };

    for (const changed of [
      { sourceURL: "https://auth.tdx.com.cn.evil.test/tdx-oauth/" },
      { sourceURL: "https://auth.tdx.com.cn/not-tdx-oauth/" },
      { deepLinkURL: `evil://oauth/callback?code=C&state=${STATE}` },
      { deepLinkURL: "workbuddy://oauth/callback?code=C&state=short" },
      { backendBaseURL: "https://example.com" },
      { backendBaseURL: "http://127.0.0.1.evil.test:8000" },
    ]) {
      expect(
        oauthDeepLink.buildMcpOAuthCallbackURL({ ...base, ...changed }),
      ).toBeNull();
    }
  });

  it("rejects malformed and control-character-bearing callback data", () => {
    expect(
      oauthDeepLink.buildMcpOAuthCallbackURL({
        sourceURL: SOURCE,
        deepLinkURL: `workbuddy://oauth/callback?code=${encodeURIComponent("bad\ncode")}&state=${STATE}`,
        backendBaseURL: "http://127.0.0.1:8000",
      }),
    ).toBeNull();
  });

  it("intercepts navigation, redirect and window-open paths without replaying", async () => {
    const contents = new EventEmitter();
    contents.getURL = vi.fn(() => SOURCE);
    let finishLoad;
    contents.loadURL = vi.fn(
      () =>
        new Promise((resolve) => {
          finishLoad = resolve;
        }),
    );
    const bridge = oauthDeepLink.attachMcpOAuthDeepLinkBridge(contents, {
      backendBaseURL: () => "http://127.0.0.1:8000",
    });
    const event = { preventDefault: vi.fn() };
    const deepLink = `workbuddy://oauth/callback?code=C&state=${STATE}`;

    contents.emit("will-navigate", event, deepLink);
    contents.emit("will-redirect", event, deepLink);
    contents.emit("will-frame-navigate", event, { url: deepLink });
    expect(event.preventDefault).toHaveBeenCalledTimes(3);
    expect(contents.loadURL).toHaveBeenCalledTimes(1);
    expect(bridge.handleWindowOpen(deepLink)).toBe(true);
    expect(contents.loadURL).toHaveBeenCalledTimes(1);

    finishLoad();
    await Promise.resolve();
    await Promise.resolve();
    expect(bridge.handleWindowOpen(deepLink)).toBe(true);
    expect(contents.loadURL).toHaveBeenCalledTimes(2);
  });

  it("leaves ordinary and untrusted navigation untouched", () => {
    const contents = new EventEmitter();
    contents.getURL = vi.fn(() => SOURCE);
    contents.loadURL = vi.fn();
    const bridge = oauthDeepLink.attachMcpOAuthDeepLinkBridge(contents, {
      backendBaseURL: "http://127.0.0.1:8000",
    });
    const event = { preventDefault: vi.fn() };

    contents.emit("will-navigate", event, "https://example.com/next");
    expect(bridge.handleWindowOpen("evil://oauth/callback?code=C")).toBe(
      false,
    );
    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(contents.loadURL).not.toHaveBeenCalled();
  });
});
