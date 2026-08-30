import { describe, expect, test } from "vitest";

import {
  agentPhaseDisplayTitle,
  businessAgentPhaseKey,
  deriveAgentPhases,
  normalizeBusinessPhaseKey,
  progressForPhases,
} from "./agent-phases";
import type { LiveToolEvent } from "./live-tool-timeline";

function event(partial: Partial<LiveToolEvent>): LiveToolEvent {
  return {
    id: "event-1",
    name: "read_file",
    status: "done",
    startedAt: 1000,
    iteration: 0,
    ...partial,
  };
}

describe("agent phases", () => {
  test("does not turn an approval step into done when a run settles", () => {
    const state = deriveAgentPhases(
      [
        event({
          id: "approval",
          name: "write_text_file",
          status: "waiting_approval",
          input: { path: "plan.md" },
        }),
      ],
      { hasAnswer: true, runSettled: true },
    );

    expect(state.currentPhase?.status).toBe("waiting_approval");
    expect(state.currentPhase?.titleKey).toBe("genericExecute");
    expect(state.currentPhase?.title).toBe("Work through leads");
    expect(progressForPhases(state.phases, state.currentPhase!)).toEqual({
      current: 1,
      total: 2,
    });
  });

  test("keeps waiting approval active before the final answer", () => {
    const state = deriveAgentPhases([
      event({
        id: "approval",
        name: "write_text_file",
        status: "waiting_approval",
        input: { path: "plan.md" },
      }),
    ]);

    expect(state.currentPhase?.status).toBe("waiting_approval");
    expect(progressForPhases(state.phases, state.currentPhase!)).toEqual({
      current: 1,
      total: 2,
    });
  });

  test("keeps unfinished todo phases active when an interim answer exists", () => {
    const state = deriveAgentPhases(
      [
        event({
          id: "todo-1",
          name: "todo_write",
          status: "done",
          input: {
            items: [
              { content: "已确认调研范围", status: "completed" },
              { content: "撰写 plan.md", status: "completed" },
              {
                content: "执行 deep-research-swarm 多源调研",
                status: "pending",
              },
              { content: "正在汇总最终交付", status: "pending" },
            ],
          },
        }),
      ],
      { hasAnswer: true },
    );

    expect(state.phases.map((phase) => phase.status)).toEqual([
      "done",
      "done",
      "pending",
      "pending",
    ]);
    expect(progressForPhases(state.phases, state.currentPhase!)).toEqual({
      current: 3,
      total: 4,
    });
  });

  test("preserves unfinished todo phases after a settled answer", () => {
    const state = deriveAgentPhases(
      [
        event({
          id: "todo-1",
          name: "todo_write",
          status: "done",
          input: {
            items: [
              { content: "已确认调研范围", status: "completed" },
              { content: "撰写 plan.md", status: "completed" },
              {
                content: "执行 deep-research-swarm 多源调研",
                status: "pending",
              },
              { content: "正在汇总最终交付", status: "pending" },
            ],
          },
        }),
      ],
      { hasAnswer: true, runSettled: true },
    );

    expect(state.phases.map((phase) => phase.status)).toEqual([
      "done",
      "done",
      "pending",
      "pending",
    ]);
    expect(state.currentPhase?.status).toBe("pending");
    expect(progressForPhases(state.phases, state.currentPhase!)).toEqual({
      current: 3,
      total: 4,
    });
  });

  test("prefers source todo state over a later server phase projection", () => {
    const state = deriveAgentPhases(
      [
        event({
          id: "todo-source",
          name: "todo_write",
          input: {
            items: [
              { content: "Inspect architecture", status: "completed" },
              { content: "Implement fix", status: "in_progress" },
            ],
          },
        }),
        event({
          id: "server-phases:turn-1",
          name: "todo_write",
          startedAt: 2000,
          input: {
            source: "turn.phases",
            items: [
              { content: "Inspect architecture", status: "in_progress" },
              { content: "Implement fix", status: "pending" },
            ],
          },
        }),
      ],
      { hasAnswer: true, runSettled: true },
    );

    expect(state.phases.map((phase) => phase.status)).toEqual([
      "done",
      "running",
    ]);
  });

  test("marks the first unfinished todo phase failed when a settled run has no deliverable", () => {
    const state = deriveAgentPhases(
      [
        event({
          id: "todo-1",
          name: "todo_write",
          status: "done",
          input: {
            items: [
              { content: "create plan.md", status: "completed" },
              { content: "run deep research", status: "in_progress" },
              { content: "write long report", status: "pending" },
              { content: "deliver final answer", status: "pending" },
            ],
          },
        }),
      ],
      { runSettled: true, runFailed: true },
    );

    expect(state.phases.map((phase) => phase.status)).toEqual([
      "done",
      "error",
      "pending",
      "pending",
    ]);
    expect(state.currentPhase?.title).toBe("run deep research");
    expect(progressForPhases(state.phases, state.currentPhase!)).toEqual({
      current: 2,
      total: 4,
    });
  });

  test("keeps unfinished todo phases pending when the run is paused", () => {
    const state = deriveAgentPhases(
      [
        event({
          id: "todo-1",
          name: "todo_write",
          status: "done",
          input: {
            items: [
              { content: "confirm scope", status: "completed" },
              { content: "write plan.md", status: "completed" },
              { content: "run deep-research-swarm", status: "pending" },
              { content: "assemble final report", status: "pending" },
            ],
          },
        }),
      ],
      { hasAnswer: true, runSettled: true, paused: true },
    );

    expect(state.phases.map((phase) => phase.status)).toEqual([
      "done",
      "done",
      "pending",
      "pending",
    ]);
    expect(progressForPhases(state.phases, state.currentPhase!)).toEqual({
      current: 3,
      total: 4,
    });
  });

  test("allows only the earliest active todo phase to run", () => {
    const state = deriveAgentPhases([
      event({
        id: "todo-1",
        name: "todo_write",
        status: "done",
        input: {
          items: [
            { content: "create research plan", status: "completed" },
            { content: "deep research NAS market", status: "in_progress" },
            { content: "write research report", status: "in_progress" },
          ],
        },
      }),
    ]);

    expect(state.phases.map((phase) => phase.status)).toEqual([
      "done",
      "running",
      "pending",
    ]);
    expect(state.currentPhase?.title).toBe("deep research NAS market");
    expect(progressForPhases(state.phases, state.currentPhase!)).toEqual({
      current: 2,
      total: 3,
    });
  });

  test("keeps todo phase identity stable across progress updates and reordering", () => {
    const initial = deriveAgentPhases([
      event({
        id: "todo-initial",
        name: "todo_write",
        input: {
          items: [
            { content: "Inspect architecture", status: "in_progress" },
            { content: "Run verification", status: "pending" },
          ],
        },
      }),
    ]);
    const updated = deriveAgentPhases([
      event({
        id: "todo-updated",
        name: "todo_write",
        input: {
          items: [
            { content: "Run verification", status: "in_progress" },
            { content: "Inspect architecture", status: "completed" },
          ],
        },
      }),
    ]);

    expect(
      Object.fromEntries(
        initial.phases.map((phase) => [phase.title, phase.id]),
      ),
    ).toEqual(
      Object.fromEntries(
        updated.phases.map((phase) => [phase.title, phase.id]),
      ),
    );
  });

  test("plain research-shaped events use generic phases instead of a fixed research template", () => {
    const state = deriveAgentPhases([
      event({
        id: "ev-search",
        name: "web_search",
        status: "done",
      }),
      event({
        id: "ev-write",
        name: "write_text_file",
        status: "running",
      }),
    ]);

    expect(state.phases.length).toBe(2);
    const statuses = state.phases.map((phase) => phase.status);
    expect(state.phases[0]?.id).toBe("generic:execute");
    expect(state.phases[1]?.id).toBe("generic:deliver");
    expect(statuses).toEqual(["running", "pending"]);
  });

  test("prioritizes waiting approval over running in generic phases", () => {
    const state = deriveAgentPhases([
      event({
        id: "ev-search-running",
        name: "web_search",
        status: "running",
        input: { query: "market signal" },
      }),
      event({
        id: "ev-fetch-approval",
        name: "fetch_url",
        status: "waiting_approval",
        input: { url: "https://example.com/report" },
      }),
    ]);

    expect(state.currentPhase?.status).toBe("waiting_approval");
    expect(state.phases.map((phase) => phase.status)).toEqual([
      "waiting_approval",
    ]);
  });

  test("treats manual verification-required audit as waiting in generic phases", () => {
    const state = deriveAgentPhases([
      event({
        id: "read-package",
        name: "read_file",
        status: "done",
        input: { path: "package.json" },
      }),
      event({
        id: "verify-required",
        name: "verification:manual",
        status: "error",
        input: { command: "verification required" },
        output: {
          summary:
            "Code changes were produced but no verification step was recorded before final answer.",
        },
      }),
    ]);

    expect(state.blocks.map((block) => [block.id, block.status])).toEqual([
      ["read-package", "done"],
      ["verify-required", "waiting_approval"],
    ]);
    expect(state.currentPhase?.status).toBe("waiting_approval");
    expect(state.phases.map((phase) => phase.status)).toEqual([
      "done",
      "waiting_approval",
    ]);
    expect(state.phases[0]?.blockIds).toEqual(["read-package"]);
    expect(state.phases[1]?.blockIds).toEqual(["verify-required"]);
  });

  test("settled research-shaped events still resolve through generic phases", () => {
    const state = deriveAgentPhases(
      [
        event({
          id: "ev-search",
          name: "web_search",
          status: "done",
        }),
        event({
          id: "ev-report",
          name: "write_text_file",
          status: "done",
          input: { path: "report.md" },
        }),
      ],
      { hasAnswer: true, runSettled: true },
    );

    expect(state.phases.length).toBe(2);
    expect(state.phases.map((phase) => phase.status)).toEqual(["done", "done"]);
    expect(state.currentPhase?.titleKey).toBe("genericDeliver");
    expect(state.currentPhase?.title).toBe("Pull the answer together");
    expect(progressForPhases(state.phases, state.currentPhase!)).toEqual({
      current: 2,
      total: 2,
    });
  });

  test("maps free-form phase titles to business phase keys", () => {
    expect(businessAgentPhaseKey("分析需求并给出方案")).toBe("planning");
    expect(businessAgentPhaseKey("了解代码结构")).toBe("exploring");
    expect(businessAgentPhaseKey("修改登录页实现")).toBe("implementing");
    expect(businessAgentPhaseKey("运行测试验证修改")).toBe("testing");
    expect(businessAgentPhaseKey("部署到预发环境")).toBe("deploying");
    expect(businessAgentPhaseKey("随便聊聊")).toBeNull();
  });

  test("normalizeBusinessPhaseKey accepts known phase kinds and rejects others", () => {
    expect(normalizeBusinessPhaseKey("planning")).toBe("planning");
    expect(normalizeBusinessPhaseKey("exploring")).toBe("exploring");
    expect(normalizeBusinessPhaseKey("implementing")).toBe("implementing");
    expect(normalizeBusinessPhaseKey("testing")).toBe("testing");
    expect(normalizeBusinessPhaseKey("deploying")).toBe("deploying");
    expect(normalizeBusinessPhaseKey("other")).toBeNull();
    expect(normalizeBusinessPhaseKey("unknown_value")).toBeNull();
    expect(normalizeBusinessPhaseKey(null)).toBeNull();
    expect(normalizeBusinessPhaseKey(undefined)).toBeNull();
  });

  test("prefers backend phaseKind over local title mapping", () => {
    const state = deriveAgentPhases([
      event({
        id: "todo-1",
        name: "todo_write",
        status: "done",
        input: {
          items: [
            {
              // local mapping would yield "exploring" (analyze)
              content: "analyze requirements",
              status: "completed",
              phaseKind: "implementing",
            },
            {
              // local mapping would yield "implementing" (write)
              content: "write report",
              status: "in_progress",
              phaseKind: "deploying",
            },
          ],
        },
      }),
    ]);

    expect(state.phases.map((phase) => phase.businessKey)).toEqual([
      "implementing",
      "deploying",
    ]);
  });

  test("falls back to local title mapping when backend phaseKind is absent", () => {
    const state = deriveAgentPhases([
      event({
        id: "todo-1",
        name: "todo_write",
        status: "done",
        input: {
          items: [
            { content: "分析需求并给出方案", status: "completed" },
            { content: "运行测试验证修改", status: "in_progress" },
          ],
        },
      }),
    ]);

    expect(state.phases.map((phase) => phase.businessKey)).toEqual([
      "planning",
      "testing",
    ]);
  });

  test("falls back to local title mapping when backend phaseKind is invalid", () => {
    const state = deriveAgentPhases([
      event({
        id: "todo-1",
        name: "todo_write",
        status: "done",
        input: {
          items: [
            {
              content: "分析需求并给出方案",
              status: "completed",
              phaseKind: "other",
            },
            {
              content: "运行测试验证修改",
              status: "in_progress",
              phaseKind: "unknown_value",
            },
          ],
        },
      }),
    ]);

    expect(state.phases.map((phase) => phase.businessKey)).toEqual([
      "planning",
      "testing",
    ]);
  });

  test("leaves businessKey undefined when neither backend nor local mapping resolve", () => {
    const state = deriveAgentPhases([
      event({
        id: "todo-1",
        name: "todo_write",
        status: "done",
        input: {
          items: [
            { content: "随便聊聊", status: "completed" },
            { content: "打发时间", status: "in_progress" },
          ],
        },
      }),
    ]);

    expect(state.phases.map((phase) => phase.businessKey)).toEqual([
      undefined,
      undefined,
    ]);
  });

  test("tolerates snake_case phase_kind when the adapter does not camelCase", () => {
    const state = deriveAgentPhases([
      event({
        id: "todo-1",
        name: "todo_write",
        status: "done",
        input: {
          items: [
            {
              content: "analyze requirements",
              status: "completed",
              phase_kind: "exploring",
            },
            {
              content: "write report",
              status: "in_progress",
              phase_kind: "deploying",
            },
          ],
        },
      }),
    ]);

    expect(state.phases.map((phase) => phase.businessKey)).toEqual([
      "exploring",
      "deploying",
    ]);
  });

  test("uses a generic preparation label for a single read to avoid duplicating the context list", () => {
    const state = deriveAgentPhases([
      event({
        id: "read-1",
        name: "read_file",
        input: { path: "runtime/core/cerebrum/react_public_updates.py" },
      }),
    ]);
    expect(state.phases).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          title: "Gather context",
          titleKey: "genericPrepare",
        }),
      ]),
    );
  });

  test("keeps real todo titles instead of replacing them with business labels", () => {
    expect(
      agentPhaseDisplayTitle(
        {
          id: "phase-1",
          title: "核对消息分组和右栏联动边界",
          status: "running",
          businessKey: "testing",
          eventIds: [],
        },
        {
          genericPrepare: "准备",
          genericExecute: "执行",
          genericDeliver: "交付",
          planning: "制定方案",
          exploring: "调查分析",
          implementing: "执行修改",
          testing: "验证修改",
          deploying: "交付上线",
        },
      ),
    ).toBe("核对消息分组和右栏联动边界");
  });
});
