/**
 * 原生 shell 系统手层 · .desktop 解析、屏蔽、分类与无 shell 启动测试。
 * 用 node 内置 assert,免框架:`node electron/system-shell.test.cjs`。
 * 真实应用枚举/图标/启动是 Linux 行为,在 VM/真机验证;此处只测可移植的解析逻辑。
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const {
  parseDesktopEntry,
  isLaunchableApp,
  isValidApplicationId,
  listApplicationRecords,
  launchApplicationById,
} = require("./system-shell.cjs");

const tests = [];
function test(name, fn) {
  tests.push({ name, fn });
}

const GIMP = `[Desktop Entry]
Type=Application
Name=GIMP
Name[zh_CN]=GNU 图像处理程序
Comment=Create images
Icon=gimp
Exec=gimp-2.10 %U
Categories=Graphics;GTK;
Terminal=false
`;

test("解析基础键(忽略本地化 Name[zh_CN])", () => {
  const e = parseDesktopEntry(GIMP);
  assert.strictEqual(e.Name, "GIMP");
  assert.strictEqual(e.Icon, "gimp");
  assert.strictEqual(e.Exec, "gimp-2.10 %U");
  assert.strictEqual(e.Categories, "Graphics;GTK;");
});

test("只取 [Desktop Entry] 段,忽略其他段(如 Desktop Action)", () => {
  const e = parseDesktopEntry(`[Desktop Entry]
Name=Foo
Exec=foo
[Desktop Action new]
Name=New Window
Exec=foo --new
`);
  assert.strictEqual(e.Name, "Foo");
  assert.strictEqual(e.Exec, "foo"); // 不被 action 段覆盖
});

test("isLaunchableApp:正常应用通过", () => {
  assert.strictEqual(isLaunchableApp(parseDesktopEntry(GIMP)), true);
});

test("高优先级 Hidden desktop entry 会屏蔽低优先级同名应用", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "echo-app-mask-"));
  const high = path.join(root, "high");
  const low = path.join(root, "low");
  fs.mkdirSync(high);
  fs.mkdirSync(low);
  fs.writeFileSync(
    path.join(high, "org.example.App.desktop"),
    "[Desktop Entry]\nType=Application\nHidden=true\n",
  );
  fs.writeFileSync(path.join(low, "org.example.App.desktop"), GIMP);
  assert.deepStrictEqual(listApplicationRecords([high, low], []), []);
  fs.rmSync(root, { recursive: true, force: true });
});

test("Flatpak export 被识别，并由 gio 直接启动 desktop file", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "echo-flatpak-app-"));
  const appDir = path.join(root, "flatpak", "exports", "share", "applications");
  fs.mkdirSync(appDir, { recursive: true });
  const desktopFile = path.join(appDir, "org.gimp.GIMP.desktop");
  fs.writeFileSync(desktopFile, GIMP);

  const records = listApplicationRecords([appDir], []);
  assert.strictEqual(records.length, 1);
  assert.strictEqual(records[0].source, "flatpak");

  const calls = [];
  const result = await launchApplicationById("org.gimp.GIMP", {
    appDirs: [appDir],
    iconRoots: [],
    execFile: (command, args, options, callback) => {
      calls.push({ command, args, options });
      callback(null, "", "");
    },
  });
  assert.deepStrictEqual(result, { ok: true });
  assert.deepStrictEqual(calls[0].command, "/usr/bin/gio");
  assert.deepStrictEqual(calls[0].args, ["launch", desktopFile]);
  assert.strictEqual(calls[0].options.timeout, 10_000);
  assert.strictEqual(calls[0].options.maxBuffer, 64 * 1024);
  assert.strictEqual("shell" in calls[0].options, false);
  fs.rmSync(root, { recursive: true, force: true });
});

test("gio 异步失败、非零退出和过长 stderr 不能冒充启动成功", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "echo-native-fail-"));
  const appDir = path.join(root, "applications");
  fs.mkdirSync(appDir);
  fs.writeFileSync(path.join(appDir, "org.gimp.GIMP.desktop"), GIMP);

  const missing = new Error("spawn ENOENT");
  missing.code = "ENOENT";
  const missingResult = await launchApplicationById("org.gimp.GIMP", {
    appDirs: [appDir],
    iconRoots: [],
    execFile: (_command, _args, _options, callback) =>
      callback(missing, "", ""),
  });
  assert.deepStrictEqual(missingResult, {
    ok: false,
    error: "system application launcher is missing",
  });

  const nonZero = new Error("gio exited 1");
  nonZero.code = 1;
  const nonZeroResult = await launchApplicationById("org.gimp.GIMP", {
    appDirs: [appDir],
    iconRoots: [],
    execFile: (_command, _args, _options, callback) =>
      callback(nonZero, "", `bad desktop entry ${"x".repeat(1024)}`),
  });
  assert.strictEqual(nonZeroResult.ok, false);
  assert.match(
    nonZeroResult.error,
    /^application launch failed: bad desktop entry/,
  );
  assert.ok(nonZeroResult.error.length <= 283);

  const oversized = new Error("stderr maxBuffer exceeded");
  oversized.code = "ERR_CHILD_PROCESS_STDIO_MAXBUFFER";
  oversized.killed = true;
  const oversizedResult = await launchApplicationById("org.gimp.GIMP", {
    appDirs: [appDir],
    iconRoots: [],
    execFile: (_command, _args, _options, callback) =>
      callback(oversized, "", "unbounded output"),
  });
  assert.deepStrictEqual(oversizedResult, {
    ok: false,
    error: "system application launcher output exceeded its limit",
  });
  fs.rmSync(root, { recursive: true, force: true });
});

test("gio 同步抛错也返回有界失败结果", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "echo-native-throw-"));
  fs.writeFileSync(path.join(root, "org.gimp.GIMP.desktop"), GIMP);
  const result = await launchApplicationById("org.gimp.GIMP", {
    appDirs: [root],
    iconRoots: [],
    execFile: () => {
      throw new Error("synchronous failure");
    },
  });
  assert.deepStrictEqual(result, {
    ok: false,
    error: "application launch failed",
  });
  fs.rmSync(root, { recursive: true, force: true });
});

test("应用 id 校验拒绝路径和 shell 元字符", () => {
  assert.strictEqual(isValidApplicationId("org.gimp.GIMP"), true);
  assert.strictEqual(isValidApplicationId("../../bin/sh"), false);
  assert.strictEqual(isValidApplicationId("ok;touch-pwned"), false);
});

test("isLaunchableApp:NoDisplay / Hidden / 无 Exec / 非 Application 被排除", () => {
  assert.strictEqual(
    isLaunchableApp(
      parseDesktopEntry(
        "[Desktop Entry]\nType=Application\nName=X\nExec=x\nNoDisplay=true\n",
      ),
    ),
    false,
  );
  assert.strictEqual(
    isLaunchableApp(
      parseDesktopEntry(
        "[Desktop Entry]\nType=Application\nName=X\nExec=x\nHidden=true\n",
      ),
    ),
    false,
  );
  assert.strictEqual(
    isLaunchableApp(
      parseDesktopEntry("[Desktop Entry]\nType=Application\nName=X\n"),
    ),
    false, // 无 Exec
  );
  assert.strictEqual(
    isLaunchableApp(
      parseDesktopEntry(
        "[Desktop Entry]\nType=Link\nName=X\nExec=x\nURL=http://x\n",
      ),
    ),
    false, // 非 Application
  );
});

async function main() {
  let passed = 0;
  for (const { name, fn } of tests) {
    await fn();
    passed += 1;
    console.log("  ✓", name);
  }
  console.log(`\n系统手解析测试:${passed} passed`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
