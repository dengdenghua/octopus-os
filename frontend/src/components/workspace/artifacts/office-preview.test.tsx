import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const dispatchQuickReply = vi.fn(() => true);

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));
vi.mock("@/core/messages/quick-reply", () => ({
  dispatchQuickReply: (...args: unknown[]) => dispatchQuickReply(...args),
}));
vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { preview: "预览", loading: "加载中" },
      livePreview: {
        officeEdit: "AI 修改",
        officeSelect: "选择内容",
        officeCancelSelect: "取消选择",
        officeSelected: "已选择",
        officeEditTitle: "修改这份办公文件",
        officeEditPlaceholder: "输入修改要求",
        officeEditHint: "完成后自动刷新预览",
        aiEditSend: "发送修改",
        aiEditQueued: "已发送",
        aiEditUnavailable: "暂时无法发送",
        previewError: "预览加载失败",
        previewRetry: "重新加载预览",
        officeFidelity: "原貌预览",
      },
    },
  }),
}));
vi.mock("../messages/context", () => ({
  useThread: () => ({
    isMock: false,
    thread: { isLoading: false },
  }),
}));

import { OfficePreview } from "./artifact-file-detail";

describe("OfficePreview", () => {
  beforeEach(() => {
    dispatchQuickReply.mockClear();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            '<!doctype html><html><body><section data-office-node="slide:1">Deck</section></body></html>',
            { status: 200, headers: { "Content-Type": "text/html" } },
          ),
        ),
    );
  });

  it("renders a safe office preview and sends a task-scoped edit", async () => {
    render(
      <OfficePreview
        displayPath="deck.pptx"
        filepath="workspace-output:final:deck.pptx"
        isMock={false}
        kind="presentation"
        threadId="thread-1"
      />,
    );

    expect(await screen.findByTitle("deck.pptx 预览")).toHaveAttribute(
      "srcdoc",
      expect.stringContaining('data-office-node="slide:1"'),
    );
    await userEvent.click(screen.getByRole("button", { name: "AI 修改" }));
    await userEvent.type(
      screen.getByPlaceholderText("输入修改要求"),
      "把第三页改成风险矩阵",
    );
    await userEvent.click(screen.getByRole("button", { name: "发送修改" }));

    expect(dispatchQuickReply).toHaveBeenCalledWith({
      threadId: "thread-1",
      text: expect.stringContaining("把第三页改成风险矩阵"),
    });
    expect(screen.queryByText("修改这份办公文件")).not.toBeInTheDocument();
  });

  it("turns a selected slide into a location-scoped edit", async () => {
    render(
      <OfficePreview
        displayPath="deck.pptx"
        filepath="workspace-output:final:deck.pptx"
        isMock={false}
        kind="presentation"
        threadId="thread-1"
      />,
    );
    const iframe = (await screen.findByTitle(
      "deck.pptx 预览",
    )) as HTMLIFrameElement;
    await waitFor(() => expect(iframe.contentWindow).toBeTruthy());
    const source = iframe.contentWindow!;
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: { type: "echo:office:ready" },
      }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "选择内容" }),
    );
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: {
          type: "echo:office:select",
          payload: {
            node: "slide:3",
            label: "Slide 3",
            text: "Current risks",
          },
        },
      }),
    );

    expect(await screen.findByText(/已选择: Slide 3/)).toBeInTheDocument();
    await userEvent.type(
      screen.getByPlaceholderText("输入修改要求"),
      "改成风险矩阵",
    );
    await userEvent.click(screen.getByRole("button", { name: "发送修改" }));

    expect(dispatchQuickReply).toHaveBeenCalledWith({
      threadId: "thread-1",
      text: expect.stringContaining('"node": "slide:3"'),
    });
  });

  it("offers an in-place retry when the authenticated preview request fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("unauthorized", { status: 401 }))
      .mockResolvedValueOnce(
        new Response("<!doctype html><html><body>Recovered</body></html>", {
          status: 200,
          headers: { "Content-Type": "text/html" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <OfficePreview
        displayPath="deck.pptx"
        filepath="workspace-output:final:deck.pptx"
        isMock={false}
        kind="presentation"
        threadId="thread-1"
      />,
    );

    expect(await screen.findByText("预览加载失败")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重新加载预览" }));

    expect(await screen.findByTitle("deck.pptx 预览")).toHaveAttribute(
      "srcdoc",
      expect.stringContaining("Recovered"),
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rejects a legacy binary response instead of injecting it as HTML", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("legacy-binary", {
          status: 200,
          headers: { "Content-Type": "application/msword" },
        }),
      ),
    );
    render(
      <OfficePreview
        displayPath="legacy.doc"
        filepath="workspace-output:final:legacy.doc"
        isMock={false}
        kind="document"
        threadId="thread-1"
      />,
    );

    expect(await screen.findByText("预览加载失败")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "AI 修改" })).toBeEnabled();
    expect(screen.queryByTitle("legacy.doc 预览")).not.toBeInTheDocument();
  });

  it("starts with a high-fidelity layout and switches to selectable structure", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          "<!doctype html><html><body>Original layout</body></html>",
          {
            status: 200,
            headers: {
              "Content-Type": "text/html",
              "X-Echo-Office-Preview": "fidelity",
            },
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          '<!doctype html><html><body><section data-office-node="slide:1">Deck</section></body></html>',
          { status: 200, headers: { "Content-Type": "text/html" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          "<!doctype html><html><body>Original layout restored</body></html>",
          {
            status: 200,
            headers: {
              "Content-Type": "text/html",
              "X-Echo-Office-Preview": "fidelity",
            },
          },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <OfficePreview
        displayPath="deck.pptx"
        filepath="workspace-output:final:deck.pptx"
        isMock={false}
        kind="presentation"
        threadId="thread-1"
      />,
    );

    const fidelityIframe = await screen.findByTitle("deck.pptx 预览");
    expect(fidelityIframe).toHaveAttribute(
      "srcdoc",
      expect.stringContaining("Original layout"),
    );
    expect(fidelityIframe).toHaveAttribute("sandbox", "");
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "office_fidelity_preview=true",
    );
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ cache: "no-store" });

    await userEvent.click(screen.getByRole("button", { name: "选择内容" }));
    await waitFor(() =>
      expect(screen.getByTitle("deck.pptx 预览")).toHaveAttribute(
        "srcdoc",
        expect.stringContaining('data-office-node="slide:1"'),
      ),
    );
    const selectableIframe = screen.getByTitle(
      "deck.pptx 预览",
    ) as HTMLIFrameElement;
    expect(selectableIframe).toHaveAttribute("sandbox", "allow-scripts");
    fireEvent(
      window,
      new MessageEvent("message", {
        source: selectableIframe.contentWindow,
        data: { type: "echo:office:ready" },
      }),
    );

    expect(
      await screen.findByRole("button", { name: "取消选择" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "原貌预览" }),
    ).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[1]?.[0])).not.toContain(
      "office_fidelity_preview=true",
    );

    await userEvent.click(screen.getByRole("button", { name: "原貌预览" }));
    await waitFor(() =>
      expect(screen.getByTitle("deck.pptx 预览")).toHaveAttribute(
        "srcdoc",
        expect.stringContaining("Original layout restored"),
      ),
    );
    expect(screen.getByTitle("deck.pptx 预览")).toHaveAttribute("sandbox", "");
    expect(String(fetchMock.mock.calls[2]?.[0])).toContain(
      "office_fidelity_preview=true",
    );
  });
});
