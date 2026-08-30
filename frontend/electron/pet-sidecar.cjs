/**
 * Echo Pet Sidecar manager.
 *
 * Owns the lifetime of the Godot-based desktop pet runtime and forwards
 * agent state events to it over UDP (port 8765). The Godot sidecar already
 * listens on that port (see pet-sidecar/scripts/IPCServer.gd) and maps the
 * incoming events onto the pet's behavior FSM.
 *
 * Resolution order for the Godot binary:
 *   1. env ECHO_GODOT_BIN          (explicit, dev/preview)
 *   2. packaged resources: resources/pet-sidecar/<godot-bin>
 *   3. nothing → pet stays disabled (honest no-op), never crashes
 *
 * Resolution order for the project path (dev only):
 *   - env ECHO_PET_PROJECT, else <repo>/pet-sidecar
 *
 * Set ECHO_PET_DISABLED=1 to hard-disable pet launch.
 */

const { spawn } = require("child_process");
const dgram = require("dgram");
const fs = require("fs");
const path = require("path");

const PET_IPC_HOST = "127.0.0.1";
const PET_IPC_PORT = 8765;

let child = null;
let socket = null;
let enabled = true;
let windowSyncTimer = null;

// Writer shared by event + world messages; coalesced to 10Hz.
let sendQueue = [];
let sendTimer = null;

function _enqueue(payload) {
  sendQueue.push(payload);
  if (sendTimer) return;
  sendTimer = setTimeout(() => {
    sendTimer = null;
    if (sendQueue.length === 0) return;
    const latest = sendQueue[sendQueue.length - 1];
    sendQueue.length = 0;
    _flush(latest);
  }, 100);
}

function _flush(payload) {
  if (!enabled || !isPetRunning() || !ensureSocket()) return;
  socket.send(payload, 0, payload.length, PET_IPC_PORT, PET_IPC_HOST, () => {
    /* fire-and-forget */
  });
}

function petProjectPath() {
  if (process.env.ECHO_PET_PROJECT) return process.env.ECHO_PET_PROJECT;
  return path.join(__dirname, "..", "..", "pet-sidecar");
}

function packagedPetDir() {
  return path.join(process.resourcesPath, "pet-sidecar");
}

function resolveGodot() {
  if (process.env.ECHO_GODOT_BIN) return process.env.ECHO_GODOT_BIN;
  if (appIsPackaged()) {
    // Packaged: the built sidecar executable shipped inside resources.
    const dir = packagedPetDir();
    const candidates = {
      darwin: ["EchoPet", "EchoPet.app/Contents/MacOS/EchoPet"],
      win32: ["EchoPet.exe"],
      linux: ["EchoPet", "echo-pet"],
    };
    const names = candidates[process.platform] || candidates.linux;
    for (const n of names) {
      const p = path.join(dir, n);
      if (fs.existsSync(p)) return p;
    }
  }
  return null;
}

function appIsPackaged() {
  return !!(process.env.NODE_ENV === "production" || require("electron")?.app?.isPackaged);
}

function resolveProject() {
  if (appIsPackaged()) return packagedPetDir();
  const dir = petProjectPath();
  if (fs.existsSync(path.join(dir, "project.godot"))) return dir;
  return null;
}

function ensureSocket() {
  if (socket) return true;
  try {
    socket = dgram.createSocket("udp4");
    socket.unref();
    return true;
  } catch {
    return false;
  }
}

function resolveStartCommand() {
  const godot = resolveGodot();
  const project = resolveProject();
  if (!godot && !project) {
    console.warn("[pet] no Godot binary or project found; pet disabled");
    return null;
  }
  if (godot) {
    // Dev: run the project with the selected Godot binary.
    return { cmd: godot, args: project ? ["--path", project] : [] };
  }
  // Packaged fallback: project alone (custom launcher).
  return { cmd: project, args: [] };
}

/**
 * Start the Godot pet sidecar process. Idempotent.
 * @returns {{ ok: boolean; reason?: string }}
 */
function startPet() {
  if (!enabled) return { ok: false, reason: "pet disabled" };
  if (child && !child.killed) return { ok: true, alreadyRunning: true };

  const cmd = resolveStartCommand();
  if (!cmd) return { ok: false, reason: "Godot pet not resolvable" };

  try {
    child = spawn(cmd.cmd, cmd.args, {
      cwd: resolveProject() || undefined,
      stdio: "ignore",
      detached: false,
    });
    child.on("error", (err) => {
      console.warn("[pet] godot spawn error:", err.message);
    });
    child.on("exit", (code, signal) => {
      console.log(`[pet] sidecar exited (code=${code}, signal=${signal})`);
      child = null;
      stopWindowSync();
    });
    console.log(`[pet] started: ${cmd.cmd} ${cmd.args.join(" ")}`);
    startWindowSync();
    return { ok: true };
  } catch (err) {
    console.warn("[pet] failed to start sidecar:", err.message);
    return { ok: false, reason: err.message };
  }
}

/**
 * Stop the Godot pet sidecar process. Idempotent.
 */
function stopPet() {
  if (child && !child.killed) {
    try {
      child.kill();
    } catch {
      /* ignore */
    }
    child = null;
  }
  stopWindowSync();
}

function isPetRunning() {
  return !!child && !child.killed;
}

/**
 * Send an agent state event to the Godot sidecar over UDP.
 * @param {string} type  e.g. "agent.thinking" | "agent.waiting_user"
 * @param {Record<string, unknown>} [extra]
 */
function sendPetEvent(type, extra) {
  if (!isPetRunning()) return false;
  const payload = Buffer.from(
    JSON.stringify(Object.assign({ type }, extra || {})) + "\n",
    "utf8",
  );
  _enqueue(payload);
  return true;
}

// ── window sync (obstacle avoidance) ───────────────────────────
// Collects the current BrowserWindow bounds (screen coords) and pushes them
// to the Godot sidecar as a `world.windows` message so it can steer around
// them. Runs on a fixed interval while the pet is up; the Godot sidecar
// filters out the pet's own fullscreen transparent window by size.
function _collectWindowRects() {
  const { BrowserWindow, screen } = require("electron");
  const rects = [];
  const screenSize = screen.getPrimaryDisplay().size;
  for (const win of BrowserWindow.getAllWindows()) {
    if (win.isDestroyed() || win.isMinimized()) continue;
    const b = win.getBounds();
    const w = Math.round(b.width);
    const h = Math.round(b.height);
    // Skip the pet's own fullscreen transparent canvas (matches screen size).
    if (w >= screenSize.width && h >= screenSize.height) continue;
    rects.push({ x: Math.round(b.x), y: Math.round(b.y), w, h });
  }
  return rects;
}

function startWindowSync() {
  if (windowSyncTimer) return;
  const push = () => {
    if (!isPetRunning()) return;
    const payload = Buffer.from(
      JSON.stringify({ type: "world.windows", windows: _collectWindowRects() }) + "\n",
      "utf8",
    );
    _enqueue(payload);
  };
  push();
  windowSyncTimer = setInterval(push, 2000);
}

function stopWindowSync() {
  if (windowSyncTimer) {
    clearInterval(windowSyncTimer);
    windowSyncTimer = null;
  }
}

// Convenience helpers mapped to the agent lifecycle.
function petEventForAgentState(state) {
  switch (state) {
    case "idle":
      return { type: "agent.idle" };
    case "thinking":
      return { type: "agent.thinking" };
    case "working":
      return { type: "agent.working", intensity: 0.6 };
    case "waiting_user":
      return { type: "agent.waiting_user" };
    case "success":
      return { type: "agent.success" };
    case "error":
      return { type: "agent.error" };
    default:
      return null;
  }
}

// Extended state semantics (mirrors runtime/pet/pet_state_map.py):
// emotion / tired / presence give the pet a richer inner life.
function clamp01(value, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(0, Math.min(1, n));
}

function sendEmotion(emotion, intensity = 1.0) {
  const valid = ["happy", "sad", "curious", "surprised", "concerned"];
  if (!valid.includes(emotion)) return false;
  return sendPetEvent("agent.emotion", {
    emotion,
    intensity: clamp01(intensity, 1.0),
  });
}

function sendTired(intensity = 0.5) {
  return sendPetEvent("agent.tired", { intensity: clamp01(intensity, 0.5) });
}

function sendPresence(online, deviceId = "") {
  return sendPetEvent("agent.presence", { online: !!online, device_id: deviceId });
}

function shutdown() {
  stopPet();
  stopWindowSync();
  if (sendTimer) {
    clearTimeout(sendTimer);
    sendTimer = null;
  }
  if (socket) {
    try {
      socket.close();
    } catch {
      /* ignore */
    }
    socket = null;
  }
}

module.exports = {
  startPet,
  stopPet,
  isPetRunning,
  sendPetEvent,
  sendEmotion,
  sendTired,
  sendPresence,
  petEventForAgentState,
  shutdown,
};
