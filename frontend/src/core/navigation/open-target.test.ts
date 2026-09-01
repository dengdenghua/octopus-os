import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  BROWSER_OPEN_URL_ACK_EVENT,
  BROWSER_OPEN_URL_REQUEST_EVENT,
  BROWSER_OPEN_URL_REQUEST_KEY,
  type BrowserOpenUrlAck,
  type BrowserOpenUrlRequest,
} from "@/components/browser/browser-store";
import { setLinkOpenTarget } from "@/core/settings/automation-preferences";

import { openTarget, shouldRouteAnchorClick } from "./open-target";

describe("openTarget", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.location.hash = "#/workspace";
    Object.defineProperty(window, "echo", {
      configurable: true,
      value: undefined,
    });
    vi.restoreAllMocks();
  });

  it("routes web links into the built-in browser when preferred", async () => {
    setLinkOpenTarget("in_app");
    window.addEventListener(
      BROWSER_OPEN_URL_REQUEST_EVENT,
      (event) => {
        const request = (event as CustomEvent<BrowserOpenUrlRequest>).detail;
        window.dispatchEvent(
          new CustomEvent<BrowserOpenUrlAck>(BROWSER_OPEN_URL_ACK_EVENT, {
            detail: { requestId: request.requestId!, accepted: true },
          }),
        );
      },
      { once: true },
    );
    await expect(
      openTarget("https://example.com/docs", { source: "message" }),
    ).resolves.toBe("in_app");
    expect(window.location.hash).toBe("#/browser");
    expect(
      JSON.parse(
        window.localStorage.getItem(BROWSER_OPEN_URL_REQUEST_KEY) ?? "{}",
      ),
    ).toMatchObject({ url: "https://example.com/docs", source: "message" });
  });

  it("honors an explicit external target", async () => {
    const openExternal = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window, "echo", {
      configurable: true,
      value: { app: { openExternal } },
    });
    await expect(
      openTarget("https://example.com", { target: "external" }),
    ).resolves.toBe("external");
    expect(openExternal).toHaveBeenCalledWith("https://example.com");
  });

  it("falls back to a native external window when the desktop bridge fails", async () => {
    const openExternal = vi.fn().mockRejectedValue(new Error("bridge gone"));
    const openedWindow = { opener: window };
    const windowOpen = vi
      .spyOn(window, "open")
      .mockReturnValue(openedWindow as unknown as Window);
    Object.defineProperty(window, "echo", {
      configurable: true,
      value: { app: { openExternal } },
    });

    await expect(
      openTarget("https://example.com/fallback", { target: "external" }),
    ).resolves.toBe("external");
    expect(windowOpen).toHaveBeenCalledWith(
      "https://example.com/fallback",
      "_blank",
      "noopener,noreferrer",
    );
    expect(openedWindow.opener).toBeNull();
  });

  it("falls back externally when the in-app request cannot be persisted", async () => {
    const originalStorage = window.localStorage;
    const setItem = vi.fn(() => {
      throw new Error("storage unavailable");
    });
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        getItem: vi.fn(() => "in_app"),
        setItem,
      } as unknown as Storage,
    });
    const windowOpen = vi.spyOn(window, "open").mockReturnValue(null);

    let result: Awaited<ReturnType<typeof openTarget>>;
    try {
      result = await openTarget("https://example.com/no-storage");
    } finally {
      Object.defineProperty(window, "localStorage", {
        configurable: true,
        value: originalStorage,
      });
    }

    expect(result).toBe("blocked");
    expect(setItem).toHaveBeenCalled();
    expect(windowOpen).toHaveBeenCalledWith(
      "https://example.com/no-storage",
      "_blank",
      "noopener,noreferrer",
    );
    expect(window.location.hash).toBe("#/workspace");
  });

  it("falls back externally when the built-in browser never acknowledges", async () => {
    vi.useFakeTimers();
    setLinkOpenTarget("in_app");
    const openedWindow = { opener: window };
    const windowOpen = vi
      .spyOn(window, "open")
      .mockReturnValue(openedWindow as unknown as Window);

    try {
      const pending = openTarget("https://example.com/no-browser-shell");
      expect(window.location.hash).toBe("#/browser");
      await vi.advanceTimersByTimeAsync(1_600);
      await expect(pending).resolves.toBe("external");
    } finally {
      vi.useRealTimers();
    }

    expect(window.location.hash).toBe("#/workspace");
    expect(
      window.localStorage.getItem(BROWSER_OPEN_URL_REQUEST_KEY),
    ).toBeNull();
    expect(windowOpen).toHaveBeenCalledWith(
      "https://example.com/no-browser-shell",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("blocks non-web targets without navigating", async () => {
    const windowOpen = vi.spyOn(window, "open");

    await expect(openTarget("javascript:alert(1)")).resolves.toBe("blocked");
    expect(windowOpen).not.toHaveBeenCalled();
    expect(window.location.hash).toBe("#/workspace");
  });

  it("leaves modified clicks to the browser", () => {
    expect(
      shouldRouteAnchorClick({
        button: 0,
        metaKey: true,
        ctrlKey: false,
        shiftKey: false,
        altKey: false,
      }),
    ).toBe(false);
  });
});
