import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// LivePreviewPanel pulls localized strings off ``t.livePreview.*`` etc. A Proxy
// that returns "" for any access keeps every leaf render-safe (``t.x`` is "",
// ``t.x.y`` is undefined — both valid React children) without enumerating keys.
// Exception: ``codeMode.previewConsoleCount`` is *called* as a function by
// PreviewConsole, so that namespace needs a real callable.
vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: new Proxy(
      {},
      {
        get: (_, key) =>
          key === "codeMode"
            ? { previewConsoleCount: (n: number) => `${n}` }
            : "",
      },
    ),
    locale: "zh",
    setLocale: () => Promise.resolve(),
  }),
}));

// Stub the heavy unified surface so we only assert the delegation decision —
// the real BrowserPreviewPanel hits the browser-session API on mount.
vi.mock("./browser-preview-panel", () => ({
  BrowserPreviewPanel: (props: { initialUrl?: string; threadId: string }) => (
    <div
      data-testid="browser-surface"
      data-url={props.initialUrl}
      data-thread={props.threadId}
    />
  ),
}));

import { LivePreviewPanel } from "./live-preview-panel";

describe("LivePreviewPanel browser-surface delegation", () => {
  it("delegates a non-blob http(s) previewUrl to the unified BrowserPreviewPanel", () => {
    render(
      <LivePreviewPanel previewUrl="https://example.com/app" threadId="t1" />,
    );
    const surface = screen.getByTestId("browser-surface");
    expect(surface.getAttribute("data-url")).toBe("https://example.com/app");
    expect(surface.getAttribute("data-thread")).toBe("t1");
  });

  it("keeps a blob: previewUrl in the inline srcDoc iframe (no delegation)", () => {
    render(
      <LivePreviewPanel
        previewUrl="blob:abc"
        htmlContent="<p>hi</p>"
        threadId="t1"
      />,
    );
    expect(screen.queryByTestId("browser-surface")).not.toBeInTheDocument();
  });

  it("does not delegate without a threadId (session can't be bound)", () => {
    render(<LivePreviewPanel previewUrl="https://example.com" />);
    expect(screen.queryByTestId("browser-surface")).not.toBeInTheDocument();
  });
});
