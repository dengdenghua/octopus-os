import { afterEach, describe, expect, test, vi } from "vitest";

import {
  installHashRouterShellUrlNormalizer,
  normalizeHashRouterShellUrl,
  toHashRouterShellUrl,
} from "./hash-shell-url";

describe("hash router shell URL normalization", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState(null, "", "/");
  });

  test("strips stale pathname when a hash route is active", () => {
    window.history.replaceState(null, "", "/desktop#/workspace/store");

    normalizeHashRouterShellUrl();

    expect(window.location.pathname).toBe("/");
    expect(window.location.hash).toBe("#/workspace/store");
  });

  test("strips stale pathname when a realtime hash route is active", () => {
    window.history.replaceState(null, "", "/desktop#/workspace/realtime/new");

    normalizeHashRouterShellUrl();

    expect(window.location.pathname).toBe("/");
    expect(window.location.hash).toBe("#/workspace/realtime/new");
  });

  test("formats programmatic routes for the hash router shell", () => {
    expect(toHashRouterShellUrl("/workspace/realtime?agent=general")).toBe(
      "/#/workspace/realtime/new?agent=general",
    );
    expect(toHashRouterShellUrl("/workspace/agents?surface=chat")).toBe(
      "/#/workspace/agents?surface=chat",
    );
  });

  test("rewrites direct app path loads into hash routes", () => {
    window.history.replaceState(
      null,
      "",
      "/workspace/realtime/new?agent=general",
    );

    normalizeHashRouterShellUrl();

    expect(window.location.pathname).toBe("/");
    expect(window.location.hash).toBe("#/workspace/realtime/new?agent=general");
  });

  test("rewrites workspace roots into the realtime coding screen", () => {
    window.history.replaceState(null, "", "/#/workspace");

    normalizeHashRouterShellUrl();

    expect(window.location.hash).toBe("#/workspace/realtime/new");

    window.history.replaceState(null, "", "/#/workspace/realtime");

    normalizeHashRouterShellUrl();

    expect(window.location.hash).toBe("#/workspace/realtime/new");
  });

  test("does not rewrite unknown non-realtime workspace entries", () => {
    window.history.replaceState(null, "", "/#/workspace/removed/thread-1");

    normalizeHashRouterShellUrl();

    expect(window.location.hash).toBe("#/workspace/removed/thread-1");

    window.history.replaceState(null, "", "/#/workspace/removed/new");

    normalizeHashRouterShellUrl();

    expect(window.location.hash).toBe("#/workspace/removed/new");

    window.history.replaceState(null, "", "/#/workspace/realtime/rt-thread");

    normalizeHashRouterShellUrl();

    expect(window.location.hash).toBe("#/workspace/realtime/rt-thread");
  });

  test("keeps team invite links on the join route", () => {
    window.history.replaceState(null, "", "/#/workspace/team/join?token=abc");

    normalizeHashRouterShellUrl();

    expect(window.location.hash).toBe("#/workspace/team/join?token=abc");
  });

  test("preserves the FastAPI /ui shell for hash routes", () => {
    window.history.replaceState(
      null,
      "",
      "/ui/#/workspace/realtime?agent=general",
    );

    normalizeHashRouterShellUrl();

    expect(window.location.pathname).toBe("/ui/");
    expect(window.location.hash).toBe("#/workspace/realtime/new?agent=general");

    window.history.replaceState(null, "", "/ui/#/share/public-token");
    normalizeHashRouterShellUrl();
    expect(window.location.pathname).toBe("/ui/");
    expect(window.location.hash).toBe("#/share/public-token");
  });

  test("moves direct /ui routes into the mounted hash shell", () => {
    window.history.replaceState(
      null,
      "",
      "/ui/workspace/realtime/new?agent=general",
    );

    normalizeHashRouterShellUrl();

    expect(window.location.pathname).toBe("/ui/");
    expect(window.location.hash).toBe("#/workspace/realtime/new?agent=general");
  });

  test("patches history writes so pathname routes become hash routes", () => {
    installHashRouterShellUrlNormalizer();
    window.history.replaceState(null, "", "/");
    window.history.pushState(null, "", "/workspace/realtime/abc");
    expect(window.location.pathname).toBe("/");
    expect(window.location.hash).toBe("#/workspace/realtime/abc");

    window.history.replaceState(
      null,
      "",
      `${window.location.origin}/ui/#/workspace/realtime/new`,
    );
    window.history.pushState(null, "", "/workspace/realtime/inside-ui");
    expect(window.location.pathname).toBe("/ui/");
    expect(window.location.hash).toBe("#/workspace/realtime/inside-ui");

    // Leave the manually-patched history in the root shell for afterEach and
    // the final listener test.
    window.history.replaceState(null, "", `${window.location.origin}/`);
  });

  test("installs a hashchange listener so navigation keeps the URL canonical", () => {
    const spy = vi.spyOn(window, "addEventListener");

    installHashRouterShellUrlNormalizer();

    expect(spy).toHaveBeenCalledWith("hashchange", normalizeHashRouterShellUrl);
  });
});
