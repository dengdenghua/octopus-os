import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const artifactContext = vi.hoisted(() => ({
  clearSelection: vi.fn(),
  select: vi.fn(),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));
vi.mock("@/core/auth/api", () => ({ authHeaders: () => ({}) }));
vi.mock("@/core/messages/quick-reply", () => ({
  dispatchQuickReply: vi.fn(() => true),
}));
vi.mock("@/core/artifacts/hooks", () => ({
  useArtifactContent: () => ({
    content: "<!doctype html><html><body><h1>Old</h1></body></html>",
    url: undefined,
    refetch: vi.fn(),
  }),
  useArtifactDiff: () => ({
    originalContent: "",
    newContent: "",
    isDiffAvailable: false,
    isLoading: false,
  }),
}));
vi.mock("@/core/streamdown", () => ({ useStreamdownPlugins: () => ({}) }));
vi.mock("./context", () => ({
  useArtifacts: () => ({
    artifacts: ["workspace-output:final:site.html"],
    select: artifactContext.select,
    clearSelection: artifactContext.clearSelection,
  }),
}));
vi.mock("./use-install-skill", () => ({
  useInstallSkill: () => ({ installingFile: null, install: vi.fn() }),
}));
vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: {
        cancel: "取消",
        delete: "删除",
        close: "关闭",
        preview: "预览",
        openInNewWindow: "新窗口打开",
        download: "下载",
        install: "安装",
        loading: "加载中",
      },
      clipboard: {
        copyToClipboard: "复制",
        copiedToClipboard: "已复制",
        failedToCopyToClipboard: "复制失败",
      },
      toolCalls: { skillInstallTooltip: "安装技能" },
      livePreview: {
        inspectElement: "选择元素",
        cancelInspect: "取消选择",
        inspectHint: "选择元素提示",
        loading: "加载中",
        aiEditTitle: "AI 修改",
        aiEditCancel: "取消 AI 修改",
        aiEditPlaceholder: "修改要求",
        aiEditSend: "发送修改",
        aiEditQueued: "已发送",
        aiEditUnavailable: "无法发送",
        humanEdit: "直接编辑",
        humanEditing: "正在人工编辑页面",
        humanUnsaved: "有未保存修改",
        humanSave: "保存页面",
        humanCancel: "放弃修改",
        humanSaved: "页面修改已保存",
        humanUndo: "撤销上次保存",
        humanRestored: "已恢复到保存前版本",
        humanConflict: "Agent 已更新页面，你的修改尚未保存",
        humanReloadLatest: "放弃并载入最新版",
        humanDiscardTitle: "放弃未保存的页面修改？",
        humanDiscardDescription: "离开会丢失修改",
        humanDiscardConfirm: "放弃并离开",
      },
      codeEditor: { fileSaveFailed: "保存失败" },
    },
  }),
}));
vi.mock("../messages/context", () => ({
  useThread: () => ({ thread: { isLoading: false } }),
}));

import { ArtifactFileDetail, HtmlPreview } from "./artifact-file-detail";

function bridgeTokenOf(iframe: HTMLIFrameElement): string {
  const match = iframe.srcdoc.match(/const BRIDGE_TOKEN = ("[^"]+");/);
  if (!match?.[1]) throw new Error("private bridge token missing from srcDoc");
  return JSON.parse(match[1]) as string;
}

describe("HtmlPreview human editing", () => {
  beforeEach(() => {
    artifactContext.clearSelection.mockReset();
    artifactContext.select.mockReset();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              success: true,
              path: "/site.html",
              bytes: 32,
              sha256: "saved",
              revision_id: "1-aaaaaaaaaaaa.bak",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        ),
      ),
    );
  });

  it("edits the rendered body and saves it back to the scoped artifact", async () => {
    const onSaved = vi.fn();
    render(
      <HtmlPreview
        artifactRef="workspace-output:final:site.html"
        content="<!doctype html><html><head><title>Echo</title></head><body><h1>Old</h1></body></html>"
        filepath="site.html"
        onSaved={onSaved}
        threadId="thread-1"
      />,
    );
    const iframe = screen.getByTitle("Artifact preview") as HTMLIFrameElement;
    expect(iframe).toHaveAttribute("sandbox", "allow-scripts");
    const source = iframe.contentWindow!;
    const bridgeToken = bridgeTokenOf(iframe);
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: {
          type: "echo:inspect:ready",
          echoBridgeToken: bridgeToken,
        },
      }),
    );

    await userEvent.click(screen.getByRole("button", { name: "直接编辑" }));
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: {
          type: "echo:edit:state",
          active: true,
          echoBridgeToken: bridgeToken,
        },
      }),
    );
    expect(screen.getByText("正在人工编辑页面")).toBeInTheDocument();

    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: {
          type: "echo:edit:state",
          active: true,
          dirty: true,
          echoBridgeToken: bridgeToken,
        },
      }),
    );
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    const beforeUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(beforeUnload);
    expect(beforeUnload.defaultPrevented).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "保存页面" }));
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: {
          type: "echo:edit:content",
          bodyHtml: "<h1>Human edited</h1>",
          echoBridgeToken: bridgeToken,
        },
      }),
    );

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    const saved = onSaved.mock.calls[0]?.[0] as string;
    expect(saved).toContain("<head><title>Echo</title></head>");
    expect(saved).toContain("<body><h1>Human edited</h1></body>");
    expect(screen.getByTitle("Artifact preview")).toHaveAttribute(
      "srcdoc",
      expect.stringContaining("<h1>Human edited</h1>"),
    );
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8001/api/threads/thread-1/outputs/site.html?area=final",
      expect.objectContaining({ method: "PUT" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "撤销上次保存" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(2));
    expect(onSaved.mock.calls[1]?.[0]).toContain("<body><h1>Old</h1></body>");
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8001/api/threads/thread-1/output-revisions/site.html?area=final",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("does not offer direct editing for unscoped HTML", () => {
    render(
      <HtmlPreview
        artifactRef="/tmp/site.html"
        content="<h1>Read only</h1>"
        filepath="/tmp/site.html"
        threadId="thread-1"
      />,
    );

    expect(
      screen.queryByRole("button", { name: "直接编辑" }),
    ).not.toBeInTheDocument();
  });

  it("ignores forged save messages from scripts inside the artifact", () => {
    render(
      <HtmlPreview
        artifactRef="workspace-output:final:site.html"
        content="<body><h1>Safe</h1></body>"
        filepath="site.html"
        threadId="thread-1"
      />,
    );
    const iframe = screen.getByTitle("Artifact preview") as HTMLIFrameElement;

    fireEvent(
      window,
      new MessageEvent("message", {
        source: iframe.contentWindow!,
        data: {
          type: "echo:edit:content",
          bodyHtml: "<script>forged()</script>",
        },
      }),
    );

    expect(fetch).not.toHaveBeenCalled();
  });

  it("keeps the editing session open when the artifact changed concurrently", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ detail: { message: "文件已被 Agent 更新" } }),
            { status: 409, headers: { "Content-Type": "application/json" } },
          ),
        ),
    );
    const onSaved = vi.fn();
    const onReload = vi.fn();
    render(
      <HtmlPreview
        artifactRef="workspace-output:final:site.html"
        content="<body><h1>Old</h1></body>"
        filepath="site.html"
        onSaved={onSaved}
        onReload={onReload}
        threadId="thread-1"
      />,
    );
    const iframe = screen.getByTitle("Artifact preview") as HTMLIFrameElement;
    const source = iframe.contentWindow!;
    const bridgeToken = bridgeTokenOf(iframe);
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: {
          type: "echo:inspect:ready",
          echoBridgeToken: bridgeToken,
        },
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "直接编辑" }));
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: {
          type: "echo:edit:state",
          active: true,
          echoBridgeToken: bridgeToken,
        },
      }),
    );
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: {
          type: "echo:edit:content",
          bodyHtml: "<h1>Mine</h1>",
          echoBridgeToken: bridgeToken,
        },
      }),
    );

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(onSaved).not.toHaveBeenCalled();
    expect(
      screen.getByText("Agent 已更新页面，你的修改尚未保存"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存页面" })).toBeDisabled();
    await userEvent.click(
      screen.getByRole("button", { name: "放弃并载入最新版" }),
    );
    expect(onReload).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole("button", { name: "直接编辑" }),
    ).toBeInTheDocument();
  });

  it("reports edit protection and coalesces duplicate save requests", async () => {
    const onEditProtectionChange = vi.fn();
    render(
      <HtmlPreview
        artifactRef="workspace-output:final:site.html"
        content="<body><h1>Old</h1></body>"
        filepath="site.html"
        onEditProtectionChange={onEditProtectionChange}
        threadId="thread-1"
      />,
    );
    const iframe = screen.getByTitle("Artifact preview") as HTMLIFrameElement;
    const source = iframe.contentWindow!;
    const bridgeToken = bridgeTokenOf(iframe);
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: {
          type: "echo:inspect:ready",
          echoBridgeToken: bridgeToken,
        },
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "直接编辑" }));
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: {
          type: "echo:edit:state",
          active: true,
          dirty: true,
          echoBridgeToken: bridgeToken,
        },
      }),
    );
    await waitFor(() =>
      expect(onEditProtectionChange).toHaveBeenLastCalledWith(true),
    );

    const saveMessage = new MessageEvent("message", {
      source,
      data: {
        type: "echo:edit:content",
        bodyHtml: "<h1>Saved once</h1>",
        echoBridgeToken: bridgeToken,
      },
    });
    fireEvent(window, saveMessage);
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: saveMessage.data,
      }),
    );

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(onEditProtectionChange).toHaveBeenLastCalledWith(false),
    );
  });

  it("asks before closing an artifact with unsaved HTML edits", async () => {
    render(
      <ArtifactFileDetail
        filepath="workspace-output:final:site.html"
        threadId="thread-1"
      />,
    );
    const iframe = screen.getByTitle("Artifact preview") as HTMLIFrameElement;
    const source = iframe.contentWindow!;
    const bridgeToken = bridgeTokenOf(iframe);
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: {
          type: "echo:inspect:ready",
          echoBridgeToken: bridgeToken,
        },
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "直接编辑" }));
    fireEvent(
      window,
      new MessageEvent("message", {
        source,
        data: {
          type: "echo:edit:state",
          active: true,
          dirty: true,
          echoBridgeToken: bridgeToken,
        },
      }),
    );

    await userEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.getByText("放弃未保存的页面修改？")).toBeInTheDocument();
    expect(artifactContext.clearSelection).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(artifactContext.clearSelection).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "关闭" }));
    await userEvent.click(screen.getByRole("button", { name: "放弃并离开" }));
    expect(artifactContext.clearSelection).toHaveBeenCalledTimes(1);
  });
});
