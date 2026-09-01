import assert from "node:assert/strict";
import test from "node:test";

import { heavyDependencyChunk, safeChunkName } from "./chunk-policy.mjs";

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
