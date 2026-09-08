import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { LocalDiskView } from "./local-database-content";
import { AllProviders } from "@/test/harness";
import { consumeComposerFiles } from "@/core/composer-file-inbox";
import { storageFileResourceId } from "@/core/storage/api";

const browse = vi.hoisted(() => vi.fn());
vi.mock("@/core/storage/api", async (original) => ({
  ...(await original<object>()),
  listNASDirectory: browse,
}));
beforeEach(() => {
  browse.mockReset();
});

function show(file: string, sourceThread = "") {
  return render(
    <AllProviders
      locale="zh-CN"
      initialRoute={`/workspace/storage?${new URLSearchParams({ file, sourceThread })}`}
    >
      <LocalDiskView
        query=""
        setQuery={() => {}}
        runSearch={() => {}}
        isSearching={false}
        manifest={null}
      />
    </AllProviders>,
  );
}

it("opens the original directory and identifies the original file", async () => {
  browse.mockResolvedValue([
    { name: "报告.txt", path: "C:/资料/报告.txt", type: "file", size: 20 },
  ]);
  show("C:/资料/报告.txt");
  expect(await screen.findByText("报告.txt")).toBeInTheDocument();
  expect(browse).toHaveBeenCalledWith("C:/资料");
  expect(screen.getByText("报告.txt").closest("button")).toHaveAttribute(
    "aria-current",
    "true",
  );
  await userEvent.click(screen.getByRole("button", { name: "返回上一级" }));
  await waitFor(() => expect(browse).toHaveBeenLastCalledWith("C:/"));
});

it("quotes the original file to the original task without copying or sending it", async () => {
  browse.mockResolvedValue([
    { name: "报告.txt", path: "C:/资料/报告.txt", type: "file", size: 20 },
  ]);
  show("C:/资料/报告.txt", "source-task");
  await userEvent.click(
    await screen.findByRole("button", { name: "引用到原任务：报告.txt" }),
  );
  expect(window.location.hash).toBe("#/workspace/realtime/source-task");
  expect(consumeComposerFiles("echo-assistant")).toEqual([]);
  expect(consumeComposerFiles("source-task")).toEqual([
    { path: "C:/资料/报告.txt", sourceLabel: "本地数据库" },
  ]);
  expect(consumeComposerFiles("source-task")).toEqual([]);
});

it("reports access errors without fake folder counts or stale contents", async () => {
  browse.mockRejectedValue(new Error("无权访问该目录"));
  show("/private/report.txt");
  expect(await screen.findByText("无权访问该目录")).toBeInTheDocument();
  expect(screen.getByText("无法读取当前目录")).toBeInTheDocument();
  expect(screen.queryByText("Applications")).not.toBeInTheDocument();
});

it("locates embedded Storage resources without treating external paths as local", async () => {
  const resourceId = storageFileResourceId("desktop-source", "docs/report.txt");
  browse.mockResolvedValue([
    { name: "report.txt", path: "docs/report.txt", type: "file", size: 20 },
  ]);
  render(
    <AllProviders
      locale="zh-CN"
      initialRoute={`/workspace/storage?${new URLSearchParams({ resource_id: resourceId! })}`}
    >
      <LocalDiskView
        query=""
        setQuery={() => {}}
        runSearch={() => {}}
        isSearching={false}
        manifest={{
          service: "echo-storage-local",
          version: "1",
          role: "embedded",
          capabilities: ["browse"],
        }}
      />
    </AllProviders>,
  );
  expect(await screen.findByText("report.txt")).toBeInTheDocument();
  expect(browse).toHaveBeenCalledWith("docs");
});
