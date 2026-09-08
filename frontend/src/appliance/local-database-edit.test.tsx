import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { FileTextIcon } from "lucide-react";
import { AllProviders } from "@/test/harness";
import { NASRequestError, storageFileResourceId } from "@/core/storage/api";
import { FilePreviewDialog } from "./local-database-content";

const api = vi.hoisted(() => ({ read: vi.fn(), save: vi.fn(), diff: vi.fn() }));
vi.mock("@/core/storage/api", async (original) => ({
  ...(await original<object>()),
  loadNASAssetText: () => Promise.resolve("truncated preview"),
  readNASTextDocument: api.read,
  saveNASTextDocument: api.save,
  diffNASTextDocument: api.diff,
}));
beforeEach(() => {
  vi.clearAllMocks();
});

it("edits the full document and preserves drafts when a save conflicts", async () => {
  const resourceId = storageFileResourceId("a".repeat(64), "notes.txt")!;
  const full = {
    resource_id: resourceId,
    text: "full document END",
    revision: "b".repeat(64),
    size: 17,
    encoding: "utf-8",
    complete: true,
  };
  api.read.mockResolvedValue(full);
  api.save.mockRejectedValue(new NASRequestError("/text", 409));
  const close = vi.fn();
  render(
    <AllProviders locale="zh-CN">
      <FilePreviewDialog
        item={{
          name: "notes.txt",
          path: "notes.txt",
          assetId: resourceId,
          resourceId,
          kind: "TXT",
          updated: "today",
          size: "17 B",
          icon: FileTextIcon,
          tone: "blue",
        }}
        manifest={{
          service: "echo-storage",
          version: "1",
          role: "external",
          capabilities: ["text-edit.v1"],
        }}
        onClose={close}
      />
    </AllProviders>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "编辑文本" }),
  );
  const editor = await screen.findByRole("textbox", { name: "编辑 notes.txt" });
  expect(editor).toHaveValue(full.text);
  fireEvent.change(editor, { target: { value: "my draft" } });
  await userEvent.click(screen.getByRole("button", { name: "保存" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("草稿已保留");
  expect(editor).toHaveValue("my draft");
  expect(api.save).toHaveBeenCalledWith(resourceId, "my draft", full.revision);
  api.read.mockResolvedValue({
    ...full,
    text: "other window",
    revision: "c".repeat(64),
  });
  await userEvent.click(
    screen.getByRole("button", { name: "读取最新版本（保留草稿）" }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
  );
  expect(editor).toHaveValue("my draft");
  api.diff.mockResolvedValue({
    patch: "-other window\n+my draft",
    complete: true,
  });
  await userEvent.click(screen.getByRole("button", { name: "查看差异" }));
  expect(api.diff).toHaveBeenCalledWith(resourceId, "my draft", "c".repeat(64));
  expect(
    await screen.findByLabelText("notes.txt 的修改差异"),
  ).toHaveTextContent("-other window");
  expect(close).not.toHaveBeenCalled();
});
