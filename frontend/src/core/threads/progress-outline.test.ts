import { describe, expect, test } from "vitest";

import {
  buildProgressOutline,
  type ProgressOutlineStep,
} from "./progress-outline";

function reasoning(
  text: string,
  extra: Partial<ProgressOutlineStep> = {},
): ProgressOutlineStep {
  return { type: "reasoning", reasoning: text, ...extra };
}

function commentary(
  text: string,
  extra: Partial<ProgressOutlineStep> = {},
): ProgressOutlineStep {
  return { type: "commentary", commentary: text, ...extra };
}

function toolCall(extra: Partial<ProgressOutlineStep> = {}): ProgressOutlineStep {
  return { type: "toolCall", ...extra };
}

describe("buildProgressOutline", () => {
  test("空输入 → 空大纲", () => {
    expect(buildProgressOutline([])).toEqual([]);
  });

  test("≥3 轮场景：按 iteration 分组，每轮意图/执行计数/事实聚合正确", () => {
    const steps: ProgressOutlineStep[] = [
      reasoning("先查看项目结构", { iteration: 1, role: "intent" }),
      toolCall({ iteration: 1, role: "execution" }),
      toolCall({ iteration: 1, role: "execution" }),
      commentary("已确认入口文件位置", { iteration: 1, role: "fact" }),
      reasoning("修复构建错误", { iteration: 2, role: "intent" }),
      toolCall({ iteration: 2, role: "execution" }),
      commentary("已确认配置存在", { iteration: 2, role: "fact" }),
      commentary("已确认依赖版本", { iteration: 2, role: "fact" }),
      reasoning("运行测试验证", { iteration: 3, role: "intent" }),
      toolCall({ iteration: 3, role: "execution" }),
      toolCall({ iteration: 3, role: "execution" }),
      toolCall({ iteration: 3, role: "execution" }),
      commentary("测试全部通过", { iteration: 3, role: "fact" }),
    ];

    const outline = buildProgressOutline(steps);

    expect(outline).toHaveLength(3);
    expect(outline.map((round) => round.iteration)).toEqual([1, 2, 3]);
    expect(outline[0]).toEqual({
      iteration: 1,
      intentText: "先查看项目结构",
      executionCount: 2,
      facts: ["已确认入口文件位置"],
    });
    expect(outline[1]).toEqual({
      iteration: 2,
      intentText: "修复构建错误",
      executionCount: 1,
      facts: ["已确认配置存在", "已确认依赖版本"],
    });
    expect(outline[2]).toEqual({
      iteration: 3,
      intentText: "运行测试验证",
      executionCount: 3,
      facts: ["测试全部通过"],
    });
  });

  test("actionCallback 计入执行计数", () => {
    const outline = buildProgressOutline([
      reasoning("执行动作", { iteration: 1, role: "intent" }),
      { type: "actionCallback", iteration: 1, role: "execution" },
      toolCall({ iteration: 1, role: "execution" }),
    ]);
    expect(outline[0]?.executionCount).toBe(2);
  });

  test("无 role 时按位置 fallback：首个叙事为意图，执行之后为事实", () => {
    const outline = buildProgressOutline([
      reasoning("我先搜索相关文件", { iteration: 1 }),
      toolCall({ iteration: 1 }),
      toolCall({ iteration: 1 }),
      commentary("已确认配置存在", { iteration: 1 }),
      reasoning("继续修复", { iteration: 2 }),
      toolCall({ iteration: 2 }),
    ]);

    expect(outline).toHaveLength(2);
    expect(outline[0]?.intentText).toBe("我先搜索相关文件");
    expect(outline[0]?.executionCount).toBe(2);
    expect(outline[0]?.facts).toEqual(["已确认配置存在"]);
    // 第二轮首个叙事在执行之后 → 视为事实，意图为空（不编造）
    expect(outline[1]?.intentText).toBeNull();
    expect(outline[1]?.facts).toEqual(["继续修复"]);
    expect(outline[1]?.executionCount).toBe(1);
  });

  test("缺失 iteration 的步骤归入第 1 轮", () => {
    const outline = buildProgressOutline([
      reasoning("排查问题", { role: "intent" }),
      toolCall({ role: "execution" }),
    ]);
    expect(outline).toHaveLength(1);
    expect(outline[0]?.iteration).toBe(1);
  });

  test("每轮只保留首个意图条目", () => {
    const outline = buildProgressOutline([
      reasoning("第一版意图", { iteration: 1, role: "intent" }),
      reasoning("补充的意图", { iteration: 1, role: "intent" }),
    ]);
    expect(outline[0]?.intentText).toBe("第一版意图");
  });

  test("空文本叙事不产生意图或事实，但仍归轮", () => {
    const outline = buildProgressOutline([
      reasoning("   ", { iteration: 1, role: "intent" }),
      toolCall({ iteration: 1, role: "execution" }),
    ]);
    expect(outline).toHaveLength(1);
    expect(outline[0]?.intentText).toBeNull();
    expect(outline[0]?.facts).toEqual([]);
    expect(outline[0]?.executionCount).toBe(1);
  });

  test("长文本截断为一行：折叠换行并以 … 结尾", () => {
    const longText = `第一段\n第二行    多余空白 ${"很长的内容".repeat(30)}`;
    const outline = buildProgressOutline([
      reasoning(longText, { iteration: 1, role: "intent" }),
      commentary(longText, { iteration: 1, role: "fact" }),
    ]);

    const intent = outline[0]?.intentText ?? "";
    const fact = outline[0]?.facts[0] ?? "";
    expect(intent).not.toContain("\n");
    expect(intent.length).toBeLessThanOrEqual(120);
    expect(intent.endsWith("…")).toBe(true);
    expect(intent.startsWith("第一段 第二行 多余空白")).toBe(true);
    expect(fact).not.toContain("\n");
    expect(fact.length).toBeLessThanOrEqual(120);
    expect(fact.endsWith("…")).toBe(true);
  });

  test("短文本不截断", () => {
    const outline = buildProgressOutline([
      reasoning("简短意图", { iteration: 1, role: "intent" }),
    ]);
    expect(outline[0]?.intentText).toBe("简短意图");
  });
});
