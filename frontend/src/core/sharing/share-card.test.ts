import { describe, expect, it } from "vitest";

import {
  SHARE_CARD_HEIGHT,
  SHARE_CARD_WIDTH,
  buildShareCard,
  escapeXml,
  renderShareCardSvg,
  wrapLines,
} from "./share-card";

describe("buildShareCard", () => {
  it("applies defaults and trims", () => {
    const card = buildShareCard({ title: "  Build a landing page  " });
    expect(card.title).toBe("Build a landing page");
    expect(card.brand).toBe("EchoAI");
    expect(card.prompt).toBe("");
    expect(card.summary).toBe("");
    expect(card.footer).toBe("");
  });

  it("falls back to a placeholder for an empty title", () => {
    expect(buildShareCard({ title: "   " }).title).toBe("Untitled task");
  });

  it("keeps caller-provided prompt / summary / brand / footer", () => {
    const card = buildShareCard({
      title: "T",
      prompt: "do the thing",
      summary: "did the thing",
      brand: "Acme",
      footer: "2026-06-13",
    });
    expect(card).toEqual({
      title: "T",
      prompt: "do the thing",
      summary: "did the thing",
      brand: "Acme",
      footer: "2026-06-13",
    });
  });
});

describe("escapeXml", () => {
  it("escapes the five XML-significant characters", () => {
    expect(escapeXml(`<a href="x" data='y'>&</a>`)).toBe(
      "&lt;a href=&quot;x&quot; data=&#39;y&#39;&gt;&amp;&lt;/a&gt;",
    );
  });
});

describe("wrapLines", () => {
  it("returns no lines for empty / whitespace input", () => {
    expect(wrapLines("", 10, 3)).toEqual([]);
    expect(wrapLines("   \n  ", 10, 3)).toEqual([]);
  });

  it("greedily packs words within maxChars", () => {
    expect(wrapLines("alpha beta gamma", 11, 5)).toEqual([
      "alpha beta",
      "gamma",
    ]);
  });

  it("truncates to maxLines with an ellipsis", () => {
    const lines = wrapLines("one two three four five six seven", 7, 2);
    expect(lines).toHaveLength(2);
    expect(lines[lines.length - 1]).toContain("…");
  });

  it("hard-breaks a single word longer than a line (e.g. CJK run)", () => {
    const lines = wrapLines("从一个新产品想法开始帮我验证市场预算团队", 6, 5);
    expect(lines.every((l) => l.length <= 6)).toBe(true);
    expect(lines.join("")).toContain("从一个新产");
  });
});

describe("renderShareCardSvg", () => {
  const base = buildShareCard({ title: "Ship the share feature" });

  it("emits a well-formed 1200×630 svg", () => {
    const svg = renderShareCardSvg(base);
    expect(svg.startsWith("<svg")).toBe(true);
    expect(svg).toContain(`width="${SHARE_CARD_WIDTH}"`);
    expect(svg).toContain(`height="${SHARE_CARD_HEIGHT}"`);
    expect(svg.trimEnd().endsWith("</svg>")).toBe(true);
  });

  it("renders the title and brand", () => {
    const svg = renderShareCardSvg(base);
    expect(svg).toContain("Ship the share feature");
    expect(svg).toContain("EchoAI");
  });

  it("includes the prompt block only when a prompt is present", () => {
    expect(renderShareCardSvg(base)).not.toContain("做同款");
    const withPrompt = renderShareCardSvg(
      buildShareCard({ title: "T", prompt: "recreate me" }),
    );
    expect(withPrompt).toContain("做同款");
    expect(withPrompt).toContain("recreate me");
  });

  it("escapes hostile text so it can't break out of the SVG", () => {
    const svg = renderShareCardSvg(
      buildShareCard({ title: `</text><script>alert(1)</script>` }),
    );
    expect(svg).not.toContain("<script>");
    expect(svg).toContain("&lt;script&gt;");
  });
});
