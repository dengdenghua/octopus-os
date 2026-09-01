import { describe, expect, test } from "vitest";

import type { LiveToolEvent } from "./live-tool-timeline";
import {
  diffEntriesFromBlocks,
  finalOutputArtifactEntries,
  isInternalWorkingFilePath,
} from "./agent-workbench-utils";
import { toWorkBlocks } from "./work-blocks";

function event(partial: Partial<LiveToolEvent>): LiveToolEvent {
  return {
    id: "event-1",
    name: "read_file",
    status: "done",
    startedAt: 1_000,
    iteration: 0,
    ...partial,
  };
}

describe("agent workbench diff entries", () => {
  test("keeps working files out of final artifacts while preserving their trace", () => {
    expect(isInternalWorkingFilePath("output/final/plan.md")).toBe(true);
    expect(isInternalWorkingFilePath("output/final/research-plan.md")).toBe(
      false,
    );

    const artifacts = finalOutputArtifactEntries([
      event({
        id: "write-plan",
        name: "write_text_file",
        input: { path: "data/workspaces/thread-1/output/final/plan.md" },
      }),
      event({
        id: "write-report",
        name: "write_text_file",
        input: { path: "data/workspaces/thread-1/output/final/report.md" },
      }),
    ]);

    expect(artifacts.map((entry) => entry.path)).toEqual([
      "data/workspaces/thread-1/output/final/report.md",
    ]);
  });

  test("does not classify read-only source evidence as changed files", () => {
    const blocks = toWorkBlocks([
      event({
        id: "read-runtime",
        input: { path: "runtime/core/cerebrum/react_loop.py" },
        output: { content: "def stream_react_loop(): ..." },
      }),
      event({
        id: "read-frontend",
        input: { path: "frontend/src/core/realtime/items.ts" },
        output: { content: "export interface AgentMessageItem {}" },
      }),
    ]);

    expect(blocks.every((block) => block.kind === "read")).toBe(true);
    expect(diffEntriesFromBlocks(blocks)).toEqual([]);
  });

  test("keeps real file mutations in the changed-file surface", () => {
    const blocks = toWorkBlocks([
      event({
        id: "write-frontend",
        name: "write_file",
        input: { path: "frontend/src/app.tsx", content: "export default App" },
        output: {
          path: "frontend/src/app.tsx",
          diff: "--- a/frontend/src/app.tsx\n+++ b/frontend/src/app.tsx\n@@ -1 +1 @@",
        },
      }),
    ]);

    expect(diffEntriesFromBlocks(blocks)).toMatchObject([
      { path: "frontend/src/app.tsx", status: "done" },
    ]);
  });
});
