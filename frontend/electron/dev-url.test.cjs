const assert = require("node:assert/strict");

const { DEFAULT_DEV_URL, resolveDevURL } = require("./dev-url.cjs");

assert.equal(DEFAULT_DEV_URL, "http://localhost:3000");
assert.equal(resolveDevURL({}), "http://localhost:3000");
assert.equal(
  resolveDevURL({ ELECTRON_START_URL: " http://echo.local:3000 " }),
  "http://echo.local:3000",
);

console.log("electron dev URL tests passed");
