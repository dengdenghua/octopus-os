import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { Sidebar, SidebarProvider, SidebarTrigger } from "./sidebar";

describe("mobile sidebar", () => {
  beforeEach(() => {
    vi.mocked(window.matchMedia).mockImplementation((query: string) => ({
      matches: query.includes("max-width"),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
  });

  it("uses the mobile open state and localized accessible copy", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <SidebarProvider>
        <SidebarTrigger aria-label="打开侧栏菜单" />
        <Sidebar>侧栏内容</Sidebar>
      </SidebarProvider>,
      { locale: "zh-CN" },
    );

    const trigger = screen.getByRole("button", { name: "打开侧栏菜单" });
    expect(trigger).toHaveTextContent("展开侧栏 (⌘B)");

    await user.click(trigger);

    expect(await screen.findByRole("dialog", { name: "导航" })).toBeVisible();
    expect(screen.getByText("打开侧栏菜单")).toBeInTheDocument();
    expect(trigger).toHaveTextContent("收起侧栏 (⌘B)");
  });
});
