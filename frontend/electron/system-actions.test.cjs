/** Echo OS 会话/电源动作的边界测试；绝不执行真实 loginctl/systemctl。 */
"use strict";

const assert = require("assert");
const {
  getSystemActionCapabilities,
  resolveLoginctlPath,
  resolveSystemctlPath,
  runSystemAction,
} = require("./system-actions.cjs");

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log("  ✓", name);
}

(async () => {
  await test("网页或普通开发进程没有系统动作权限", async () => {
    const capabilities = getSystemActionCapabilities({
      platform: "linux",
      nativeShell: false,
      systemctlPath: "/usr/bin/systemctl",
      loginctlPath: "/usr/bin/loginctl",
      lockScreenReady: true,
    });
    assert.strictEqual(capabilities.lock, false);
    assert.strictEqual(capabilities.shutdown, false);
  });

  await test("只有原生 Linux 会话且锁屏处理器就绪时开放动作", async () => {
    const capabilities = getSystemActionCapabilities({
      platform: "linux",
      nativeShell: true,
      systemctlPath: "/usr/bin/systemctl",
      loginctlPath: "/usr/bin/loginctl",
      lockScreenReady: true,
    });
    assert.deepStrictEqual(
      {
        lock: capabilities.lock,
        logout: capabilities.logout,
        suspend: capabilities.suspend,
        restart: capabilities.restart,
        shutdown: capabilities.shutdown,
      },
      {
        lock: true,
        logout: true,
        suspend: true,
        restart: true,
        shutdown: true,
      },
    );
  });

  await test("没有会话锁处理器时绝不伪报锁屏能力", async () => {
    const capabilities = getSystemActionCapabilities({
      platform: "linux",
      nativeShell: true,
      systemctlPath: "/usr/bin/systemctl",
      loginctlPath: "/usr/bin/loginctl",
      lockScreenReady: false,
    });
    assert.strictEqual(capabilities.lock, false);
    assert.strictEqual(capabilities.logout, true);
  });

  await test("systemctl 路径只从固定白名单解析", async () => {
    assert.strictEqual(
      resolveSystemctlPath((candidate) => candidate === "/bin/systemctl"),
      "/bin/systemctl",
    );
    assert.strictEqual(
      resolveSystemctlPath(() => false),
      null,
    );
    assert.strictEqual(
      resolveLoginctlPath((candidate) => candidate === "/usr/bin/loginctl"),
      "/usr/bin/loginctl",
    );
  });

  await test("锁屏和注销只调用 loginctl 固定会话参数", async () => {
    const calls = [];
    const options = {
      platform: "linux",
      nativeShell: true,
      systemctlPath: "/usr/bin/systemctl",
      loginctlPath: "/usr/bin/loginctl",
      lockScreenReady: true,
      execFileImpl: (file, args, execOptions, callback) => {
        calls.push({ file, args, execOptions });
        callback(null, "", "");
      },
    };
    assert.deepStrictEqual(await runSystemAction("lock", options), {
      ok: true,
      action: "lock",
    });
    assert.deepStrictEqual(await runSystemAction("logout", options), {
      ok: true,
      action: "logout",
    });
    assert.deepStrictEqual(
      calls.map(({ file, args }) => ({ file, args })),
      [
        { file: "/usr/bin/loginctl", args: ["lock-session", "self"] },
        { file: "/usr/bin/loginctl", args: ["terminate-session", "self"] },
      ],
    );
  });

  await test("关机映射为无 shell 的白名单参数", async () => {
    let call = null;
    const result = await runSystemAction("shutdown", {
      platform: "linux",
      nativeShell: true,
      systemctlPath: "/usr/bin/systemctl",
      loginctlPath: "/usr/bin/loginctl",
      lockScreenReady: true,
      execFileImpl: (file, args, options, callback) => {
        call = { file, args, options };
        callback(null, "", "");
      },
    });
    assert.deepStrictEqual(result, { ok: true, action: "shutdown" });
    assert.strictEqual(call.file, "/usr/bin/systemctl");
    assert.deepStrictEqual(call.args, ["poweroff", "--no-block"]);
    assert.strictEqual(call.options.timeout, 15_000);
  });

  await test("未知动作与非原生环境都会被拒绝", async () => {
    let executed = false;
    const unknown = await runSystemAction("erase", {
      execFileImpl: () => {
        executed = true;
      },
    });
    const unsafeHost = await runSystemAction("restart", {
      platform: "darwin",
      nativeShell: true,
      systemctlPath: "/usr/bin/systemctl",
      loginctlPath: "/usr/bin/loginctl",
      lockScreenReady: true,
      execFileImpl: () => {
        executed = true;
      },
    });
    assert.strictEqual(unknown.ok, false);
    assert.strictEqual(unsafeHost.ok, false);
    assert.strictEqual(executed, false);
  });

  console.log(`\nEcho OS 会话/电源动作测试：${passed} passed`);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
