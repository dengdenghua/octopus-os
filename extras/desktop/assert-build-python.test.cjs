const assert = require("node:assert/strict");
const test = require("node:test");

const {
  normalizeArchitecture,
  validateBuildPythonIdentity,
} = require("./assert-build-python.cjs");

test("normalizes release runner architecture names", () => {
  assert.equal(normalizeArchitecture("AMD64"), "x64");
  assert.equal(normalizeArchitecture("x86_64"), "x64");
  assert.equal(normalizeArchitecture("aarch64"), "arm64");
});

test("accepts only the locked Python platform identity", () => {
  assert.doesNotThrow(() =>
    validateBuildPythonIdentity(
      { version: "3.11.9", platform: "darwin", machine: "arm64" },
      { platform: "darwin", architecture: "arm64" },
    ),
  );
  assert.throws(
    () =>
      validateBuildPythonIdentity(
        { version: "3.12.13", platform: "darwin", machine: "arm64" },
        { platform: "darwin", architecture: "arm64" },
      ),
    /expected 3\.11\.9/,
  );
  assert.throws(
    () =>
      validateBuildPythonIdentity(
        { version: "3.11.9", platform: "linux", machine: "x86_64" },
        { platform: "darwin", architecture: "arm64" },
      ),
    /expected darwin/,
  );
});
