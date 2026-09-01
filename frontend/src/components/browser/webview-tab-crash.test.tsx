import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import type { BrowserTab } from "./browser-store";
import { WebviewTab } from "./webview-tab";

describe("browser tab crash recovery", () => {
  it("stops a repeated crash loop and offers reload and close actions", () => {
    const onClose = vi.fn();
    const tab: BrowserTab = {
      id: "crashed-tab",
      url: "https://example.com",
      title: "Example",
      isLoading: false,
      device: "desktop",
      crash: {
        reason: "crashed",
        exitCode: -1,
        occurredAt: Date.now(),
        attempts: 2,
        autoRecovering: false,
      },
    };

    renderWithProviders(
      <WebviewTab tab={tab} active onPatch={vi.fn()} onClose={onClose} />,
      { locale: "zh-CN" },
    );

    expect(screen.getByText(/60 秒内已异常 2 次/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重载页面" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "关闭标签页" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
