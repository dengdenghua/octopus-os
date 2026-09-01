import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      livePreview: {
        inspectHint: "点击页面元素 · Esc 取消",
        cancelInspect: "取消选择",
        inspectElement: "选择元素",
        loading: "加载中...",
        aiEditTitle: "让 AI 修改所选元素",
        aiEditCancel: "取消元素修改",
        aiEditPlaceholder: "例如：把标题改得更有科技感，并保持现有布局",
        aiEditSend: "发送修改",
      },
    },
  }),
}));

import { InspectOverlay } from "./inspect-overlay";

describe("InspectOverlay AI edit handoff", () => {
  it("prepares a URL preview before activating element inspection", async () => {
    const onPrepareInspect = vi.fn();
    const iframeRef = createRef<HTMLIFrameElement>();
    const { rerender } = render(
      <InspectOverlay
        enabled
        filepath="/workspace/output/final/index.html"
        iframeRef={iframeRef}
        onPrepareInspect={onPrepareInspect}
      >
        <iframe ref={iframeRef} title="preview" />
      </InspectOverlay>,
    );

    await userEvent.click(screen.getByRole("button", { name: "选择元素" }));
    expect(onPrepareInspect).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "选择元素" })).toHaveTextContent(
      "加载中...",
    );

    rerender(
      <InspectOverlay
        enabled
        filepath="/workspace/output/final/index.html"
        iframeRef={iframeRef}
      >
        <iframe ref={iframeRef} title="preview" />
      </InspectOverlay>,
    );
    await waitFor(() => expect(iframeRef.current?.contentWindow).toBeTruthy());
    const source = iframeRef.current!.contentWindow!;
    const postMessage = vi.spyOn(source, "postMessage");
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: { type: "echo:inspect:ready" },
      }),
    );

    await waitFor(() =>
      expect(postMessage).toHaveBeenCalledWith(
        { type: "echo:inspect:enable" },
        "*",
      ),
    );
  });

  it("turns an iframe element selection into an AI edit request", async () => {
    const onRequestAiEdit = vi.fn();
    const iframeRef = createRef<HTMLIFrameElement>();
    render(
      <InspectOverlay
        enabled
        filepath="/workspace/output/final/index.html"
        iframeRef={iframeRef}
        onRequestAiEdit={onRequestAiEdit}
      >
        <iframe ref={iframeRef} title="preview" />
      </InspectOverlay>,
    );

    await waitFor(() => expect(iframeRef.current?.contentWindow).toBeTruthy());
    const source = iframeRef.current!.contentWindow!;
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: {
          type: "echo:inspect:select",
          payload: {
            selector: "main > h1.hero",
            tagName: "h1",
            outerHTML: '<h1 class="hero">Old title</h1>',
            textContent: "Old title",
            rect: { x: 10, y: 20, w: 200, h: 40 },
          },
        },
      }),
    );

    expect(await screen.findByText("让 AI 修改所选元素")).toBeInTheDocument();
    const input = screen.getByPlaceholderText(
      "例如：把标题改得更有科技感，并保持现有布局",
    );
    await userEvent.type(input, "改成发光的蓝色标题");
    await userEvent.click(screen.getByRole("button", { name: "发送修改" }));

    expect(onRequestAiEdit).toHaveBeenCalledWith(
      expect.objectContaining({
        selector: "main > h1.hero",
        textContent: "Old title",
      }),
      "改成发光的蓝色标题",
    );
    expect(screen.queryByText("让 AI 修改所选元素")).not.toBeInTheDocument();
  });
});
