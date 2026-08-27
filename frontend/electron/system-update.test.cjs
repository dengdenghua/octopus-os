/** Echo OS 图形化系统更新边界测试；绝不执行真实 pkexec。 */
"use strict";

const assert = require("assert");
const {
  APPLY_HELPER,
  APPLY_INTERPRETER,
  applySystemUpdate,
  getSystemUpdateCapabilities,
  getSystemUpdateStatus,
  resolvePkexecPath,
  validateStatusRecord,
} = require("./system-update.cjs");

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log("  ✓", name);
}

(async () => {
  await test("only the native Linux session exposes update capabilities", async () => {
    const web = getSystemUpdateCapabilities({
      platform: "linux",
      nativeShell: false,
      pkexecPath: "/usr/bin/pkexec",
      existsSync: () => true,
    });
    const native = getSystemUpdateCapabilities({
      platform: "linux",
      nativeShell: true,
      pkexecPath: "/usr/bin/pkexec",
      existsSync: () => true,
    });
    assert.strictEqual(web.apply, false);
    assert.strictEqual(web.status, false);
    assert.strictEqual(native.apply, true);
    assert.strictEqual(native.status, true);
  });

  await test("pkexec is resolved only from the fixed absolute whitelist", async () => {
    assert.strictEqual(
      resolvePkexecPath((candidate) => candidate === "/bin/pkexec"),
      "/bin/pkexec",
    );
    assert.strictEqual(
      resolvePkexecPath(() => false),
      null,
    );
  });

  await test("status accepts one authenticated bounded candidate", async () => {
    const record = {
      schema: 1,
      state: "ready",
      phase: "fetch",
      version: "0.2.1",
      manifestSha256: "d".repeat(64),
      updatedAt: 1_800_000_000,
    };
    assert.strictEqual(validateStatusRecord(record), record);
    assert.deepStrictEqual(
      getSystemUpdateStatus({
        platform: "linux",
        nativeShell: true,
        readStatusImpl: () => record,
      }),
      record,
    );
  });

  await test("status rejects raw error text and incomplete candidates", async () => {
    assert.throws(
      () =>
        validateStatusRecord({
          schema: 1,
          state: "failed",
          phase: "fetch",
          updatedAt: 1_800_000_000,
          errorCode: 1,
          error: "/private/cache/path",
        }),
      /unknown fields/,
    );
    assert.throws(
      () =>
        validateStatusRecord({
          schema: 1,
          state: "ready",
          phase: "fetch",
          version: "0.2.1",
          updatedAt: 1_800_000_000,
        }),
      /incomplete/,
    );
  });

  await test("non-native status never touches the host state file", async () => {
    let read = false;
    const result = getSystemUpdateStatus({
      platform: "darwin",
      nativeShell: true,
      readStatusImpl: () => {
        read = true;
      },
    });
    assert.strictEqual(result.state, "unavailable");
    assert.strictEqual(read, false);
  });

  await test("apply invokes only fixed pkexec and the argument-free helper", async () => {
    let call = null;
    const result = await applySystemUpdate({
      platform: "linux",
      nativeShell: true,
      pkexecPath: "/usr/bin/pkexec",
      existsSync: (candidate) =>
        candidate === APPLY_INTERPRETER || candidate === APPLY_HELPER,
      execFileImpl: (file, args, options, callback) => {
        call = { file, args, options };
        callback(null, "", "");
      },
    });
    assert.deepStrictEqual(result, { ok: true });
    assert.strictEqual(call.file, "/usr/bin/pkexec");
    assert.deepStrictEqual(call.args, [
      "--disable-internal-agent",
      APPLY_INTERPRETER,
      APPLY_HELPER,
    ]);
    assert.strictEqual(call.options.timeout, 4 * 60 * 60 * 1000);
  });

  await test("missing helper and cancelled authorization fail without execution", async () => {
    let executed = false;
    const missing = await applySystemUpdate({
      platform: "linux",
      nativeShell: true,
      pkexecPath: "/usr/bin/pkexec",
      existsSync: () => false,
      execFileImpl: () => {
        executed = true;
      },
    });
    const cancelled = await applySystemUpdate({
      platform: "linux",
      nativeShell: true,
      pkexecPath: "/usr/bin/pkexec",
      existsSync: () => true,
      execFileImpl: (_file, _args, _options, callback) => {
        const error = new Error("cancelled");
        error.code = 126;
        callback(error, "", "");
      },
    });
    assert.strictEqual(missing.ok, false);
    assert.strictEqual(executed, false);
    assert.deepStrictEqual(cancelled, {
      ok: false,
      cancelled: true,
      error: "administrator authorization was cancelled",
    });
  });

  console.log(`\nEcho OS 系统更新桥接测试：${passed} passed`);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
