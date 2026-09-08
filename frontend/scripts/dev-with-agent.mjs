import { spawn, execFile } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline";
import { promisify } from "node:util";
import { setTimeout as delay } from "node:timers/promises";
import { resolveDevConfiguration } from "./dev-appliance-config.mjs";
import {
  assertPortFree,
  devPorts,
  probeDevService,
  waitForDevService,
} from "./dev-service-health.mjs";

const frontendRoot = fileURLToPath(new URL("..", import.meta.url));
const ports = devPorts();
const require = createRequire(import.meta.url);

if (process.argv.includes("--status")) {
  const [backend, desktop] = await Promise.all([
    probeDevService(ports.backend),
    probeDevService(ports.frontend),
  ]);
  console.info(JSON.stringify({ backend, desktop }, null, 2));
  process.exitCode =
    backend.ready &&
    desktop.ready &&
    !backend.restartRequired &&
    !desktop.restartRequired
      ? 0
      : 1;
} else {
  await run();
}

async function run() {
  resolveDevConfiguration(resolve(frontendRoot, ".."));
  const children = new Set();
  const expectedExits = new WeakSet();
  let stopping = false;
  let restarting = false;
  let controller = new AbortController();
  let input;

  function launch(script, args = []) {
    const child = spawn(process.execPath, [script, ...args], {
      cwd: frontendRoot,
      env: {
        ...process.env,
        GATEWAY_PORT: String(ports.backend),
        FRONTEND_PORT: String(ports.frontend),
        ECHO_INTERNAL_GATEWAY_BASE_URL: `http://127.0.0.1:${ports.backend}`,
      },
      stdio: ["ignore", "inherit", "inherit"],
      windowsHide: true,
      detached: process.platform !== "win32",
    });
    children.add(child);
    child.once("error", (error) => void fail(error));
    return child;
  }

  async function stopChildren() {
    controller.abort();
    await Promise.all(
      [...children].map(async (child) => {
        expectedExits.add(child);
        // Only process trees started by this launcher, never a PID read from disk or a port owner.
        if (child.exitCode !== null || child.signalCode !== null || !child.pid)
          return;
        if (process.platform === "win32") {
          await promisify(execFile)(
            "taskkill.exe",
            ["/PID", String(child.pid), "/T", "/F"],
            { windowsHide: true },
          ).catch(() => {});
          // taskkill may return before the process handle and listening socket close.
          const deadline = Date.now() + 5000;
          while (
            child.exitCode === null &&
            child.signalCode === null &&
            Date.now() < deadline
          )
            await delay(50);
          if (child.exitCode === null && child.signalCode === null)
            throw new Error("An owned service did not stop; restart cancelled");
        } else {
          try {
            process.kill(-child.pid, "SIGTERM");
          } catch {
            return;
          }
          await new Promise((done) => {
            const timer = setTimeout(() => {
              try {
                process.kill(-child.pid, "SIGKILL");
              } catch {
                /* already exited */
              }
              done();
            }, 3000);
            child.once("exit", () => {
              clearTimeout(timer);
              done();
            });
          });
        }
      }),
    );
    children.clear();
    if (restarting && !stopping) {
      const deadline = Date.now() + 5000;
      while (true) {
        try {
          await Promise.all([
            assertPortFree(ports.backend),
            assertPortFree(ports.frontend),
          ]);
          break;
        } catch (error) {
          if (Date.now() >= deadline) throw error;
          await delay(100);
        }
      }
    }
  }

  async function fail(error) {
    if (stopping) return;
    stopping = true;
    console.error(`[echo] ${error.message}`);
    input?.close();
    process.stdin.destroy();
    await stopChildren();
    process.exitCode = 1;
  }

  async function start() {
    if (stopping) return;
    await Promise.all([
      assertPortFree(ports.backend),
      assertPortFree(ports.frontend),
    ]);
    if (stopping) return;
    controller = new AbortController();
    const watch = (child, name) =>
      child.once("exit", (code) => {
        if (!stopping && !expectedExits.has(child))
          void fail(
            new Error(
              `${name} exited (${code ?? "signal"}). Restart with pnpm dev:with-agent.`,
            ),
          );
      });
    console.info(
      "[echo] Starting backend; checking runtime and device session…",
    );
    watch(
      launch(resolve(frontendRoot, "scripts/dev-appliance-backend.mjs")),
      "Backend",
    );
    await waitForDevService(ports.backend, { signal: controller.signal });
    const vite = resolve(
      dirname(require.resolve("vite/package.json")),
      "bin/vite.js",
    );
    watch(
      launch(vite, [
        "--host",
        "127.0.0.1",
        "--port",
        String(ports.frontend),
        "--strictPort",
      ]),
      "Desktop",
    );
    await waitForDevService(ports.frontend, {
      timeoutMs: 30000,
      signal: controller.signal,
    });
    console.info(
      `[echo] Ready: http://localhost:${ports.frontend}/#/desktop · runtime, session and proxy verified`,
    );
    console.info(
      "[echo] Enter rs to restart, quit or Ctrl+C to stop; pnpm dev:status to check.",
    );
  }

  async function shutdown() {
    if (stopping) return;
    stopping = true;
    input?.close();
    process.stdin.destroy();
    await stopChildren();
  }
  for (const signal of ["SIGINT", "SIGTERM"]) process.once(signal, shutdown);

  try {
    await start();
    if (stopping) return;
    input = createInterface({ input: process.stdin });
    input.on("line", async (line) => {
      if (line.trim() === "quit") {
        await shutdown();
        return;
      }
      if (line.trim() !== "rs" || restarting || stopping) return;
      restarting = true;
      try {
        console.info("[echo] Restarting owned services…");
        await stopChildren();
        await start();
      } catch (error) {
        await fail(error);
      } finally {
        restarting = false;
      }
    });
  } catch (error) {
    if (!stopping) await fail(error);
  }
}
