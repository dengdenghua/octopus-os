#!/usr/bin/env node
/**
 * Dev helper: detect a Godot 4 binary and launch `electron:dev` with
 * ECHO_GODOT_BIN set, so the desktop pet sidecar is started automatically.
 *
 * Detection order (first match wins):
 *   1. Existing ECHO_GODOT_BIN env
 *   2. Common install paths (macOS .app, Windows, Linux snap/flatpak/brew)
 *   3. `godot` / `godot4` on PATH
 *
 * If nothing is found, it still launches Electron (pet stays disabled) and
 * prints a hint. Use `--no-pet` to skip probing entirely.
 *
 * Usage: node scripts/dev-electron-with-pet.mjs
 *        node scripts/dev-electron-with-pet.mjs --no-pet
 */

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

const CANDIDATES = (() => {
  const home = homedir();
  const list = [
    "/Applications/Godot.app/Contents/MacOS/Godot",
    "/Applications/Godot_mono.app/Contents/MacOS/Godot",
    "C:\\Program Files\\Godot\\Godot.exe",
    "C:\\Program Files (x86)\\Godot\\Godot.exe",
    "/snap/bin/godot",
    "/var/lib/flatpak/exports/bin/org.godotengine.Godot",
    "/usr/local/bin/godot",
    "/opt/homebrew/bin/godot",
    "/usr/bin/godot",
    // 临时目录里解压的 Godot(本机 /tmp/godot43 即此情形)。
    "/tmp/godot43/Godot.app/Contents/MacOS/Godot",
  ];
  return list.map((p) => (p.startsWith("~") ? path.join(home, p.slice(1)) : p));
})();

function findOnPath(names) {
  for (const exe of names) {
    try {
      const res = spawnSync("which", [exe], { stdio: "ignore" });
      if (res.status === 0 && res.stdout) {
        return res.stdout.toString().trim();
      }
    } catch {
      /* keep looking */
    }
  }
  return null;
}

function detectGodot() {
  if (process.env.ECHO_GODOT_BIN) return process.env.ECHO_GODOT_BIN;
  for (const p of CANDIDATES) {
    if (existsSync(p)) return p;
  }
  return findOnPath(["godot", "godot4"]);
}

const noPet = process.argv.includes("--no-pet");
const godot = noPet ? null : detectGodot();

if (godot) {
  process.env.ECHO_GODOT_BIN = godot;
  console.log(`[dev-pet] Godot detected: ${godot}`);
  console.log("[dev-pet] Desktop pet will be launched with electron:dev.");
} else if (!noPet) {
  console.warn(
    "[dev-pet] No Godot binary found. Pet will stay disabled. " +
      "Install Godot 4 or set ECHO_GODOT_BIN, or pass --no-pet to silence this.",
  );
}

// Re-run the same args (minus the flags we consumed) via the package manager.
import { spawn } from "node:child_process";
const runner = process.platform === "win32" ? "cmd" : "sh";
const runnerArgs =
  process.platform === "win32"
    ? ["/c", "pnpm", "electron:dev"]
    : ["-c", "pnpm electron:dev"];
const child = spawn(runner, runnerArgs, { stdio: "inherit", env: process.env });

child.on("error", (err) => {
  console.error("[dev-pet] failed to launch electron:dev:", err.message);
  process.exit(1);
});

child.on("exit", (code) => process.exit(code ?? 0));
