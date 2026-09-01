import assert from "node:assert/strict";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { seedDevCustomModels } from "./dev-appliance-model-seed.mjs";

function withTemporaryDirectory(run) {
  const directory = mkdtempSync(join(tmpdir(), "echo-model-seed-"));
  try {
    run(directory);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

test("seeds custom models once with private permissions", () => {
  withTemporaryDirectory((directory) => {
    const sourcePath = join(directory, "agent", "custom_models.json");
    const targetPath = join(directory, "appliance", "custom_models.json");
    mkdirSync(join(directory, "agent"), { recursive: true });
    writeFileSync(sourcePath, '{"deepseek": {"model": "deepseek-v4"}}');

    assert.equal(seedDevCustomModels({ sourcePath, targetPath }), true);
    assert.equal(
      readFileSync(targetPath, "utf8"),
      readFileSync(sourcePath, "utf8"),
    );
    assert.equal(statSync(targetPath).mode & 0o777, 0o600);
  });
});

test("does not overwrite appliance models that already exist", () => {
  withTemporaryDirectory((directory) => {
    const sourcePath = join(directory, "agent", "custom_models.json");
    const targetPath = join(directory, "appliance", "custom_models.json");
    mkdirSync(join(directory, "agent"), { recursive: true });
    mkdirSync(join(directory, "appliance"), { recursive: true });
    writeFileSync(sourcePath, '{"source": true}');
    writeFileSync(targetPath, '{"appliance": true}');

    assert.equal(seedDevCustomModels({ sourcePath, targetPath }), false);
    assert.equal(readFileSync(targetPath, "utf8"), '{"appliance": true}');
  });
});

test("is a no-op when the Agent model configuration is absent", () => {
  withTemporaryDirectory((directory) => {
    const sourcePath = join(directory, "agent", "custom_models.json");
    const targetPath = join(directory, "appliance", "custom_models.json");

    assert.equal(seedDevCustomModels({ sourcePath, targetPath }), false);
  });
});
