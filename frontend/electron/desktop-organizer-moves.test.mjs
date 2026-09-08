// Real temporary files; injected calls only choose a deterministic race/fault
// window. No Electron process, user Desktop, OCR provider, or user files used.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import core from "./desktop-shell-core.cjs";
import moves from "./desktop-organizer-moves.cjs";

let root, desktop, journal;
const source = (name = "invoice.txt") => path.join(desktop, name);
const target = (name = "invoice.txt", folder = "documents") =>
  path.join(desktop, folder, name);
const put = (file, content = "original invoice") => {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, content);
};
const read = (file) => fs.readFileSync(file, "utf8");
const entries = () => JSON.parse(read(journal));
const organizer = (options = {}) =>
  moves.createDesktopOrganizer({
    desktopDir: desktop,
    journalPath: journal,
    ...options,
  });
const batch = (name = "invoice.txt") => [
  { srcPath: source(name), category: "documents" },
];

beforeEach(() => {
  root = fs.mkdtempSync(path.join(tmpdir(), "echo-organizer-"));
  desktop = path.join(root, "Desktop");
  journal = path.join(root, "journal.json");
  fs.mkdirSync(desktop);
});
afterEach(() => fs.rmSync(root, { recursive: true, force: true }));

describe("production Desktop move and undo coordinator", () => {
  it("records a content-verified move and restores the same file on undo", async () => {
    put(source());
    const service = organizer();
    const result = await service.moveItemsBatch(batch());
    expect(result).toMatchObject({ ok: true, moved: 1, conflicts: 0 });
    expect(read(target())).toBe("original invoice");
    expect(fs.existsSync(source())).toBe(false);
    expect(entries()[0]).toMatchObject({
      status: "moved",
      operationId: result.operationId,
    });
    expect(entries()[0].fingerprint.sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(await service.undoMoves(result.operationId)).toMatchObject({
      ok: true,
      undone: 1,
    });
    expect(read(source())).toBe("original invoice");
    expect(fs.existsSync(target())).toBe(false);
    expect(entries()[0].status).toBe("undone");
    expect(await service.undoMoves(result.operationId)).toMatchObject({
      ok: true,
      undone: 0,
    });
  });

  it("never overwrites an existing destination and continues other batch items", async () => {
    put(source());
    put(target(), "later document");
    put(source("second.txt"), "second");
    const result = await organizer().moveItemsBatch([
      ...batch(),
      ...batch("second.txt"),
    ]);
    expect(result).toMatchObject({
      ok: false,
      moved: 1,
      skipped: 1,
      conflicts: 1,
    });
    expect(read(source())).toBe("original invoice");
    expect(read(target())).toBe("later document");
    expect(read(target("second.txt"))).toBe("second");
  });

  it("does not overwrite a destination created after the preflight check", async () => {
    put(source());
    const service = organizer({
      io: {
        ...fsp,
        link: async (from, to) => {
          put(to, "race winner");
          await fsp.link(from, to);
        },
      },
    });
    const result = await service.moveItemsBatch(batch());
    expect(result).toMatchObject({ moved: 0, conflicts: 1 });
    expect(result.outcomes[0].code).toBe("destination_exists");
    expect(read(source())).toBe("original invoice");
    expect(read(target())).toBe("race winner");
  });

  for (const direction of ["move", "undo"]) {
    it(`preserves an externally replaced ${direction} source at the final removal boundary`, async () => {
      put(source());
      const moved =
        direction === "undo"
          ? await organizer().moveItemsBatch(batch())
          : undefined;
      const service = organizer({
        io: {
          ...fsp,
          rename: async (from, to) => {
            await fsp.unlink(from);
            put(from, "new external document");
            await fsp.rename(from, to);
          },
        },
      });
      const result =
        direction === "move"
          ? await service.moveItemsBatch(batch())
          : await service.undoMoves(moved.operationId);
      expect(result.outcomes[0]).toMatchObject({
        status: "uncertain",
        code: "source_replaced",
      });
      expect(read(direction === "move" ? source() : target())).toBe(
        "new external document",
      );
      expect(read(direction === "move" ? target() : source())).toBe(
        "original invoice",
      );
      const pending = result.outcomes[0].recoveryPaths.find((file) =>
        fs.existsSync(file),
      );
      expect(read(pending)).toBe("new external document");
      expect((await organizer().undoMoves()).outcomes[0].code).toBe(
        "recovery_required",
      );
    });
  }

  it("enforces direct Desktop sources and descendant destinations in the production path", async () => {
    const outside = path.join(root, "outside.txt");
    put(outside);
    put(source());
    const service = organizer();
    expect(
      (await service.moveItem(outside, "documents")).outcomes[0].code,
    ).toBe("invalid_path");
    expect(
      (await service.moveItem(source(), "../outside")).outcomes[0].code,
    ).toBe("invalid_path");
    expect(read(outside)).toBe("original invoice");
    expect(fs.existsSync(path.join(root, "outside"))).toBe(false);
  });

  it("rejects destination junctions/symlinks without writing outside the Desktop", async () => {
    put(source());
    const outside = path.join(root, "external");
    fs.mkdirSync(outside);
    fs.symlinkSync(
      outside,
      path.join(desktop, "documents"),
      process.platform === "win32" ? "junction" : "dir",
    );
    const result = await organizer().moveItemsBatch(batch());
    expect(result.outcomes[0].code).toBe("unsafe_directory");
    expect(fs.readdirSync(outside)).toEqual([]);
    expect(read(source())).toBe("original invoice");
  });

  it("does not move folders or an application bundle", async () => {
    fs.mkdirSync(source("Example.app"));
    const result = await organizer().moveItem(source("Example.app"), "apps");
    expect(result.outcomes[0].code).toBe("not_regular_file");
    expect(fs.existsSync(source("Example.app"))).toBe(true);
  });

  it("blocks all moves when the existing journal cannot be parsed", async () => {
    put(source());
    put(journal, "{broken");
    const result = await organizer().moveItemsBatch(batch());
    expect(result.outcomes[0].code).toBe("journal_invalid");
    expect(read(source())).toBe("original invoice");
    expect(read(journal)).toBe("{broken");
    expect(fs.existsSync(target())).toBe(false);
  });

  it("does not move any file if its prepared receipt cannot be persisted", async () => {
    put(source());
    const result = await organizer({
      writeJournal: () => {
        throw new Error("disk full");
      },
    }).moveItemsBatch(batch());
    expect(result).toMatchObject({ moved: 0, failed: 1, uncertain: 0 });
    expect(result.outcomes[0]).toMatchObject({
      code: "journal_write_failed",
      committed: false,
    });
    expect(read(source())).toBe("original invoice");
    expect(fs.existsSync(target())).toBe(false);
  });

  it("reports a committed move if final journal persistence fails, and can undo after restart", async () => {
    put(source());
    let writes = 0;
    const service = organizer({
      writeJournal: (file, value) => {
        if (++writes > 1) throw new Error("disk full");
        core.writeJournalFile(file, value);
      },
    });
    const result = await service.moveItemsBatch(batch());
    expect(result.outcomes[0]).toMatchObject({
      status: "uncertain",
      code: "journal_write_failed",
      committed: true,
    });
    expect(entries()[0].status).toBe("prepared");
    expect(read(target())).toBe("original invoice");
    expect(await organizer().undoMoves(result.operationId)).toMatchObject({
      ok: true,
      undone: 1,
    });
    expect(read(source())).toBe("original invoice");
  });

  it("refuses undo when content changed even if size and mtime match", async () => {
    put(source(), "aaaaaaaa");
    const result = await organizer().moveItemsBatch(batch());
    const before = fs.statSync(target());
    put(target(), "bbbbbbbb");
    fs.utimesSync(target(), before.atime, before.mtime);
    const undo = await organizer().undoMoves(result.operationId);
    expect(undo).toMatchObject({ ok: false, undone: 0, conflicts: 1 });
    expect(undo.outcomes[0].code).toBe("destination_changed");
    expect(read(target())).toBe("bbbbbbbb");
    expect(fs.existsSync(source())).toBe(false);
    expect(entries()[0].status).toBe("conflict");
  });

  it("never overwrites a recreated source and retains the receipt for a later retry", async () => {
    put(source());
    const result = await organizer().moveItemsBatch(batch());
    put(source(), "new original-path document");
    const service = organizer();
    const undo = await service.undoMoves(result.operationId);
    expect(undo.outcomes[0].code).toBe("source_conflict");
    expect(read(source())).toBe("new original-path document");
    expect(read(target())).toBe("original invoice");
    fs.renameSync(source(), path.join(root, "kept-conflict.txt"));
    expect(await service.undoMoves(result.operationId)).toMatchObject({
      ok: true,
      undone: 1,
    });
    expect(read(source())).toBe("original invoice");
    expect(read(path.join(root, "kept-conflict.txt"))).toBe(
      "new original-path document",
    );
  });

  it("defaults to the most recent operation, not every historical move", async () => {
    put(source("first.txt"), "one");
    put(source("second.txt"), "two");
    const service = organizer();
    const first = await service.moveItemsBatch(batch("first.txt"));
    const second = await service.moveItemsBatch(batch("second.txt"));
    expect(await service.undoMoves()).toMatchObject({
      operationId: second.operationId,
      undone: 1,
    });
    expect(read(target("first.txt"))).toBe("one");
    expect(await service.undoMoves()).toMatchObject({
      operationId: first.operationId,
      undone: 1,
    });
  });

  it("does not undo an older receipt across a newer move of the same unchanged file", async () => {
    put(source());
    const service = organizer();
    const first = await service.moveItemsBatch(batch());
    // An external move back preserves the same inode and content. A later
    // organizer operation therefore cannot be distinguished by hash alone.
    await fsp.link(target(), source());
    await fsp.unlink(target());
    const second = await service.moveItemsBatch(batch());
    const olderUndo = await service.undoMoves(first.operationId);
    expect(olderUndo.outcomes[0].code).toBe("newer_operation_pending");
    expect(read(target())).toBe("original invoice");
    expect(fs.existsSync(source())).toBe(false);
    expect(await service.undoMoves(second.operationId)).toMatchObject({
      ok: true,
      undone: 1,
    });
    expect(await service.undoMoves(first.operationId)).toMatchObject({
      ok: true,
      undone: 0,
    });
  });

  it("serializes concurrent requests rather than losing journal updates", async () => {
    put(source("first.txt"), "one");
    put(source("second.txt"), "two");
    const service = organizer();
    const result = await Promise.all([
      service.moveItemsBatch(batch("first.txt")),
      service.moveItemsBatch(batch("second.txt")),
    ]);
    expect(result.map((item) => item.moved)).toEqual([1, 1]);
    expect(entries()).toHaveLength(2);
    expect(new Set(entries().map((entry) => entry.operationId)).size).toBe(2);
  });

  it("retains legacy receipts without guessing whether a file was modified", async () => {
    put(target());
    core.writeJournalFile(journal, [{ from: source(), to: target(), ts: 1 }]);
    const result = await organizer().undoMoves();
    expect(result).toMatchObject({ undone: 0, conflicts: 1 });
    expect(result.outcomes[0].code).toBe("legacy_unverifiable");
    expect(read(target())).toBe("original invoice");
    expect(entries()).toHaveLength(1);
  });

  it("does not pretend an unsupported filesystem completed a move", async () => {
    put(source());
    const service = organizer({
      io: {
        ...fsp,
        link: async () => {
          throw Object.assign(new Error("different device"), { code: "EXDEV" });
        },
      },
    });
    const result = await service.moveItemsBatch(batch());
    expect(result).toMatchObject({ moved: 0, failed: 1, uncertain: 0 });
    expect(result.outcomes[0].code).toBe("filesystem_unsupported");
    expect(read(source())).toBe("original invoice");
    expect(await organizer().undoMoves()).toMatchObject({
      ok: true,
      undone: 0,
    });
  });

  it("recovers a physically completed undo when its final receipt was not persisted", async () => {
    put(source());
    const moved = await organizer().moveItemsBatch(batch());
    let writes = 0;
    const service = organizer({
      writeJournal: (file, value) => {
        if (++writes > 1) throw new Error("disk full");
        core.writeJournalFile(file, value);
      },
    });
    const undone = await service.undoMoves(moved.operationId);
    expect(undone.outcomes[0]).toMatchObject({
      status: "uncertain",
      committed: true,
    });
    expect(entries()[0].status).toBe("undo_prepared");
    expect(read(source())).toBe("original invoice");
    expect(await organizer().undoMoves(moved.operationId)).toMatchObject({
      ok: true,
      undone: 1,
    });
  });

  for (const stage of ["linked", "captured", "unlinked"]) {
    it(`recovers a real child-process exit after the move ${stage} stage`, async () => {
      put(source());
      const modulePath = path.join(
        path.dirname(fileURLToPath(import.meta.url)),
        "desktop-organizer-moves.cjs",
      );
      const script = `
        const fsp = require('node:fs/promises');
        const path = require('node:path');
        const { createDesktopOrganizer } = require(process.argv[1]);
        const desktop = process.argv[2], journal = process.argv[3], stage = process.argv[4];
        const io = { ...fsp,
          link: async (...args) => { await fsp.link(...args); if (stage === 'linked') process.exit(73); },
          rename: async (...args) => { await fsp.rename(...args); if (stage === 'captured') process.exit(73); },
          unlink: async (...args) => { await fsp.unlink(...args); if (stage === 'unlinked') process.exit(73); }
        };
        createDesktopOrganizer({ desktopDir: desktop, journalPath: journal, io }).moveItemsBatch([
          { srcPath: path.join(desktop, 'invoice.txt'), category: 'documents' }
        ]).then(() => process.exit(99));
      `;
      const child = spawnSync(
        process.execPath,
        ["-e", script, modulePath, desktop, journal, stage],
        { encoding: "utf8", timeout: 10000 },
      );
      expect(child.stderr).toBe("");
      expect(child.status).toBe(73);
      expect(entries()[0].status).toBe("prepared");
      expect(fs.existsSync(source())).toBe(stage === "linked");
      expect(read(target())).toBe("original invoice");
      expect(await organizer().undoMoves()).toMatchObject({
        ok: true,
        undone: 1,
      });
      expect(read(source())).toBe("original invoice");
      expect(fs.existsSync(target())).toBe(false);
    });
  }
});
