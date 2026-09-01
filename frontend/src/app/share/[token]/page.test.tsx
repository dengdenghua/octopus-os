import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const { getPublicThreadShare } = vi.hoisted(() => ({
  getPublicThreadShare: vi.fn(),
}));

vi.mock("@/core/sharing/public-thread-share", () => ({
  getPublicThreadShare,
}));

vi.mock("@/core/streamdown", () => ({
  useStreamdownPlugins: () => ({ remarkPlugins: [], rehypePlugins: [] }),
}));

vi.mock("@/components/workspace/messages/markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => (
    <div data-testid="public-assistant-markdown">{content}</div>
  ),
}));

import PublicThreadSharePage from "./page";

const publicShare = {
  schema: "echo.thread-share.v1",
  created_at: "2026-08-25T00:00:00Z",
  title: "发布检查",
  messages: [
    { role: "user" as const, content: "请检查发布结果" },
    { role: "assistant" as const, content: "## 已完成\n发布检查通过。" },
  ],
  artifacts: ["/Users/private/release-notes.md", "C:\\private\\report.pdf"],
  stats: { turns: 1, messages: 2, artifacts: 2 },
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/share/public-token"]}>
      <Routes>
        <Route path="/share/:token" element={<PublicThreadSharePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("PublicThreadSharePage", () => {
  beforeEach(() => {
    getPublicThreadShare.mockReset();
    document.title = "Echo";
    document.head.querySelector('meta[name="robots"]')?.remove();
  });

  afterEach(() => {
    cleanup();
  });

  it("loads the anonymous snapshot and renders only public presentation data", async () => {
    getPublicThreadShare.mockResolvedValue(publicShare);
    const view = renderPage();

    expect(screen.getByRole("status")).toHaveTextContent("正在打开分享内容");
    expect(
      await screen.findByRole("heading", { name: "发布检查" }),
    ).toBeInTheDocument();
    expect(getPublicThreadShare).toHaveBeenCalledWith("public-token");
    expect(screen.getByText("1 轮对话")).toBeInTheDocument();
    expect(screen.getByText("2 条消息")).toBeInTheDocument();
    expect(screen.getByText("请检查发布结果")).toBeInTheDocument();
    expect(screen.getByTestId("public-assistant-markdown")).toHaveTextContent(
      "发布检查通过",
    );
    expect(screen.getByText("release-notes.md")).toBeInTheDocument();
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
    expect(screen.queryByText(/Users\/private/)).not.toBeInTheDocument();
    expect(screen.getByText(/AI 生成内容可能存在错误/)).toBeInTheDocument();

    await waitFor(() => {
      expect(document.title).toBe("发布检查 · EchoAI 分享");
      expect(
        document.head.querySelector('meta[name="robots"]'),
      ).toHaveAttribute("content", "noindex, nofollow, noarchive");
    });

    view.unmount();
    expect(document.title).toBe("Echo");
    expect(document.head.querySelector('meta[name="robots"]')).toBeNull();
  });

  it("shows a recoverable error for a revoked link", async () => {
    getPublicThreadShare
      .mockRejectedValueOnce(new Error("分享内容不存在或已被取消"))
      .mockResolvedValueOnce(publicShare);
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "分享内容不存在或已被取消",
    );
    await user.click(screen.getByRole("button", { name: "重新加载" }));

    expect(
      await screen.findByRole("heading", { name: "发布检查" }),
    ).toBeInTheDocument();
    expect(getPublicThreadShare).toHaveBeenCalledTimes(2);
  });
});
