import { expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { ChatInputBox } from "@/components/workspace/chat-input-box";
import { renderWithProviders } from "@/test/harness";

import { Banner } from "./banner";

it("localizes the shared dismiss control", () => {
  renderWithProviders(<Banner onDismiss={vi.fn()}>提示内容</Banner>, {
    locale: "zh-CN",
  });

  expect(screen.getByRole("button", { name: "关闭" })).toBeInTheDocument();
});

it("localizes the composer send button", () => {
  renderWithProviders(<ChatInputBox onSubmit={vi.fn()} />, {
    locale: "zh-CN",
  });

  expect(screen.getByRole("button", { name: "发送" })).toBeInTheDocument();
});

it.each([
  ["ja-JP" as const, "送信", "停止"],
  ["ko-KR" as const, "보내기", "중지"],
])("localizes composer send and stop in %s", (locale, send, stop) => {
  renderWithProviders(<ChatInputBox onSubmit={vi.fn()} />, {
    locale,
  });
  expect(screen.getByRole("button", { name: send })).toBeInTheDocument();

  renderWithProviders(
    <ChatInputBox onSubmit={vi.fn()} status="streaming" defaultValue="hello" />,
    { locale },
  );
  expect(screen.getByRole("button", { name: stop })).toBeInTheDocument();
});
