import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import test from "node:test";

import { seedDevCustomModels } from "./dev-appliance-model-seed.mjs";

function withTemporaryDirectory(run) {
  const directory = mkdtempSync(join(tmpdir(), "echo-model-seed-"));
  try {
    run(directory);
  } finally {
    assert.equal(dirname(resolve(directory)), resolve(tmpdir()));
    assert.ok(basename(directory).startsWith("echo-model-seed-"));
    rmSync(directory, { recursive: true, force: true });
  }
}

test("seeds custom models once with private permissions", () => {
  withTemporaryDirectory((directory) => {
    const sourcePath = join(directory, "agent", "custom_models.json");
    const targetPath = join(
      directory,
      "appliance ' [literal] $data",
      "custom_models.json",
    );
    mkdirSync(join(directory, "agent"), { recursive: true });
    writeFileSync(sourcePath, '{"deepseek": {"model": "deepseek-v4"}}');

    assert.equal(seedDevCustomModels({ sourcePath, targetPath }), true);
    assert.equal(
      readFileSync(targetPath, "utf8"),
      readFileSync(sourcePath, "utf8"),
    );
    if (process.platform === "win32") {
      const result = execFileSync(
        join(
          process.env.SystemRoot || "C:\\Windows",
          "System32/WindowsPowerShell/v1.0/powershell.exe",
        ),
        [
          "-NoProfile",
          "-NonInteractive",
          "-Command",
          `
$ErrorActionPreference = 'Stop'
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$acl = [System.IO.File]::GetAccessControl($env:ECHO_TEST_MODEL_PATH)
$rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
@{ sid = $sid; rules = @($rules | ForEach-Object { @{ sid = $_.IdentityReference.Value; type = $_.AccessControlType.ToString(); rights = $_.FileSystemRights.ToString() } }) } | ConvertTo-Json -Depth 4 -Compress
`,
        ],
        {
          encoding: "utf8",
          env: { ...process.env, ECHO_TEST_MODEL_PATH: targetPath },
          windowsHide: true,
          timeout: 15_000,
        },
      );
      const acl = JSON.parse(result);
      assert.equal(acl.rules.length, 1);
      assert.equal(acl.rules[0].sid, acl.sid);
      assert.equal(acl.rules[0].type, "Allow");
      assert.equal(acl.rules[0].rights, "FullControl");
    } else {
      assert.equal(statSync(targetPath).mode & 0o777, 0o600);
    }
    assert.deepEqual(readdirSync(dirname(targetPath)), ["custom_models.json"]);
  });
});

test("failed source read publishes nothing and cleans the private stage", () => {
  withTemporaryDirectory((directory) => {
    const sourcePath = join(directory, "source-directory");
    const targetPath = join(directory, "target", "custom_models.json");
    mkdirSync(sourcePath);
    assert.throws(() => seedDevCustomModels({ sourcePath, targetPath }));
    assert.deepEqual(readdirSync(dirname(targetPath)), []);
  });
});

test(
  "Windows ACL setup failure copies no credentials",
  { skip: process.platform !== "win32" },
  () => {
    withTemporaryDirectory((directory) => {
      const sourcePath = join(directory, "source.json");
      const targetPath = join(directory, "target", "custom_models.json");
      writeFileSync(sourcePath, '{"secret":"fixture-only"}');
      const original = process.env.SystemRoot;
      try {
        process.env.SystemRoot = join(directory, "missing-windows");
        assert.throws(
          () => seedDevCustomModels({ sourcePath, targetPath }),
          /Cannot protect/,
        );
        assert.deepEqual(readdirSync(dirname(targetPath)), []);
      } finally {
        if (original === undefined) delete process.env.SystemRoot;
        else process.env.SystemRoot = original;
      }
    });
  },
);

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
