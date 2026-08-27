/** Echo Agent systemd restart boundary tests; never invoke the host systemctl. */
"use strict";

const assert = require("assert");
const {
  AGENT_SERVICE,
  HEALTH_VERIFIER,
  getAgentServiceCapabilities,
  resolveHealthVerifierPath,
  resolveSystemctlPath,
  restartAgentService,
} = require("./agent-service.cjs");

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log("  ✓", name);
}

(async () => {
  await test("网页和非 Linux 进程不能控制 Agent 服务", async () => {
    for (const options of [
      {
        platform: "linux",
        nativeShell: false,
        systemctlPath: "/usr/bin/systemctl",
        healthVerifierPath: HEALTH_VERIFIER,
      },
      {
        platform: "darwin",
        nativeShell: true,
        systemctlPath: "/usr/bin/systemctl",
        healthVerifierPath: HEALTH_VERIFIER,
      },
    ]) {
      assert.strictEqual(
        getAgentServiceCapabilities(options).restart,
        false,
      );
      let executed = false;
      const result = await restartAgentService({
        ...options,
        execFileImpl: () => {
          executed = true;
        },
      });
      assert.strictEqual(result.ok, false);
      assert.strictEqual(executed, false);
    }
  });

  await test("systemctl 只从固定绝对路径解析", async () => {
    assert.strictEqual(
      resolveSystemctlPath((candidate) => candidate === "/bin/systemctl"),
      "/bin/systemctl",
    );
    assert.strictEqual(resolveSystemctlPath(() => false), null);
    assert.strictEqual(
      resolveHealthVerifierPath((candidate) => candidate === HEALTH_VERIFIER),
      HEALTH_VERIFIER,
    );
    assert.strictEqual(resolveHealthVerifierPath(() => false), null);
  });

  await test("重启后必须通过固定 Agent 健康门且不经过 shell", async () => {
    const calls = [];
    const result = await restartAgentService({
      platform: "linux",
      nativeShell: true,
      systemctlPath: "/usr/bin/systemctl",
      healthVerifierPath: HEALTH_VERIFIER,
      execFileImpl: (file, args, options, callback) => {
        calls.push({ file, args, options });
        callback(null, "", "");
      },
    });
    assert.deepStrictEqual(result, { ok: true });
    assert.strictEqual(AGENT_SERVICE, "echo-agent.service");
    assert.deepStrictEqual(
      calls.map(({ file, args }) => ({ file, args })),
      [
        {
          file: "/usr/bin/systemctl",
          args: ["restart", "echo-agent.service"],
        },
        { file: HEALTH_VERIFIER, args: [] },
      ],
    );
    assert.strictEqual(calls[0].options.timeout, 30_000);
    assert.strictEqual(calls[1].options.timeout, 135_000);
    for (const call of calls) {
      assert.strictEqual(call.options.maxBuffer, 64 * 1024);
      assert.strictEqual("shell" in call.options, false);
    }
  });

  await test("systemd 拒绝原因有界返回且不伪报成功", async () => {
    const result = await restartAgentService({
      platform: "linux",
      nativeShell: true,
      systemctlPath: "/usr/bin/systemctl",
      healthVerifierPath: HEALTH_VERIFIER,
      execFileImpl: (_file, _args, _options, callback) => {
        callback(new Error("denied"), "ignored stdout", "x".repeat(800));
      },
    });
    assert.strictEqual(result.ok, false);
    assert.strictEqual(result.reason.length, 512);
    assert.match(result.reason, /^systemd restart failed: x+/);
  });

  await test("Agent 健康门失败时不伪报重启成功", async () => {
    let callCount = 0;
    const result = await restartAgentService({
      platform: "linux",
      nativeShell: true,
      systemctlPath: "/usr/bin/systemctl",
      healthVerifierPath: HEALTH_VERIFIER,
      execFileImpl: (_file, _args, _options, callback) => {
        callCount += 1;
        if (callCount === 1) callback(null, "", "");
        else callback(new Error("unhealthy"), "", "identity mismatch");
      },
    });
    assert.strictEqual(result.ok, false);
    assert.match(result.reason, /health gate failed: identity mismatch/);
    assert.strictEqual(callCount, 2);
  });

  console.log(`\nEcho Agent service control tests: ${passed} passed`);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
