// ---------------------------------------------------------------------------
// Agent World Data Layer — extracted from agent-world-unified.tsx (2026-06)
//
// Pure data + type definitions + helper functions. No React, no UI. Keeping
// this file separate means:
//   1. Data mutations don't invalidate component memoization.
//   2. The root component file stays under the god-file threshold.
//   3. Data can be unit-tested independently.
// ---------------------------------------------------------------------------

import type { LucideIcon } from "lucide-react";
import {
  BotIcon,
  Code2Icon,
  Layers3Icon,
  LandmarkIcon,
  PaletteIcon,
  SearchCheckIcon,
  ShoppingBagIcon,
  WorkflowIcon,
} from "lucide-react";

import type { Agent, AgentWorldAgent } from "@/core/agents/types";
import {
  WHITE_GHOST_AGENT_IDS,
  WHITE_GHOST_AGENT_ORDER,
} from "@/core/agents/persona-policy";

/**
 * User-facing discovery domains. These deliberately do not mirror the raw
 * profile category: `expert`/`specialist` describe a member's form, while
 * commerce/finance/coding describe what the member helps with.
 */
export type AgentCategoryFilter =
  | "all"
  | "general"
  | "coding"
  | "research"
  | "creative"
  | "automation"
  | "ecommerce"
  | "finance";

/** @deprecated Prefer the primary-persona names from core/agents. */
export const LOCAL_AGENT_ORDER = WHITE_GHOST_AGENT_ORDER;
/** @deprecated Prefer the primary-persona names from core/agents. */
export const LOCAL_AGENT_IDS = WHITE_GHOST_AGENT_IDS;
export const LOCAL_AGENT_RANK = new Map<string, number>(
  LOCAL_AGENT_ORDER.map((id, index) => [id, index]),
);
export const AGENT_CATEGORY_FILTERS: AgentCategoryFilter[] = [
  "all",
  "general",
  "coding",
  "research",
  "creative",
  "automation",
  "ecommerce",
  "finance",
];
export const CATEGORY_ICONS: Record<AgentCategoryFilter, LucideIcon> = {
  all: Layers3Icon,
  general: BotIcon,
  coding: Code2Icon,
  research: SearchCheckIcon,
  creative: PaletteIcon,
  automation: WorkflowIcon,
  ecommerce: ShoppingBagIcon,
  finance: LandmarkIcon,
};

const DOMAIN_HINTS: Record<Exclude<AgentCategoryFilter, "all">, RegExp> = {
  general: /\b(?:assistant|general)\b|助手|通用/i,
  coding:
    /\b(?:coder|coding|code|developer|engineering|engineer|firmware|software|hardware|linux|android|algorithm|pcb|robotics)\b|编程|代码|开发|工程|固件|算法|硬件|软件/i,
  research: /\b(?:research|researcher|analysis|analyst)\b|研究|调研|分析/i,
  creative:
    /\b(?:creative|design|designer|media|image|video|audio|writer)\b|创意|设计|图像|视频|音频|写作/i,
  automation:
    /\b(?:automation|operator|workflow|desktop|browser)\b|自动化|工作流|桌面操作|浏览器操作/i,
  ecommerce:
    /\b(?:ecommerce|e-commerce|commerce|selling|retail|shopify|tiktok shop|product listing|merchandising)\b|电商|零售|带货|选品|商品运营|店铺/i,
  finance:
    /\b(?:finance|financial|stock|investment|investor|valuation|trading|audit|tax)\b|财经|金融|股票|投资|估值|交易|审计|税务/i,
};

const RAW_CATEGORY_DOMAINS: Record<string, AgentCategoryFilter> = {
  assistant: "general",
  coder: "coding",
  engineering: "coding",
  researcher: "research",
  creative: "creative",
  automation: "automation",
  finance: "finance",
  financial: "finance",
};

/** Return every business domain a member belongs to, in stable UI order. */
export function getAgentDomains(agent: AgentWorldAgent): AgentCategoryFilter[] {
  const domains = new Set<AgentCategoryFilter>();
  const rawCategory = String(agent.category ?? "").toLowerCase();
  const mappedCategory = RAW_CATEGORY_DOMAINS[rawCategory];
  if (mappedCategory) domains.add(mappedCategory);

  const searchable = [
    agent.id,
    agent.name,
    agent.display_name,
    agent.description,
    rawCategory,
    ...(agent.tags ?? []),
    ...(agent.tool_groups ?? []),
    ...(agent.key_skills ?? []),
    ...(agent.available_skills ?? []),
  ].join(" ");

  for (const category of AGENT_CATEGORY_FILTERS) {
    if (category !== "all" && DOMAIN_HINTS[category].test(searchable)) {
      domains.add(category);
    }
  }

  // `specialist`, `expert`, and `product` are profile shapes rather than
  // discoverable domains. Keep an otherwise unclassified member findable.
  if (domains.size === 0) domains.add("general");
  return AGENT_CATEGORY_FILTERS.filter(
    (category) => category !== "all" && domains.has(category),
  );
}

export function agentMatchesCategory(
  agent: AgentWorldAgent,
  category: AgentCategoryFilter,
): boolean {
  return category === "all" || getAgentDomains(agent).includes(category);
}

export function localAgentToWorldAgent(agent: Agent): AgentWorldAgent {
  const displayName = agent.display_name ?? agent.name;
  const toolGroups = agent.tool_groups ?? [];
  return {
    id: agent.name,
    name: agent.name,
    display_name: displayName,
    description: agent.description || `${displayName} Agent`,
    author: "EchoOS",
    category: toolGroups.length > 0 ? "automation" : "assistant",
    tags: toolGroups,
    icon: agent.icon || "🤖",
    avatar_url: agent.avatar_url ?? undefined,
    visual_urls: agent.visual_urls ?? undefined,
    model: agent.model ?? null,
    soul: agent.soul ?? null,
    tool_groups: toolGroups,
    private_skills: [],
    key_skills: [],
    available_skills: [],
    extra_affinity: [],
    version: "1.0.0",
    downloads: 0,
    rating: 4.8,
    rating_count: Math.max(1, toolGroups.length),
    is_featured: false,
    is_official: true,
    is_installed: true,
    created_at: new Date().toISOString(),
  };
}

export function worldAgentToAgent(agent: AgentWorldAgent): Agent {
  return {
    name: agent.id,
    display_name: agent.display_name,
    description: agent.description,
    icon: agent.icon,
    avatar_url: agent.avatar_url ?? null,
    visual_urls: agent.visual_urls ?? null,
    model: agent.model ?? null,
    tool_groups: agent.tool_groups ?? agent.tags,
    soul: agent.soul ?? null,
  };
}
