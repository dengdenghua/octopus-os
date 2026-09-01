import { describe, expect, it } from "vitest";

import {
  inferToolActionKind,
  inferToolActionKindFromText,
} from "./tool-action-kind";

describe("tool action kind inference", () => {
  it("classifies common tools into user-visible action kinds", () => {
    expect(inferToolActionKind("web_search")).toBe("search");
    expect(inferToolActionKind("list_cwd")).toBe("list");
    expect(inferToolActionKind("read_file")).toBe("read");
    expect(inferToolActionKind("create_file")).toBe("create");
    expect(inferToolActionKind("write_text_file")).toBe("write");
    expect(inferToolActionKind("exec_shell")).toBe("run");
    expect(inferToolActionKind("deep-research-swarm")).toBe("skill");
    expect(inferToolActionKind("planning")).toBe("plan");
  });

  it("classifies action callback text in English and Chinese", () => {
    expect(inferToolActionKindFromText("Action: web_search({})")).toBe(
      "search",
    );
    expect(inferToolActionKindFromText("search official docs")).toBe("search");
    expect(
      inferToolActionKindFromText(
        "\u641c\u7d22 OpenClaw \u5b98\u65b9\u6587\u6863",
      ),
    ).toBe("search");
    expect(inferToolActionKindFromText("\u8bfb\u53d6 README.md")).toBe("read");
    expect(
      inferToolActionKindFromText(
        "\u6b63\u5728\u521b\u5efa\u6587\u4ef6 plan.md",
      ),
    ).toBe("create");
    expect(inferToolActionKindFromText("\u89c4\u5212\u4e0b\u4e00\u6b65")).toBe(
      "plan",
    );
  });
});
