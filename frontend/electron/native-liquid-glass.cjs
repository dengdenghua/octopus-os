const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const MAXIMUM_SURFACES = 8;
const IDENTIFIER_PATTERN = /^[a-z0-9][a-z0-9:_-]{0,63}$/;
const MATERIALS = new Set([
  "ultra-thin",
  "thin",
  "thick",
  "thick-dark",
  "ultra-thick",
]);
const KWIN_BLUR_REGION = "_KDE_NET_WM_BLUR_BEHIND_REGION";
const DEFAULT_XPROP_PATH = "/usr/bin/xprop";
const DEFAULT_GDBUS_PATH = "/usr/bin/gdbus";
const KWIN_LIQUID_GLASS_PATH = "/org/echoos/KWin/LiquidGlass";
const KWIN_LIQUID_GLASS_INTERFACE = "org.echoos.KWin.LiquidGlass1";

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function quarterPoint(value) {
  return Math.round(value * 4) / 4;
}

function validateNativeGlassPayload(payload, viewport) {
  if (!payload || payload.wallpaper !== "orbit") {
    return { ok: false, reason: "unsupported-wallpaper", surfaces: [] };
  }
  if (!Array.isArray(payload.surfaces)) {
    return { ok: false, reason: "invalid-surfaces", surfaces: [] };
  }

  const viewportWidth = Math.max(1, finiteNumber(viewport?.width) ?? 1);
  const viewportHeight = Math.max(1, finiteNumber(viewport?.height) ?? 1);
  const surfaces = [];
  const identifiers = new Set();

  for (const candidate of payload.surfaces.slice(0, MAXIMUM_SURFACES)) {
    if (!candidate || typeof candidate !== "object") continue;
    if (
      typeof candidate.id !== "string" ||
      !IDENTIFIER_PATTERN.test(candidate.id) ||
      identifiers.has(candidate.id)
    ) {
      continue;
    }
    const x = finiteNumber(candidate.x);
    const y = finiteNumber(candidate.y);
    const width = finiteNumber(candidate.width);
    const height = finiteNumber(candidate.height);
    const cornerRadius = finiteNumber(candidate.cornerRadius);
    if (
      x === null ||
      y === null ||
      width === null ||
      height === null ||
      cornerRadius === null ||
      width <= 1 ||
      height <= 1
    ) {
      continue;
    }

    const left = Math.max(0, Math.min(viewportWidth, x));
    const top = Math.max(0, Math.min(viewportHeight, y));
    const right = Math.max(left, Math.min(viewportWidth, x + width));
    const bottom = Math.max(top, Math.min(viewportHeight, y + height));
    const clampedWidth = right - left;
    const clampedHeight = bottom - top;
    if (clampedWidth <= 1 || clampedHeight <= 1) continue;

    identifiers.add(candidate.id);
    surfaces.push({
      id: candidate.id,
      x: quarterPoint(left),
      y: quarterPoint(top),
      width: quarterPoint(clampedWidth),
      height: quarterPoint(clampedHeight),
      cornerRadius: quarterPoint(
        Math.max(
          0,
          Math.min(cornerRadius, clampedWidth / 2, clampedHeight / 2),
        ),
      ),
      material: MATERIALS.has(candidate.material)
        ? candidate.material
        : "thick",
    });
  }

  return { ok: true, surfaces };
}

function getMacOSMajorVersion() {
  if (process.platform !== "darwin") return null;
  try {
    const output = execFileSync("/usr/bin/sw_vers", ["-productVersion"], {
      encoding: "utf8",
      timeout: 2000,
      stdio: ["ignore", "pipe", "ignore"],
    });
    const major = Number.parseInt(output.trim().split(".")[0] || "", 10);
    return Number.isFinite(major) ? major : null;
  } catch {
    return null;
  }
}

function getLinuxGlassCapabilities(options = {}) {
  const platform = options.platform ?? process.platform;
  const environment = options.environment ?? process.env;
  const xpropAvailable =
    options.xpropAvailable ?? fs.existsSync(DEFAULT_XPROP_PATH);
  const gdbusAvailable =
    options.gdbusAvailable ?? fs.existsSync(DEFAULT_GDBUS_PATH);

  if (platform !== "linux") {
    return {
      supported: false,
      reason: "linux-only",
      material: null,
      backend: null,
    };
  }
  if (environment.ECHO_SHELL_MODE !== "desktop") {
    return {
      supported: false,
      reason: "requires-echo-desktop-session",
      material: null,
      backend: null,
    };
  }
  const sessionType = (environment.XDG_SESSION_TYPE || "x11").toLowerCase();
  if (sessionType === "wayland") {
    if (!environment.WAYLAND_DISPLAY) {
      return {
        supported: false,
        reason: "wayland-display-unavailable",
        material: null,
        backend: null,
      };
    }
    if (!gdbusAvailable) {
      return {
        supported: false,
        reason: "gdbus-unavailable",
        material: null,
        backend: null,
      };
    }
    return {
      supported: true,
      reason: null,
      material: "KWinLiquidGlassEffect",
      backend: "kwin-wayland-effect",
    };
  }
  if (sessionType !== "x11") {
    return {
      supported: false,
      reason: "unsupported-linux-session",
      material: null,
      backend: null,
    };
  }
  if (!environment.DISPLAY) {
    return {
      supported: false,
      reason: "x11-display-unavailable",
      material: null,
      backend: null,
    };
  }
  if (!xpropAvailable) {
    return {
      supported: false,
      reason: "xprop-unavailable",
      material: null,
      backend: null,
    };
  }
  return {
    supported: true,
    reason: null,
    material: "KWinBlurRegion+EchoOptics",
    backend: "kwin-x11",
  };
}

function shouldUseTransparentWindow(options = {}) {
  const platform = options.platform ?? process.platform;
  if (platform === "darwin") return (getMacOSMajorVersion() ?? 0) >= 26;
  return getLinuxGlassCapabilities(options).supported;
}

function x11WindowIdentifier(handle) {
  if (!Buffer.isBuffer(handle) || handle.length < 4) {
    throw new Error("invalid-x11-window-handle");
  }
  const identifier = handle.readUInt32LE(0);
  if (!identifier) throw new Error("invalid-x11-window-id");
  return `0x${identifier.toString(16)}`;
}

function kwinBlurRegionValue(surfaces) {
  return surfaces
    .flatMap((surface) => [
      Math.round(surface.x),
      Math.round(surface.y),
      Math.max(1, Math.round(surface.width)),
      Math.max(1, Math.round(surface.height)),
    ])
    .join(",");
}

function installKWinBlurRegion(
  handle,
  surfaces,
  { execFile = execFileSync, xpropPath = DEFAULT_XPROP_PATH } = {},
) {
  const windowId = x11WindowIdentifier(handle);
  execFile(
    xpropPath,
    [
      "-id",
      windowId,
      "-f",
      KWIN_BLUR_REGION,
      "32c",
      "-set",
      KWIN_BLUR_REGION,
      kwinBlurRegionValue(surfaces),
    ],
    { timeout: 2000, stdio: "ignore" },
  );
  return windowId;
}

function removeKWinBlurRegion(
  handle,
  { execFile = execFileSync, xpropPath = DEFAULT_XPROP_PATH } = {},
) {
  execFile(
    xpropPath,
    ["-id", x11WindowIdentifier(handle), "-remove", KWIN_BLUR_REGION],
    { timeout: 2000, stdio: "ignore" },
  );
}

function kwinWaylandEffectPayload(surfaces) {
  return JSON.stringify({
    version: 2,
    surfaces: surfaces.map((surface) => ({
      x: surface.x,
      y: surface.y,
      width: surface.width,
      height: surface.height,
      cornerRadius: surface.cornerRadius,
      material: surface.material,
    })),
  });
}

function syncKWinWaylandEffect(
  surfaces,
  { execFile = execFileSync, gdbusPath = DEFAULT_GDBUS_PATH } = {},
) {
  const output = execFile(
    gdbusPath,
    [
      "call",
      "--session",
      "--dest",
      "org.kde.KWin",
      "--object-path",
      KWIN_LIQUID_GLASS_PATH,
      "--method",
      `${KWIN_LIQUID_GLASS_INTERFACE}.SyncSurfaces`,
      kwinWaylandEffectPayload(surfaces),
    ],
    { encoding: "utf8", timeout: 2000, stdio: ["ignore", "pipe", "ignore"] },
  );
  if (String(output || "").trim() !== "(true,)") {
    throw new Error("kwin-wayland-effect-rejected-surfaces");
  }
}

function clearKWinWaylandEffect({
  execFile = execFileSync,
  gdbusPath = DEFAULT_GDBUS_PATH,
} = {}) {
  execFile(
    gdbusPath,
    [
      "call",
      "--session",
      "--dest",
      "org.kde.KWin",
      "--object-path",
      KWIN_LIQUID_GLASS_PATH,
      "--method",
      `${KWIN_LIQUID_GLASS_INTERFACE}.Clear`,
    ],
    { encoding: "utf8", timeout: 2000, stdio: "ignore" },
  );
}

function addonCandidates(resourcesPath) {
  return [
    resourcesPath
      ? path.join(
          resourcesPath,
          "app.asar.unpacked",
          "native",
          "echo-liquid-glass",
          "build",
          "Release",
          "echo_liquid_glass.node",
        )
      : "",
    path.join(
      __dirname,
      "..",
      "native",
      "echo-liquid-glass",
      "build",
      "Release",
      "echo_liquid_glass.node",
    ),
  ].filter(Boolean);
}

function loadAddon(resourcesPath) {
  const candidate = addonCandidates(resourcesPath).find((filePath) =>
    fs.existsSync(filePath),
  );
  if (!candidate) {
    return { addon: null, error: "native-addon-not-built" };
  }
  try {
    return { addon: require(candidate), error: null };
  } catch (error) {
    return { addon: null, error: error.message };
  }
}

function resolveWallpaperPath({ packaged, resourcesPath }) {
  return packaged
    ? path.join(resourcesPath, "liquid-glass", "wallpaper-day2.jpg")
    : path.join(
        __dirname,
        "..",
        "public",
        "third-party",
        "appletechie-macos",
        "wallpaper-day2.jpg",
      );
}

class NativeLiquidGlassController {
  constructor({
    window,
    packaged,
    resourcesPath,
    createLinuxScene = null,
    environment = process.env,
    platform = process.platform,
    execFile = execFileSync,
    xpropPath = DEFAULT_XPROP_PATH,
    gdbusPath = DEFAULT_GDBUS_PATH,
  }) {
    this.window = window;
    this.packaged = packaged;
    this.resourcesPath = resourcesPath;
    this.active = false;
    this.material = null;
    this.backend = null;
    this.linuxScene = null;
    this.createLinuxScene = createLinuxScene;
    this.environment = environment;
    this.platform = platform;
    this.execFile = execFile;
    this.xpropPath = xpropPath;
    this.gdbusPath = gdbusPath;
    const loaded = platform === "darwin" ? loadAddon(resourcesPath) : {};
    this.addon = loaded.addon;
    this.loadError = loaded.error;
  }

  getCapabilities() {
    if (this.platform === "linux") {
      const capabilities = getLinuxGlassCapabilities({
        platform: this.platform,
        environment: this.environment,
        xpropAvailable: fs.existsSync(this.xpropPath),
        gdbusAvailable: fs.existsSync(this.gdbusPath),
      });
      if (capabilities.supported && !this.createLinuxScene) {
        return {
          supported: false,
          reason: "linux-background-scene-unavailable",
          material: null,
          backend: null,
        };
      }
      return capabilities;
    }
    if (this.platform !== "darwin") {
      return {
        supported: false,
        reason: "unsupported-platform",
        material: null,
        backend: null,
      };
    }
    if ((getMacOSMajorVersion() ?? 0) < 26) {
      return {
        supported: false,
        reason: "requires-macos-26",
        material: null,
        backend: null,
      };
    }
    if (!this.addon) {
      return {
        supported: false,
        reason: this.loadError || "native-addon-unavailable",
        material: null,
        backend: null,
      };
    }
    if (!this.addon.hasLiquidGlass()) {
      return {
        supported: false,
        reason: "system-liquid-glass-unavailable",
        material: "NSVisualEffectView",
        backend: null,
      };
    }
    return {
      supported: true,
      reason: null,
      material: "NSGlassEffectView",
      backend: "appkit",
    };
  }

  async sync(payload) {
    const capabilities = this.getCapabilities();
    if (!capabilities.supported || !this.window || this.window.isDestroyed()) {
      return { active: false, ...capabilities, surfaceCount: 0 };
    }

    const [width, height] = this.window.getContentSize();
    const validated = validateNativeGlassPayload(payload, { width, height });
    if (!validated.ok) {
      this.deactivate();
      return {
        active: false,
        supported: true,
        reason: validated.reason,
        material: capabilities.material,
        backend: capabilities.backend,
        surfaceCount: 0,
      };
    }

    const wallpaperPath = resolveWallpaperPath({
      packaged: this.packaged,
      resourcesPath: this.resourcesPath,
    });
    if (!fs.existsSync(wallpaperPath)) {
      this.deactivate();
      return {
        active: false,
        supported: true,
        reason: "wallpaper-resource-missing",
        material: capabilities.material,
        backend: capabilities.backend,
        surfaceCount: 0,
      };
    }

    try {
      if (capabilities.backend === "kwin-x11") {
        const handle = this.window.getNativeWindowHandle();
        if (!this.linuxScene) {
          this.linuxScene = await this.createLinuxScene(wallpaperPath);
        }
        const windowId = installKWinBlurRegion(handle, validated.surfaces, {
          execFile: this.execFile,
          xpropPath: this.xpropPath,
        });
        this.active = true;
        this.material = capabilities.material;
        this.backend = capabilities.backend;
        this.diagnostics = {
          backend: capabilities.backend,
          windowId,
          wallpaperVisible: Boolean(this.linuxScene?.visible),
          blurRegionCount: validated.surfaces.length,
        };
        return {
          active: true,
          ...capabilities,
          surfaceCount: validated.surfaces.length,
        };
      }
      if (capabilities.backend === "kwin-wayland-effect") {
        if (!this.linuxScene) {
          this.linuxScene = await this.createLinuxScene(wallpaperPath);
        }
        syncKWinWaylandEffect(validated.surfaces, {
          execFile: this.execFile,
          gdbusPath: this.gdbusPath,
        });
        this.active = true;
        this.material = capabilities.material;
        this.backend = capabilities.backend;
        this.diagnostics = {
          backend: capabilities.backend,
          effect: "org.echoos.liquidglass",
          opticsOwner: "kwin",
          wallpaperVisible: Boolean(this.linuxScene?.visible),
          blurRegionCount: validated.surfaces.length,
        };
        return {
          active: true,
          ...capabilities,
          surfaceCount: validated.surfaces.length,
        };
      }
      const handle = this.window.getNativeWindowHandle();
      const installed = this.addon.installScene(handle, wallpaperPath);
      if (!installed?.ok) {
        this.active = false;
        return {
          active: false,
          supported: true,
          reason: "native-scene-install-failed",
          material: capabilities.material,
          backend: capabilities.backend,
          surfaceCount: 0,
        };
      }
      const surfaceCount = this.addon.updateSurfaces(
        handle,
        validated.surfaces,
      );
      this.active = true;
      this.material = installed.material;
      this.backend = capabilities.backend;
      this.diagnostics = installed;
      return {
        active: true,
        supported: true,
        reason: null,
        material: installed.material,
        backend: capabilities.backend,
        surfaceCount,
      };
    } catch (error) {
      this.active = false;
      if (capabilities.backend === "kwin-wayland-effect") {
        try {
          clearKWinWaylandEffect({
            execFile: this.execFile,
            gdbusPath: this.gdbusPath,
          });
        } catch {
          // Preserve the original compositor error.
        }
      }
      if (capabilities.backend?.startsWith("kwin-")) {
        try {
          this.linuxScene?.close?.();
        } catch {
          // Preserve the original compositor error.
        }
        this.linuxScene = null;
      }
      return {
        active: false,
        supported: true,
        reason: error.message,
        material: capabilities.material,
        backend: capabilities.backend,
        surfaceCount: 0,
      };
    }
  }

  deactivate() {
    if (this.platform === "linux") {
      if (this.backend === "kwin-wayland-effect") {
        try {
          clearKWinWaylandEffect({
            execFile: this.execFile,
            gdbusPath: this.gdbusPath,
          });
        } catch {
          // KWin may already have gone away during logout.
        }
      } else if (this.backend === "kwin-x11") {
        if (this.window && !this.window.isDestroyed()) {
          try {
            removeKWinBlurRegion(this.window.getNativeWindowHandle(), {
              execFile: this.execFile,
              xpropPath: this.xpropPath,
            });
          } catch {
            // KWin or the X11 window may already have gone away during logout.
          }
        }
      }
      try {
        this.linuxScene?.close?.();
      } catch {
        // The background window follows the same shutdown lifecycle.
      }
      this.linuxScene = null;
      this.active = false;
      this.material = null;
      this.backend = null;
      return { active: false };
    }
    if (!this.addon || !this.window || this.window.isDestroyed()) {
      this.active = false;
      return { active: false };
    }
    try {
      this.addon.removeScene(this.window.getNativeWindowHandle());
    } catch {
      // Window teardown can invalidate the native handle before the JS object.
    }
    this.active = false;
    this.material = null;
    this.backend = null;
    return { active: false };
  }
}

module.exports = {
  DEFAULT_GDBUS_PATH,
  DEFAULT_XPROP_PATH,
  KWIN_BLUR_REGION,
  KWIN_LIQUID_GLASS_INTERFACE,
  KWIN_LIQUID_GLASS_PATH,
  MAXIMUM_SURFACES,
  NativeLiquidGlassController,
  clearKWinWaylandEffect,
  getLinuxGlassCapabilities,
  getMacOSMajorVersion,
  installKWinBlurRegion,
  kwinBlurRegionValue,
  kwinWaylandEffectPayload,
  removeKWinBlurRegion,
  resolveWallpaperPath,
  shouldUseTransparentWindow,
  syncKWinWaylandEffect,
  validateNativeGlassPayload,
  x11WindowIdentifier,
};
