/**
 * Echo OS native hardware controls.
 *
 * The renderer never supplies command text. Every operation is mapped to a
 * fixed executable and a bounded argument set, and the bridge remains inert
 * outside the native Linux desktop session.
 */
"use strict";

const fs = require("fs");
const { execFile } = require("child_process");

const TOOL_CANDIDATES = Object.freeze({
  nmcli: ["/usr/bin/nmcli", "/bin/nmcli"],
  bluetoothctl: ["/usr/bin/bluetoothctl", "/bin/bluetoothctl"],
  wpctl: ["/usr/bin/wpctl", "/bin/wpctl"],
  brightnessctl: ["/usr/bin/brightnessctl", "/bin/brightnessctl"],
});
const DEFAULT_POWER_SUPPLY_ROOT = "/sys/class/power_supply";

function resolveTool(candidates, existsSync = fs.existsSync) {
  return candidates.find((candidate) => existsSync(candidate)) || null;
}

function resolveSystemControlTools(existsSync = fs.existsSync) {
  return Object.fromEntries(
    Object.entries(TOOL_CANDIDATES).map(([name, candidates]) => [
      name,
      resolveTool(candidates, existsSync),
    ]),
  );
}

function getSystemControlCapabilities({
  platform = process.platform,
  nativeShell = false,
  tools = resolveSystemControlTools(),
  powerSupplyRoot = DEFAULT_POWER_SUPPLY_ROOT,
  existsSync = fs.existsSync,
} = {}) {
  const sessionShell = nativeShell && platform === "linux";
  return {
    nativeShell: sessionShell,
    wifi: sessionShell && Boolean(tools.nmcli),
    bluetooth: sessionShell && Boolean(tools.bluetoothctl),
    audio: sessionShell && Boolean(tools.wpctl),
    display: sessionShell && Boolean(tools.brightnessctl),
    battery: sessionShell && existsSync(powerSupplyRoot),
    reason: !sessionShell
      ? "hardware controls require the native Linux session shell"
      : undefined,
  };
}

function boundedError(value) {
  return String(value || "hardware control failed").trim().slice(0, 512);
}

function runTool(executable, args, execFileImpl = execFile) {
  return new Promise((resolve) => {
    execFileImpl(
      executable,
      args,
      { timeout: 5_000, windowsHide: true, maxBuffer: 64 * 1024 },
      (error, stdout, stderr) => {
        if (!error) {
          resolve({ ok: true, stdout: String(stdout || ""), stderr: "" });
          return;
        }
        resolve({
          ok: false,
          stdout: String(stdout || ""),
          stderr: boundedError(stderr || error.message || error),
        });
      },
    );
  });
}

function parseNmcliTerseLine(line) {
  const fields = [];
  let field = "";
  let escaped = false;
  for (const character of String(line)) {
    if (escaped) {
      field += character;
      escaped = false;
    } else if (character === "\\") {
      escaped = true;
    } else if (character === ":") {
      fields.push(field);
      field = "";
    } else {
      field += character;
    }
  }
  if (escaped) field += "\\";
  fields.push(field);
  return fields;
}

async function readWifiState(nmcliPath, execFileImpl) {
  if (!nmcliPath) return { available: false, enabled: null, connection: null };
  const radio = await runTool(nmcliPath, ["radio", "wifi"], execFileImpl);
  if (!radio.ok) {
    return {
      available: true,
      enabled: null,
      connection: null,
      error: radio.stderr,
    };
  }
  const enabled = radio.stdout.trim().toLowerCase() === "enabled";
  let connection = null;
  if (enabled) {
    const devices = await runTool(
      nmcliPath,
      ["-t", "-f", "TYPE,STATE,CONNECTION", "device", "status"],
      execFileImpl,
    );
    if (devices.ok) {
      for (const line of devices.stdout.split(/\r?\n/)) {
        const [type, state, ...nameParts] = parseNmcliTerseLine(line);
        if (type === "wifi" && state === "connected") {
          connection = nameParts.join(":") || null;
          break;
        }
      }
    }
  }
  return { available: true, enabled, connection };
}

async function readBluetoothState(bluetoothctlPath, execFileImpl) {
  if (!bluetoothctlPath) {
    return { available: false, present: false, enabled: null, controller: null };
  }
  const result = await runTool(bluetoothctlPath, ["show"], execFileImpl);
  if (!result.ok) {
    return {
      available: true,
      present: false,
      enabled: null,
      controller: null,
      error: result.stderr,
    };
  }
  const controller = result.stdout.match(/^Controller\s+\S+\s+(.+)$/m)?.[1]?.trim();
  const powered = result.stdout.match(/^\s*Powered:\s*(yes|no)\s*$/im)?.[1];
  return {
    available: true,
    present: Boolean(powered),
    enabled: powered ? powered.toLowerCase() === "yes" : null,
    controller: controller || null,
  };
}

async function readAudioState(wpctlPath, execFileImpl) {
  if (!wpctlPath) return { available: false, volume: null, muted: null };
  const result = await runTool(
    wpctlPath,
    ["get-volume", "@DEFAULT_AUDIO_SINK@"],
    execFileImpl,
  );
  if (!result.ok) {
    return {
      available: true,
      volume: null,
      muted: null,
      error: result.stderr,
    };
  }
  const rawVolume = Number(result.stdout.match(/Volume:\s*([0-9.]+)/i)?.[1]);
  return {
    available: true,
    volume: Number.isFinite(rawVolume)
      ? Math.max(0, Math.min(100, Math.round(rawVolume * 100)))
      : null,
    muted: /\[MUTED\]/i.test(result.stdout),
  };
}

async function readDisplayState(brightnessctlPath, execFileImpl) {
  if (!brightnessctlPath) return { available: false, brightness: null };
  const result = await runTool(brightnessctlPath, ["-m", "info"], execFileImpl);
  if (!result.ok) {
    return { available: true, brightness: null, error: result.stderr };
  }
  const match = result.stdout.match(/(?:^|,)([0-9]{1,3})%(?:,|$)/m);
  return {
    available: true,
    brightness: match
      ? Math.max(0, Math.min(100, Number.parseInt(match[1], 10)))
      : null,
  };
}

function readBatteryState({
  powerSupplyRoot = DEFAULT_POWER_SUPPLY_ROOT,
  readdirSync = fs.readdirSync,
  readFileSync = fs.readFileSync,
} = {}) {
  let entries;
  try {
    entries = readdirSync(powerSupplyRoot, { withFileTypes: true });
  } catch (error) {
    return {
      available: false,
      present: false,
      percentage: null,
      state: null,
      error: boundedError(error.message || error),
    };
  }
  for (const entry of entries) {
    if (!entry.isDirectory() && !entry.isSymbolicLink()) continue;
    const base = `${powerSupplyRoot}/${entry.name}`;
    let type;
    try {
      type = String(readFileSync(`${base}/type`, "utf8")).trim();
    } catch {
      continue;
    }
    if (type !== "Battery") continue;
    let percentage = null;
    let state = null;
    try {
      const value = Number.parseInt(
        String(readFileSync(`${base}/capacity`, "utf8")).trim(),
        10,
      );
      if (Number.isFinite(value)) percentage = Math.max(0, Math.min(100, value));
    } catch {
      /* capacity is optional on unusual supplies */
    }
    try {
      state = String(readFileSync(`${base}/status`, "utf8")).trim() || null;
    } catch {
      /* status is optional */
    }
    return { available: true, present: true, percentage, state };
  }
  return {
    available: true,
    present: false,
    percentage: null,
    state: null,
  };
}

function emptySystemControlState(capabilities) {
  return {
    nativeShell: capabilities.nativeShell,
    wifi: { available: capabilities.wifi, enabled: null, connection: null },
    bluetooth: {
      available: capabilities.bluetooth,
      present: false,
      enabled: null,
      controller: null,
    },
    audio: { available: capabilities.audio, volume: null, muted: null },
    display: { available: capabilities.display, brightness: null },
    battery: {
      available: capabilities.battery,
      present: false,
      percentage: null,
      state: null,
    },
    reason: capabilities.reason,
  };
}

async function getSystemControlState(options = {}) {
  const tools = options.tools || resolveSystemControlTools(options.existsSync);
  const capabilities = getSystemControlCapabilities({ ...options, tools });
  const state = emptySystemControlState(capabilities);
  if (!capabilities.nativeShell) return state;
  const [wifi, bluetooth, audio, display] = await Promise.all([
    readWifiState(tools.nmcli, options.execFileImpl),
    readBluetoothState(tools.bluetoothctl, options.execFileImpl),
    readAudioState(tools.wpctl, options.execFileImpl),
    readDisplayState(tools.brightnessctl, options.execFileImpl),
  ]);
  return {
    nativeShell: true,
    wifi,
    bluetooth,
    audio,
    display,
    battery: readBatteryState(options),
  };
}

function formatSystemControlsReadyMarker(state) {
  if (!state?.nativeShell) return null;
  const capability = (section) =>
    section?.available === true ? "ready" : "missing";
  return [
    "ECHO_SYSTEM_CONTROLS_READY",
    "provider=linux-native",
    "bridge=ready",
    `wifi=${capability(state.wifi)}`,
    `bluetooth=${capability(state.bluetooth)}`,
    `audio=${capability(state.audio)}`,
    `display=${capability(state.display)}`,
    `battery=${state.battery?.present === true ? "present" : "absent"}`,
  ].join(" ");
}

function requireBoolean(value) {
  if (typeof value !== "boolean") throw new TypeError("enabled must be boolean");
  return value;
}

function requirePercentage(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TypeError("percentage must be a finite number");
  }
  return Math.max(0, Math.min(100, Math.round(value)));
}

function nativeTool(options, name) {
  const tools = options.tools || resolveSystemControlTools(options.existsSync);
  const capabilities = getSystemControlCapabilities({ ...options, tools });
  if (!capabilities.nativeShell) {
    return { error: capabilities.reason, executable: null, tools };
  }
  if (!tools[name]) {
    return { error: `${name} is unavailable`, executable: null, tools };
  }
  return { error: null, executable: tools[name], tools };
}

async function setWifiEnabled(enabled, options = {}) {
  const value = requireBoolean(enabled);
  const selected = nativeTool(options, "nmcli");
  if (!selected.executable) return { ok: false, error: selected.error };
  const result = await runTool(
    selected.executable,
    ["radio", "wifi", value ? "on" : "off"],
    options.execFileImpl,
  );
  return result.ok
    ? {
        ok: true,
        wifi: await readWifiState(selected.executable, options.execFileImpl),
      }
    : { ok: false, error: result.stderr };
}

async function setBluetoothEnabled(enabled, options = {}) {
  const value = requireBoolean(enabled);
  const selected = nativeTool(options, "bluetoothctl");
  if (!selected.executable) return { ok: false, error: selected.error };
  const result = await runTool(
    selected.executable,
    ["power", value ? "on" : "off"],
    options.execFileImpl,
  );
  return result.ok
    ? {
        ok: true,
        bluetooth: await readBluetoothState(
          selected.executable,
          options.execFileImpl,
        ),
      }
    : { ok: false, error: result.stderr };
}

async function setAudioVolume(percentage, options = {}) {
  const value = requirePercentage(percentage);
  const selected = nativeTool(options, "wpctl");
  if (!selected.executable) return { ok: false, error: selected.error };
  const result = await runTool(
    selected.executable,
    ["set-volume", "@DEFAULT_AUDIO_SINK@", `${value}%`],
    options.execFileImpl,
  );
  return result.ok
    ? {
        ok: true,
        audio: await readAudioState(selected.executable, options.execFileImpl),
      }
    : { ok: false, error: result.stderr };
}

async function setDisplayBrightness(percentage, options = {}) {
  const value = requirePercentage(percentage);
  const selected = nativeTool(options, "brightnessctl");
  if (!selected.executable) return { ok: false, error: selected.error };
  const result = await runTool(
    selected.executable,
    ["-q", "set", `${value}%`],
    options.execFileImpl,
  );
  return result.ok
    ? {
        ok: true,
        display: await readDisplayState(
          selected.executable,
          options.execFileImpl,
        ),
      }
    : { ok: false, error: result.stderr };
}

module.exports = {
  TOOL_CANDIDATES,
  formatSystemControlsReadyMarker,
  getSystemControlCapabilities,
  getSystemControlState,
  parseNmcliTerseLine,
  readBatteryState,
  resolveSystemControlTools,
  setAudioVolume,
  setBluetoothEnabled,
  setDisplayBrightness,
  setWifiEnabled,
};
