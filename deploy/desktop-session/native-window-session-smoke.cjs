/**
 * Live window-manager contract probe for the target-C desktop session.
 *
 * This is intentionally separate from the mocked Node unit suite: it talks to
 * the production bridge while KWin owns a real X11 or Wayland window. The
 * surrounding shell script creates that window and passes its id through the
 * environment.
 */
"use strict";

const assert = require("node:assert/strict");
const { execFile } = require("node:child_process");
const path = require("node:path");
const { promisify } = require("node:util");

const execFileAsync = promisify(execFile);
const repoRoot = path.resolve(__dirname, "../..");
const bridge = require(
  path.join(repoRoot, "frontend/electron/native-windows.cjs"),
);

const expectedProvider =
  process.env.XDG_SESSION_TYPE === "wayland" ? "kwin-wayland" : "ewmh-x11";
const windowId =
  expectedProvider === "kwin-wayland"
    ? bridge.normalizeKWinWindowId(process.env.ECHO_SMOKE_NATIVE_WINDOW_ID)
    : bridge.normalizeWindowId(process.env.ECHO_SMOKE_NATIVE_WINDOW_ID);
const skipClose = process.env.ECHO_SMOKE_SKIP_CLOSE === "1";

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitFor(probe, description, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const value = await probe();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await sleep(100);
  }
  const detail = lastError ? `: ${lastError.message}` : "";
  throw new Error(`timed out waiting for ${description}${detail}`);
}

async function listWindows() {
  const result = await bridge.listNativeWindows({ nativeShell: true });
  assert.equal(result.ok, true, result.error || "window list failed");
  assert.equal(result.provider, expectedProvider);
  return result.windows;
}

async function isHidden() {
  if (expectedProvider === "kwin-wayland") {
    return Boolean(
      (await listWindows()).find((candidate) => candidate.id === windowId)
        ?.minimized,
    );
  }
  const { stdout } = await execFileAsync("/usr/bin/xprop", [
    "-id",
    windowId,
    "_NET_WM_STATE",
  ]);
  return stdout.includes("_NET_WM_STATE_HIDDEN");
}

async function runAction(action) {
  const result = await bridge.runNativeWindowAction(action, windowId, {
    nativeShell: true,
  });
  assert.equal(result.ok, true, result.error || `${action} failed`);
  assert.equal(result.provider, expectedProvider);
}

void (async () => {
  const capabilities = bridge.getNativeWindowCapabilities({
    nativeShell: true,
  });
  assert.deepEqual(
    {
      provider: capabilities.provider,
      list: capabilities.list,
      focus: capabilities.focus,
      minimize: capabilities.minimize,
      close: capabilities.close,
    },
    {
      provider: expectedProvider,
      list: true,
      focus: true,
      minimize: true,
      close: true,
    },
  );

  const nativeWindow = await waitFor(
    async () =>
      (await listWindows()).find((candidate) => candidate.id === windowId),
    "production bridge to enumerate the native window",
  );
  assert.match(nativeWindow.title, /Echo (Native Bridge|Wayland Bridge) Smoke/);
  console.log(`  ✓ enumerated ${windowId}: ${nativeWindow.title}`);

  await runAction("focus");
  await waitFor(
    async () =>
      (await listWindows()).find(
        (candidate) => candidate.id === windowId && candidate.active,
      ),
    "native window to become active",
  );
  console.log("  ✓ focused the real KWin window");

  await runAction("minimize");
  await waitFor(isHidden, "KWin to expose _NET_WM_STATE_HIDDEN");
  console.log("  ✓ minimized the real KWin window");

  await runAction("focus");
  await waitFor(async () => !(await isHidden()), "native window to restore");
  await waitFor(
    async () =>
      (await listWindows()).find(
        (candidate) => candidate.id === windowId && candidate.active,
      ),
    "restored native window to become active",
  );
  console.log("  ✓ restored and re-focused the real KWin window");

  if (skipClose) {
    console.log("  ✓ left close action to the compositor-native UUID provider");
  } else {
    await runAction("close");
    await waitFor(
      async () =>
        !(await listWindows()).some((candidate) => candidate.id === windowId),
      "native window to close",
    );
    console.log("  ✓ closed the real KWin window");
  }
  console.log(`Live native-window ${expectedProvider} lifecycle OK`);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
