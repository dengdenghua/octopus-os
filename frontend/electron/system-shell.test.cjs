/**
 * 原生 shell 系统手层 · 纯函数测试(.desktop 解析 / Exec 清洗 / 可启动判定)。
 * 用 node 内置 assert,免框架:`node electron/system-shell.test.cjs`。
 * 真实应用枚举/图标/启动是 Linux 行为,在 VM/真机验证;此处只测可移植的解析逻辑。
 */
"use strict";

const assert = require("assert");
const {
  parseDesktopEntry,
  cleanExec,
  isLaunchableApp,
} = require("./system-shell.cjs");

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log("  ✓", name);
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

test("cleanExec 去掉字段码 %U %f %F %i 等", () => {
  assert.strictEqual(cleanExec("gimp-2.10 %U"), "gimp-2.10");
  assert.strictEqual(cleanExec("code %F --new"), "code --new");
  assert.strictEqual(cleanExec("app %i %c %k %f"), "app");
  assert.strictEqual(cleanExec(""), "");
});

test("isLaunchableApp:正常应用通过", () => {
  assert.strictEqual(isLaunchableApp(parseDesktopEntry(GIMP)), true);
});

test("isLaunchableApp:NoDisplay / Hidden / 无 Exec / 非 Application 被排除", () => {
  assert.strictEqual(
    isLaunchableApp(parseDesktopEntry("[Desktop Entry]\nType=Application\nName=X\nExec=x\nNoDisplay=true\n")),
    false,
  );
  assert.strictEqual(
    isLaunchableApp(parseDesktopEntry("[Desktop Entry]\nType=Application\nName=X\nExec=x\nHidden=true\n")),
    false,
  );
  assert.strictEqual(
    isLaunchableApp(parseDesktopEntry("[Desktop Entry]\nType=Application\nName=X\n")),
    false, // 无 Exec
  );
  assert.strictEqual(
    isLaunchableApp(parseDesktopEntry("[Desktop Entry]\nType=Link\nName=X\nExec=x\nURL=http://x\n")),
    false, // 非 Application
  );
});

console.log(`\n系统手解析测试:${passed} passed`);
