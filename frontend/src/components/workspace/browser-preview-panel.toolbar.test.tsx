import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { ComponentProps } from "react";

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { BrowserPreviewToolbar } from "./browser-preview-panel";

type ToolbarProps = ComponentProps<typeof BrowserPreviewToolbar>;

function toolbarProps(overrides: Partial<ToolbarProps> = {}): ToolbarProps {
  return {
    urlInput: "https://example.com",
    onUrlInputChange: vi.fn(),
    onNavigate: vi.fn(),
    onBack: vi.fn(),
    onForward: vi.fn(),
    onReload: vi.fn(),
    onOpenFullBrowser: vi.fn(),
    onEndSession: vi.fn(),
    canLivePreview: true,
    surfaceMode: "screenshot",
    onSurfaceModeChange: vi.fn(),
    devicePreview: "desktop",
    viewportChanging: false,
    onDevicePreviewChange: vi.fn(),
    onAttachScreenshot: vi.fn(),
    autoRefresh: false,
    onAutoRefreshChange: vi.fn(),
    sessionHealthy: true,
    runtimeLabel: "chromium",
    ...overrides,
  };
}

describe("BrowserPreviewToolbar", () => {
  it("keeps the persistent row to navigation, address, full browser, and more", () => {
    renderWithProviders(<BrowserPreviewToolbar {...toolbarProps()} />, {
      locale: "en-US",
    });

    const toolbar = screen.getByRole("toolbar", {
      name: "Browser Automation",
    });
    const buttons = within(toolbar).getAllByRole("button");
    expect(buttons.map((button) => button.getAttribute("aria-label"))).toEqual([
      "Back",
      "Forward",
      "Reload",
      "Continue in full browser",
      "More",
    ]);
    expect(
      buttons.every((button) => button.getAttribute("type") === "button"),
    ).toBe(true);
    expect(
      within(toolbar).getByRole("textbox", { name: "Enter URL..." }),
    ).toHaveValue("https://example.com");

    const fullBrowser = within(toolbar).getByRole("button", {
      name: "Continue in full browser",
    });
    expect(fullBrowser.querySelector(".lucide-maximize-2")).not.toBeNull();
    expect(within(fullBrowser).getByText("AI Browser")).toHaveClass(
      "hidden",
      "@min-[520px]/browser-preview-toolbar:inline",
    );
    expect(within(toolbar).queryByText("Annotate")).not.toBeInTheDocument();
    expect(
      within(toolbar).queryByRole("button", { name: "End browser session" }),
    ).not.toBeInTheDocument();
  });

  it("describes the target preview mode and keeps status passive and session end destructive", async () => {
    const user = userEvent.setup();
    const onSurfaceModeChange = vi.fn();
    const onEndSession = vi.fn();
    renderWithProviders(
      <BrowserPreviewToolbar
        {...toolbarProps({ onSurfaceModeChange, onEndSession })}
      />,
      { locale: "en-US" },
    );

    await user.click(screen.getByRole("button", { name: "More" }));
    await user.click(
      screen.getByRole("menuitem", {
        name: /Switch to live preview\s*Continue in an interactive page/,
      }),
    );
    expect(onSurfaceModeChange).toHaveBeenCalledWith("live");

    await user.click(screen.getByRole("button", { name: "More" }));
    const status = screen.getByRole("status", {
      name: "Session running normally",
    });
    expect(status).toHaveTextContent("chromium");
    expect(status.closest('[role="menuitem"]')).toBeNull();

    const endSession = screen.getByRole("menuitem", {
      name: "End browser session",
    });
    expect(endSession).toHaveAttribute("data-variant", "destructive");
    await user.click(endSession);
    expect(onEndSession).toHaveBeenCalledOnce();
  });

  it("labels the live-mode action with the screenshot target", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <BrowserPreviewToolbar {...toolbarProps({ surfaceMode: "live" })} />,
      { locale: "en-US" },
    );

    await user.click(screen.getByRole("button", { name: "More" }));
    expect(
      screen.getByRole("menuitem", {
        name: /Switch to screenshot preview\s*View and operate the latest screenshot/,
      }),
    ).toBeInTheDocument();
  });

  it("keeps screenshot annotation contextual to the canvas", () => {
    const source = readFileSync(
      resolve(
        process.cwd(),
        "src/components/workspace/browser-preview-panel.tsx",
      ),
      "utf8",
    );

    expect(source).toContain(
      'screenshot && effectiveSurfaceMode === "screenshot" && (',
    );
    expect(source).toContain("aria-pressed={annotationMode}");
    expect(source).not.toContain("ExternalLinkIcon");
  });
});
