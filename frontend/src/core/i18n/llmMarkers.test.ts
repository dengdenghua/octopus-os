import { describe, expect, it } from "vitest";

import {
  LLM_TRACE_MARKERS,
  TRACE_KINDS,
  getLLMTraceLocaleCoverage,
  hasLLMTraceMarkers,
  mentionsCompletion,
  mentionsDelivered,
  normalizeTraceKind,
  segmentLLMTrace,
} from "./llmMarkers";
import { SUPPORTED_LOCALES } from "./locale";

describe("LLM_TRACE_MARKERS", () => {
  it("has a spelling for every trace kind", () => {
    expect(Object.keys(LLM_TRACE_MARKERS).sort()).toEqual(
      [...TRACE_KINDS].sort(),
    );
  });

  it("includes English, Chinese, Japanese and Korean spellings", () => {
    for (const kind of TRACE_KINDS) {
      const spellings = LLM_TRACE_MARKERS[kind];
      // English form
      expect(spellings).toContain(
        kind === "finalAnswer"
          ? "Final Answer"
          : kind.charAt(0).toUpperCase() + kind.slice(1),
      );
      // Chinese (CJK)
      const cjk = spellings.find((s) => /[\u4e00-\u9fff]/.test(s));
      expect(cjk, `Missing CJK spelling for ${kind}`).toBeDefined();
      // Japanese (Hiragana or Katakana)
      const jp = spellings.find((s) => /[\u3040-\u30ff]/.test(s));
      expect(jp, `Missing Japanese spelling for ${kind}`).toBeDefined();
      // Korean (Hangul)
      const ko = spellings.find((s) => /[\uac00-\ud7af]/.test(s));
      expect(ko, `Missing Korean spelling for ${kind}`).toBeDefined();
    }
  });
});

describe("hasLLMTraceMarkers", () => {
  it("returns false for empty / plain text", () => {
    expect(hasLLMTraceMarkers("")).toBe(false);
    expect(hasLLMTraceMarkers("just a normal answer")).toBe(false);
  });

  it("detects English markers", () => {
    expect(hasLLMTraceMarkers("Thought: hmm")).toBe(true);
    expect(hasLLMTraceMarkers("Update: checking files")).toBe(true);
    expect(hasLLMTraceMarkers("Final Answer: 42")).toBe(true);
  });

  it("detects Chinese markers", () => {
    expect(hasLLMTraceMarkers("思考: 嗯")).toBe(true);
    expect(hasLLMTraceMarkers("最终答案: 42")).toBe(true);
  });

  it("detects Japanese markers", () => {
    expect(hasLLMTraceMarkers("考え: うーん")).toBe(true);
    expect(hasLLMTraceMarkers("最終回答: 42")).toBe(true);
  });

  it("detects Korean markers", () => {
    expect(hasLLMTraceMarkers("생각: 흠")).toBe(true);
    expect(hasLLMTraceMarkers("최종답변: 42")).toBe(true);
  });

  it("accepts full-width colons", () => {
    expect(hasLLMTraceMarkers("思考：嗯")).toBe(true);
    expect(hasLLMTraceMarkers("考え：うーん")).toBe(true);
  });
});

describe("normalizeTraceKind", () => {
  it("maps every supported spelling back to its canonical kind", () => {
    for (const kind of TRACE_KINDS) {
      for (const spelling of LLM_TRACE_MARKERS[kind]) {
        expect(normalizeTraceKind(spelling)).toBe(kind);
      }
    }
  });

  it("is case-insensitive for English forms", () => {
    expect(normalizeTraceKind("thought")).toBe("thought");
    expect(normalizeTraceKind("THOUGHT")).toBe("thought");
    expect(normalizeTraceKind("final answer")).toBe("finalAnswer");
  });

  it("returns prelude for unknown text", () => {
    expect(normalizeTraceKind("nope")).toBe("prelude");
    expect(normalizeTraceKind("")).toBe("prelude");
  });
});

describe("segmentLLMTrace", () => {
  it("returns an empty array for empty input", () => {
    expect(segmentLLMTrace("")).toEqual([]);
  });

  it("emits prelude when no markers are present", () => {
    const segs = segmentLLMTrace("just a chat reply");
    expect(segs).toEqual([{ kind: "prelude", text: "just a chat reply" }]);
  });

  it("segments an English ReAct trace", () => {
    const trace =
      "Thought: I should look it up.\nUpdate: Checking the source now.\nAction: lookup()\nObservation: ok\nFinal Answer: 42";
    const segs = segmentLLMTrace(trace);
    const byKind = Object.fromEntries(segs.map((s) => [s.kind, s.text]));
    expect(byKind.thought).toContain("look it up");
    expect(byKind.update).toContain("Checking the source");
    expect(byKind.action).toContain("lookup()");
    expect(byKind.observation).toContain("ok");
    expect(byKind.finalAnswer).toBe("42");
  });

  it("segments a Chinese ReAct trace", () => {
    const trace = "思考: 让我查一下\n行动: lookup()\n观察: ok\n最终答案: 42";
    const segs = segmentLLMTrace(trace);
    const byKind = Object.fromEntries(segs.map((s) => [s.kind, s.text]));
    expect(byKind.thought).toContain("让我查一下");
    expect(byKind.finalAnswer).toBe("42");
  });

  it("segments a Japanese ReAct trace", () => {
    const trace = "考え: 調べます\n行動: lookup()\n観察: ok\n最終回答: 42";
    const segs = segmentLLMTrace(trace);
    const byKind = Object.fromEntries(segs.map((s) => [s.kind, s.text]));
    expect(byKind.thought).toContain("調べます");
    expect(byKind.finalAnswer).toBe("42");
  });

  it("segments a Korean ReAct trace", () => {
    const trace = "생각: 찾아봅니다\n행동: lookup()\n관찰: ok\n최종답변: 42";
    const segs = segmentLLMTrace(trace);
    const byKind = Object.fromEntries(segs.map((s) => [s.kind, s.text]));
    expect(byKind.thought).toContain("찾아봅니다");
    expect(byKind.finalAnswer).toBe("42");
  });

  it("handles mixed locales in a single trace", () => {
    const trace = "Thought: 让我查一下\nFinal Answer: 답은 42";
    const segs = segmentLLMTrace(trace);
    const byKind = Object.fromEntries(segs.map((s) => [s.kind, s.text]));
    expect(byKind.thought).toContain("让我查一下");
    expect(byKind.finalAnswer).toBe("답은 42");
  });
});

describe("mentionsDelivered", () => {
  it("recognises English delivery phrases", () => {
    expect(mentionsDelivered("Final Answer: ready")).toBe(true);
    expect(mentionsDelivered("report has been delivered")).toBe(true);
    expect(mentionsDelivered("already delivered above")).toBe(true);
  });

  it("recognises Chinese delivery phrases", () => {
    expect(mentionsDelivered("报告 已交付")).toBe(true);
  });

  it("recognises Japanese delivery phrases", () => {
    expect(mentionsDelivered("最終回答: ok")).toBe(true);
    expect(mentionsDelivered("thinking again")).toBe(false);
  });

  it("recognises Korean delivery phrases", () => {
    expect(mentionsDelivered("최종답변 완료")).toBe(true);
    expect(mentionsDelivered("보고 전달")).toBe(true);
  });

  it("returns false for unrelated text", () => {
    expect(mentionsDelivered("thinking out loud")).toBe(false);
  });
});

describe("mentionsCompletion", () => {
  it("recognises English completion keywords", () => {
    expect(mentionsCompletion("all tasks completed")).toBe(true);
    expect(mentionsCompletion("todo list fully completed")).toBe(true);
  });

  it("recognises Chinese completion keywords", () => {
    expect(mentionsCompletion("任务 全部 完成")).toBe(true);
    expect(mentionsCompletion("清单 已完成")).toBe(true);
  });

  it("recognises Japanese completion keywords", () => {
    expect(mentionsCompletion("タスク 完了")).toBe(true);
    expect(mentionsCompletion("チェックリスト 完了")).toBe(true);
  });

  it("recognises Korean completion keywords", () => {
    expect(mentionsCompletion("모든 작업 완료")).toBe(true);
    expect(mentionsCompletion("체크리스트 완료")).toBe(true);
  });

  it("returns false for unrelated text", () => {
    expect(mentionsCompletion("still working on it")).toBe(false);
  });
});

describe("getLLMTraceLocaleCoverage", () => {
  it("lists every supported locale", () => {
    expect([...getLLMTraceLocaleCoverage()].sort()).toEqual(
      [...SUPPORTED_LOCALES].sort(),
    );
  });
});
