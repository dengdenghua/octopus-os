import { describe, expect, test } from "vitest";

import type { AgentWorldAgent } from "@/core/agents/types";

import {
  agentWorldIdentityKey,
  dedupeAgentWorldAgents,
  resolveHudAgent,
} from "./agent-world-unified";

function agent(overrides: Partial<AgentWorldAgent>): AgentWorldAgent {
  return {
    id: "agent",
    name: "agent",
    display_name: "Agent",
    description: "",
    author: "echo",
    category: "assistant",
    tags: [],
    icon: "🤖",
    version: "1.0.0",
    downloads: 0,
    rating: 0,
    rating_count: 0,
    is_featured: false,
    is_official: true,
    is_installed: true,
    created_at: "0",
    ...overrides,
  };
}

describe("Agent Hub dedupe", () => {
  test("uses character profile name as the display identity", () => {
    expect(
      agentWorldIdentityKey(
        agent({
          id: "echo_noah",
          name: "echo_noah",
          display_name: "Noah / Probability",
          character_profile: { name: "Noah" },
        }),
      ),
    ).toBe("noah");
  });

  test("collapses internal role agents and echo character duplicates", () => {
    const deduped = dedupeAgentWorldAgents([
      agent({
        id: "market_researcher",
        name: "market_researcher",
        display_name: "Noah",
        category: "researcher",
        is_official: true,
      }),
      agent({
        id: "echo_noah",
        name: "echo_noah",
        display_name: "Noah / Probability",
        author: "echo-universe-engine",
        category: "creative",
        is_official: false,
        character_profile: { name: "Noah" },
      }),
    ]);

    expect(deduped.map((item) => item.id)).toEqual(["market_researcher"]);
  });

  test("does not merge unrelated slash names outside known Echo characters", () => {
    const deduped = dedupeAgentWorldAgents([
      agent({ id: "sales", name: "sales", display_name: "Buyer" }),
      agent({
        id: "buyer_seller",
        name: "buyer_seller",
        display_name: "Buyer / Seller",
      }),
    ]);

    expect(deduped.map((item) => item.id)).toEqual(["sales", "buyer_seller"]);
  });
});

describe("resolveHudAgent", () => {
  const marketResearcher = agent({
    id: "market_researcher",
    name: "market_researcher",
    display_name: "Noah",
    category: "researcher",
  });
  const echoNoah = agent({
    id: "echo_noah",
    name: "echo_noah",
    display_name: "Noah / Probability",
    author: "echo-universe-engine",
    is_official: false,
    downloads: 5_000,
    character_profile: { name: "Noah" },
  });
  const eve = agent({ id: "general", name: "general", display_name: "Eve" });
  const all = [marketResearcher, echoNoah, eve];

  test("matches a name that survived dedupe directly", () => {
    const deduped = [echoNoah, eve];
    expect(resolveHudAgent(all, deduped, "general")?.id).toBe("general");
  });

  test("maps a deduped-away name onto its surviving identity", () => {
    // The switcher's Noah row is `market_researcher`, but the HUD keeps
    // `echo_noah` for the same character. A plain name match would miss and the
    // HUD would open on an arbitrary role.
    const deduped = [echoNoah, eve];
    expect(resolveHudAgent(all, deduped, "market_researcher")?.id).toBe(
      "echo_noah",
    );
  });

  test("also resolves by id", () => {
    expect(resolveHudAgent(all, [echoNoah], "market_researcher")?.id).toBe(
      "echo_noah",
    );
  });

  test("returns null for blank and unknown names", () => {
    expect(resolveHudAgent(all, [eve], "  ")).toBeNull();
    expect(resolveHudAgent(all, [eve], "nobody")).toBeNull();
  });

  test("returns null when nothing with that identity is visible", () => {
    // `market_researcher` exists but every Noah was filtered out of the HUD —
    // the caller must fall back rather than open a mismatched role.
    expect(resolveHudAgent(all, [eve], "market_researcher")).toBeNull();
  });
});
