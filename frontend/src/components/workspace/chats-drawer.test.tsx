import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { eventBus } from "@/core/events";
import { renderWithProviders } from "@/test/harness";

import { ChatsDrawer } from "./chats-drawer";

const { useThreadsMock } = vi.hoisted(() => ({
  useThreadsMock: vi.fn(() => ({ data: [] })),
}));

vi.mock("@/core/threads/hooks", () => ({
  useThreads: (...args: unknown[]) => useThreadsMock(...args),
  useDeleteThread: () => ({ isPending: false, mutate: vi.fn() }),
  useRenameThread: () => ({ isPending: false, mutate: vi.fn() }),
}));

describe("ChatsDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.setItem("echo.active-agent", "installed_researcher");
  });

  it("migrates an on-demand identity selection to the default fixed persona", () => {
    renderWithProviders(<ChatsDrawer open onOpenChange={vi.fn()} />, {
      locale: "zh-CN",
    });

    expect(useThreadsMock).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 50 }),
      undefined,
      "general",
    );
  });

  it("keeps settings reachable from the narrow-screen navigation", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    const openSettings = vi.fn();
    const offOpenSettings = eventBus.on("ui:open-settings", openSettings);

    renderWithProviders(<ChatsDrawer open onOpenChange={onOpenChange} />, {
      locale: "zh-CN",
    });

    await user.click(screen.getByRole("button", { name: "设置" }));

    expect(onOpenChange).toHaveBeenCalledWith(false);
    await waitFor(() => expect(openSettings).toHaveBeenCalledTimes(1));
    offOpenSettings();
  });

  it("keeps primary workspace destinations reachable on narrow screens", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();

    renderWithProviders(<ChatsDrawer open onOpenChange={onOpenChange} />, {
      locale: "zh-CN",
    });

    expect(screen.getByRole("link", { name: "HUB" })).toHaveAttribute(
      "href",
      "/workspace/agents?surface=chat",
    );
    expect(screen.getByRole("link", { name: "助手" })).toHaveAttribute(
      "href",
      "/workspace/realtime/echo-assistant?agent=echo",
    );
    expect(screen.getByRole("link", { name: "发现社区" })).toHaveAttribute(
      "href",
      "/workspace/community",
    );
    expect(screen.getByRole("link", { name: "订阅" })).toHaveAttribute(
      "href",
      "/workspace/intelligence?surface=chat",
    );
    expect(screen.getByRole("link", { name: "自进化" })).toHaveAttribute(
      "href",
      "/workspace/evolution?surface=chat",
    );
    expect(screen.getByRole("link", { name: "本地数据库" })).toHaveAttribute(
      "href",
      "/workspace/storage?surface=company&library=docs",
    );

    await user.click(screen.getByRole("link", { name: "本地数据库" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
