import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchStorageUsage } from "./files";
import type * as FilesModule from "./files";
import { StorageCenterPanel } from "./storage-center-panel";

vi.mock("./files", async (importOriginal) => {
  const original = await importOriginal<typeof FilesModule>();
  return { ...original, fetchStorageUsage: vi.fn() };
});
vi.mock("./omv-storage-health", () => ({
  OmvStorageHealth: () => <div>真实磁盘健康页</div>,
}));
vi.mock("./omv-sharing-panel", () => ({
  OmvSharingPanel: () => <div>真实共享管理页</div>,
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchStorageUsage).mockResolvedValue({
    schema: "echo.storage.usage.v1",
    readOnly: true,
    generatedAt: 1,
    disk: {
      totalBytes: 1_000,
      usedBytes: 400,
      freeBytes: 600,
      reserveBytes: 100,
      availableForUploadsBytes: 480,
      usedPercent: 40,
    },
    library: {
      logicalBytes: 300,
      files: 3,
      directories: 2,
      scannedEntries: 5,
      maxEntries: 200_000,
      truncated: false,
      skippedLinks: 1,
    },
    categories: [
      { id: "photos", bytes: 200, files: 2 },
      { id: "videos", bytes: 100, files: 1 },
      { id: "audio", bytes: 0, files: 0 },
      { id: "documents", bytes: 0, files: 0 },
      { id: "archives", bytes: 0, files: 0 },
      { id: "other", bytes: 0, files: 0 },
    ],
    topFolders: [{ name: "家庭相册", bytes: 300, files: 3 }],
    trash: { bytes: 20, files: 1 },
    uploads: { reservedBytes: 80, active: 1 },
    quotas: [
      {
        path: "家庭相册",
        limitBytes: 500,
        usedBytes: 300,
        reservedBytes: 0,
        availableBytes: 200,
        estimated: false,
      },
    ],
  });
});

describe("storage center", () => {
  it("shows capacity, content distribution, folders, trash and reservations", async () => {
    const onOpenFiles = vi.fn();
    const user = userEvent.setup();
    render(
      <StorageCenterPanel open onClose={vi.fn()} onOpenFiles={onOpenFiles} />,
    );

    expect(await screen.findByText("设备总容量")).toBeInTheDocument();
    expect(screen.getByText("照片 · 2 个文件")).toBeInTheDocument();
    expect(screen.getByText("视频 · 1 个文件")).toBeInTheDocument();
    expect(screen.getByText("家庭相册")).toBeInTheDocument();
    expect(screen.getByText(/1 个上传任务保留空间/)).toBeInTheDocument();
    expect(screen.getByText(/已跳过 1 个符号链接/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "管理文件" }));
    expect(onOpenFiles).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: "重新分析" }));
    await waitFor(() =>
      expect(fetchStorageUsage).toHaveBeenLastCalledWith(true),
    );
  });

  it("reuses the real OMV health and sharing surfaces", async () => {
    const user = userEvent.setup();
    render(<StorageCenterPanel open onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "磁盘健康" }));
    expect(screen.getByText("真实磁盘健康页")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "共享与用户" }));
    expect(screen.getByText("真实共享管理页")).toBeInTheDocument();
  });
});
