import { describe, expect, it } from "vitest";

import type { AgentWorldAgent } from "@/core/agents/types";

import { agentMatchesCategory, getAgentDomains } from "./agent-world-data";

function createAgent(
  overrides: Partial<AgentWorldAgent> & Pick<AgentWorldAgent, "id">,
): AgentWorldAgent {
  return {
    name: overrides.id,
    display_name: overrides.id,
    description: "",
    author: "EchoOS",
    category: "specialist",
    tags: [],
    icon: "🤖",
    version: "1.0.0",
    downloads: 0,
    rating: 0,
    rating_count: 0,
    is_featured: false,
    is_official: true,
    is_installed: true,
    created_at: "2026-08-24T00:00:00Z",
    ...overrides,
  };
}

describe("agent business-domain discovery", () => {
  it("finds the ecommerce core member without exposing specialist as a domain", () => {
    const agent = createAgent({
      id: "ecommerce_mind",
      display_name: "电商大脑",
      category: "specialist",
    });

    expect(getAgentDomains(agent)).toContain("ecommerce");
    expect(agentMatchesCategory(agent, "ecommerce")).toBe(true);
  });

  it("normalizes both finance category spellings", () => {
    const financeAgent = createAgent({
      id: "valuation-analyst",
      category: "finance" as AgentWorldAgent["category"],
    });
    const financialAgent = createAgent({
      id: "twin_finance",
      category: "financial",
    });

    expect(getAgentDomains(financeAgent)).toContain("finance");
    expect(getAgentDomains(financialAgent)).toContain("finance");
  });

  it("allows one member to appear in more than one useful domain", () => {
    const agent = createAgent({
      id: "vibe_selling",
      description: "创意电商带货与商品设计",
      category: "creative",
    });

    expect(getAgentDomains(agent)).toEqual(["creative", "ecommerce"]);
  });

  it("keeps an unclassified expert findable under general", () => {
    const agent = createAgent({ id: "domain_expert" });
    expect(getAgentDomains(agent)).toEqual(["general"]);
  });
});
