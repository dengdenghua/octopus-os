import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/harness";

import ConversationSettingsPage from "./conversation-settings-page";

describe("ConversationSettingsPage", () => {
  it("owns the three detail levels and chat font preference", async () => {
    renderWithProviders(<ConversationSettingsPage />, { locale: "zh-CN" });

    expect(screen.getByRole("heading", { name: "对话" })).toBeInTheDocument();
    const detail = screen.getByRole("combobox", { name: "对话细节级别" });
    expect(detail).toHaveTextContent("中 - 平衡");

    fireEvent.pointerDown(detail, { button: 0 });
    fireEvent.click(detail);
    const low = await screen.findByRole("option", { name: "低 - 简洁视图" });
    expect(low).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "中 - 平衡" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "高 - 详细" }),
    ).toBeInTheDocument();
    fireEvent.click(low);
    expect(
      screen.getByRole("combobox", { name: "聊天字号" }),
    ).toBeInTheDocument();
  });
});
