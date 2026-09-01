import { describe, expect, it } from "vitest";

import { enUS } from "@/core/i18n/locales/en-US";
import { zhCN } from "@/core/i18n/locales/zh-CN";

import {
  buildHeaderSummary,
  parseUnifiedDiff,
  type ActivityItem,
} from "./collapsible-activity-group";

describe("CollapsibleActivityGroup public wording", () => {
  const items: ActivityItem[] = [
    { id: "read-1", label: "Read file" },
    { id: "search-1", label: "Search source" },
  ];

  it("describes grouped operations without exposing tool-call jargon in Chinese", () => {
    const summary = buildHeaderSummary("tool_calls", items, zhCN);

    expect(summary).toContain("操作记录");
    expect(summary).not.toContain("工具调用");
  });

  it("describes grouped operations without exposing tool-call jargon in English", () => {
    const summary = buildHeaderSummary("tool_calls", items, enUS);

    expect(summary).toContain("action record");
    expect(summary?.toLowerCase()).not.toContain("tool call");
  });
});

describe("parseUnifiedDiff", () => {
  it("classifies +/- lines and skips diff meta headers", () => {
    const diff = [
      "diff --git a/src/a.ts b/src/a.ts",
      "--- a/src/a.ts",
      "+++ b/src/a.ts",
      "@@ -1,3 +1,4 @@",
      " const ctx = 1;",
      "-const oldLine = 2;",
      "+const newLine = 2;",
      "+const another = 3;",
      " const tail = 4;",
    ].join("\n");

    const rows = parseUnifiedDiff(diff);
    expect(rows.map((r) => r.type)).toEqual([
      "ctx",
      "del",
      "add",
      "add",
      "ctx",
    ]);
    expect(rows.map((r) => r.text)).toEqual([
      " const ctx = 1;",
      "const oldLine = 2;",
      "const newLine = 2;",
      "const another = 3;",
      " const tail = 4;",
    ]);
  });

  it("handles empty and header-only diffs", () => {
    expect(parseUnifiedDiff("")).toEqual([]);
    expect(parseUnifiedDiff("--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n\\ No newline")).toEqual(
      [],
    );
  });
});
