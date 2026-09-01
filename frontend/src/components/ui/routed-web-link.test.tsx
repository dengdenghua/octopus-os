import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { MouseEvent } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BROWSER_OPEN_URL_ACK_EVENT,
  BROWSER_OPEN_URL_REQUEST_EVENT,
  type BrowserOpenUrlAck,
  type BrowserOpenUrlRequest,
} from "@/components/browser/browser-store";

import { setLinkOpenTarget } from "@/core/settings/automation-preferences";

import { RoutedWebLink } from "./routed-web-link";

describe("RoutedWebLink", () => {
  let acknowledge: (event: Event) => void;

  beforeEach(() => {
    window.localStorage.clear();
    window.location.hash = "#/workspace";
    Object.defineProperty(window, "echo", {
      configurable: true,
      value: undefined,
    });
    vi.restoreAllMocks();
    acknowledge = (event) => {
      const request = (event as CustomEvent<BrowserOpenUrlRequest>).detail;
      window.dispatchEvent(
        new CustomEvent<BrowserOpenUrlAck>(BROWSER_OPEN_URL_ACK_EVENT, {
          detail: { requestId: request.requestId!, accepted: true },
        }),
      );
    };
    window.addEventListener(BROWSER_OPEN_URL_REQUEST_EVENT, acknowledge);
  });

  afterEach(() => {
    window.removeEventListener(BROWSER_OPEN_URL_REQUEST_EVENT, acknowledge);
  });

  it("routes an ordinary content link into the built-in browser", () => {
    setLinkOpenTarget("in_app");
    render(
      <RoutedWebLink
        href="https://example.com/source"
        openTargetSource="citation"
      >
        Source
      </RoutedWebLink>,
    );

    fireEvent.click(screen.getByRole("link", { name: "Source" }));

    expect(window.location.hash).toBe("#/browser");
  });

  it("preserves modified clicks and caller cancellation", () => {
    setLinkOpenTarget("in_app");
    const onClick = vi.fn((event: MouseEvent<HTMLAnchorElement>) =>
      event.preventDefault(),
    );
    const { rerender } = render(
      <RoutedWebLink href="https://example.com" onClick={onClick}>
        Cancelled
      </RoutedWebLink>,
    );
    fireEvent.click(screen.getByRole("link", { name: "Cancelled" }));
    expect(window.location.hash).toBe("#/workspace");

    rerender(
      <RoutedWebLink href="https://example.com">Modified</RoutedWebLink>,
    );
    fireEvent.click(screen.getByRole("link", { name: "Modified" }), {
      metaKey: true,
    });
    expect(window.location.hash).toBe("#/workspace");
  });

  it("uses the external preference for ordinary links", async () => {
    setLinkOpenTarget("external");
    const openExternal = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window, "echo", {
      configurable: true,
      value: { app: { openExternal } },
    });
    render(<RoutedWebLink href="https://example.com">External</RoutedWebLink>);

    fireEvent.click(screen.getByRole("link", { name: "External" }));

    await waitFor(() =>
      expect(openExternal).toHaveBeenCalledWith("https://example.com"),
    );
    expect(window.location.hash).toBe("#/workspace");
  });

  it("keeps relative and download links on their native path", () => {
    setLinkOpenTarget("in_app");
    const { rerender } = render(
      <RoutedWebLink href="/workspace/help">Relative</RoutedWebLink>,
    );
    expect(screen.getByRole("link", { name: "Relative" })).not.toHaveAttribute(
      "target",
    );

    rerender(
      <RoutedWebLink href="https://example.com/report.csv" download>
        Download
      </RoutedWebLink>,
    );
    fireEvent.click(screen.getByRole("link", { name: "Download" }));
    expect(window.location.hash).toBe("#/workspace");
  });

  it("promotes a schemeless tool-supplied host to https", () => {
    setLinkOpenTarget("in_app");
    render(<RoutedWebLink href="example.com/page">Bare</RoutedWebLink>);

    const link = screen.getByRole("link", { name: "Bare" });
    // Without promotion the browser resolves this against the current SPA
    // route, yielding a dead in-app URL with no rel.
    expect(link).toHaveAttribute("href", "https://example.com/page");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("leaves non-host hrefs exactly as given", () => {
    setLinkOpenTarget("in_app");
    for (const href of [
      "/workspace/help",
      "#section",
      "?q=1",
      "mailto:ops@example.com",
      "//cdn.example.com/x.js",
      "not a url",
    ]) {
      const { unmount } = render(
        <RoutedWebLink href={href}>Raw</RoutedWebLink>,
      );
      expect(screen.getByRole("link", { name: "Raw" })).toHaveAttribute(
        "href",
        href,
      );
      unmount();
    }
  });
});
