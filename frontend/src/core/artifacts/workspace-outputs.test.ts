import { describe, expect, it } from "vitest";

import {
  isInternalArtifactRef,
  isInternalWorkspaceOutput,
  listWorkspaceArtifactRefs,
} from "./workspace-outputs";

describe("isInternalWorkspaceOutput", () => {
  it.each([
    "plan.md",
    "notes.md",
    "US10792461B2-full.jsonl",
    "US10792461B2-full.jsonl.lock",
    "output/final/US10792461B2.jsonl",
    "final/report.md",
    "stages/draft.md",
  ])("hides working evidence %s", (path) => {
    expect(isInternalWorkspaceOutput(path)).toBe(true);
  });

  it.each([
    "US10792461B2_权利要求1设计规避分析报告.md",
    "reports/final-report.pdf",
    "export/data.jsonl",
  ])("keeps user-facing deliverable %s", (path) => {
    expect(isInternalWorkspaceOutput(path)).toBe(false);
  });

  it("filters stable workspace artifact references too", () => {
    expect(isInternalArtifactRef("workspace-output:final:plan.md")).toBe(true);
    expect(
      isInternalArtifactRef("workspace-output:output:cache/item-full.jsonl"),
    ).toBe(true);
    expect(isInternalArtifactRef("workspace-output:final:report.md")).toBe(
      false,
    );
  });

  it("keeps a valid server resource identity and rejects another thread", async () => {
    const originalFetch = globalThis.fetch;
    const calls: string[] = [];
    globalThis.fetch = (async (input) => {
      const url = String(input);
      calls.push(url);
      const area = new URL(url, "http://localhost").searchParams.get("area");
      const file =
        area === "deploy"
          ? {
              name: "report.md",
              area: "deploy",
              relative_path: "report.md",
              path: "C:/workspace/report.md",
              size: 4,
              modified: 1,
              download_url: "/download",
              resource_id:
                "workspace-file:v1:dGhyZWFkLTE:ZGVwbG95:cmVwb3J0Lm1k",
            }
          : area === "stages"
            ? {
                name: "mismatched.md",
                area,
                relative_path: "mismatched.md",
                path: "C:/workspace/mismatched.md",
                size: 6,
                modified: 1,
                download_url: "/download",
                resource_id:
                  "workspace-file:v1:dGhyZWFkLTE:ZmluYWw:bWlzbWF0Y2hlZC5tZA",
              }
            : {
                name: "legacy.md",
                area,
                relative_path: "legacy.md",
                path: "C:/workspace/legacy.md",
                size: 6,
                modified: 1,
                download_url: "/download",
                resource_id:
                  "workspace-file:v1:dGhyZWFkLXR3bzo:ZmluYWw:bGVnYWN5Lm1k",
              };
      return new Response(JSON.stringify({ files: [file] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;
    try {
      const refs = await listWorkspaceArtifactRefs("thread-1");
      expect(refs).toContain(
        "workspace-file:v1:dGhyZWFkLTE:ZGVwbG95:cmVwb3J0Lm1k",
      );
      expect(refs).toContain("workspace-output:output:legacy.md");
      expect(refs).toContain("workspace-output:stages:mismatched.md");
      expect(refs).not.toContain(
        "workspace-file:v1:dGhyZWFkLXR3bzo:ZmluYWw:bGVnYWN5Lm1k",
      );
      expect(calls).toHaveLength(4);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
