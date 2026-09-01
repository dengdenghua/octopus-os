/**
 * Echo OS · 原生 shell(A 路线)的"系统手"层。
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
const { execFile } = require("child_process");

const LAUNCH_TIMEOUT_MS = 10_000;
const LAUNCH_MAX_BUFFER_BYTES = 64 * 1024;

// ── XDG 应用目录(freedesktop)──────────────────────────────────
function appDirs() {
  const dataHome =
    process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share");
  const dataDirs = process.env.XDG_DATA_DIRS || "/usr/local/share:/usr/share";
  return [dataHome, ...dataDirs.split(":").filter(Boolean)].map((directory) =>
    path.join(directory, "applications"),
  );
}

function iconThemeRoots() {
  const dataHome =
    process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share");
  const dataDirs = process.env.XDG_DATA_DIRS || "/usr/local/share:/usr/share";
  return [...new Set([dataHome, ...dataDirs.split(":").filter(Boolean)])].map(
    (directory) => path.join(directory, "icons"),
  );
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

/** 一个 entry 是否是应该显示在启动器里的应用。 */
function isLaunchableApp(entry) {
  if (!entry) return false;
  if ((entry.Type || "Application") !== "Application") return false;
  if (entry.NoDisplay === "true" || entry.Hidden === "true") return false;
  if (!entry.Exec) return false;
  return true;
}

// ── 图标解析(freedesktop 图标主题,尽力而为)────────────────────
const _ICON_SIZES = [
  "scalable",
  "512x512",
  "256x256",
  "128x128",
  "64x64",
  "48x48",
];
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

function resolveIcon(iconName, roots = iconThemeRoots()) {
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
  for (const root of roots) {
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

function isFlatpakExport(desktopFile) {
  return desktopFile.includes(
    `${path.sep}flatpak${path.sep}exports${path.sep}share${path.sep}applications${path.sep}`,
  );
}

// ── 枚举已装应用 ───────────────────────────────────────────────
function listApplicationRecords(
  directories = appDirs(),
  iconRoots = iconThemeRoots(),
) {
  const seen = new Set();
  const apps = [];
  for (const dir of directories) {
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
      // Hidden=true in a higher-priority XDG directory masks the same desktop
      // id below it. Mark it seen before launchability filtering.
      seen.add(id);
      try {
        const desktopFile = path.join(dir, file);
        const entry = parseDesktopEntry(fs.readFileSync(desktopFile, "utf-8"));
        if (!isLaunchableApp(entry)) continue;
        const iconPath = resolveIcon(entry.Icon, iconRoots);
        apps.push({
          id,
          name: entry.Name || id,
          desktopFile,
          startupWmClass: entry.StartupWMClass || null,
          icon: iconPath,
          iconDataUrl: iconDataUrl(iconPath),
          categories: (entry.Categories || "").split(";").filter(Boolean),
          source: isFlatpakExport(desktopFile) ? "flatpak" : "native",
        });
      } catch {
        // 单个 .desktop 坏了不影响其余
      }
    }
  }
  apps.sort((a, b) => a.name.localeCompare(b.name));
  return apps;
}

function listApplications() {
  return listApplicationRecords().map(
    ({ desktopFile: _desktopFile, ...app }) => app,
  );
}

// ── 启动应用 ───────────────────────────────────────────────────
function isValidApplicationId(appId) {
  return (
    typeof appId === "string" &&
    /^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$/.test(appId)
  );
}

function launchErrorMessage(error, stderr) {
  if (error && error.code === "ENOENT") {
    return "system application launcher is missing";
  }
  if (error && error.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER") {
    return "system application launcher output exceeded its limit";
  }
  if (error && error.killed) {
    return "system application launcher timed out";
  }
  const detail = String(stderr || "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 256);
  return detail
    ? `application launch failed: ${detail}`
    : "application launch failed";
}

function launchDesktopFile(desktopFile, execFileImpl = execFile) {
  if (!path.isAbsolute(desktopFile) || !desktopFile.endsWith(".desktop")) {
    return Promise.resolve({ ok: false, error: "invalid desktop file" });
  }
  return new Promise((resolve) => {
    try {
      // GLib interprets the freedesktop Exec field and its field codes without
      // passing untrusted desktop-file text through a command shell. Success is
      // reported only after the gio helper exits zero; asynchronous spawn and
      // non-zero failures must reach the renderer instead of looking successful.
      execFileImpl(
        "/usr/bin/gio",
        ["launch", desktopFile],
        {
          timeout: LAUNCH_TIMEOUT_MS,
          windowsHide: true,
          maxBuffer: LAUNCH_MAX_BUFFER_BYTES,
        },
        (error, _stdout, stderr) => {
          if (error) {
            resolve({ ok: false, error: launchErrorMessage(error, stderr) });
            return;
          }
          resolve({ ok: true });
        },
      );
    } catch (error) {
      resolve({ ok: false, error: launchErrorMessage(error, "") });
    }
  });
}

/** Renderer may select an enumerated application, but may not supply a shell
 * command. This keeps the privileged IPC boundary narrower than `Exec` text. */
async function launchApplicationById(appId, options = {}) {
  const id = String(appId || "");
  if (!isValidApplicationId(id)) {
    return { ok: false, error: "invalid native application id" };
  }
  const app = listApplicationRecords(
    options.appDirs || appDirs(),
    options.iconRoots || iconThemeRoots(),
  ).find((candidate) => candidate.id === id);
  if (!app) return { ok: false, error: "native application not found" };
  return launchDesktopFile(app.desktopFile, options.execFile || execFile);
}

/** 注册 IPC:渲染进程经 window.echo.apps.* 调用。 */
function registerSystemShellIpc(ipcMain) {
  ipcMain.handle("apps:list", async () => listApplications());
  ipcMain.handle("apps:launch", async (_e, appId) =>
    launchApplicationById(appId),
  );
}

module.exports = {
  parseDesktopEntry,
  isLaunchableApp,
  isValidApplicationId,
  appDirs,
  iconThemeRoots,
  resolveIcon,
  iconDataUrl,
  listApplicationRecords,
  listApplications,
  launchDesktopFile,
  launchApplicationById,
  registerSystemShellIpc,
};
