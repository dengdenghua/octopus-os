import { describe, expect, test } from "vitest";

import { extractClarificationQuestionnaire } from "../clarification-questionnaire";
import { parseClarificationChoices } from "./clarification-choice-card";

describe("parseClarificationChoices", () => {
  test("builds clickable combinations from scoped research questions", () => {
    const parsed = parseClarificationChoices(`
请帮我确认两件事：

1. 调研对象（选一个）

A. 消费级 / SOHO NAS
B. 企业级存储

2. 调研目的（选一个）

① 市场调研
② 产品选型
`);

    expect(parsed?.defaultChoice.text).toBe("A①");
    expect(parsed?.choices.map((choice) => choice.text)).toEqual([
      "A①",
      "A②",
      "B①",
      "B②",
    ]);
  });

  test("ignores ordinary option-looking text without a clarification cue", () => {
    expect(parseClarificationChoices("A. Alpha\nB. Beta")).toBeNull();
  });

  test("keeps duplicate labels render-safe when the model repeats option letters", () => {
    const parsed = parseClarificationChoices(`
请选择一个方向：

A. 第一组 A
B. 第一组 B

A. 第二组 A
B. 第二组 B
`);

    expect(parsed?.choices.map((choice) => choice.key)).toEqual([
      "A",
      "B",
      "A",
      "B",
    ]);
  });
});

describe("extractClarificationQuestionnaire", () => {
  test("extracts a structured questionnaire from model content and hides the payload", () => {
    const parsed = extractClarificationQuestionnaire(`
I need to clarify a few choices first.

<clarification_questionnaire>
{
  "type": "clarification_questionnaire",
  "title": "Clarify requirements",
  "prompt": "Research a promising niche market",
  "questions": [
    {
      "id": "goal",
      "title": "What decision should this research support?",
      "options": [
        {
          "value": "pick",
          "label": "Pick one market",
          "description": "Compare candidates and choose the best option."
        },
        {
          "value": "map",
          "label": "Map the market"
        }
      ]
    }
  ]
}
</clarification_questionnaire>
`);

    expect(parsed?.visibleContent).toBe(
      "I need to clarify a few choices first.",
    );
    expect(parsed?.payload.title).toBe("Clarify requirements");
    expect(
      parsed?.payload.questions[0]?.options.map((option) => option.label),
    ).toEqual(["Pick one market", "Map the market"]);
  });

  test("extracts a structured questionnaire from a fenced JSON block", () => {
    const parsed = extractClarificationQuestionnaire(`
\`\`\`json
{
  "type": "clarification_questionnaire",
  "questions": [
    {
      "title": "Choose a scope",
      "options": ["China", "Global"]
    }
  ]
}
\`\`\`
`);

    expect(parsed?.visibleContent).toBe("");
    expect(parsed?.payload.questions[0]?.id).toBe("question_1");
    expect(parsed?.payload.questions[0]?.options[1]?.label).toBe("Global");
  });

  test("converts ask_user_question tool results into a questionnaire", () => {
    const parsed = extractClarificationQuestionnaire(
      JSON.stringify({
        ok: true,
        posted: true,
        question: "Which output format do you prefer?",
        options: ["Short answer", "Detailed report"],
        allow_other: true,
      }),
    );

    expect(parsed?.payload.questions[0]?.title).toBe(
      "Which output format do you prefer?",
    );
    expect(
      parsed?.payload.questions[0]?.options.map((option) => option.label),
    ).toEqual(["Short answer", "Detailed report"]);
  });

  test("does not infer a research questionnaire from ordinary clarification prose", () => {
    const parsed = extractClarificationQuestionnaire(`
先问一个关键问题再动手，避免方向偏了：

**你有偏好的行业方向或资源背景吗？** 比如：

- 你在某个行业有供应链/技术/渠道资源？
- 关注消费品、B2B SaaS、硬件，还是其他？
- 预算规模和团队能力大致是什么量级？

一句话告诉我方向，我直接开挖。
`);

    expect(parsed).toBeNull();
  });

  test("ignores unrelated JSON", () => {
    expect(
      extractClarificationQuestionnaire(
        '```json\n{"type":"not_a_questionnaire"}\n```',
      ),
    ).toBeNull();
  });
});
