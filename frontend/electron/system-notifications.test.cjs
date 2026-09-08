/** Echo notification bridge tests; only a private temporary Unix socket is used. */
"use strict";

const assert = require("assert");
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");
const {
  clearNotifications,
  closeNotification,
  getNotificationCapabilities,
  listNotifications,
  resolveNotificationSocket,
} = require("./system-notifications.cjs");

// The production bridge intentionally exposes a Linux Unix-socket contract;
// Windows and macOS reject the native session before touching a socket.
if (process.platform !== "linux") {
  console.log(
    "Echo system notification bridge tests skipped (Linux socket only)",
  );
  process.exit(0);
}

// Keep the fixture in the host's temporary directory so Linux path lengths are
// bounded without assuming a particular /tmp mount.
const root = fs.mkdtempSync(path.join(os.tmpdir(), "echo-note-test-"));
const runtime = path.join(root, "runtime");
const privateDir = path.join(runtime, "echo-os");
const socketPath = path.join(privateDir, "notifications.sock");
fs.mkdirSync(privateDir, { recursive: true, mode: 0o700 });
fs.chmodSync(privateDir, 0o700);

const calls = [];
const server = net.createServer((client) => {
  let input = "";
  client.on("data", (chunk) => {
    input += chunk.toString("utf8");
    if (!input.includes("\n")) return;
    const request = JSON.parse(input.split("\n", 1)[0]);
    calls.push(request);
    const responses = {
      capabilities: {
        ok: true,
        list: true,
        close: true,
        clear: true,
      },
      list: {
        ok: true,
        notifications: [
          {
            id: 7,
            appName: "Mail",
            summary: "New message",
            body: "Hello",
            createdAt: 1000,
            updatedAt: 2000,
          },
          { id: "bad", appName: "Rejected" },
        ],
      },
      close: { ok: true, id: request.id },
      clear: { ok: true, removed: 1 },
    };
    client.end(`${JSON.stringify(responses[request.op])}\n`);
  });
});

server.listen(socketPath, async () => {
  fs.chmodSync(socketPath, 0o600);
  const options = {
    platform: "linux",
    nativeShell: true,
    runtimeDir: runtime,
    socketPath,
  };
  try {
    assert.strictEqual(resolveNotificationSocket(options).ok, true);
    assert.strictEqual(
      resolveNotificationSocket({ ...options, nativeShell: false }).ok,
      false,
    );
    assert.strictEqual(
      resolveNotificationSocket({
        ...options,
        socketPath: `${socketPath}-other`,
      }).ok,
      false,
    );

    const capabilities = await getNotificationCapabilities(options);
    assert.deepStrictEqual(capabilities, {
      nativeShell: true,
      provider: "echo-native",
      list: true,
      close: true,
      clear: true,
    });
    const listed = await listNotifications(options);
    assert.strictEqual(listed.ok, true);
    assert.strictEqual(listed.notifications.length, 1);
    assert.strictEqual(listed.notifications[0].summary, "New message");
    assert.strictEqual((await closeNotification(7, options)).ok, true);
    assert.strictEqual((await closeNotification(";rm", options)).ok, false);
    assert.strictEqual((await clearNotifications(options)).removed, 1);
    assert.deepStrictEqual(
      calls.map((call) => call.op),
      ["capabilities", "list", "close", "clear"],
    );
    console.log("Echo system notification bridge tests passed");
  } finally {
    server.close(() => {
      fs.rmSync(root, { recursive: true, force: true });
    });
  }
});
