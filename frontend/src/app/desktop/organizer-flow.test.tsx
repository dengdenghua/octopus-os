import { createRequire } from "node:module";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { basename, join, relative, resolve } from "node:path";

import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { DesktopMoveResult } from "@/types/electron";
import { OrganizerResult, useDesktopOrganizer } from "./organizer-result";

const require = createRequire(import.meta.url);
const { createDesktopOrganizer } =
  require("../../../electron/desktop-organizer-moves.cjs") as {
    createDesktopOrganizer: (options: {
      desktopDir: string;
      journalPath: string;
    }) => {
      moveItemsBatch: (
        items: Array<{ srcPath: string; category: string }>,
      ) => Promise<DesktopMoveResult>;
      undoMoves: (operationId?: string) => Promise<DesktopMoveResult>;
    };
  };
const temporaryBase = resolve(process.cwd(), "../tmp");
const directories: string[] = [];

afterEach(() => {
  cleanup();
  for (const directory of directories.splice(0)) {
    if (
      relative(temporaryBase, directory).startsWith("..") ||
      !basename(directory).startsWith("organizer-ui-")
    ) {
      throw new Error(
        "Refusing cleanup outside this test's temporary directory",
      );
    }
    rmSync(directory, { recursive: true, force: true });
  }
});

it("real temporary Desktop move, edited target conflict and exact-batch undo reach honest UI receipts", async () => {
  mkdirSync(temporaryBase, { recursive: true });
  const directory = mkdtempSync(join(temporaryBase, "organizer-ui-"));
  directories.push(directory);
  const desktopDir = join(directory, "Desktop");
  mkdirSync(desktopDir);
  const first = join(desktopDir, "invoice-a.txt");
  const second = join(desktopDir, "invoice-b.txt");
  writeFileSync(first, "original-a");
  writeFileSync(second, "original-b");
  const desktop = createDesktopOrganizer({
    desktopDir,
    journalPath: join(directory, "journal.json"),
  });
  const refresh = vi.fn();
  const { result } = renderHook(() => useDesktopOrganizer(refresh));
  await act(async () =>
    result.current.run("move", () =>
      desktop.moveItemsBatch([
        { srcPath: first, category: "documents" },
        { srcPath: second, category: "documents" },
      ]),
    ),
  );
  const moved = result.current.receipt!;
  expect(moved.completed).toBe(2);
  expect(moved.incomplete).toBe(false);
  const secondTarget = moved.result.outcomes!.find(
    (row) => row.srcPath === second,
  )!.destPath!;
  writeFileSync(secondTarget, "user edited b after move");
  await act(async () =>
    result.current.run("undo", (operationId) => desktop.undoMoves(operationId)),
  );
  const partial = result.current.receipt!;
  expect(partial.completed).toBe(1);
  expect(partial.conflicts).toBe(1);
  expect(partial.incomplete).toBe(true);
  expect(readFileSync(first, "utf-8")).toBe("original-a");
  expect(readFileSync(secondTarget, "utf-8")).toBe("user edited b after move");
  const view = render(<OrganizerResult receipt={partial} />);
  expect(screen.getByText("操作尚未全部完成")).toBeTruthy();
  expect(screen.getByText(/已撤销 1 项/).textContent).toContain("冲突 1 项");
  view.unmount();
  await act(async () =>
    result.current.run("undo", (operationId) => desktop.undoMoves(operationId)),
  );
  expect(result.current.receipt!.result.operationId).toBe(
    moved.result.operationId,
  );
  expect(result.current.receipt!.completed).toBe(0);
  expect(result.current.receipt!.conflicts).toBe(1);
  expect(readFileSync(secondTarget, "utf-8")).toBe("user edited b after move");
  expect(refresh).toHaveBeenCalledTimes(3);

  // A name collision can be resolved without modifying the moved file.
  const third = join(desktopDir, "invoice-c.txt");
  writeFileSync(third, "original-c");
  await act(async () =>
    result.current.run("move", () =>
      desktop.moveItemsBatch([{ srcPath: third, category: "documents" }]),
    ),
  );
  const retryOperation = result.current.receipt!.result.operationId;
  writeFileSync(third, "new independent file");
  await act(async () =>
    result.current.run("undo", (operationId) => desktop.undoMoves(operationId)),
  );
  expect(result.current.receipt!.conflicts).toBe(1);
  const retained = join(desktopDir, "retained-independent.txt");
  renameSync(third, retained);
  await act(async () =>
    result.current.run("undo", (operationId) => desktop.undoMoves(operationId)),
  );
  expect(result.current.receipt!.result.operationId).toBe(retryOperation);
  expect(result.current.receipt!.completed).toBe(1);
  expect(result.current.receipt!.incomplete).toBe(false);
  expect(readFileSync(third, "utf-8")).toBe("original-c");
  expect(readFileSync(retained, "utf-8")).toBe("new independent file");
});
