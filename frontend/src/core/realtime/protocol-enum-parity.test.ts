// Audit A-04: the realtime protocol enums are GENERATED from
// runtime/protocol/items.py by scripts/gen_realtime_protocol_enums.py —
// the source of truth is the Python definitions. This test replaces the
// old regex-parity test: it re-runs the generator and fails when the
// committed protocol-enums.generated.ts is stale, so drift is caught by
// CI exactly like the OpenAPI snapshot gate.

import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../");

describe("realtime protocol enums are generated", () => {
  it("regenerating produces no diff (source of truth is items.py)", () => {
    expect(() =>
      execFileSync(
        "python3",
        [resolve(REPO_ROOT, "scripts/gen_realtime_protocol_enums.py"), "--check"],
        { cwd: REPO_ROOT, encoding: "utf-8" },
      ),
    ).not.toThrow();
  });
});
