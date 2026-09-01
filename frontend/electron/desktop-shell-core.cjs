/**
 * Desktop organizer core — pure, electron-free logic.
 *
 * Extracted from main.cjs so the path-validation and journal behavior of the
 * desktop bridge is unit-testable without launching Electron. Every function
 * takes its inputs explicitly (desktop dir, file paths); main.cjs wires the
 * real app paths in.
 */
"use strict";

const path = require("path");
const fs = require("fs");

/**
 * True only for a direct child of ``desktopDir``. The desktop itself, a
 * parent, or a nested path is rejected — keeps the destructive bridges
 * (trash / move) narrow so a compromised renderer cannot touch arbitrary
 * files.
 */
function isDirectDesktopItem(candidate, desktopDir) {
  const desktop = path.resolve(desktopDir);
  const resolved = path.resolve(String(candidate || ""));
  return resolved !== desktop && path.dirname(resolved) === desktop;
}

/**
 * Resolve the destination for a move without escaping the desktop.
 *
 * ``destDir`` may be a desktop-relative folder name or an absolute path; the
 * resolved destination must live strictly inside ``desktopDir`` and the
 * source must be a direct desktop item. Returns ``{ target }`` or
 * ``{ error }`` — never throws for user-controlled input.
 */
function resolveMoveTarget(srcPath, destDir, desktopDir) {
  const desktop = path.resolve(desktopDir);
  if (!isDirectDesktopItem(srcPath, desktop)) {
    return { error: "Only direct items on the Desktop can be moved" };
  }
  const dest = path.isAbsolute(destDir)
    ? path.resolve(destDir)
    : path.resolve(desktop, destDir);
  if (dest === desktop || !dest.startsWith(desktop + path.sep)) {
    return { error: "Destination must be a folder inside the Desktop" };
  }
  return { target: path.join(dest, path.basename(srcPath)) };
}

/**
 * Build a desktop entry record from a name, absolute path, and stat.
 * ``st`` is a Node ``fs.Stats``-like object (``isDirectory``, ``size``,
 * ``mtimeMs``).
 */
function buildDesktopItem(name, absPath, st) {
  const ext = path.extname(name).replace(/^\./, "").toLowerCase();
  const kind = st.isDirectory()
    ? name.endsWith(".app")
      ? "app"
      : "folder"
    : "file";
  const subtitle = st.isDirectory()
    ? new Date(st.mtimeMs).toLocaleDateString()
    : `${(st.size / 1024).toFixed(0)} KB · ${new Date(
        st.mtimeMs,
      ).toLocaleDateString()}`;
  return { id: absPath, name, subtitle, path: absPath, kind, extension: ext };
}

/** Read the organizer journal; malformed/missing → empty list. */
function readJournalFile(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return [];
  }
}

/** Persist the organizer journal (pretty JSON). */
function writeJournalFile(filePath, entries) {
  fs.writeFileSync(filePath, JSON.stringify(entries, null, 2));
}

module.exports = {
  isDirectDesktopItem,
  resolveMoveTarget,
  buildDesktopItem,
  readJournalFile,
  writeJournalFile,
};
