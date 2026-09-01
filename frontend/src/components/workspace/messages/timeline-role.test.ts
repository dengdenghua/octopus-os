import { describe, expect, test } from "vitest";

import type { Message } from "@/core/api/types";

import {
  assignTimelineRoles,
  isAnswerContent,
  narrativeFingerprint,
  roleForStep,
  type RoleAssignableStep,
} from "./timeline-role";

function reasoningStep(
  id: string,
  text: string,
  extra: Partial<RoleAssignableStep> = {},
): RoleAssignableStep {
  return { id, messageId: id, type: "reasoning", reasoning: text, ...extra };
}

function commentaryStep(
  id: string,
  text: string,
  extra: Partial<RoleAssignableStep> = {},
): RoleAssignableStep {
  return { id, messageId: id, type: "commentary", commentary: text, ...extra };
}

function toolCallStep(
  id: string,
  extra: Partial<RoleAssignableStep> = {},
): RoleAssignableStep {
  return { id, messageId: id, type: "toolCall", ...extra };
}

describe("assignTimelineRoles", () => {
  test("协议齐全场景：角色全部来自结构化判定，inferred=false", () => {
    const steps: RoleAssignableStep[] = [
      commentaryStep("m1-commentary", "先查看项目结构", {
        phaseId: "phase-1",
        progressSequence: 1,
      }),
      toolCallStep("tc-1", { phaseId: "phase-1", timelineSequence: 2 }),
      // 该 commentary 只带来源消息的 public_progress 标记，无步骤级协议字段
      commentaryStep("m2-commentary", "已确认入口文件位置"),
    ];
    const assigned = assignTimelineRoles(steps, {
      publicProgressMessageIds: new Set(["m1-commentary", "m2-commentary"]),
    });

    expect(assigned.map((s) => s.role)).toEqual([
      "intent",
      "execution",
      "fact",
    ]);
    expect(assigned.every((s) => s.inferred === false)).toBe(true);
    // 顺序保持不变
    expect(assigned.map((s) => s.id)).toEqual([
      "m1-commentary",
      "tc-1",
      "m2-commentary",
    ]);
  });

  test("裸场景：无协议字段时按位置 fallback，inferred=true，顺序保持", () => {
    const steps: RoleAssignableStep[] = [
      reasoningStep("r1", "我先搜索相关文件"),
      toolCallStep("tc1"),
      toolCallStep("tc2"),
      reasoningStep("r2", "已确认配置存在"),
    ];
    const assigned = assignTimelineRoles(steps);

    expect(assigned.map((s) => s.role)).toEqual([
      "intent",
      "execution",
      "execution",
      "fact",
    ]);
    expect(assigned.every((s) => s.inferred === true)).toBe(true);
    expect(assigned.map((s) => s.id)).toEqual(["r1", "tc1", "tc2", "r2"]);
  });

  test("去重兼容：同一 fingerprint 文本不会同时被标成 intent 与 fact", () => {
    const steps: RoleAssignableStep[] = [
      reasoningStep("r1", "相同的旁白文本"),
      toolCallStep("tc1"),
      // 位于工具调用之后，按位置本应标 fact，但文本已被标过 intent
      reasoningStep("r2", "相同的旁白文本"),
    ];
    const assigned = assignTimelineRoles(steps);

    expect(assigned[0]!.role).toBe("intent");
    expect(assigned[2]!.role).toBe("intent");
    // fingerprint 归一化口径与时间线去重逻辑一致（大小写 / 空白不敏感）
    expect(assigned[0]!.role).toBe(assigned[2]!.role);
  });
});

describe("roleForStep", () => {
  test("toolCall 无协议字段 → execution 且 inferred=true", () => {
    const inference = roleForStep(toolCallStep("tc"), {
      hasExecutionBefore: false,
    });
    expect(inference).toEqual({ role: "execution", inferred: true });
  });

  test("工具调用之后带协议字段的 commentary → fact 且 inferred=false", () => {
    const inference = roleForStep(
      commentaryStep("c", "已确认", { phaseId: "p1" }),
      { hasExecutionBefore: true },
    );
    expect(inference).toEqual({ role: "fact", inferred: false });
  });

  test("首个 commentary（协议齐全）→ intent", () => {
    const inference = roleForStep(
      commentaryStep("c", "计划先做排查", { progressSequence: 0 }),
      { hasExecutionBefore: false },
    );
    expect(inference).toEqual({ role: "intent", inferred: false });
  });

  test("actionCallback 视为 execution", () => {
    const inference = roleForStep(
      { id: "a1", type: "actionCallback", actionText: "Read file: a.ts" },
      { hasExecutionBefore: false },
    );
    expect(inference.role).toBe("execution");
  });
});

describe("narrativeFingerprint", () => {
  test("与既有去重口径一致：忽略大小写与多余空白", () => {
    expect(narrativeFingerprint("  Hello   World ")).toBe(
      narrativeFingerprint("hello world"),
    );
  });
});

describe("isAnswerContent", () => {
  const messages = [
    { id: "h1", type: "human", content: "问题" },
    { id: "a1", type: "ai", content: "中间过程说明" },
    {
      id: "a2",
      type: "ai",
      content: "",
      tool_calls: [{ id: "t1", name: "read_file", args: {} }],
    },
    { id: "a3", type: "ai", content: "最终回答" },
  ] as unknown as Message[];

  test("最后一条含可见正文的 ai 消息是最终回答", () => {
    expect(isAnswerContent(messages[3]!, messages)).toBe(true);
  });

  test("中间的 ai 正文消息不是最终回答", () => {
    expect(isAnswerContent(messages[1]!, messages)).toBe(false);
  });

  test("无可见正文的 ai 消息不是最终回答", () => {
    expect(isAnswerContent(messages[2]!, messages)).toBe(false);
  });

  test("非 ai 消息不是最终回答", () => {
    expect(isAnswerContent(messages[0]!, messages)).toBe(false);
  });

  test("数组 content 中只有 thinking 部分不算可见正文", () => {
    const thread = [
      {
        id: "a1",
        type: "ai",
        content: [{ type: "thinking", thinking: "内部推理" }],
      },
      { id: "a2", type: "ai", content: [{ type: "text", text: "答案" }] },
    ] as unknown as Message[];
    expect(isAnswerContent(thread[0]!, thread)).toBe(false);
    expect(isAnswerContent(thread[1]!, thread)).toBe(true);
  });
});
