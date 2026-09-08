import test from "node:test";
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { once } from "node:events";
import {
  mkdtempSync,
  mkdirSync,
  copyFileSync,
  writeFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import net from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import { assertPortFree } from "./dev-service-health.mjs";

async function freePort() {
  const server = net.createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const port = server.address().port;
  await new Promise((done) => server.close(done));
  return port;
}

test(
  "launcher verifies startup, replaces both owned services on rs, and cleans up on quit",
  { timeout: 25000 },
  async (t) => {
    const root = mkdtempSync(join(tmpdir(), "echo-launcher-test-"));
    const frontend = join(root, "frontend");
    const scripts = join(frontend, "scripts");
    const vite = join(frontend, "node_modules/vite");
    mkdirSync(scripts, { recursive: true });
    mkdirSync(join(vite, "bin"), { recursive: true });
    mkdirSync(join(root, ".venv/Scripts"), { recursive: true });
    writeFileSync(join(root, "config.example.yaml"), "{}");
    writeFileSync(join(root, ".venv/Scripts/python.exe"), "fixture");
    for (const file of [
      "dev-with-agent.mjs",
      "dev-service-health.mjs",
      "dev-appliance-config.mjs",
    ])
      copyFileSync(new URL(file, import.meta.url), join(scripts, file));
    writeFileSync(
      join(vite, "package.json"),
      JSON.stringify({ name: "vite", type: "module" }),
    );
    const service = `import http from 'node:http';
const port = Number(process.env[process.argv[1].includes('vite.js') ? 'FRONTEND_PORT' : 'GATEWAY_PORT']);
http.createServer((req,res)=>{
 res.setHeader('Content-Type','application/json');
 res.end(JSON.stringify(req.url === '/api/health'
 ? {status:'ok',runtime:{name:'echo-agent-runtime'}}
 : {authRequired:true,authenticated:false,role:null}));
}).listen(port,'127.0.0.1',()=>console.log('fixture-port='+port+' pid='+process.pid));`;
    writeFileSync(join(scripts, "dev-appliance-backend.mjs"), service);
    writeFileSync(join(vite, "bin/vite.js"), service);
    const backend = await freePort();
    let desktop = await freePort();
    while (desktop === backend) desktop = await freePort();
    const child = spawn(
      process.execPath,
      [join(scripts, "dev-with-agent.mjs")],
      {
        env: {
          ...process.env,
          GATEWAY_PORT: String(backend),
          FRONTEND_PORT: String(desktop),
          ECHO_AGENT_CONFIG: join(root, "config.example.yaml"),
          ECHO_AGENT_PYTHON: join(root, ".venv/Scripts/python.exe"),
          ECHO_DEV_DATA_DIR: join(root, "data"),
          ECHO_PACKAGED_CODEX_VERSION: "0.153.4",
        },
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      },
    );
    let output = "";
    child.stdout.on("data", (data) => {
      output += data;
    });
    child.stderr.on("data", (data) => {
      output += data;
    });
    const exited = once(child, "exit");
    t.after(async () => {
      if (child.exitCode === null && child.signalCode === null) {
        if (process.platform === "win32") {
          try {
            execFileSync(
              "taskkill.exe",
              ["/PID", String(child.pid), "/T", "/F"],
              { windowsHide: true, stdio: "ignore" },
            );
          } catch {
            /* exited */
          }
        } else child.stdin.write("quit\n");
        await Promise.race([exited, delay(3000)]);
      }
      rmSync(root, { recursive: true, force: true });
    });
    async function ready(count) {
      const deadline = Date.now() + 8000;
      while (
        (output.match(/\[echo\] Ready:/g) || []).length < count &&
        Date.now() < deadline &&
        child.exitCode === null
      )
        await delay(50);
      assert.equal(
        (output.match(/\[echo\] Ready:/g) || []).length,
        count,
        output,
      );
    }
    await ready(1);
    child.stdin.write("rs\n");
    await ready(2);
    const pids = [...output.matchAll(/fixture-port=\d+ pid=(\d+)/g)].map(
      (match) => +match[1],
    );
    assert.equal(pids.length, 4, output);
    assert.equal(new Set(pids).size, 4);
    for (const pid of pids.slice(0, 2))
      assert.throws(() => process.kill(pid, 0));
    child.stdin.write("quit\n");
    const result = await Promise.race([exited, delay(5000).then(() => null)]);
    assert.ok(result, `Launcher did not exit after quit:\n${output}`);
    const [code] = result;
    assert.equal(code, 0, output);
    for (const pid of pids) assert.throws(() => process.kill(pid, 0));
    await Promise.all([assertPortFree(backend), assertPortFree(desktop)]);
  },
);
