import { describe, expect, it } from "vitest";

import type { LiveToolEvent } from "./live-tool-timeline";
import { buildReplayFromBlocks, redactSecrets } from "./replay-from-blocks";
import type { WorkBlock, WorkBlockKind, WorkBlockTitle } from "./work-blocks";

function block(
  over: Partial<Omit<WorkBlock, "kind" | "event" | "title">> & {
    kind: WorkBlockKind;
    event?: Partial<LiveToolEvent>;
    title?: string | WorkBlockTitle;
  },
): WorkBlock {
  const event: LiveToolEvent = {
    id: over.id ?? "e1",
    name: "tool",
    status: "done",
    startedAt: 0,
    iteration: 0,
    ...over.event,
  };
  const title: WorkBlockTitle =
    typeof over.title === "string"
      ? { key: "raw", text: over.title }
      : (over.title ?? { key: "raw", text: "title" });
  return {
    id: over.id ?? "b1",
    event,
    kind: over.kind,
    actionKey: over.actionKey ?? "execute",
    target: over.target ?? "",
    title,
    subtitle: over.subtitle ?? "",
    status: over.status ?? "done",
    startedAt: 0,
    inputText: over.inputText ?? "",
    outputText: over.outputText ?? "",
  };
}

describe("buildReplayFromBlocks", () => {
  it("maps blocks into steps preserving kind/title/subtitle/status", () => {
    const data = buildReplayFromBlocks(
      [
        block({
          kind: "file",
          title: "edit auth.ts",
          subtitle: "src/auth.ts",
          status: "done",
        }),
      ],
      { title: "Run" },
    );
    expect(data.title).toBe("Run");
    expect(data.steps).toHaveLength(1);
    expect(data.steps[0]).toMatchObject({
      kind: "file",
      title: "edit auth.ts",
      subtitle: "src/auth.ts",
      status: "done",
    });
  });

  it("turns the latest todo list into an auditable replay receipt", () => {
    const data = buildReplayFromBlocks(
      [
        block({ kind: "terminal", title: "Run verification", status: "done" }),
        block({
          kind: "todo",
          title: "Update plan",
          event: {
            name: "todo_write",
            input: {
              items: [
                { content: "Inspect the affected component", status: "completed" },
                { content: "Verify the preview", status: "in_progress" },
              ],
            },
          },
        }),
      ],
      { title: "Run" },
    );

    expect(data.receipt?.items).toEqual([
      expect.objectContaining({
        title: "Inspect the affected component",
        status: "done",
      }),
      expect.objectContaining({
        title: "Verify the preview",
        status: "running",
      }),
    ]);
    expect(data.receipt?.verification).toContain("Run verification");
  });

  it("renders a terminal step body as public output without the raw command", () => {
    const data = buildReplayFromBlocks(
      [
        block({
          kind: "terminal",
          title: "shell",
          event: { input: { command: "ls -la" } },
          outputText: "file1\nfile2",
        }),
      ],
      { title: "Run" },
    );
    expect(data.steps[0].body).toBe("file1\nfile2");
    expect(data.steps[0].body).not.toContain("ls -la");
  });

  it("drops sub-agent lifecycle blocks", () => {
    const data = buildReplayFromBlocks(
      [
        block({
          id: "a",
          kind: "agent",
          title: "spawn",
          event: { lifecycle: "spawned" },
        }),
        block({
          id: "b",
          kind: "terminal",
          title: "real step",
          outputText: "ok",
        }),
      ],
      { title: "Run" },
    );
    expect(data.steps.map((s) => s.title)).toEqual(["real step"]);
  });

  it("inlines a screenshot only when it is already a data-URL", () => {
    const withData = buildReplayFromBlocks(
      [
        block({
          kind: "browser",
          title: "shot",
          event: { output: { screenshot: "data:image/png;base64,AAA" } },
        }),
      ],
      { title: "Run" },
    );
    expect(withData.steps[0].image).toBe("data:image/png;base64,AAA");

    const withUrl = buildReplayFromBlocks(
      [
        block({
          kind: "browser",
          title: "shot",
          event: { output: { screenshot: "https://x/y.png" } },
        }),
      ],
      { title: "Run" },
    );
    expect(withUrl.steps[0].image).toBeUndefined();
  });

  it("reconstructs a data-URL from a read-image record", () => {
    const data = buildReplayFromBlocks(
      [
        block({
          kind: "read",
          title: "read chart.png",
          event: {
            output: {
              kind: "image",
              media_type: "image/png",
              data_base64: "AAAA",
            },
          },
        }),
      ],
      { title: "Run" },
    );
    expect(data.steps[0].image).toBe("data:image/png;base64,AAAA");
  });

  it("skips an oversized or non-image read record", () => {
    const big = buildReplayFromBlocks(
      [
        block({
          kind: "read",
          title: "huge",
          event: {
            output: {
              kind: "image",
              media_type: "image/png",
              data_base64: "A".repeat(2_000_001),
            },
          },
        }),
      ],
      { title: "Run" },
    );
    expect(big.steps[0].image).toBeUndefined();

    const notImage = buildReplayFromBlocks(
      [
        block({
          kind: "read",
          title: "pdf",
          event: {
            output: {
              kind: "image",
              media_type: "application/pdf",
              data_base64: "AAAA",
            },
          },
        }),
      ],
      { title: "Run" },
    );
    expect(notImage.steps[0].image).toBeUndefined();
  });

  it("truncates an overlong body", () => {
    const huge = "x".repeat(5000);
    const data = buildReplayFromBlocks(
      [
        block({
          kind: "terminal",
          title: "t",
          event: { input: { command: "echo" } },
          outputText: huge,
        }),
      ],
      { title: "Run" },
    );
    expect(data.steps[0].body!.length).toBeLessThan(1300);
    expect(data.steps[0].body!.endsWith("…")).toBe(true);
  });

  it("strips protocol tags, renderer markers and machine phase prefixes", () => {
    const data = buildReplayFromBlocks(
      [
        block({
          kind: "read",
          title: "Phase 1: <TextBlock>读取协议</TextBlock>",
          subtitle: "<read_only> </read_only> runtime/protocol/items.py",
          inputText:
            "<read_only> </read_only>\n<TextBlock>只读比较字段</TextBlock>",
          outputText:
            "<ToolCallBlock>private tool args</ToolCallBlock>\n已确认字段一致",
        }),
      ],
      { title: "Run" },
    );

    expect(data.steps[0]).toMatchObject({
      title: "读取协议",
      subtitle: "runtime/protocol/items.py",
    });
    expect(data.steps[0].body).toContain("只读比较字段");
    expect(data.steps[0].body).toContain("已确认字段一致");
    expect(data.steps[0].body).not.toContain("private tool args");
    expect(JSON.stringify(data)).not.toMatch(
      /read_only|TextBlock|Phase 1/i,
    );
  });

  it("does not package raw tool names or terminal commands into share replay data", () => {
    const data = buildReplayFromBlocks(
      [
        block({
          kind: "terminal",
          title: "exec_shell",
          subtitle: "read_file",
          event: {
            input: {
              command: "cat ~/.ssh/id_rsa && pnpm test",
            },
          },
          outputText:
            "12 passed\nAction: exec_shell\nObservation: token=super-secret",
        }),
      ],
      { title: "Run" },
    );

    expect(data.steps[0]).toMatchObject({
      title: "operation",
      subtitle: "operation",
    });
    expect(data.steps[0].body).toContain("12 passed");
    expect(JSON.stringify(data)).not.toMatch(
      /exec_shell|read_file|cat ~\/\.ssh|pnpm test|super-secret|Observation:/i,
    );
  });

  it("passes meta through to the replay data", () => {
    const data = buildReplayFromBlocks([block({ kind: "file", title: "x" })], {
      title: "My run",
      footer: "2026-06-13",
      brand: "Echo",
    });
    expect(data).toMatchObject({
      title: "My run",
      footer: "2026-06-13",
      brand: "Echo",
    });
  });
});

describe("redactSecrets", () => {
  it("scrubs api keys, bearer tokens, jwts and long hex", () => {
    expect(redactSecrets("key sk-abcdefABCDEF0123456789")).toContain(
      "«redacted-token»",
    );
    expect(redactSecrets("Authorization: Bearer abcdefghijklmnop")).toContain(
      "«redacted-bearer»",
    );
    expect(
      redactSecrets(
        "t=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36",
      ),
    ).toContain("«redacted-jwt»");
    expect(redactSecrets('password = "hunter2secret"')).toContain("«redacted»");
    expect(redactSecrets(`x ${"a".repeat(40)}`)).toContain("«redacted-hex»");
  });

  it("leaves ordinary text untouched", () => {
    const text = "ran pnpm test, 12 passed in 3.2s";
    expect(redactSecrets(text)).toBe(text);
  });
});
