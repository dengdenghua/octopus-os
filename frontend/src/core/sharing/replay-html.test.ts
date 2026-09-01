import { describe, expect, it } from "vitest";

import { buildReplayHtml, type ReplayData } from "./replay-html";

function sample(overrides: Partial<ReplayData> = {}): ReplayData {
  return {
    title: "Refactor the auth module",
    steps: [
      {
        kind: "terminal",
        title: "run tests",
        subtitle: "pnpm test",
        body: "12 passed",
        status: "done",
      },
      {
        kind: "file",
        title: "edit auth.ts",
        subtitle: "src/auth.ts",
        status: "done",
      },
    ],
    footer: "2026-06-13",
    ...overrides,
  };
}

describe("buildReplayHtml", () => {
  it("emits a self-contained html document with a step player", () => {
    const html = buildReplayHtml(sample());
    expect(html.startsWith("<!doctype html")).toBe(true);
    expect(html.trimEnd().endsWith("</html>")).toBe(true);
    expect(html).toContain("<title>Refactor the auth module");
    // embedded vanilla-JS player + controls + step list
    expect(html).toContain("<script>");
    expect(html).toContain('id="play"');
    expect(html).toContain('id="seek"');
    expect(html).toContain('id="steps"');
    // no external resource references → self-contained
    expect(html).not.toMatch(/src=["']https?:\/\//);
  });

  it("exposes loop, speed and keyboard controls", () => {
    const html = buildReplayHtml(sample());
    expect(html).toContain('id="loop"');
    expect(html).toContain('id="speed"');
    expect(html).toContain("addEventListener('keydown'");
    expect(html).toContain("ArrowRight");
    expect(html).toContain("SPEEDS");
  });

  it("embeds every step's title and body in the data", () => {
    const html = buildReplayHtml(sample());
    expect(html).toContain("run tests");
    expect(html).toContain("12 passed");
    expect(html).toContain("edit auth.ts");
  });

  it("inlines an optional screenshot data-url", () => {
    const img = "data:image/png;base64,AAAA";
    const html = buildReplayHtml(
      sample({ steps: [{ title: "shot", image: img }] }),
    );
    expect(html).toContain(img);
  });

  it("renders a completion receipt and browser verification checklist", () => {
    const html = buildReplayHtml(
      sample({
        receipt: {
          summary: "2 changes completed · ready to verify",
          items: [
            { title: "Update layout", status: "done", detail: "Grid is now responsive" },
          ],
          verification: ["Open the preview and confirm the responsive grid"],
        },
      }),
    );
    expect(html).toContain("RESULT RECEIPT");
    expect(html).toContain("What was delivered");
    expect(html).toContain("Update layout");
    expect(html).toContain("VERIFY IN YOUR BROWSER");
    expect(html).toContain("id=\"dock-play\"");
  });

  it("renders an empty-state (and no script) when there are no steps", () => {
    const html = buildReplayHtml(sample({ steps: [] }));
    expect(html).toContain("No steps");
    expect(html).not.toContain("<script>");
  });

  it("drops empty steps (no title, body or image)", () => {
    const html = buildReplayHtml(
      sample({
        steps: [
          { title: "keep me", kind: "terminal" },
          { title: "", subtitle: "drop me", kind: "file" },
        ],
      }),
    );
    expect(html).toContain("keep me");
    expect(html).not.toContain("drop me");
  });

  it("escapes the title in page chrome (no markup injection)", () => {
    const html = buildReplayHtml(
      sample({ title: "</title><img src=x onerror=alert(1)>" }),
    );
    expect(html).not.toContain("<img src=x onerror=alert(1)>");
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
  });

  it("neutralises step content that tries to close the script early", () => {
    const html = buildReplayHtml(
      sample({
        steps: [{ title: "x", body: "</script><script>alert(1)</script>" }],
      }),
    );
    expect(html).not.toContain("</script><script>alert(1)");
    expect(html).toContain("<\\/script>");
  });

  it("does not package leaked renderer or protocol tags into replay data", () => {
    const html = buildReplayHtml(
      sample({
        title: "<TextBlock>Run</TextBlock>",
        footer: "<read_only> </read_only> 2026-06-13",
        steps: [
          {
            title: "<TextBlock>读取协议</TextBlock>",
            subtitle: "<read_only> </read_only> runtime/protocol/items.py",
            body: "<read_only> </read_only>\n<ToolCallBlock>private tool args</ToolCallBlock>\n已确认",
          },
        ],
      }),
    );

    expect(html).toContain("Run");
    expect(html).toContain("读取协议");
    expect(html).toContain("runtime/protocol/items.py");
    expect(html).toContain("已确认");
    expect(html).not.toContain("private tool args");
    expect(html).not.toMatch(/read_only|TextBlock|ToolCallBlock/);
  });

  it("scrubs raw tool names and secrets even when replay data is passed directly", () => {
    const html = buildReplayHtml(
      sample({
        steps: [
          {
            title: "read_file",
            subtitle: "exec_shell",
            body: "Authorization: Bearer abcdefghijklmnop\n12 passed",
          },
        ],
      }),
    );

    expect(html).toContain("operation");
    expect(html).toContain("12 passed");
    expect(html).not.toMatch(/read_file|exec_shell|Bearer abcdef/i);
  });
});
