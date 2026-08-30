const assert = require("node:assert/strict");
const test = require("node:test");

const {
  KWIN_BLUR_REGION,
  KWIN_LIQUID_GLASS_INTERFACE,
  KWIN_LIQUID_GLASS_PATH,
  MAXIMUM_SURFACES,
  NativeLiquidGlassController,
  clearKWinWaylandEffect,
  getLinuxGlassCapabilities,
  installKWinBlurRegion,
  kwinBlurRegionValue,
  kwinWaylandEffectPayload,
  syncKWinWaylandEffect,
  validateNativeGlassPayload,
  x11WindowIdentifier,
} = require("./native-liquid-glass.cjs");

test("native glass accepts only the Echo Orbit scene", () => {
  assert.deepEqual(
    validateNativeGlassPayload(
      { wallpaper: "sunset", surfaces: [] },
      { width: 1000, height: 700 },
    ),
    { ok: false, reason: "unsupported-wallpaper", surfaces: [] },
  );
});

test("native glass clamps geometry and material to a bounded contract", () => {
  const result = validateNativeGlassPayload(
    {
      wallpaper: "orbit",
      surfaces: [
        {
          id: "surface:1",
          x: -10.1,
          y: 20.12,
          width: 240.17,
          height: 160.13,
          cornerRadius: 200,
          material: "not-a-material",
        },
      ],
    },
    { width: 200, height: 100 },
  );

  assert.equal(result.ok, true);
  assert.deepEqual(result.surfaces, [
    {
      id: "surface:1",
      x: 0,
      y: 20,
      width: 200,
      height: 80,
      cornerRadius: 40,
      material: "thick",
    },
  ]);
});

test("native glass rejects duplicates and caps the native view count", () => {
  const candidates = Array.from(
    { length: MAXIMUM_SURFACES + 4 },
    (_, index) => ({
      id: index === 1 ? "surface:0" : `surface:${index}`,
      x: 8,
      y: 8,
      width: 80,
      height: 60,
      cornerRadius: 18,
      material: "thin",
    }),
  );
  const result = validateNativeGlassPayload(
    { wallpaper: "orbit", surfaces: candidates },
    { width: 1000, height: 700 },
  );

  assert.equal(result.ok, true);
  assert.equal(result.surfaces.length, MAXIMUM_SURFACES - 1);
  assert.equal(
    new Set(result.surfaces.map((surface) => surface.id)).size,
    result.surfaces.length,
  );
});

test("Linux native glass supports the Echo KWin X11 and Wayland routes", () => {
  const x11 = getLinuxGlassCapabilities({
    platform: "linux",
    environment: {
      ECHO_SHELL_MODE: "desktop",
      XDG_SESSION_TYPE: "x11",
      DISPLAY: ":0",
    },
    xpropAvailable: true,
  });
  assert.deepEqual(x11, {
    supported: true,
    reason: null,
    material: "KWinBlurRegion+EchoOptics",
    backend: "kwin-x11",
  });

  const wayland = getLinuxGlassCapabilities({
    platform: "linux",
    environment: {
      ECHO_SHELL_MODE: "desktop",
      XDG_SESSION_TYPE: "wayland",
      WAYLAND_DISPLAY: "wayland-0",
    },
    gdbusAvailable: true,
  });
  assert.deepEqual(wayland, {
    supported: true,
    reason: null,
    material: "KWinLiquidGlassEffect",
    backend: "kwin-wayland-effect",
  });

  assert.equal(
    getLinuxGlassCapabilities({
      platform: "linux",
      environment: {
        ECHO_SHELL_MODE: "desktop",
        XDG_SESSION_TYPE: "wayland",
      },
      gdbusAvailable: true,
    }).reason,
    "wayland-display-unavailable",
  );
});

test("KWin Wayland receives the bounded native material contract", () => {
  const calls = [];
  const surfaces = [
    {
      id: "surface:1",
      x: 10.25,
      y: 20.75,
      width: 101.5,
      height: 52.25,
      cornerRadius: 18,
      material: "thick-dark",
    },
  ];

  syncKWinWaylandEffect(surfaces, {
    gdbusPath: "/test/gdbus",
    execFile: (...args) => {
      calls.push(args);
      return "(true,)\n";
    },
  });
  clearKWinWaylandEffect({
    gdbusPath: "/test/gdbus",
    execFile: (...args) => calls.push(args),
  });

  assert.deepEqual(JSON.parse(kwinWaylandEffectPayload(surfaces)), {
    version: 2,
    surfaces: [
      {
        x: 10.25,
        y: 20.75,
        width: 101.5,
        height: 52.25,
        cornerRadius: 18,
        material: "thick-dark",
      },
    ],
  });
  assert.deepEqual(calls[0][0], "/test/gdbus");
  assert.deepEqual(calls[0][1].slice(0, 8), [
    "call",
    "--session",
    "--dest",
    "org.kde.KWin",
    "--object-path",
    KWIN_LIQUID_GLASS_PATH,
    "--method",
    `${KWIN_LIQUID_GLASS_INTERFACE}.SyncSurfaces`,
  ]);
  assert.deepEqual(calls[1][1], [
    "call",
    "--session",
    "--dest",
    "org.kde.KWin",
    "--object-path",
    KWIN_LIQUID_GLASS_PATH,
    "--method",
    `${KWIN_LIQUID_GLASS_INTERFACE}.Clear`,
  ]);
});

test("KWin blur receives only the validated rectangular surface region", () => {
  const calls = [];
  const handle = Buffer.alloc(8);
  handle.writeUInt32LE(0x2a00007, 0);
  const surfaces = [
    { x: 10.25, y: 20.75, width: 101.4, height: 52.2 },
    { x: 300, y: 640, width: 520, height: 84 },
  ];
  const windowId = installKWinBlurRegion(handle, surfaces, {
    xpropPath: "/test/xprop",
    execFile: (...args) => calls.push(args),
  });

  assert.equal(windowId, "0x2a00007");
  assert.equal(x11WindowIdentifier(handle), windowId);
  assert.equal(kwinBlurRegionValue(surfaces), "10,21,101,52,300,640,520,84");
  assert.deepEqual(calls[0][0], "/test/xprop");
  assert.deepEqual(calls[0][1], [
    "-id",
    windowId,
    "-f",
    KWIN_BLUR_REGION,
    "32c",
    "-set",
    KWIN_BLUR_REGION,
    "10,21,101,52,300,640,520,84",
  ]);
});

test("Linux controller installs and removes one compositor scene", async () => {
  const calls = [];
  let sceneClosed = false;
  const handle = Buffer.alloc(8);
  handle.writeUInt32LE(0x400017, 0);
  const controller = new NativeLiquidGlassController({
    window: {
      getContentSize: () => [1440, 900],
      getNativeWindowHandle: () => handle,
      isDestroyed: () => false,
    },
    packaged: false,
    resourcesPath: "/unused",
    platform: "linux",
    environment: {
      ECHO_SHELL_MODE: "desktop",
      XDG_SESSION_TYPE: "x11",
      DISPLAY: ":0",
    },
    xpropPath: "/usr/bin/true",
    execFile: (...args) => calls.push(args),
    createLinuxScene: async () => ({
      visible: true,
      close: () => {
        sceneClosed = true;
      },
    }),
  });

  const result = await controller.sync({
    wallpaper: "orbit",
    surfaces: [
      {
        id: "surface:1",
        x: 24,
        y: 28,
        width: 260,
        height: 170,
        cornerRadius: 28,
        material: "thick",
      },
    ],
  });
  assert.equal(result.active, true);
  assert.equal(result.backend, "kwin-x11");
  assert.equal(result.surfaceCount, 1);

  controller.deactivate();
  assert.equal(sceneClosed, true);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1][1], [
    "-id",
    "0x400017",
    "-remove",
    KWIN_BLUR_REGION,
  ]);
});

test("Wayland controller syncs and clears the native KWin effect", async () => {
  const calls = [];
  let sceneClosed = false;
  const controller = new NativeLiquidGlassController({
    window: {
      getContentSize: () => [1440, 900],
      isDestroyed: () => false,
    },
    packaged: false,
    resourcesPath: "/unused",
    platform: "linux",
    environment: {
      ECHO_SHELL_MODE: "desktop",
      XDG_SESSION_TYPE: "wayland",
      WAYLAND_DISPLAY: "wayland-0",
    },
    gdbusPath: "/usr/bin/true",
    execFile: (...args) => {
      calls.push(args);
      return args[1].at(-2)?.endsWith(".SyncSurfaces") ? "(true,)\n" : "";
    },
    createLinuxScene: async () => ({
      visible: true,
      close: () => {
        sceneClosed = true;
      },
    }),
  });

  const result = await controller.sync({
    wallpaper: "orbit",
    surfaces: [
      {
        id: "surface:1",
        x: 24,
        y: 28,
        width: 260,
        height: 170,
        cornerRadius: 28,
        material: "thick",
      },
    ],
  });
  assert.equal(result.active, true);
  assert.equal(result.backend, "kwin-wayland-effect");
  assert.equal(result.surfaceCount, 1);
  assert.equal(controller.diagnostics.opticsOwner, "kwin");

  controller.deactivate();
  assert.equal(sceneClosed, true);
  assert.equal(calls.length, 2);
  assert.equal(
    calls[0][1].at(-2),
    `${KWIN_LIQUID_GLASS_INTERFACE}.SyncSurfaces`,
  );
  assert.equal(calls[1][1].at(-1), `${KWIN_LIQUID_GLASS_INTERFACE}.Clear`);
});
