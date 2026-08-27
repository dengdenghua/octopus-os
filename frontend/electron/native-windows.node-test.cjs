/** Native window bridge contract tests. Run with plain Node (not Vitest). */
"use strict";

const assert = require("assert");
const {
  getNativeWindowCapabilities,
  normalizeKWinWindowId,
  normalizeWindowId,
  parseActiveWindow,
  parseKWinWindowList,
  parseWmctrlWindowList,
  listNativeWindows,
  runNativeWindowAction,
} = require("./native-windows.cjs");

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log("  ✓", name);
}

void (async () => {
  await test("解析 EWMH 窗口列表、标题空格和活动窗口", () => {
    const windows = parseWmctrlWindowList(
      [
        "0x04600007  0 2314 echo-host org.gnome.Nautilus Files — Home",
        "0x04a0000a -1 8821 echo-host code.Code Visual Studio Code",
      ].join("\n"),
      "0x04a0000a",
    );
    assert.deepStrictEqual(windows[0], {
      id: "0x4600007",
      desktop: 0,
      pid: 2314,
      host: "echo-host",
      wmClass: "org.gnome.Nautilus",
      title: "Files — Home",
      active: false,
      minimized: null,
      provider: "ewmh-x11",
    });
    assert.strictEqual(windows[1].active, true);
    assert.strictEqual(windows[1].desktop, -1);
  });

  await test("解析 _NET_ACTIVE_WINDOW 并忽略空窗口", () => {
    assert.strictEqual(
      parseActiveWindow("_NET_ACTIVE_WINDOW(WINDOW): window id # 0x4A0000A"),
      "0x4a0000a",
    );
    assert.strictEqual(parseActiveWindow("window id # 0x0"), null);
  });

  await test("拒绝渲染端注入的窗口 id", () => {
    assert.throws(() => normalizeWindowId("0x1; shutdown -h now"));
    assert.strictEqual(normalizeWindowId(" 0x0ABC123 "), "0xabc123");
    assert.throws(() =>
      normalizeKWinWindowId("23d24387-4430-4c58-9d2f-83e89095d625;reboot"),
    );
    assert.strictEqual(
      normalizeKWinWindowId("{23D24387-4430-4C58-9D2F-83E89095D625}"),
      "23d24387-4430-4c58-9d2f-83e89095d625",
    );
  });

  await test("能力只在 Linux 原生图形会话且 wmctrl 存在时开放", () => {
    assert.strictEqual(
      getNativeWindowCapabilities({
        nativeShell: false,
        platform: "linux",
        display: ":0",
        wmctrlPath: "/usr/bin/wmctrl",
      }).list,
      false,
    );
    assert.strictEqual(
      getNativeWindowCapabilities({
        nativeShell: true,
        platform: "linux",
        display: ":0",
        wmctrlPath: "/usr/bin/wmctrl",
      }).list,
      true,
    );
  });

  await test("枚举调用固定的 wmctrl/xprop 参数", async () => {
    const calls = [];
    const execFileImpl = (file, args, _options, callback) => {
      calls.push([file, args]);
      callback(
        null,
        file.endsWith("xprop")
          ? "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x1000001"
          : "0x01000001  0 42 host app.App A window",
        "",
      );
    };
    const result = await listNativeWindows({
      nativeShell: true,
      platform: "linux",
      display: ":0",
      wmctrlPath: "/usr/bin/wmctrl",
      xpropPath: "/usr/bin/xprop",
      execFileImpl,
    });
    assert.strictEqual(result.ok, true);
    assert.strictEqual(result.windows[0].active, true);
    assert.deepStrictEqual(calls, [
      ["/usr/bin/wmctrl", ["-l", "-x", "-p"]],
      ["/usr/bin/xprop", ["-root", "_NET_ACTIVE_WINDOW"]],
    ]);
  });

  await test("Wayland 只在用户私有 KWin bridge socket 就绪时开放", () => {
    const unavailable = getNativeWindowCapabilities({
      nativeShell: true,
      platform: "linux",
      sessionType: "wayland",
      waylandBridgePath: "/run/user/1000/echo-os/missing.sock",
      waylandBridgeReady: false,
    });
    assert.strictEqual(unavailable.list, false);
    assert.match(unavailable.reason, /unavailable/);

    const available = getNativeWindowCapabilities({
      nativeShell: true,
      platform: "linux",
      sessionType: "wayland",
      waylandBridgePath: "/run/user/1000/echo-os/kwin-window-bridge.sock",
      waylandBridgeReady: true,
    });
    assert.strictEqual(available.provider, "kwin-wayland");
    assert.strictEqual(available.close, true);
  });

  await test("Wayland 窗口快照经过二次校验后才返回 renderer", async () => {
    const calls = [];
    const options = {
      nativeShell: true,
      platform: "linux",
      sessionType: "wayland",
      waylandBridgePath: "/run/user/1000/echo-os/kwin-window-bridge.sock",
      waylandBridgeReady: true,
      waylandRequestImpl: async (socketPath, request) => {
        calls.push([socketPath, request]);
        return {
          ok: true,
          provider: "kwin-wayland",
          windows: [
            {
              id: "23D24387-4430-4C58-9D2F-83E89095D625",
              desktop: 1,
              pid: 4242,
              host: "",
              wmClass: "org.kde.dolphin",
              title: "Home — Dolphin",
              active: true,
              minimized: false,
              provider: "kwin-wayland",
            },
          ],
        };
      },
    };
    const result = await listNativeWindows(options);
    assert.strictEqual(result.ok, true);
    assert.strictEqual(result.provider, "kwin-wayland");
    assert.deepStrictEqual(result.windows[0], {
      id: "23d24387-4430-4c58-9d2f-83e89095d625",
      desktop: 1,
      pid: 4242,
      host: "",
      wmClass: "org.kde.dolphin",
      title: "Home — Dolphin",
      active: true,
      minimized: false,
      provider: "kwin-wayland",
    });
    assert.deepStrictEqual(calls, [
      ["/run/user/1000/echo-os/kwin-window-bridge.sock", { method: "list" }],
    ]);
    assert.throws(() =>
      parseKWinWindowList([
        {
          ...result.windows[0],
          title: "x".repeat(1025),
        },
      ]),
    );
  });

  await test("Wayland 动作只接受 UUID 和固定动作", async () => {
    const calls = [];
    const options = {
      nativeShell: true,
      platform: "linux",
      sessionType: "wayland",
      waylandBridgePath: "/run/user/1000/echo-os/kwin-window-bridge.sock",
      waylandBridgeReady: true,
      waylandRequestImpl: async (socketPath, request) => {
        calls.push([socketPath, request]);
        return { ok: true };
      },
    };
    const id = "23d24387-4430-4c58-9d2f-83e89095d625";
    assert.deepStrictEqual(
      await runNativeWindowAction("minimize", id, options),
      {
        ok: true,
        action: "minimize",
        windowId: id,
        provider: "kwin-wayland",
      },
    );
    const injected = await runNativeWindowAction(
      "close",
      `${id};shutdown`,
      options,
    );
    assert.strictEqual(injected.ok, false);
    assert.strictEqual(calls.length, 1);
    assert.deepStrictEqual(calls[0][1], {
      method: "action",
      action: "minimize",
      windowId: id,
    });
  });

  await test("聚焦先恢复隐藏窗口，再激活；最小化和关闭使用固定动作", async () => {
    const calls = [];
    const execFileImpl = (file, args, _options, callback) => {
      calls.push([file, args]);
      callback(null, "", "");
    };
    const options = {
      nativeShell: true,
      platform: "linux",
      display: ":0",
      wmctrlPath: "/usr/bin/wmctrl",
      xpropPath: null,
      execFileImpl,
    };
    assert.strictEqual(
      (await runNativeWindowAction("focus", "0xABC", options)).ok,
      true,
    );
    assert.strictEqual(
      (await runNativeWindowAction("minimize", "0xABC", options)).ok,
      true,
    );
    assert.strictEqual(
      (await runNativeWindowAction("close", "0xABC", options)).ok,
      true,
    );
    assert.deepStrictEqual(
      calls.map((call) => call[1]),
      [
        ["-ir", "0xabc", "-b", "remove,hidden"],
        ["-ia", "0xabc"],
        ["-ir", "0xabc", "-b", "add,hidden"],
        ["-ic", "0xabc"],
      ],
    );
  });

  console.log(`\n原生窗口桥测试: ${passed} passed`);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
