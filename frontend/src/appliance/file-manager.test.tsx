import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type * as FilesModule from "./files";

import {
  copyEntry,
  downloadFile,
  emptyTrash,
  FileServiceUnavailableError,
  listDir,
  listTrash,
  uploadFile,
} from "./files";
import { requestHighRiskApproval } from "./approval";
import { FileManager } from "./file-manager";

vi.mock("./files", async (importOriginal) => {
  const actual = await importOriginal<typeof FilesModule>();
  return {
    ...actual,
    copyEntry: vi.fn(),
    downloadFile: vi.fn(),
    emptyTrash: vi.fn(),
    listDir: vi.fn(),
    listTrash: vi.fn().mockResolvedValue({ entries: [] }),
    restoreTrash: vi.fn(),
    trashEntry: vi.fn(),
    uploadFile: vi.fn(),
  };
});

vi.mock("./storage", () => ({
  answerStorage: vi.fn(),
  searchStorage: vi.fn(),
}));

vi.mock("./approval", () => ({
  requestHighRiskApproval: vi.fn(),
}));

const report = {
  name: "report.txt",
  path: "report.txt",
  kind: "file" as const,
  size: 4,
  mtime: 1,
};

beforeEach(() => {
  vi.mocked(listDir).mockResolvedValue({ path: "", entries: [report] });
  vi.mocked(uploadFile).mockResolvedValue({
    ok: true,
    entry: report,
    sha256: "a".repeat(64),
    hashVerified: false,
  });
  vi.mocked(downloadFile).mockResolvedValue();
  vi.mocked(copyEntry).mockResolvedValue({
    ok: true,
    entry: { ...report, name: "report 副本.txt", path: "report 副本.txt" },
  });
  vi.mocked(listTrash).mockResolvedValue({ entries: [] });
  vi.mocked(emptyTrash).mockResolvedValue({ ok: true, emptied: 1 });
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "one-shot.signature",
    expiresIn: 90,
    action: "files.trash.empty",
    target: "recycle-bin",
  });
});

describe("Echo OS file manager transfers", () => {
  it("exposes Echo HD only when a native file manager is available", async () => {
    const onOpenSystemFiles = vi.fn();
    const first = render(<FileManager onClose={vi.fn()} />);
    await screen.findByText("report.txt");
    expect(
      screen.queryByRole("button", { name: "Echo HD" }),
    ).not.toBeInTheDocument();
    first.unmount();

    const user = userEvent.setup();
    render(
      <FileManager onClose={vi.fn()} onOpenSystemFiles={onOpenSystemFiles} />,
    );
    await screen.findByText("report.txt");
    await user.click(screen.getByRole("button", { name: "Echo HD" }));
    expect(onOpenSystemFiles).toHaveBeenCalledOnce();
  });

  it("shows a recoverable product state when the NAS route is unavailable", async () => {
    const user = userEvent.setup();
    const onOpenSettings = vi.fn();
    vi.mocked(listDir).mockRejectedValueOnce(new FileServiceUnavailableError());

    render(<FileManager onClose={vi.fn()} onOpenSettings={onOpenSettings} />);

    await screen.findByText("NAS 文件服务尚未启用");
    expect(screen.queryByText(/Not Found/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传文件" })).toBeDisabled();
    expect(screen.getByText("NAS 服务未连接")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "打开系统设置" }));
    expect(onOpenSettings).toHaveBeenCalledOnce();

    await user.click(screen.getByRole("button", { name: "重试连接" }));
    await screen.findByText("report.txt");
    expect(screen.getByRole("button", { name: "上传文件" })).toBeEnabled();
  });

  it("keeps unsupported local locations explicit instead of showing NAS entries", async () => {
    const user = userEvent.setup();
    render(<FileManager onClose={vi.fn()} />);
    await screen.findByText("report.txt");

    await user.click(screen.getByRole("button", { name: "个人" }));

    expect(screen.getByRole("heading", { name: "个人" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "此位置由宿主系统管理，当前桌面会话未接入本地文件桥接。",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("report.txt")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传文件" })).toBeDisabled();
    expect(listDir).toHaveBeenCalled();
  });

  it("uploads files selected from the toolbar", async () => {
    const user = userEvent.setup();
    const { container } = render(<FileManager onClose={vi.fn()} />);
    await screen.findByText("report.txt");
    const input =
      container.querySelector<HTMLInputElement>('input[type="file"]')!;
    const file = new File(["new"], "new.txt", { type: "text/plain" });

    await user.upload(input, file);

    await waitFor(() => expect(uploadFile).toHaveBeenCalled());
    expect(vi.mocked(uploadFile).mock.calls[0]![0]).toBe("");
    expect(vi.mocked(uploadFile).mock.calls[0]![1]).toBe(file);
  });

  it("offers pause, resume and cancel controls for an active upload", async () => {
    vi.mocked(uploadFile).mockImplementation(
      (_path, _file, _progress, options) =>
        new Promise((_resolve, reject) => {
          options?.control?.signal.addEventListener(
            "abort",
            () => reject(new Error("上传已取消")),
            { once: true },
          );
        }),
    );
    const user = userEvent.setup();
    const { container } = render(<FileManager onClose={vi.fn()} />);
    await screen.findByText("report.txt");
    const input =
      container.querySelector<HTMLInputElement>('input[type="file"]')!;

    await user.upload(input, new File(["new"], "new.txt"));
    await user.click(await screen.findByRole("button", { name: "暂停上传" }));
    expect(
      screen.getByRole("button", { name: "继续上传" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "继续上传" }));
    expect(
      screen.getByRole("button", { name: "暂停上传" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "取消上传" }));
    await screen.findByText("上传已取消");
  });

  it("downloads and copies a listed file from row actions", async () => {
    const user = userEvent.setup();
    render(<FileManager onClose={vi.fn()} />);
    await screen.findByText("report.txt");

    await user.click(screen.getByRole("button", { name: "下载 report.txt" }));
    await user.click(screen.getByRole("button", { name: "复制 report.txt" }));

    expect(downloadFile).toHaveBeenCalledWith(
      "report.txt",
      "report.txt",
      expect.any(Function),
      { signal: expect.any(AbortSignal) },
    );
    expect(copyEntry).toHaveBeenCalledWith("report.txt", "report 副本.txt");
  });

  it("cancels an active download from the fixed-height transfer bar", async () => {
    vi.mocked(downloadFile).mockImplementation(
      (_path, _filename, _progress, options) =>
        new Promise((_resolve, reject) => {
          options?.signal?.addEventListener(
            "abort",
            () => reject(new Error("下载已取消")),
            { once: true },
          );
        }),
    );
    const user = userEvent.setup();
    render(<FileManager onClose={vi.fn()} />);
    await screen.findByText("report.txt");

    await user.click(screen.getByRole("button", { name: "下载 report.txt" }));
    await user.click(await screen.findByRole("button", { name: "取消下载" }));

    await screen.findByText("下载已取消");
  });

  it("requires password step-up before permanently emptying trash", async () => {
    const user = userEvent.setup();
    vi.mocked(listTrash).mockResolvedValue({
      entries: [
        {
          id: "trash-1",
          name: "old.txt",
          original: "old.txt",
          kind: "file",
          trashed_at: 1,
        },
      ],
    });
    render(<FileManager onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "回收站" }));
    await waitFor(() => expect(screen.getAllByText("old.txt")).toHaveLength(2));
    await user.click(screen.getByRole("button", { name: "清空回收站" }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(emptyTrash).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "永久删除" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "files.trash.empty",
        "recycle-bin",
        "device-password",
      ),
    );
    expect(emptyTrash).toHaveBeenCalledWith("one-shot.signature");
  });
});
