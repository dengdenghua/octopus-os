import assert from "node:assert/strict";
import test from "node:test";

import {
  heavyDependencyChunk,
  manualChunks,
  packageNameFromNodeModule,
  safeChunkName,
} from "./chunk-policy.mjs";

test("extracts scoped and unscoped dependency names on Windows paths", () => {
  assert.equal(
    packageNameFromNodeModule(
      "C:\\repo\\node_modules\\@tanstack\\react-query\\build.js",
    ),
    "@tanstack/react-query",
  );
  assert.equal(
    packageNameFromNodeModule("/repo/node_modules/react/index.js"),
    "react",
  );
  assert.equal(packageNameFromNodeModule("/repo/src/app.tsx"), null);
});

test("shares the main-app dependency split policy with workbenches", () => {
  assert.equal(
    manualChunks("C:\\repo\\node_modules\\react\\index.js"),
    "react-vendor",
  );
  assert.equal(
    manualChunks("C:\\repo\\node_modules\\@tanstack\\react-query\\build.js"),
    "query-virtual",
  );
  assert.equal(
    manualChunks("C:\\repo\\node_modules\\mermaid\\dist\\index.js"),
    undefined,
  );
});

test("uses readable stable package suffixes", () => {
  assert.equal(safeChunkName("@codemirror/lang-json"), "codemirror-lang-json");
  assert.equal(safeChunkName("--@lezer//common--"), "lezer-common");
});

test("keeps CodeMirror language packages independently cacheable", () => {
  assert.equal(
    heavyDependencyChunk("@codemirror/lang-python"),
    "codemirror-codemirror-lang-python",
  );
  assert.equal(
    heavyDependencyChunk("@uiw/codemirror-theme-monokai"),
    "codemirror-uiw-codemirror-theme-monokai",
  );
  assert.equal(heavyDependencyChunk("@lezer/python"), "lezer-lezer-python");
});

test("does not collapse Mermaid's native dynamic diagram boundaries", () => {
  assert.equal(heavyDependencyChunk("mermaid"), undefined);
  assert.equal(heavyDependencyChunk("cytoscape"), "diagram-cytoscape");
  assert.equal(heavyDependencyChunk("d3-scale"), undefined);
});
