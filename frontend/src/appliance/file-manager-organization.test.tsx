import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FileManager } from "./file-manager";
import { FileServiceUnavailableError, listDir } from "./files";
import type * as FilesModule from "./files";
import {
  baseUrl,
  jsonResponse,
  organizationPlan,
  organizationPlanList,
} from "./file-organization.test-support";

vi.mock("./files", async (importOriginal) => ({
  ...(await importOriginal<typeof FilesModule>()),
  listDir: vi.fn(),
  listTrash: vi.fn().mockResolvedValue({ entries: [] }),
}));

beforeEach(() => {
  localStorage.clear();
  vi.mocked(listDir).mockImplementation(async (path) => ({
    path,
    entries: path
      ? [
          {
            name: "invoice.txt",
            path: "receipts/invoice.txt",
            kind: "file",
            size: 128,
            mtime: 1,
          },
        ]
      : [
          {
            name: "receipts",
            path: "receipts",
            kind: "dir",
            size: 0,
            mtime: 1,
          },
        ],
  }));
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("FileManager organization entry", () => {
  it.each(["", "receipts"])(
    "previews the selected NAS directory '%s' through the real panel and typed API",
    async (path) => {
      const fetch = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) =>
        jsonResponse(
          init?.method === "POST"
            ? organizationPlan({ path: JSON.parse(String(init.body)).path })
            : organizationPlanList([], { path }),
        ),
      );
      vi.stubGlobal("fetch", fetch);
      const user = userEvent.setup();
      render(<FileManager onClose={vi.fn()} />);
      const directory = await screen.findByText("receipts");
      if (path) {
        await user.dblClick(directory);
        await screen.findByText("invoice.txt");
      }
      await user.click(screen.getByRole("button", { name: "整理此目录" }));
      const panel = screen.getByRole("dialog", { name: "按年月整理文档" });
      expect(
        within(panel).getByText(`目录：${path || "NAS 根目录"}`),
      ).toBeInTheDocument();
      expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(
        false,
      );
      await user.click(
        within(panel).getByRole("button", { name: "生成整理预览" }),
      );
      await within(panel).findByText("拟移动至：receipts/2026/09/invoice.txt");
      expect(fetch).toHaveBeenCalledWith(
        baseUrl,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ path }),
        }),
      );
      await user.click(
        within(panel).getByRole("button", { name: "关闭整理面板" }),
      );
      expect(
        screen.queryByRole("dialog", { name: "按年月整理文档" }),
      ).not.toBeInTheDocument();
    },
  );

  it("does not expose NAS organization from unconnected local folders or the recycle bin", async () => {
    const user = userEvent.setup();
    render(<FileManager onClose={vi.fn()} />);
    await screen.findByText("receipts");
    await user.click(screen.getByRole("button", { name: "个人" }));
    expect(screen.getByRole("button", { name: "整理此目录" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "回收站" }));
    await screen.findByText("回收站是空的");
    expect(screen.getByRole("button", { name: "整理此目录" })).toBeDisabled();
  });

  it("does not offer directory organization while NAS storage is unavailable", async () => {
    vi.mocked(listDir).mockRejectedValueOnce(new FileServiceUnavailableError());
    render(<FileManager onClose={vi.fn()} />);
    await screen.findByText("NAS 文件服务尚未启用");
    expect(screen.getByRole("button", { name: "整理此目录" })).toBeDisabled();
  });
});
