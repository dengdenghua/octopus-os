/**
 * Octopus OS · 原生 shell(A 路线)的"系统手"层。
 *
 * Electron 会话 shell 模式下,主进程经此模块拥有真实系统能力:枚举已装本地应用
 * (freedesktop .desktop)、解析应用图标、启动应用。暴露给渲染进程的 React 桌面
 * (Dock/启动器渲染真实应用清单)。Docker 应用仍走后端 app_registry;此模块管
 * **原生已装应用**,两者在 Dock 合并显示。
 *
 * 非 Linux(开发用 mac/win)→ 应用枚举返回空数组(无 XDG 应用目录),纯解析函数
 * 仍可单测。真实枚举/启动需在 Linux(VM/真机)验证。
 */

"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

// ── XDG 应用目录(freedesktop)──────────────────────────────────
function _appDirs() {
  const dirs = [];
  const dataHome =
    process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share");
  dirs.push(path.join(dataHome, "applications"));
  const dataDirs =
    process.env.XDG_DATA_DIRS || "/usr/local/share:/usr/share";
  for (const d of dataDirs.split(":")) {
    if (d) dirs.push(path.join(d, "applications"));
  }
  return dirs;
}

// ── .desktop 解析(纯函数,可单测)──────────────────────────────
/**
 * 解析一个 .desktop 文件内容,返回 [Desktop Entry] 段的键值。
 * 只取基础键(忽略本地化 Name[zh_CN] 等的语言后缀,取无后缀基础值)。
 */
function parseDesktopEntry(text) {
  const lines = text.split(/\r?\n/);
  let inEntry = false;
  const entry = {};
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.startsWith("[")) {
      inEntry = line === "[Desktop Entry]";
      continue;
    }
    if (!inEntry) continue;
    const eq = line.indexOf("=");
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim();
    // 跳过本地化键(Name[xx]);基础键已足够 Dock 显示。
    if (key.includes("[")) continue;
    if (!(key in entry)) entry[key] = value;
  }
  return entry;
}

/** Exec 去掉 freedesktop 字段码(%f %u %F %U %i %c %k …),供启动用。 */
function cleanExec(exec) {
  if (!exec) return "";
  return exec
    .replace(/%[fFuUdDnNickvm]/g, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/** 一个 entry 是否是应该显示在启动器里的应用。 */
function isLaunchableApp(entry) {
  if (!entry) return false;
  if ((entry.Type || "Application") !== "Application") return false;
  if (entry.NoDisplay === "true" || entry.Hidden === "true") return false;
  if (!entry.Exec) return false;
  return true;
}

// ── 图标解析(freedesktop 图标主题,尽力而为)────────────────────
const _ICON_THEME_ROOTS = [
  path.join(os.homedir(), ".local", "share", "icons"),
  "/usr/local/share/icons",
  "/usr/share/icons",
];
const _ICON_SIZES = ["scalable", "512x512", "256x256", "128x128", "64x64", "48x48"];
const _PIXMAPS = "/usr/share/pixmaps";

const _ICON_MIME = { ".svg": "image/svg+xml", ".png": "image/png" };

/** 把图标文件读成 data URL,供渲染端 <img> 直接显示(避免 file:// 沙箱限制)。
 *  读不了/过大(>256KB)→ null,渲染端回退到占位。 */
function iconDataUrl(iconPath) {
  if (!iconPath) return null;
  const mime = _ICON_MIME[path.extname(iconPath).toLowerCase()];
  if (!mime) return null; // .xpm 等浏览器不认,跳过
  try {
    const stat = fs.statSync(iconPath);
    if (stat.size > 256 * 1024) return null;
    return `data:${mime};base64,${fs.readFileSync(iconPath).toString("base64")}`;
  } catch {
    return null;
  }
}

function resolveIcon(iconName) {
  if (!iconName) return null;
  // 绝对路径直接用
  if (path.isAbsolute(iconName)) {
    return fs.existsSync(iconName) ? iconName : null;
  }
  for (const ext of [".png", ".svg", ".xpm"]) {
    const direct = path.join(_PIXMAPS, iconName + ext);
    if (fs.existsSync(direct)) return direct;
  }
  // hicolor / 各主题 的 <size>/apps/<name>.{svg,png}
  for (const root of _ICON_THEME_ROOTS) {
    for (const theme of ["hicolor", "Adwaita"]) {
      for (const size of _ICON_SIZES) {
        for (const ext of [".svg", ".png"]) {
          const p = path.join(root, theme, size, "apps", iconName + ext);
          if (fs.existsSync(p)) return p;
        }
      }
    }
  }
  return null;
}

// ── 枚举已装应用 ───────────────────────────────────────────────
function listApplications() {
  const seen = new Set();
  const apps = [];
  for (const dir of _appDirs()) {
    let files;
    try {
      files = fs.readdirSync(dir);
    } catch {
      continue; // 目录不存在(非 Linux / 无该目录)→ 跳过
    }
    for (const file of files) {
      if (!file.endsWith(".desktop")) continue;
      const id = file.slice(0, -".desktop".length);
      if (seen.has(id)) continue; // 前面的目录优先(XDG 顺序)
      try {
        const entry = parseDesktopEntry(
          fs.readFileSync(path.join(dir, file), "utf-8"),
        );
        if (!isLaunchableApp(entry)) continue;
        seen.add(id);
        const iconPath = resolveIcon(entry.Icon);
        apps.push({
          id,
          name: entry.Name || id,
          exec: cleanExec(entry.Exec),
          icon: iconPath,
          iconDataUrl: iconDataUrl(iconPath),
          categories: (entry.Categories || "")
            .split(";")
            .filter(Boolean),
          source: "native",
        });
      } catch {
        // 单个 .desktop 坏了不影响其余
      }
    }
  }
  apps.sort((a, b) => a.name.localeCompare(b.name));
  return apps;
}

// ── 启动应用 ───────────────────────────────────────────────────
function launchApplication(exec) {
  const cmd = cleanExec(exec);
  if (!cmd) return { ok: false, error: "empty exec" };
  try {
    // .desktop Exec 设计为被类 shell 启动器执行(gtk-launch 同理)。
    // .desktop 是系统安装的可信文件;detached 让应用独立于 shell 存活。
    const child = spawn("/bin/sh", ["-c", cmd], {
      detached: true,
      stdio: "ignore",
    });
    child.unref();
    return { ok: true, pid: child.pid };
  } catch (err) {
    return { ok: false, error: String(err && err.message) };
  }
}

/** 注册 IPC:渲染进程经 window.octopus.apps.* 调用。 */
function registerSystemShellIpc(ipcMain) {
  ipcMain.handle("apps:list", async () => listApplications());
  ipcMain.handle("apps:launch", async (_e, exec) => launchApplication(exec));
}

module.exports = {
  parseDesktopEntry,
  cleanExec,
  isLaunchableApp,
  resolveIcon,
  iconDataUrl,
  listApplications,
  launchApplication,
  registerSystemShellIpc,
};
