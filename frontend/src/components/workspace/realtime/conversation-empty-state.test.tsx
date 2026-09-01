import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { ConversationEmptyState } from "./conversation-empty-state";

describe("ConversationEmptyState", () => {
  it("centres a normal empty group prompt without a misleading retry", () => {
    renderWithProviders(
      <ConversationEmptyState
        isGroupConversation
        hasError={false}
        onRetry={vi.fn()}
      />,
      { locale: "zh-CN" },
    );

    const state = screen.getByTestId("conversation-empty-state");
    expect(state).toHaveClass(
      "min-h-[clamp(12rem,38vh,22rem)]",
      "justify-center",
    );
    expect(state).toHaveTextContent("还没有消息");
    expect(state).toHaveTextContent("发消息给群成员");
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
  });

  it("uses the regular conversation hint outside group chat", () => {
    renderWithProviders(
      <ConversationEmptyState
        isGroupConversation={false}
        hasError={false}
        onRetry={vi.fn()}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByRole("status")).toHaveTextContent("开始一段新的对话吧");
  });

  it("offers retry only for a real load error", () => {
    const onRetry = vi.fn();
    renderWithProviders(
      <ConversationEmptyState isGroupConversation hasError onRetry={onRetry} />,
      { locale: "zh-CN" },
    );

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
