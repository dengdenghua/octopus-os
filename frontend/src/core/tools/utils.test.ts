import { describe, expect, it } from "vitest";

import { explainToolCall } from "./utils";

const t = {
  common: { thinking: "Thinking..." },
  toolCalls: {
    searchFor: (q: string) => `Search for "${q}"`,
    viewWebPage: "View web page",
    presentFiles: "Present files",
    writeTodos: "Update to-do list",
    useTool: (name: string) => `Use "${name}" tool`,
  },
} as any;

describe("explainToolCall", () => {
  it("explains web_search with query", () => {
    expect(
      explainToolCall(
        { name: "web_search", args: { query: "react hooks" } },
        t,
      ),
    ).toBe('Search for "react hooks"');
  });

  it("explains image_search with query", () => {
    expect(
      explainToolCall({ name: "image_search", args: { query: "cats" } }, t),
    ).toBe('Search for "cats"');
  });

  it("explains web_fetch", () => {
    expect(explainToolCall({ name: "web_fetch", args: {} }, t)).toBe(
      "View web page",
    );
  });

  it("explains present_files", () => {
    expect(explainToolCall({ name: "present_files", args: {} }, t)).toBe(
      "Present files",
    );
  });

  it("explains write_todos", () => {
    expect(explainToolCall({ name: "write_todos", args: {} }, t)).toBe(
      "Update to-do list",
    );
  });

  it("uses args.description when available", () => {
    expect(
      explainToolCall(
        { name: "custom_tool", args: { description: "doing stuff" } },
        t,
      ),
    ).toBe("doing stuff");
  });

  it("falls back to useTool for unknown tools", () => {
    expect(explainToolCall({ name: "my_tool", args: {} }, t)).toBe(
      'Use "my_tool" tool',
    );
  });
});
