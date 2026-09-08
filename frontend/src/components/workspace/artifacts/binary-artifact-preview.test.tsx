import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { BinaryArtifactPreview } from "./binary-artifact-preview";

vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer source-token" }),
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

const createObjectURL = vi.fn((_blob: Blob) => "blob:current-original");
const revokeObjectURL = vi.fn();

describe("authenticated original file preview", () => {
  beforeEach(() => {
    createObjectURL.mockClear();
    revokeObjectURL.mockClear();
    vi.stubGlobal(
      "URL",
      class extends URL {
        static createObjectURL = createObjectURL;
        static revokeObjectURL = revokeObjectURL;
      },
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("reads the original with task scope and a bearer header, then revokes its blob", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("synthetic image bytes", {
        headers: { "Content-Type": "image/png" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const view = renderWithProviders(
      <BinaryArtifactPreview
        filepath={"C:\\authorized\\original.png"}
        threadId="t1"
      />,
      { locale: "zh-CN" },
    );
    expect(await screen.findByTitle("预览")).toHaveAttribute(
      "src",
      "blob:current-original",
    );
    const url = new URL(String(fetchMock.mock.calls[0]?.[0]));
    expect(url.pathname).toBe("/api/fs/content");
    expect(url.searchParams.get("path")).toBe("C:\\authorized\\original.png");
    expect(url.searchParams.get("thread_id")).toBe("t1");
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      headers: { Authorization: "Bearer source-token" },
      cache: "no-store",
    });
    view.unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:current-original");
  });

  it.each([403, 404, 413])(
    "shows an actionable HTTP %s failure without opening an attachment substitute",
    async (status) => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(new Response("private source path", { status }));
      vi.stubGlobal("fetch", fetchMock);
      renderWithProviders(
        <BinaryArtifactPreview
          filepath="/authorized/original.png"
          threadId="t1"
        />,
        { locale: "zh-CN" },
      );
      expect(await screen.findByRole("alert")).toHaveTextContent(
        status === 403
          ? "当前任务有权访问所在目录"
          : status === 413
            ? "文件超过此处的读取上限"
            : "可能已被移动或删除",
      );
      expect(screen.queryByTitle("预览")).not.toBeInTheDocument();
      expect(screen.queryByText("private source path")).not.toBeInTheDocument();
      expect(fetchMock).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole("button", { name: "重新加载预览" }));
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    },
  );

  it("ignores a late prior file body after a new original is selected", async () => {
    let finishBody!: (blob: Blob) => void;
    const oldBody = new Promise<Blob>((resolve) => {
      finishBody = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, blob: () => oldBody })
      .mockResolvedValueOnce(
        new Response("new original", {
          headers: { "Content-Type": "image/png" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const view = renderWithProviders(
      <BinaryArtifactPreview filepath="/authorized/old.png" threadId="t1" />,
      { locale: "zh-CN" },
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    view.rerender(
      <BinaryArtifactPreview filepath="/authorized/new.png" threadId="t1" />,
    );
    await screen.findByTitle("预览");
    await act(async () => finishBody(new Blob(["old original"])));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(await createObjectURL.mock.calls[0]![0].text()).toBe("new original");
  });
});
