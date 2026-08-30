import { describe, expect, it } from "vitest";

import type { EvolutionOverview } from "@/core/evolution/api";

import { transformToAgentCard } from "./game-data-transformer";

function makeOverview(avgSuccessRate: number): EvolutionOverview {
  return {
    skills: {
      total: 3,
      auto_extracted: 1,
      manual: 2,
      avg_success_rate: avgSuccessRate,
    },
    memory: {
      total_facts: 2,
      categories: { memories: 1, rules: 1, trajectories: 0 },
    },
    knowledge_graph: null,
    learning_events: 12,
    improvement_score: 0.6,
    proactive_learning: {
      enabled: true,
      is_running: false,
      total_reports: 0,
      subscriptions: 0,
      enabled_subscriptions: 0,
      last_report_at: null,
      total_skills_created: 0,
    },
    source: "test",
  };
}

describe("transformToAgentCard", () => {
  it("converts avg_success_rate from 0-1 fraction to percentage", () => {
    const card = transformToAgentCard(
      "agent-code",
      "代码助手",
      "👨‍💻",
      makeOverview(0.875),
      [],
    );

    expect(card.successRate).toBe(88);
  });

  it("handles a mid-range success rate", () => {
    const card = transformToAgentCard(
      "agent-code",
      "代码助手",
      "👨‍💻",
      makeOverview(0.5),
      [],
    );

    expect(card.successRate).toBe(50);
  });

  it("handles zero success rate", () => {
    const card = transformToAgentCard(
      "agent-code",
      "代码助手",
      "👨‍💻",
      makeOverview(0),
      [],
    );

    expect(card.successRate).toBe(0);
  });
});
