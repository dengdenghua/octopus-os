"use strict";

// The existing organizer journal is the authority for Desktop moves. This is
// intentionally not a general file service: regular files, one Desktop, and
// same-filesystem hard links only. An exclusive link provides no-clobber
// publication on both Windows and POSIX; unlinking its source is a separate
// step, so an interrupted operation remains recoverable in the journal.
const fs = require("node:fs");
const fsp = require("node:fs/promises");
const path = require("node:path");
const { createHash, randomUUID } = require("node:crypto");
const {
  resolveMoveTarget,
  writeJournalFile,
} = require("./desktop-shell-core.cjs");

function fail(code) {
  return Object.assign(new Error(code), { organizerCode: code });
}

function errorCode(error, fallback) {
  if (error.organizerCode) return error.organizerCode;
  if (["EACCES", "EPERM"].includes(error.code)) return "permission_denied";
  if (["EXDEV", "ENOTSUP", "EOPNOTSUPP"].includes(error.code))
    return "filesystem_unsupported";
  return fallback;
}

function identity(stat) {
  return {
    dev: String(stat.dev),
    ino: String(stat.ino),
    size: String(stat.size),
    mtimeNs: String(stat.mtimeNs),
    birthtimeNs: String(stat.birthtimeNs),
  };
}

function sameIdentity(left, right) {
  return Object.keys(left).every((key) => left[key] === right[key]);
}

function matches(actual, expected) {
  return (
    !!actual &&
    actual.sha256 === expected.sha256 &&
    sameIdentity(actual.identity, expected.identity)
  );
}

async function snapshot(file) {
  let before;
  try {
    before = await fsp.lstat(file, { bigint: true });
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
  if (!before.isFile() || before.isSymbolicLink())
    throw fail("not_regular_file");
  const handle = await fsp.open(
    file,
    fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0),
  );
  try {
    const initial = await handle.stat({ bigint: true });
    if (!sameIdentity(identity(before), identity(initial)))
      throw fail("file_changed");
    const hash = createHash("sha256");
    const buffer = Buffer.alloc(128 * 1024);
    let position = 0;
    for (;;) {
      const { bytesRead } = await handle.read(
        buffer,
        0,
        buffer.length,
        position,
      );
      if (!bytesRead) break;
      hash.update(buffer.subarray(0, bytesRead));
      position += bytesRead;
    }
    const after = await handle.stat({ bigint: true });
    const named = await fsp.lstat(file, { bigint: true });
    if (
      !named.isFile() ||
      named.isSymbolicLink() ||
      !sameIdentity(identity(initial), identity(after)) ||
      !sameIdentity(identity(initial), identity(named)) ||
      initial.ctimeNs !== after.ctimeNs
    )
      throw fail("file_changed");
    return { identity: identity(after), sha256: hash.digest("hex") };
  } finally {
    await handle.close();
  }
}

function readStrictJournal(file) {
  let raw;
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw fail("journal_read_failed");
  }
  let entries;
  try {
    entries = JSON.parse(raw);
  } catch {
    throw fail("journal_invalid");
  }
  if (
    !Array.isArray(entries) ||
    entries.some(
      (entry) =>
        !entry ||
        typeof entry !== "object" ||
        typeof entry.from !== "string" ||
        typeof entry.to !== "string",
    )
  )
    throw fail("journal_invalid");
  return entries;
}

function summarize(outcomes, operationId, undo = false) {
  const count = (status) =>
    outcomes.filter((outcome) => outcome.status === status).length;
  const failed = count("failed");
  const uncertain = count("uncertain");
  const conflicts = count("conflict");
  return {
    ok: failed + uncertain + conflicts === 0,
    ...(operationId ? { operationId } : {}),
    ...(undo
      ? { undone: count("undone") }
      : { moved: count("moved"), skipped: count("skipped") + conflicts }),
    failed,
    conflicts,
    uncertain,
    outcomes,
    ...(failed + uncertain + conflicts
      ? { error: "Some files need attention; inspect the per-file results." }
      : {}),
  };
}

function createDesktopOrganizer({
  desktopDir,
  journalPath,
  writeJournal = writeJournalFile,
  io = fsp,
}) {
  const desktop = path.resolve(desktopDir);
  let queue = Promise.resolve();
  let rootIdentity;
  const serialize = (work) => {
    const pending = queue.then(work, work);
    queue = pending.catch(() => {});
    return pending;
  };
  const save = (entries) => {
    try {
      writeJournal(journalPath, entries);
    } catch {
      throw fail("journal_write_failed");
    }
  };
  async function validateRoot() {
    const root = await fsp.lstat(desktop, { bigint: true });
    if (!root.isDirectory() || root.isSymbolicLink())
      throw fail("unsafe_directory");
    const current = {
      dev: String(root.dev),
      ino: String(root.ino),
      birthtimeNs: String(root.birthtimeNs),
    };
    if (rootIdentity && !sameIdentity(current, rootIdentity))
      throw fail("desktop_changed");
    rootIdentity ||= current;
  }
  async function validateDestination(target, create = false) {
    await validateRoot();
    const parts = path
      .relative(desktop, path.dirname(target))
      .split(path.sep)
      .filter(Boolean);
    let current = desktop;
    for (const part of parts) {
      current = path.join(current, part);
      if (create) {
        try {
          await fsp.mkdir(current);
        } catch (error) {
          if (error.code !== "EEXIST") throw error;
        }
      }
      const stat = await fsp.lstat(current);
      if (!stat.isDirectory() || stat.isSymbolicLink())
        throw fail("unsafe_directory");
    }
  }
  function checkedTarget(source, destination) {
    const result = resolveMoveTarget(source, destination, desktop);
    if (result.error) throw fail("invalid_path");
    // Windows alternate data streams are not independent regular documents.
    if (
      process.platform === "win32" &&
      [
        path.basename(source),
        ...path.relative(desktop, result.target).split(path.sep),
      ].some((part) => part.includes(":"))
    )
      throw fail("invalid_path");
    return result.target;
  }
  function pendingPaths(entry) {
    if (typeof entry.id !== "string" || !/^[a-f0-9-]{36}$/.test(entry.id))
      throw fail("journal_invalid");
    return ["move", "undo"].map((direction) =>
      path.join(desktop, `.echo-organizer-${entry.id}-${direction}.pending`),
    );
  }
  async function retireVerified(from, retained, expected, pending) {
    // Capture the pathname atomically before removing it. If another process
    // replaces the original between our last read and this rename, its new
    // object is preserved at the receipt-bound pending path, never unlinked.
    try {
      await fsp.lstat(pending);
      throw fail("recovery_required");
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    await io.rename(from, pending);
    if (!matches(await snapshot(pending), expected)) {
      try {
        await io.link(pending, from);
      } catch {
        /* Never overwrite a new original pathname. */
      }
      throw fail("source_replaced");
    }
    if (!matches(await snapshot(retained), expected))
      throw fail("verification_failed");
    await io.unlink(pending);
  }
  async function reconcilePending(entry) {
    let restored = false;
    for (const pending of pendingPaths(entry)) {
      const observed = await snapshot(pending);
      if (!observed) continue;
      if (!matches(observed, entry.fingerprint))
        throw fail("recovery_required");
      let original = await snapshot(entry.from);
      const target = await snapshot(entry.to);
      if (
        !matches(original, entry.fingerprint) &&
        !matches(target, entry.fingerprint)
      ) {
        if (original || target) throw fail("recovery_required");
        // Both public names are missing, but the captured original is intact.
        await io.link(pending, entry.from);
        restored = true;
        original = await snapshot(entry.from);
        if (!matches(original, entry.fingerprint))
          throw fail("verification_failed");
      }
      // Only an additional verified link is removed; a public copy survives.
      if (!matches(await snapshot(pending), entry.fingerprint))
        throw fail("recovery_required");
      await io.unlink(pending);
    }
    return restored;
  }
  async function transfer(from, to, expected, pending) {
    let published = false;
    try {
      if (!matches(await snapshot(from), expected))
        throw fail("source_changed");
      try {
        await io.link(from, to);
      } catch (error) {
        if (error.code === "EEXIST") throw fail("destination_exists");
        throw error;
      }
      published = true;
      // Do not remove either pathname after an ambiguous link/read failure.
      // The prepared receipt lets a later undo reconcile both surviving names.
      if (
        !matches(await snapshot(from), expected) ||
        !matches(await snapshot(to), expected)
      )
        throw fail("file_changed");
      await validateDestination(path.dirname(from) === desktop ? to : from);
      await retireVerified(from, to, expected, pending);
      if ((await snapshot(from)) || !matches(await snapshot(to), expected))
        throw fail("verification_failed");
    } catch (error) {
      error.organizerMayHavePublished = published;
      throw error;
    }
  }
  async function moveBatch(items) {
    if (
      !Array.isArray(items) ||
      items.some(
        (item) =>
          !item ||
          typeof item.srcPath !== "string" ||
          typeof item.category !== "string",
      )
    )
      return summarize([{ status: "failed", code: "invalid_request" }]);
    const operationId = randomUUID();
    let journal;
    try {
      journal = readStrictJournal(journalPath);
      await validateRoot();
    } catch (error) {
      return summarize(
        items.map((item) => ({
          srcPath: item.srcPath,
          status: "failed",
          code: errorCode(error, "desktop_unavailable"),
          committed: false,
        })),
        operationId,
      );
    }
    const outcomes = [];
    for (const item of items) {
      let target;
      let entry;
      let transferred = false;
      try {
        target = checkedTarget(item.srcPath, item.category);
        await validateDestination(target, true);
        const expected = await snapshot(item.srcPath);
        if (!expected) throw fail("source_missing");
        try {
          await fsp.lstat(target);
          throw fail("destination_exists");
        } catch (error) {
          if (error.code !== "ENOENT") throw error;
        }
        entry = {
          version: 2,
          id: randomUUID(),
          operationId,
          from: path.resolve(item.srcPath),
          to: target,
          ts: Date.now(),
          fingerprint: expected,
          status: "prepared",
        };
        journal.push(entry);
        save(journal);
        await transfer(entry.from, entry.to, expected, pendingPaths(entry)[0]);
        transferred = true;
        entry.status = "moved";
        save(journal);
        outcomes.push({
          srcPath: entry.from,
          destPath: target,
          status: "moved",
          committed: true,
        });
      } catch (error) {
        const code = errorCode(error, "move_failed");
        let status = code === "destination_exists" ? "conflict" : "failed";
        let committed = false;
        if (entry) {
          // A durable prepared row may precede either half of the transfer.
          // Retain it even when the final receipt could not be written.
          if (transferred) {
            status = "uncertain";
            committed = true;
          } else if (
            entry.status === "prepared" &&
            error.organizerMayHavePublished
          ) {
            status = "uncertain";
            committed = null;
          }
          entry.status = status === "uncertain" ? status : "not_applied";
          entry.code = code;
          try {
            save(journal);
          } catch {
            /* The earlier prepared receipt remains authoritative. */
          }
        }
        outcomes.push({
          srcPath: item.srcPath,
          ...(target ? { destPath: target } : {}),
          status,
          code,
          committed,
          ...(entry && status === "uncertain"
            ? { recoveryPaths: pendingPaths(entry) }
            : {}),
        });
        // Journal failure prevents starting another unrecorded side effect.
        if (code === "journal_write_failed") {
          for (const pending of items.slice(outcomes.length))
            outcomes.push({
              srcPath: pending.srcPath,
              status: "failed",
              code: "journal_write_failed",
              committed: false,
            });
          break;
        }
      }
    }
    return summarize(outcomes, operationId);
  }
  async function undo(operationId) {
    let journal;
    try {
      journal = readStrictJournal(journalPath);
      await validateRoot();
    } catch (error) {
      return summarize(
        [
          {
            status: "failed",
            code: errorCode(error, "desktop_unavailable"),
            committed: false,
          },
        ],
        operationId,
        true,
      );
    }
    const active = journal.filter(
      (entry) => !["undone", "not_applied"].includes(entry.status),
    );
    const selected =
      operationId ||
      active.at(-1)?.operationId ||
      (active.length ? "legacy" : undefined);
    const entries = active
      .filter((entry) => (entry.operationId || "legacy") === selected)
      .reverse();
    const outcomes = [];
    for (const entry of entries) {
      let physicalUndo = false;
      try {
        if (
          entry.version !== 2 ||
          !entry.fingerprint?.sha256 ||
          !entry.fingerprint?.identity
        )
          throw fail("legacy_unverifiable");
        if (checkedTarget(entry.from, path.dirname(entry.to)) !== entry.to)
          throw fail("invalid_path");
        const pathKey = (value) =>
          process.platform === "win32"
            ? path.resolve(value).toLowerCase()
            : path.resolve(value);
        const affected = new Set([pathKey(entry.from), pathKey(entry.to)]);
        if (
          journal
            .slice(journal.indexOf(entry) + 1)
            .some(
              (newer) =>
                !["undone", "not_applied"].includes(newer.status) &&
                [newer.from, newer.to].some((value) =>
                  affected.has(pathKey(value)),
                ),
            )
        )
          throw fail("newer_operation_pending");
        await validateDestination(entry.to);
        physicalUndo = await reconcilePending(entry);
        const source = await snapshot(entry.from);
        const target = await snapshot(entry.to);
        if (!target && matches(source, entry.fingerprint)) {
          const restored = physicalUndo || entry.undoStarted === true;
          entry.status = restored ? "undone" : "not_applied";
          save(journal);
          outcomes.push({
            srcPath: entry.from,
            destPath: entry.to,
            status: restored ? "undone" : "skipped",
            ...(restored ? {} : { code: "already_at_source" }),
            committed: restored,
          });
          continue;
        }
        if (!matches(target, entry.fingerprint))
          throw fail(target ? "destination_changed" : "destination_missing");
        if (source && !matches(source, entry.fingerprint))
          throw fail("source_conflict");
        entry.status = "undo_prepared";
        entry.undoStarted = true;
        delete entry.code;
        save(journal);
        if (source) {
          // A crash between link and unlink leaves the same verified inode at
          // both names. Undo keeps the original name and removes only its link.
          if (
            !matches(await snapshot(entry.from), entry.fingerprint) ||
            !matches(await snapshot(entry.to), entry.fingerprint)
          )
            throw fail("file_changed");
          await retireVerified(
            entry.to,
            entry.from,
            entry.fingerprint,
            pendingPaths(entry)[1],
          );
          if (
            !matches(await snapshot(entry.from), entry.fingerprint) ||
            (await snapshot(entry.to))
          )
            throw fail("verification_failed");
        } else {
          await transfer(
            entry.to,
            entry.from,
            entry.fingerprint,
            pendingPaths(entry)[1],
          );
        }
        physicalUndo = true;
        entry.status = "undone";
        save(journal);
        outcomes.push({
          srcPath: entry.from,
          destPath: entry.to,
          status: "undone",
          committed: true,
        });
      } catch (error) {
        const code = errorCode(error, "undo_failed");
        const uncertain =
          physicalUndo ||
          (["undo_prepared"].includes(entry.status) &&
            !["destination_exists", "journal_write_failed"].includes(code) &&
            error.organizerMayHavePublished !== false);
        const status = uncertain
          ? "uncertain"
          : code === "journal_write_failed"
            ? "failed"
            : "conflict";
        entry.status = status;
        entry.code = code;
        try {
          save(journal);
        } catch {
          /* Keep the durable prior intent for retry. */
        }
        outcomes.push({
          srcPath: entry.from,
          destPath: entry.to,
          status,
          code,
          committed: physicalUndo ? true : uncertain ? null : false,
          ...(entry.version === 2 && (uncertain || code === "recovery_required")
            ? { recoveryPaths: pendingPaths(entry) }
            : {}),
        });
        if (code === "journal_write_failed") break;
      }
    }
    return summarize(outcomes, selected, true);
  }
  return {
    moveItemsBatch: (items) => serialize(() => moveBatch(items)),
    moveItem: (source, destination) =>
      serialize(async () => {
        const result = await moveBatch([
          { srcPath: source, category: destination },
        ]);
        const outcome = result.outcomes[0];
        return {
          ...result,
          ...(outcome?.destPath ? { destPath: outcome.destPath } : {}),
          skipped: outcome?.status === "conflict",
        };
      }),
    undoMoves: (operationId) => serialize(() => undo(operationId)),
  };
}

module.exports = { createDesktopOrganizer };
