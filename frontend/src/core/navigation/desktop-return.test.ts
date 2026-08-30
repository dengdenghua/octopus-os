import { describe, expect, test } from "vitest";

import { resolveEchoOsDesktopUrl } from "./desktop-return";

describe("Echo OS desktop return URL", () => {
  test("returns from the local Agent port to the local Echo OS shell", () => {
    expect(
      resolveEchoOsDesktopUrl({
        currentUrl: "http://localhost:3001/#/workspace/evolution?surface=chat",
      }),
    ).toBe("http://localhost:3000/#/desktop");
  });

  test("normalizes the loopback alias to the shared localhost shell", () => {
    expect(
      resolveEchoOsDesktopUrl({
        currentUrl: "http://127.0.0.1:3001/#/workspace/realtime/new",
      }),
    ).toBe("http://localhost:3000/#/desktop");
  });

  test("uses an explicitly configured desktop deployment", () => {
    expect(
      resolveEchoOsDesktopUrl({
        currentUrl: "https://agent.echo.example/#/workspace/realtime/new",
        configuredUrl: "https://os.echo.example/appliance",
      }),
    ).toBe("https://os.echo.example/appliance/#/desktop");
  });

  test("accepts a loopback shell referrer with a non-default port", () => {
    expect(
      resolveEchoOsDesktopUrl({
        currentUrl: "http://localhost:3001/#/workspace/realtime/new",
        referrer: "http://localhost:4173/",
      }),
    ).toBe("http://localhost:4173/#/desktop");
  });

  test("does not trust a remote third-party referrer", () => {
    expect(
      resolveEchoOsDesktopUrl({
        currentUrl: "https://agent.echo.example/#/workspace/realtime/new",
        referrer: "https://unrelated.example/",
      }),
    ).toBe("https://agent.echo.example/#/desktop");
  });

  test("falls back safely when a configured URL is malformed", () => {
    expect(
      resolveEchoOsDesktopUrl({
        currentUrl: "http://localhost:3001/#/workspace/realtime/new",
        configuredUrl: "http://[invalid",
      }),
    ).toBe("http://localhost:3000/#/desktop");
  });
});
