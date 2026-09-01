import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const {
  clearCachedPublicThreadShare,
  createPublicThreadShare,
  getCachedPublicThreadShare,
  isPublicThreadShareUrl,
  resolvePublicThreadShareUrl,
  revokePublicThreadShare,
  copyTextToClipboard,
} = vi.hoisted(() => ({
  clearCachedPublicThreadShare: vi.fn(),
  createPublicThreadShare: vi.fn(),
  getCachedPublicThreadShare: vi.fn(),
  isPublicThreadShareUrl: vi.fn(),
  resolvePublicThreadShareUrl: vi.fn(),
  revokePublicThreadShare: vi.fn(),
  copyTextToClipboard: vi.fn(),
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { close: "关闭" },
      share: {
        share: "分享",
        shareTask: "分享任务",
        shareDescription: "公开只读快照",
        wechat: "微信",
        moments: "朋友圈",
        copyLink: "复制链接",
        qrCode: "二维码",
        openInBrowser: "浏览器",
        creatingLink: "正在生成分享链接…",
        linkCopied: "分享链接已复制",
        linkFailed: "生成分享链接失败",
        wechatQrTitle: "分享到微信",
        momentsQrTitle: "分享到朋友圈",
        qrTitle: "分享二维码",
        wechatQrHint: "微信扫码",
        momentsQrHint: "朋友圈扫码",
        qrHint: "扫码打开",
        localOnlyHint: "仅本机可访问",
        stopSharing: "取消公开分享",
        sharingStopped: "已取消公开分享",
        stopSharingFailed: "取消分享失败",
        unavailable: "请先发送消息",
        exportReplay: "导出可回放 HTML",
      },
    },
    locale: "zh",
    setLocale: () => Promise.resolve(),
  }),
}));

vi.mock("@/core/sharing/public-thread-share", () => ({
  clearCachedPublicThreadShare,
  createPublicThreadShare,
  getCachedPublicThreadShare,
  isPublicThreadShareUrl,
  resolvePublicThreadShareUrl,
  revokePublicThreadShare,
}));

vi.mock("@/core/clipboard", () => ({ copyTextToClipboard }));

vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ value, ...props }: { value: string }) => (
    <svg data-testid="share-qr" data-value={value} {...props} />
  ),
}));

import { ShareMenu } from "./share-menu";

describe("ShareMenu · public task sharing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCachedPublicThreadShare.mockReturnValue(null);
    resolvePublicThreadShareUrl.mockImplementation(
      (path: string, canonicalUrl?: string | null) =>
        canonicalUrl ?? `http://localhost:3000/${path}`,
    );
    isPublicThreadShareUrl.mockImplementation((url: string) =>
      url.startsWith("https://"),
    );
    createPublicThreadShare.mockResolvedValue({
      token: "share-token",
      share_id: "share-id",
      share_path: "#/share/share-token",
      created_at: "2026-08-25T00:00:00Z",
      expires_at: "2099-08-25T00:00:00Z",
    });
    revokePublicThreadShare.mockResolvedValue(undefined);
    copyTextToClipboard.mockResolvedValue(undefined);
  });

  it("uses an accessible compact trigger and Tencent-style share channels", async () => {
    const user = userEvent.setup();
    render(<ShareMenu threadId="thread-1" title="Run" iconOnly />);

    const trigger = screen.getByRole("button", { name: "分享" });
    expect(trigger).toHaveAttribute("data-slot", "share-menu-trigger");
    await user.click(trigger);

    expect(await screen.findByText("分享任务")).toBeInTheDocument();
    expect(screen.getByText("微信")).toBeInTheDocument();
    expect(screen.getByText("朋友圈")).toBeInTheDocument();
    expect(screen.getByText("复制链接")).toBeInTheDocument();
    expect(screen.getByText("二维码")).toBeInTheDocument();
    expect(screen.getByText("浏览器")).toBeInTheDocument();
    expect(screen.queryByText("存为图片")).not.toBeInTheDocument();
  });

  it("creates one public snapshot lazily and copies its link", async () => {
    const user = userEvent.setup();
    render(<ShareMenu threadId="thread-1" title="Run" />);

    expect(createPublicThreadShare).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "分享" }));
    await user.click(await screen.findByText("复制链接"));

    await waitFor(() => {
      expect(createPublicThreadShare).toHaveBeenCalledWith("thread-1");
      expect(copyTextToClipboard).toHaveBeenCalledWith(
        "http://localhost:3000/#/share/share-token",
      );
      expect(resolvePublicThreadShareUrl).toHaveBeenCalledWith(
        "#/share/share-token",
        undefined,
      );
    });
  });

  it("explains that WeChat and QR need a configured HTTPS public service", async () => {
    const user = userEvent.setup();
    render(<ShareMenu threadId="thread-1" title="Run" />);

    await user.click(screen.getByRole("button", { name: "分享" }));
    await user.click(await screen.findByText("微信"));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("分享到微信")).toBeInTheDocument();
    expect(await screen.findByText("仅本机可访问")).toBeInTheDocument();
    expect(screen.queryByTestId("share-qr")).not.toBeInTheDocument();
  });

  it("uses the backend canonical HTTPS URL for a WeChat QR code", async () => {
    createPublicThreadShare.mockResolvedValueOnce({
      token: "share-token",
      share_id: "share-id",
      share_path: "#/share/share-token",
      share_url: "https://share.example.test/ui/#/share/share-token",
      created_at: "2026-08-25T00:00:00Z",
      expires_at: "2099-08-25T00:00:00Z",
    });
    const user = userEvent.setup();
    render(<ShareMenu threadId="thread-1" title="Run" />);

    await user.click(screen.getByRole("button", { name: "分享" }));
    await user.click(await screen.findByText("微信"));

    const qr = await screen.findByTestId("share-qr");
    expect(qr).toHaveAttribute(
      "data-value",
      "https://share.example.test/ui/#/share/share-token",
    );
  });

  it("keeps replay export as a separate optional action", async () => {
    const onExportReplay = vi.fn();
    const user = userEvent.setup();
    render(
      <ShareMenu
        threadId="thread-1"
        title="Run"
        onExportReplay={onExportReplay}
      />,
    );

    await user.click(screen.getByRole("button", { name: "分享" }));
    await user.click(await screen.findByText("导出可回放 HTML"));
    expect(onExportReplay).toHaveBeenCalledTimes(1);
  });

  it("lets the owner revoke the generated public link", async () => {
    const user = userEvent.setup();
    render(<ShareMenu threadId="thread-1" title="Run" />);

    await user.click(screen.getByRole("button", { name: "分享" }));
    await user.click(await screen.findByText("复制链接"));
    await waitFor(() => expect(createPublicThreadShare).toHaveBeenCalledOnce());

    await user.click(screen.getByRole("button", { name: "分享" }));
    await user.click(await screen.findByText("取消公开分享"));

    await waitFor(() => {
      expect(revokePublicThreadShare).toHaveBeenCalledWith("share-id");
      expect(clearCachedPublicThreadShare).toHaveBeenCalledWith("thread-1");
    });
  });

  it("restores a tab-cached share after refresh so it can be revoked", async () => {
    getCachedPublicThreadShare.mockReturnValueOnce({
      token: "cached-token",
      share_id: "cached-share-id",
      share_path: "#/share/cached-token",
      created_at: "2026-08-25T00:00:00Z",
      expires_at: "2099-08-25T00:00:00Z",
    });
    const user = userEvent.setup();
    render(<ShareMenu threadId="thread-1" title="Run" />);

    await user.click(screen.getByRole("button", { name: "分享" }));
    await user.click(await screen.findByText("取消公开分享"));

    await waitFor(() => {
      expect(createPublicThreadShare).not.toHaveBeenCalled();
      expect(revokePublicThreadShare).toHaveBeenCalledWith("cached-share-id");
    });
  });
});
