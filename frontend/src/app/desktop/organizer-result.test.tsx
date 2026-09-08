import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DesktopMoveResult } from "@/types/electron";
import {
  OrganizerResult,
  organizerReceipt,
  useDesktopOrganizer,
} from "./organizer-result";

afterEach(cleanup);

describe("desktop organizer receipts", () => {
  it("shows independent successes and conflicts without declaring completion", () => {
    render(
      <OrganizerResult
        receipt={organizerReceipt("move", {
          ok: false,
          moved: 1,
          skipped: 1,
          conflicts: 1,
          operationId: "batch-a",
          outcomes: [
            {
              srcPath: "/Desktop/a.txt",
              destPath: "/Desktop/Documents/a.txt",
              status: "moved",
              committed: true,
            },
            {
              srcPath: "/Desktop/b.txt",
              status: "conflict",
              code: "destination_exists",
              committed: false,
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("操作尚未全部完成")).toBeTruthy();
    expect(screen.getByText(/已移动 1 项/).textContent).toContain("冲突 1 项");
    expect(screen.getByText("目标已有同名文件，未覆盖")).toBeTruthy();
    expect(screen.queryByText("操作完成")).toBeNull();
    expect(screen.getByText("/Desktop/a.txt")).toBeTruthy();
  });

  it("does not report nothing to undo when zero restored files have conflicts", () => {
    render(
      <OrganizerResult
        receipt={organizerReceipt("undo", {
          ok: false,
          undone: 0,
          conflicts: 1,
          operationId: "batch-a",
          outcomes: [
            {
              srcPath: "/Desktop/edited.txt",
              status: "conflict",
              code: "source_changed",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("文件内容已变化，未撤销")).toBeTruthy();
    expect(screen.getByText(/检查文件后可再次点击撤销/)).toBeTruthy();
    expect(screen.queryByText("没有可撤销的操作")).toBeNull();
  });

  it("shows the exact recovery copy when a source replacement leaves uncertain commit", () => {
    render(
      <OrganizerResult
        receipt={organizerReceipt("move", {
          ok: false,
          uncertain: 1,
          outcomes: [
            {
              srcPath: "/Desktop/a.txt",
              status: "uncertain",
              code: "source_replaced",
              committed: null,
              recoveryPaths: ["/Desktop/Documents/.pending-a"],
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("部分结果待确认")).toBeTruthy();
    expect(screen.getByText(/勿重复整理/)).toBeTruthy();
    expect(
      screen.getByText("恢复副本：/Desktop/Documents/.pending-a"),
    ).toBeTruthy();
  });

  it("uses entry failures even if a stale shell returned ok true", () => {
    const receipt = organizerReceipt("move", {
      ok: true,
      moved: 1,
      outcomes: [{ srcPath: "/Desktop/b.txt", status: "failed" }],
    });
    expect(receipt.incomplete).toBe(true);
    expect(receipt.failed).toBe(1);
  });

  it("does not turn a skipped single-file result into a moved item", () => {
    const receipt = organizerReceipt("move", { ok: true, skipped: true }, true);
    expect(receipt.completed).toBe(0);
    expect(receipt.incomplete).toBe(true);
  });

  it("preserves legacy unverifiable history as an explained conflict", () => {
    render(
      <OrganizerResult
        receipt={organizerReceipt("undo", {
          ok: false,
          undone: 0,
          outcomes: [
            {
              srcPath: "/Desktop/old.txt",
              status: "conflict",
              code: "legacy_unverifiable",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("旧记录无法验证内容，保留文件供检查")).toBeTruthy();
  });
});

describe("desktop organizer IPC flow", () => {
  it("refreshes real listing after partial response and retries exact retained undo batch", async () => {
    const refresh = vi.fn();
    const { result } = renderHook(() => useDesktopOrganizer(refresh));
    await act(async () =>
      result.current.run("move", async () => ({
        ok: false,
        moved: 1,
        conflicts: 1,
        operationId: "batch-a",
      })),
    );
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(result.current.receipt?.completed).toBe(1);
    const undo = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        undone: 0,
        conflicts: 1,
        operationId: "batch-a",
      })
      .mockResolvedValueOnce({ ok: true, undone: 1, operationId: "batch-a" })
      .mockResolvedValueOnce({ ok: true, undone: 0 });
    await act(async () => result.current.run("undo", undo));
    await act(async () => result.current.run("undo", undo));
    await act(async () => result.current.run("undo", undo));
    expect(undo.mock.calls).toEqual([["batch-a"], ["batch-a"], [undefined]]);
    expect(refresh).toHaveBeenCalledTimes(4);
  });

  it("blocks a second move or undo before the first IPC settles", async () => {
    const refresh = vi.fn();
    const { result } = renderHook(() => useDesktopOrganizer(refresh));
    let resolve!: (value: DesktopMoveResult) => void;
    const pending = new Promise<DesktopMoveResult>((done) => {
      resolve = done;
    });
    const second = vi.fn();
    let first!: Promise<void>;
    act(() => {
      first = result.current.run("move", () => pending);
    });
    expect(result.current.busy).toBe("move");
    await act(async () => result.current.run("undo", second));
    await act(async () => result.current.run("move", second));
    expect(second).not.toHaveBeenCalled();
    await act(async () => {
      resolve({ ok: true, moved: 1 });
      await first;
    });
    expect(result.current.busy).toBeNull();
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("lost IPC response becomes uncertain and refreshes rather than claiming no mutation", async () => {
    const refresh = vi.fn();
    const { result } = renderHook(() => useDesktopOrganizer(refresh));
    await act(async () =>
      result.current.run("move", async () => {
        throw new Error("IPC disconnected");
      }),
    );
    expect(result.current.receipt?.uncertain).toBe(1);
    expect(result.current.receipt?.incomplete).toBe(true);
    expect(result.current.receipt?.result.error).toContain("未收到完整结果");
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("ignores late IPC responses after the desktop page unmounts", async () => {
    const refresh = vi.fn();
    const { result, unmount } = renderHook(() => useDesktopOrganizer(refresh));
    let resolve!: (value: DesktopMoveResult) => void;
    const pending = new Promise<DesktopMoveResult>((done) => {
      resolve = done;
    });
    let first!: Promise<void>;
    act(() => {
      first = result.current.run("move", () => pending);
    });
    unmount();
    await act(async () => {
      resolve({ ok: true, moved: 1 });
      await first;
    });
    expect(refresh).not.toHaveBeenCalled();
  });
});
