import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const frontendDirectory = path.resolve(scriptDirectory, "..");
const nativeDirectory = path.join(
  frontendDirectory,
  "native",
  "echo-liquid-glass",
);
const electronVersion = require("electron/package.json").version;
const nodeGypEntry = require.resolve("node-gyp/bin/node-gyp.js");

if (process.platform !== "darwin") {
  console.log("[echo] Native Liquid Glass is macOS-only; skipping build.");
  process.exit(0);
}

const child = spawn(
  process.execPath,
  [
    nodeGypEntry,
    "rebuild",
    `--target=${electronVersion}`,
    `--arch=${process.arch}`,
    "--dist-url=https://electronjs.org/headers",
  ],
  { cwd: nativeDirectory, stdio: "inherit" },
);

child.on("exit", (code, signal) => {
  if (signal) {
    console.error(`[echo] Native Liquid Glass build stopped by ${signal}.`);
    process.exit(1);
  }
  process.exit(code ?? 1);
});
