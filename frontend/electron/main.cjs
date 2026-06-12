/**
 * Octopus desktop shell — main process.
 *
 * Rebuilt 2026-06-13 against the contract in src/types/electron.d.ts
 * (the original electron/ directory was never committed and was lost).
 * See electron/README.md for what is fully implemented vs. stubbed.
 */
const {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  shell,
  session,
  webContents,
} = require("electron");
const fs = require("fs");
const fsp = require("fs/promises");
const os = require("os");
const path = require("path");

const DEV_URL = process.env.ELECTRON_START_URL || "http://127.0.0.1:3000";
const DESKTOP_DIR = path.join(os.homedir(), "Desktop");

let mainWindow = null;

// ── backend URL ────────────────────────────────────────────────
function resolveBackendBaseURL() {
  return process.env.OCTOPUS_BACKEND_URL || "http://127.0.0.1:8000";
}

// ── first-launch config (packaging/desktop/config.desktop.yaml) ──
function ensureDesktopConfig() {
  try {
    const target = path.join(app.getPath("userData"), "config.desktop.yaml");
    if (fs.existsSync(target)) return;
    const bundled = app.isPackaged
      ? path.join(process.resourcesPath, "config.desktop.yaml")
      : path.join(
          __dirname,
          "..",
          "..",
          "packaging",
          "desktop",
          "config.desktop.yaml",
        );
    if (fs.existsSync(bundled)) fs.copyFileSync(bundled, target);
  } catch (err) {
    console.warn("[octopus] config.desktop.yaml copy failed:", err.message);
  }
}

// ── desktop organizer (the 桌面助手 backend) ───────────────────
const journalFile = () =>
  path.join(app.getPath("userData"), "desktop-organizer-journal.json");

function readJournal() {
  try {
    return JSON.parse(fs.readFileSync(journalFile(), "utf8"));
  } catch {
    return [];
  }
}

function writeJournal(entries) {
  fs.writeFileSync(journalFile(), JSON.stringify(entries, null, 2));
}

async function listDesktopItems() {
  const names = await fsp.readdir(DESKTOP_DIR);
  const items = [];
  for (const name of names) {
    if (name.startsWith(".")) continue;
    const p = path.join(DESKTOP_DIR, name);
    let st;
    try {
      st = await fsp.stat(p);
    } catch {
      continue;
    }
    const ext = path.extname(name).replace(/^\./, "").toLowerCase();
    const kind = st.isDirectory()
      ? name.endsWith(".app")
        ? "app"
        : "folder"
      : "file";
    const subtitle = st.isDirectory()
      ? new Date(st.mtimeMs).toLocaleDateString()
      : `${(st.size / 1024).toFixed(0)} KB · ${new Date(st.mtimeMs).toLocaleDateString()}`;
    items.push({ id: p, name, subtitle, path: p, kind, extension: ext });
  }
  return items;
}

async function moveDesktopItem(srcPath, destDir) {
  const dest = path.isAbsolute(destDir)
    ? destDir
    : path.join(DESKTOP_DIR, destDir);
  await fsp.mkdir(dest, { recursive: true });
  const target = path.join(dest, path.basename(srcPath));
  if (fs.existsSync(target)) return { ok: true, skipped: true };
  await fsp.rename(srcPath, target);
  const journal = readJournal();
  journal.push({ from: srcPath, to: target, ts: Date.now() });
  writeJournal(journal);
  return { ok: true, destPath: target };
}

function sampleSystemInfo() {
  const cpus = os.cpus();
  const load = os.loadavg()[0];
  const total = os.totalmem();
  const free = os.freemem();
  return {
    ok: true,
    cpu: {
      model: cpus[0]?.model || "unknown",
      cores: cpus.length,
      usage: Math.min(100, Math.round((load / Math.max(1, cpus.length)) * 100)),
    },
    memory: {
      total,
      used: total - free,
      percent: Math.round(((total - free) / total) * 100),
    },
    uptime: os.uptime(),
    platform: process.platform,
  };
}

// ── browser bridge (embedded <webview> automation) ─────────────
function wc(webContentsId) {
  const target = webContents.fromId(webContentsId);
  if (!target || target.isDestroyed())
    throw new Error(`webContents ${webContentsId} not found`);
  return target;
}

const js = {
  click: (sel) => `(() => {
    const el = document.querySelector(${JSON.stringify(sel)});
    if (!el) return { ok: false, error: "selector not found" };
    el.scrollIntoView({ block: "center" });
    el.click();
    return { ok: true, tag: el.tagName.toLowerCase(), text: (el.textContent || "").trim().slice(0, 120) };
  })()`,
  type: (sel, text, clear) => `(() => {
    const el = document.querySelector(${JSON.stringify(sel)});
    if (!el) return { ok: false, error: "selector not found" };
    el.focus();
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement : HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(proto.prototype, "value")?.set;
    const next = ${JSON.stringify(!!clear)} ? ${JSON.stringify(text)} : (el.value || "") + ${JSON.stringify(text)};
    if (setter) setter.call(el, next); else el.value = next;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, value: el.value };
  })()`,
  hover: (sel) => `(() => {
    const el = document.querySelector(${JSON.stringify(sel)});
    if (!el) return { ok: false, error: "selector not found" };
    el.scrollIntoView({ block: "center" });
    for (const t of ["pointerover", "mouseover", "mouseenter"])
      el.dispatchEvent(new MouseEvent(t, { bubbles: true }));
    return { ok: true };
  })()`,
  scroll: (opts) => `(() => {
    const o = ${JSON.stringify(opts)};
    const el = o.selector ? document.querySelector(o.selector) : null;
    if (o.selector && !el) return { ok: false, error: "selector not found" };
    (el || window).scrollBy({ left: o.deltaX || 0, top: o.deltaY || 0, behavior: "instant" });
    return { ok: true, y: el ? el.scrollTop : window.scrollY };
  })()`,
  waitFor: (sel, timeout) => `new Promise((resolve) => {
    const t0 = Date.now();
    const tick = () => {
      if (document.querySelector(${JSON.stringify(sel)}))
        return resolve({ ok: true, elapsed: Date.now() - t0 });
      if (Date.now() - t0 > ${Number(timeout) || 10000})
        return resolve({ ok: false, error: "timeout", elapsed: Date.now() - t0 });
      setTimeout(tick, 100);
    };
    tick();
  })`,
  extractText: `(() => {
    const text = document.body ? document.body.innerText : "";
    const max = 200000;
    return {
      url: location.href,
      title: document.title,
      text: text.slice(0, max),
      truncated: text.length > max,
      textLength: text.length,
    };
  })()`,
};

const DEVICE_PRESETS = {
  mobile: {
    width: 390,
    height: 844,
    dpr: 3,
    mobile: true,
    ua: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
  },
  tablet: {
    width: 820,
    height: 1180,
    dpr: 2,
    mobile: true,
    ua: "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
  },
};

async function getAriaTree(target, opts) {
  const attachedHere = !target.debugger.isAttached();
  if (attachedHere) target.debugger.attach("1.3");
  try {
    const { nodes } = await target.debugger.sendCommand(
      "Accessibility.getFullAXTree",
      {
        max_depth: opts?.maxDepth,
      },
    );
    return {
      ok: true,
      nodes: nodes.map((n) => ({
        id: String(n.nodeId),
        role: n.role?.value ?? "",
        name: n.name?.value ?? "",
        value: n.value?.value ?? "",
        backendDOMNodeId: n.backendDOMNodeId,
        childIds: (n.childIds || []).map(String),
        ignored: !!n.ignored,
      })),
    };
  } finally {
    if (attachedHere) {
      try {
        target.debugger.detach();
      } catch {
        /* already detached */
      }
    }
  }
}

// ── downloads ──────────────────────────────────────────────────
const downloads = new Map();
let downloadSeq = 0;

function trackDownloads(sess) {
  sess.on("will-download", (_event, item) => {
    const id = `dl-${++downloadSeq}`;
    const send = (state) => {
      downloads.set(id, { item, path: item.getSavePath() });
      mainWindow?.webContents.send("browser:download-event", {
        id,
        filename: item.getFilename(),
        url: item.getURL(),
        state,
        receivedBytes: item.getReceivedBytes(),
        totalBytes: item.getTotalBytes(),
        path: item.getSavePath(),
      });
    };
    send("started");
    item.on("updated", (_e, state) => send(state));
    item.once("done", (_e, state) => send(state));
  });
}

// ── extensions ─────────────────────────────────────────────────
const extensionsFile = () =>
  path.join(app.getPath("userData"), "extensions.json");

function readExtensionRegistry() {
  try {
    return JSON.parse(fs.readFileSync(extensionsFile(), "utf8"));
  } catch {
    return [];
  }
}

function writeExtensionRegistry(list) {
  fs.writeFileSync(extensionsFile(), JSON.stringify(list, null, 2));
}

async function loadEnabledExtensions() {
  for (const ext of readExtensionRegistry()) {
    if (!ext.enabled) continue;
    try {
      await session.defaultSession.loadExtension(ext.path);
    } catch (err) {
      console.warn(
        `[octopus] extension ${ext.name} failed to load:`,
        err.message,
      );
    }
  }
}

// ── IPC wiring ─────────────────────────────────────────────────
function registerIpc() {
  const handle = (channel, fn) =>
    ipcMain.handle(channel, async (_event, ...args) => fn(...args));

  // app
  handle("app:getVersion", () => app.getVersion());
  handle("app:openExternal", (url) => shell.openExternal(url));
  handle("app:getPlatform", () => process.platform);

  // dialog
  handle("dialog:open", (options) =>
    dialog.showOpenDialog(mainWindow, options),
  );
  handle("dialog:save", (options) =>
    dialog.showSaveDialog(mainWindow, options),
  );

  // backend
  handle("backend:getBaseURL", () => resolveBackendBaseURL());
  ipcMain.on("backend:getBaseURLSync", (event) => {
    event.returnValue = resolveBackendBaseURL();
  });
  handle("backend:restart", () => ({
    ok: false,
    reason:
      "backend runs externally in dev (pnpm dev:full); packaged restart not reimplemented yet",
  }));

  // window
  handle("window:setDeviceBounds", (mode, width, height) => {
    if (!mainWindow) return { ok: false, reason: "no window" };
    if (width && height)
      mainWindow.setContentSize(Math.round(width), Math.round(height));
    return { ok: true, mode };
  });
  handle("window:setTitleBarOverlay", (opts) => {
    try {
      if (process.platform === "win32" && mainWindow?.setTitleBarOverlay) {
        mainWindow.setTitleBarOverlay({
          color: opts.color,
          symbolColor: opts.symbolColor,
          height: 36,
        });
      }
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("window:setMousePassthrough", (enabled) => {
    if (!mainWindow) return { ok: false, error: "no window" };
    mainWindow.setIgnoreMouseEvents(!!enabled, { forward: true });
    return { ok: true, enabled: !!enabled };
  });
  handle("window:openDevTools", () => {
    mainWindow?.webContents.openDevTools({ mode: "detach" });
    return { ok: true };
  });

  // desktop organizer
  handle("desktop:listItems", async () => {
    try {
      return {
        ok: true,
        desktopPath: DESKTOP_DIR,
        items: await listDesktopItems(),
      };
    } catch (err) {
      return { ok: false, items: [], error: err.message };
    }
  });
  handle("desktop:openItem", async (p) => {
    const error = await shell.openPath(p);
    return error ? { ok: false, error } : { ok: true };
  });
  handle("desktop:moveItem", async (srcPath, destDir) => {
    try {
      return await moveDesktopItem(srcPath, destDir);
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("desktop:moveItemsBatch", async (items) => {
    let moved = 0;
    let skipped = 0;
    try {
      for (const { srcPath, category } of items) {
        const res = await moveDesktopItem(srcPath, category);
        if (res.skipped) skipped += 1;
        else moved += 1;
      }
      return { ok: true, moved, skipped };
    } catch (err) {
      return { ok: false, moved, skipped, error: err.message };
    }
  });
  handle("desktop:undoMoves", async () => {
    const journal = readJournal();
    let undone = 0;
    for (const entry of journal.reverse()) {
      try {
        if (fs.existsSync(entry.to) && !fs.existsSync(entry.from)) {
          await fsp.rename(entry.to, entry.from);
          undone += 1;
        }
      } catch {
        /* keep undoing the rest */
      }
    }
    writeJournal([]);
    return { ok: true, undone };
  });
  handle("desktop:getSystemInfo", () => sampleSystemInfo());
  handle("desktop:installContextMenu", () => ({
    ok: false,
    error:
      "right-click menu integration was lost with the original shell; not reimplemented yet (Windows-only feature)",
  }));
  handle("desktop:removeContextMenu", () => ({ ok: true }));

  // embedded browser
  handle("browser:setDevice", async (id, mode) => {
    const target = wc(id);
    const preset = DEVICE_PRESETS[mode];
    if (!preset) {
      target.disableDeviceEmulation();
      target.setUserAgent(target.session.getUserAgent());
      return { ok: true, mode };
    }
    target.enableDeviceEmulation({
      screenPosition: "mobile",
      screenSize: { width: preset.width, height: preset.height },
      viewSize: { width: preset.width, height: preset.height },
      deviceScaleFactor: preset.dpr,
    });
    target.setUserAgent(preset.ua);
    return { ok: true, mode };
  });
  handle("browser:executeJS", (id, code) =>
    wc(id).executeJavaScript(code, true),
  );
  handle("browser:reload", (id) => wc(id).reload());
  handle("browser:goBack", (id) => wc(id).navigationHistory.goBack());
  handle("browser:goForward", (id) => wc(id).navigationHistory.goForward());
  handle("browser:openDevTools", (id) => {
    try {
      wc(id).openDevTools({ mode: "detach" });
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("browser:capturePage", async (id) => {
    const image = await wc(id).capturePage();
    const size = image.getSize();
    return {
      dataUrl: image.toDataURL(),
      width: size.width,
      height: size.height,
    };
  });
  handle("browser:extractText", (id) =>
    wc(id).executeJavaScript(js.extractText, true),
  );
  handle("browser:click", (id, sel) =>
    wc(id).executeJavaScript(js.click(sel), true),
  );
  handle("browser:type", (id, sel, text, opts) =>
    wc(id).executeJavaScript(js.type(sel, text, opts?.clear), true),
  );
  handle("browser:hover", (id, sel) =>
    wc(id).executeJavaScript(js.hover(sel), true),
  );
  handle("browser:scroll", (id, opts) =>
    wc(id).executeJavaScript(js.scroll(opts), true),
  );
  handle("browser:waitFor", (id, sel, timeout) =>
    wc(id).executeJavaScript(js.waitFor(sel, timeout), true),
  );
  handle("browser:pressKey", (id, key) => {
    const target = wc(id);
    target.focus();
    target.sendInputEvent({ type: "keyDown", keyCode: key });
    if (key.length === 1) target.sendInputEvent({ type: "char", keyCode: key });
    target.sendInputEvent({ type: "keyUp", keyCode: key });
    return { ok: true, key };
  });
  handle("browser:getAriaTree", async (id, opts) => {
    try {
      return await getAriaTree(wc(id), opts);
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("browser:getCurrentUrl", (id) => {
    const target = wc(id);
    return { ok: true, url: target.getURL(), title: target.getTitle() };
  });
  handle("browser:clearSiteData", async (id) => {
    try {
      const target = wc(id);
      const origin = new URL(target.getURL()).origin;
      await target.session.clearStorageData({ origin });
      return { ok: true, origin };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("browser:showDownloadInFolder", (id) => {
    const dl = downloads.get(id);
    if (!dl?.path) return { ok: false, error: "download not found" };
    shell.showItemInFolder(dl.path);
    return { ok: true };
  });
  handle("browser:openDownload", async (id) => {
    const dl = downloads.get(id);
    if (!dl?.path) return { ok: false, error: "download not found" };
    const error = await shell.openPath(dl.path);
    return error ? { ok: false, error } : { ok: true };
  });

  // extensions
  handle("extensions:list", () => ({
    ok: true,
    extensions: readExtensionRegistry(),
  }));
  handle("extensions:installFromFolder", async () => {
    const picked = await dialog.showOpenDialog(mainWindow, {
      properties: ["openDirectory"],
    });
    if (picked.canceled || !picked.filePaths[0])
      return { ok: false, canceled: true };
    const dir = picked.filePaths[0];
    try {
      const manifest = JSON.parse(
        await fsp.readFile(path.join(dir, "manifest.json"), "utf8"),
      );
      const loaded = await session.defaultSession.loadExtension(dir);
      const info = {
        id: loaded.id,
        name: manifest.name || path.basename(dir),
        version: manifest.version || "0.0.0",
        description: manifest.description || "",
        manifestVersion: manifest.manifest_version || 3,
        path: dir,
        enabled: true,
        installedAt: new Date().toISOString(),
      };
      const registry = readExtensionRegistry().filter((e) => e.path !== dir);
      registry.push(info);
      writeExtensionRegistry(registry);
      return { ok: true, extension: info };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("extensions:setEnabled", async (id, enabled) => {
    const registry = readExtensionRegistry();
    const ext = registry.find((e) => e.id === id);
    if (!ext) return { ok: false, error: "extension not found" };
    try {
      if (enabled) {
        const loaded = await session.defaultSession.loadExtension(ext.path);
        ext.id = loaded.id;
      } else {
        session.defaultSession.removeExtension(id);
      }
      ext.enabled = enabled;
      writeExtensionRegistry(registry);
      return { ok: true, extension: ext };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("extensions:remove", (id) => {
    try {
      session.defaultSession.removeExtension(id);
    } catch {
      /* may not be loaded */
    }
    writeExtensionRegistry(readExtensionRegistry().filter((e) => e.id !== id));
    return { ok: true };
  });

  // active-tab bridge (fire-and-forget from renderer)
  ipcMain.on("bridge:setActiveTab", () => {
    /* reserved for tab-aware main-process features */
  });
}

// ── window ─────────────────────────────────────────────────────
function createMainWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    show: false,
    ...(process.platform === "win32"
      ? { titleBarStyle: "hidden", titleBarOverlay: { height: 36 } }
      : {}),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: true,
    },
  });

  win.once("ready-to-show", () => win.show());
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  if (app.isPackaged) {
    win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  } else {
    win.loadURL(DEV_URL);
    win.webContents.on("did-fail-load", (_e, code, desc) => {
      console.error(
        `[octopus] dev server not reachable (${code} ${desc}); retrying in 1s…`,
      );
      setTimeout(() => win.loadURL(DEV_URL), 1000);
    });
  }
  return win;
}

function watchDesktop() {
  try {
    let timer = null;
    fs.watch(DESKTOP_DIR, () => {
      clearTimeout(timer);
      timer = setTimeout(
        () => mainWindow?.webContents.send("desktop:items-changed"),
        500,
      );
    });
  } catch (err) {
    console.warn("[octopus] desktop watch unavailable:", err.message);
  }
}

// ── lifecycle ──────────────────────────────────────────────────
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.on("open-url", (_event, url) => {
    mainWindow?.webContents.send("app:deep-link", url);
  });

  app.on("web-contents-created", (_event, contents) => {
    if (contents.getType() !== "webview") return;
    contents.setWindowOpenHandler(({ url }) => {
      mainWindow?.webContents.send("browser:open-tab", url);
      return { action: "deny" };
    });
    contents.on("render-process-gone", (_e, details) => {
      mainWindow?.webContents.send("browser:tab-crashed", {
        webContentsId: contents.id,
        reason: details.reason,
      });
    });
  });

  app.whenReady().then(async () => {
    if (!app.isPackaged) app.setAsDefaultProtocolClient("octopus");
    ensureDesktopConfig();
    registerIpc();
    trackDownloads(session.defaultSession);
    await loadEnabledExtensions();
    mainWindow = createMainWindow();
    watchDesktop();

    if (process.env.OCTOPUS_SMOKE === "1") {
      mainWindow.webContents.once("did-finish-load", () => {
        console.log("SMOKE OK:", mainWindow.webContents.getURL());
        setTimeout(() => app.quit(), 500);
      });
      setTimeout(() => {
        console.error("SMOKE TIMEOUT");
        app.exit(1);
      }, 30000);
    }

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0)
        mainWindow = createMainWindow();
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
}
