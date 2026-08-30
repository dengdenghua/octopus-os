import { describe, expect, it } from "vitest";

import { stripToolEnvelope, stripTraceLabelPrefixes } from "./trace-labels";

describe("stripTraceLabelPrefixes", () => {
  it("removes repeated ReAct field labels without removing the content", () => {
    expect(
      stripTraceLabelPrefixes(
        'Thought: inspect the file\nAction: read_file({"path":"a.ts"})\nObservation: ok',
      ),
    ).toBe('inspect the file\nread_file({"path":"a.ts"})\nok');
  });

  it("handles bullets and Chinese labels", () => {
    expect(
      stripTraceLabelPrefixes(
        "- \u601d\u8003: \u68c0\u67e5\u72b6\u6001\n* \u6267\u884c\uff1aweb_search({})",
      ),
    ).toBe("\u68c0\u67e5\u72b6\u6001\nweb_search({})");
  });
});

describe("stripToolEnvelope", () => {
  it("drops the success envelope + tool name, keeping the args", () => {
    expect(
      stripToolEnvelope(
        '(real tool execution succeeded) grep_text {"pattern":"useState"}',
      ),
    ).toBe('{"pattern":"useState"}');
  });

  it("collapses a failure + drops the internal retry coaching", () => {
    expect(
      stripToolEnvelope(
        "(\u5de5\u5177\u5931\u8d25) status=failed error=TypeError \u8bf7\u5728\u4e0b\u4e00\u8f6e Thought \u4e2d\u5206\u6790\u5931\u8d25\u539f\u56e0\uff0c\u7136\u540e\u6362\u4e00\u79cd\u65b9\u5f0f\u91cd\u8bd5\u3002",
      ),
    ).toBe("\u5931\u8d25\uff1aTypeError");
  });

  it("leaves ordinary text untouched", () => {
    expect(stripToolEnvelope("just a normal observation")).toBe(
      "just a normal observation",
    );
  });
});
