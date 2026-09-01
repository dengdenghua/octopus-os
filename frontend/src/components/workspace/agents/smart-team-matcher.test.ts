import { describe, expect, it } from "vitest";

import type { AgentWorldAgent } from "@/core/agents/types";
import { buildSmartTeamPlan } from "./smart-team-matcher";

function agent(
  id: string,
  description: string,
  category: AgentWorldAgent["category"] = "specialist",
): AgentWorldAgent {
  return {
    id,
    name: id,
    display_name: id,
    description,
    author: "test",
    category,
    tags: [],
    icon: "🤖",
    version: "1",
    downloads: 0,
    rating: 4,
    rating_count: 1,
    is_featured: false,
    is_official: false,
    is_installed: true,
    created_at: "",
  };
}

describe("buildSmartTeamPlan", () => {
  it("selects domain experts without requiring a fixed persona", () => {
    const plan = buildSmartTeamPlan(
      "分析股票财报、公司估值和投资风险",
      [
        agent("general", "通用办公助理", "assistant"),
        agent("semiconductor_analyst", "股票、财报、估值与投资研究", "financial"),
        agent("risk_expert", "投资组合与风险分析", "financial"),
      ],
      2,
    );

    expect(plan.members.map((item) => item.id)).toEqual([
      "semiconductor_analyst",
      "risk_expert",
    ]);
    expect(plan.plugins.map((item) => item.id)).toContain("finance");
  });

  it("recommends a production toolchain for AI short drama", () => {
    const plan = buildSmartTeamPlan("制作 AI 漫剧剧本和分镜视频", [
      agent("director", "漫剧、分镜、视频与内容创意", "creative"),
    ]);

    expect(plan.members[0]?.id).toBe("director");
    expect(plan.plugins.map((item) => item.id)).toEqual(
      expect.arrayContaining(["image", "video", "documents"]),
    );
  });
});

