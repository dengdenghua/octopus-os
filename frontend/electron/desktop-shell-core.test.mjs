/**
 * Unit tests for the desktop-organizer core (path validation + journal).
 *
 * The desktop bridge is the most security-sensitive Electron surface (it can
 * move / trash files), so the pure logic lives in desktop-shell-core.cjs and
 * is tested here without launching Electron.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import desktopCore from "./desktop-shell-core.cjs";

let desktopDir;
let tmpRoot;

beforeEach(() => {
  tmpRoot = mkdtempSync(path.join(tmpdir(), "echo-desktop-"));
  desktopDir = path.join(tmpRoot, "Desktop");
  writeFileSync(path.join(tmpRoot, "Desktop-file"), ""); // sibling outside
});

afterEach(() => {
  rmSync(tmpRoot, { recursive: true, force: true });
});

describe("isDirectDesktopItem", () => {
  it("accepts a direct child of the desktop", () => {
    const item = path.join(desktopDir, "notes.txt");
    expect(desktopCore.isDirectDesktopItem(item, desktopDir)).toBe(true);
  });

  it("rejects the desktop itself", () => {
    expect(desktopCore.isDirectDesktopItem(desktopDir, desktopDir)).toBe(false);
  });

  it("rejects a parent of the desktop", () => {
    expect(desktopCore.isDirectDesktopItem(tmpRoot, desktopDir)).toBe(false);
  });

  it("rejects a nested path inside a desktop folder", () => {
    const nested = path.join(desktopDir, "folder", "file.txt");
    expect(desktopCore.isDirectDesktopItem(nested, desktopDir)).toBe(false);
  });

  it("rejects a sibling outside the desktop", () => {
    const sibling = path.join(tmpRoot, "Desktop-file");
    expect(desktopCore.isDirectDesktopItem(sibling, desktopDir)).toBe(false);
  });

  it("rejects empty / garbage input", () => {
    expect(desktopCore.isDirectDesktopItem("", desktopDir)).toBe(false);
    expect(desktopCore.isDirectDesktopItem(null, desktopDir)).toBe(false);
    expect(desktopCore.isDirectDesktopItem(undefined, desktopDir)).toBe(false);
  });
});

describe("resolveMoveTarget", () => {
  const src = () => path.join(desktopDir, "report.pdf");

  it("resolves a desktop-relative destination", () => {
    const res = desktopCore.resolveMoveTarget(src(), "工作", desktopDir);
    expect(res.error).toBeUndefined();
    expect(res.target).toBe(path.join(desktopDir, "工作", "report.pdf"));
  });

  it("rejects a relative destination that escapes via ../", () => {
    const res = desktopCore.resolveMoveTarget(
      src(),
      "../elsewhere",
      desktopDir,
    );
    expect(res.error).toMatch(/inside the Desktop/);
  });

  it("rejects an absolute destination outside the desktop", () => {
    const res = desktopCore.resolveMoveTarget(
      src(),
      path.join(tmpRoot, "elsewhere"),
      desktopDir,
    );
    expect(res.error).toMatch(/inside the Desktop/);
  });

  it("rejects the desktop itself as destination", () => {
    const res = desktopCore.resolveMoveTarget(src(), desktopDir, desktopDir);
    expect(res.error).toMatch(/inside the Desktop/);
  });

  it("rejects a source that is not a direct desktop item", () => {
    const res = desktopCore.resolveMoveTarget(
      path.join(tmpRoot, "Desktop-file"),
      "工作",
      desktopDir,
    );
    expect(res.error).toMatch(/Only direct items/);
  });

  it("resolves an absolute destination inside the desktop", () => {
    const dest = path.join(desktopDir, "归档");
    const res = desktopCore.resolveMoveTarget(src(), dest, desktopDir);
    expect(res.target).toBe(path.join(dest, "report.pdf"));
  });
});

describe("buildDesktopItem", () => {
  it("classifies a folder", () => {
    const st = { isDirectory: () => true, mtimeMs: 1700000000000 };
    const item = desktopCore.buildDesktopItem(
      "项目",
      path.join(desktopDir, "项目"),
      st,
    );
    expect(item.kind).toBe("folder");
    expect(item.name).toBe("项目");
    expect(item.subtitle).toBeTruthy();
  });

  it("classifies a .app bundle", () => {
    const st = { isDirectory: () => true, mtimeMs: 1700000000000 };
    const item = desktopCore.buildDesktopItem(
      "Safari.app",
      path.join(desktopDir, "Safari.app"),
      st,
    );
    expect(item.kind).toBe("app");
  });

  it("classifies a file with extension + size subtitle", () => {
    const st = { isDirectory: () => false, size: 2048, mtimeMs: 1700000000000 };
    const item = desktopCore.buildDesktopItem(
      "readme.md",
      path.join(desktopDir, "readme.md"),
      st,
    );
    expect(item.kind).toBe("file");
    expect(item.extension).toBe("md");
    expect(item.subtitle).toMatch(/2 KB/);
  });
});

describe("journal persistence", () => {
  const journalPath = () => path.join(tmpRoot, "journal.json");

  it("round-trips entries", () => {
    desktopCore.writeJournalFile(journalPath(), [
      { from: "/a", to: "/b", ts: 1 },
    ]);
    const loaded = desktopCore.readJournalFile(journalPath());
    expect(loaded).toEqual([{ from: "/a", to: "/b", ts: 1 }]);
  });

  it("returns [] for a missing journal", () => {
    expect(
      desktopCore.readJournalFile(path.join(tmpRoot, "missing.json")),
    ).toEqual([]);
  });

  it("returns [] for malformed JSON", () => {
    writeFileSync(journalPath(), "{not json");
    expect(desktopCore.readJournalFile(journalPath())).toEqual([]);
  });

  it("returns [] for an empty file", () => {
    writeFileSync(journalPath(), "");
    expect(desktopCore.readJournalFile(journalPath())).toEqual([]);
  });

  it("pretty-prints on write", () => {
    desktopCore.writeJournalFile(journalPath(), [{ from: "/a", to: "/b" }]);
    const raw = readFileSync(journalPath(), "utf8");
    expect(raw).toContain("\n");
  });
});
