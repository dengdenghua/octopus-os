import assert from "node:assert/strict";
import test from "node:test";

import {
  rejectDefeatedCodeSplitting,
  rejectOversizedJavaScriptChunk,
} from "./build-warning-policy.mjs";

test("fails when a dynamic import is defeated by a static import", () => {
  assert.throws(
    () =>
      rejectDefeatedCodeSplitting(
        "DYNAMIC_IMPORT_WILL_NOT_MOVE_MODULE",
        "module is also statically imported",
      ),
    /Mixed static\/dynamic import defeats code splitting/,
  );
});

test("allows unrelated Rollup warnings to reach the normal handler", () => {
  assert.doesNotThrow(() =>
    rejectDefeatedCodeSplitting("SOURCEMAP_ERROR", "source map unavailable"),
  );
});

test("enforces the production JavaScript chunk budget", () => {
  assert.doesNotThrow(() =>
    rejectOversizedJavaScriptChunk("assets/editor.js", 900 * 1024, 900),
  );
  assert.throws(
    () =>
      rejectOversizedJavaScriptChunk("assets/editor.js", 900 * 1024 + 1, 900),
    /editor\.js is 900\.0 KiB; limit is 900 KiB/,
  );
});
