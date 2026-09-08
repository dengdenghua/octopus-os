import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import {
  assertPortFree,
  devPorts,
  probeDevService,
  waitForDevService,
} from "./dev-service-health.mjs";

const healthy = { status: "ok", runtime: { name: "echo-agent-runtime" } };
const signedOut = { authRequired: true, authenticated: false, role: null };
async function fixture(t, handler) {
  const server = http.createServer(handler);
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  return server.address().port;
}
function json(res, value, status = 200) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(value));
}

test("validates distinct configured ports", () => {
  assert.deepEqual(devPorts({}), { backend: 8000, frontend: 3000 });
  for (const value of ["0", "65536", "3000x", "-1"])
    assert.throws(() => devPorts({ FRONTEND_PORT: value }));
  assert.throws(() => devPorts({ FRONTEND_PORT: "8000" }));
});
test("a signed-out device is ready, without carrying account details into logs", async (t) => {
  const port = await fixture(t, (req, res) => {
    assert.equal(req.headers.cookie, undefined);
    json(
      res,
      req.url === "/api/health"
        ? { ...healthy, private: "DO-NOT-LOG" }
        : signedOut,
    );
  });
  assert.deepEqual(await probeDevService(port), {
    ready: true,
    restartRequired: false,
  });
});
test("an occupied port is refused and its server stays alive", async (t) => {
  const port = await fixture(t, (req, res) =>
    json(res, req.url === "/api/health" ? healthy : signedOut),
  );
  await assert.rejects(assertPortFree(port), /occupied/);
  assert.equal((await probeDevService(port)).ready, true);
});
test("healthy runtime alone cannot conceal a broken session route", async (t) => {
  const port = await fixture(t, (req, res) =>
    json(res, req.url === "/api/health" ? healthy : { authenticated: true }),
  );
  assert.equal((await probeDevService(port)).ready, false);
});
test("redirects and unrelated services are not accepted", async (t) => {
  let redirectHits = 0;
  const port = await fixture(t, (req, res) => {
    if (req.url === "/elsewhere") redirectHits++;
    res.writeHead(302, { Location: "/elsewhere" });
    res.end();
  });
  assert.equal((await probeDevService(port)).ready, false);
  assert.equal(redirectHits, 0);
  const other = await fixture(t, (_req, res) => json(res, { status: "ok" }));
  assert.equal((await probeDevService(other)).ready, false);
});
test("startup waits for both endpoints and recovers when service becomes ready", async (t) => {
  let attempts = 0;
  const port = await fixture(t, (req, res) => {
    if (req.url === "/api/health") return json(res, healthy);
    attempts++;
    json(res, attempts < 3 ? {} : signedOut, attempts < 3 ? 503 : 200);
  });
  assert.equal(
    (await waitForDevService(port, { intervalMs: 5, timeoutMs: 1000 })).ready,
    true,
  );
  assert.equal(attempts, 3);
});
test("hung responses and startup failure are bounded", async (t) => {
  const port = await fixture(t, () => {});
  assert.equal((await probeDevService(port, { timeoutMs: 30 })).ready, false);
  await assert.rejects(
    waitForDevService(port, { timeoutMs: 40, intervalMs: 5 }),
    /did not become ready/,
  );
});
test("source drift is reported and cannot pass startup verification", async (t) => {
  const port = await fixture(t, (req, res) =>
    json(
      res,
      req.url === "/api/health"
        ? { ...healthy, lifecycle: { restartRequired: true } }
        : signedOut,
    ),
  );
  assert.equal((await probeDevService(port)).restartRequired, true);
  await assert.rejects(
    waitForDevService(port, { timeoutMs: 40, intervalMs: 5 }),
    /source changes require restart/,
  );
});
test("cancelled startup stops waiting", async (t) => {
  const port = await fixture(t, (_req, res) => json(res, {}, 503));
  const controller = new AbortController();
  const pending = waitForDevService(port, {
    signal: controller.signal,
    intervalMs: 5000,
  });
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
});
