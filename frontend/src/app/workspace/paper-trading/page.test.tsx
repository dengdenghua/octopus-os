import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import PaperTradingPage, { isTrustedQuoteFrameOrigin } from "./page";

const mocks = vi.hoisted(() => ({
  getToken: vi.fn(() => "jwt-test-token"),
  refresh: vi.fn(() => Promise.resolve()),
}));

vi.mock("@/core/auth/api", () => ({
  getToken: mocks.getToken,
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "https://api.echo-age.com",
  getQuoteHubBaseURL: () => "https://quotes.echo-age.com",
}));

vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({ refresh: mocks.refresh }),
}));

describe("PaperTradingPage quote bridge", () => {
  beforeEach(() => {
    mocks.getToken.mockReturnValue("jwt-test-token");
    mocks.refresh.mockReset();
    mocks.refresh.mockResolvedValue(undefined);
  });

  it("accepts only same-origin, loopback, and exact first-party frame origins", () => {
    expect(
      isTrustedQuoteFrameOrigin(
        "https://shell.example",
        "https://shell.example",
      ),
    ).toBe(true);
    expect(
      isTrustedQuoteFrameOrigin(
        "http://127.0.0.1:8000",
        "https://shell.example",
      ),
    ).toBe(true);
    expect(
      isTrustedQuoteFrameOrigin(
        "https://api.echo-age.com",
        "https://shell.example",
      ),
    ).toBe(true);
    expect(
      isTrustedQuoteFrameOrigin(
        "https://api.echo-age.com.evil.example",
        "https://shell.example",
      ),
    ).toBe(false);
    expect(
      isTrustedQuoteFrameOrigin(
        "https://evil.example",
        "https://shell.example",
      ),
    ).toBe(false);
  });

  it("sends the in-memory Bearer only to the exact trusted iframe origin", () => {
    renderWithProviders(<PaperTradingPage />, {
      initialRoute: "/workspace/paper-trading?tab=watch",
      locale: "zh-CN",
    });

    const frame = screen.getByTitle("盯盘") as HTMLIFrameElement;
    const postMessage = vi.spyOn(frame.contentWindow!, "postMessage");
    fireEvent.load(frame);

    expect(postMessage).toHaveBeenCalledWith(
      {
        type: "echo:quote-config",
        version: 1,
        quoteBaseUrl: "https://quotes.echo-age.com",
        bearer: "jwt-test-token",
      },
      "https://api.echo-age.com",
    );
    expect(frame.src).not.toContain("token");
    const openLink = screen.getByRole("link", { name: /新窗口打开/ });
    expect(openLink.getAttribute("href")).toContain(
      "/#/workspace/paper-trading?tab=watch",
    );
    expect(openLink.getAttribute("href")).not.toContain("token");
  });

  it("ignores spoofed messages and single-flights refresh requests", async () => {
    let finishRefresh: (() => void) | undefined;
    mocks.refresh.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finishRefresh = resolve;
        }),
    );
    renderWithProviders(<PaperTradingPage />, {
      initialRoute: "/workspace/paper-trading?tab=watch",
      locale: "zh-CN",
    });

    const frame = screen.getByTitle("盯盘") as HTMLIFrameElement;
    const postMessage = vi.spyOn(frame.contentWindow!, "postMessage");
    postMessage.mockClear();

    window.dispatchEvent(
      new MessageEvent("message", {
        data: {
          type: "echo:quote-config-request",
          reason: "unauthorized",
        },
        origin: "https://evil.example",
        source: frame.contentWindow,
      }),
    );
    expect(mocks.refresh).not.toHaveBeenCalled();
    expect(postMessage).not.toHaveBeenCalled();

    const validMessage = () =>
      new MessageEvent("message", {
        data: {
          type: "echo:quote-config-request",
          reason: "unauthorized",
        },
        origin: "https://api.echo-age.com",
        source: frame.contentWindow,
      });
    window.dispatchEvent(validMessage());
    window.dispatchEvent(validMessage());

    expect(mocks.refresh).toHaveBeenCalledTimes(1);
    finishRefresh?.();
    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ bearer: "jwt-test-token" }),
      "https://api.echo-age.com",
    );
  });
});
