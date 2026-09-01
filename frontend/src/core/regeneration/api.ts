import { getBackendBaseURL } from "@/core/config";
import { authHeaders } from "@/core/auth/api";

export interface RegenerationSchedulerStatus {
  running: boolean;
  tick_count: number;
  last_summary: Record<string, unknown>;
  interval_sec: number;
  error?: string;
}

export interface LearnedRule {
  rule_id: string;
  sucker_id: string;
  error_signature: string;
  pattern: string;
  mitigation: string;
  hit_count: number;
  severity: "low" | "mid" | "high";
  source_trajectory_ids?: string[];
}

export interface LearnedRulesPayload {
  tick: number;
  ts: number;
  trajectories_scanned: number;
  failure_count: number;
  clusters_formed: number;
  rules: LearnedRule[];
}

export interface RecipeScore {
  recipe_id: string;
  uses: number;
  successes: number;
  success_rate: number;
  avg_cost_usd: number;
  avg_tokens: number;
  avg_step_count: number;
  first_seen: string;
  last_seen: string;
  verdict: string;
  score: number;
}

export interface RecipeScoresPayload {
  tick: number;
  ts: number;
  scores: RecipeScore[];
}

export interface GepaProposalResult {
  recipe_id: string;
  ok: boolean;
  skipped?: boolean;
  reason?: string;
  winner?: string;
  weights?: Record<string, number>;
  rationale?: string;
  applied?: boolean;
}

export interface GepaProposalsPayload {
  tick: number;
  ts: number;
  auto_apply: boolean;
  elapsed_s: number;
  recipes_scanned: number;
  recipes_promoted: number;
  results: GepaProposalResult[];
}

export interface CamouflageVariantInfo {
  name: string;
  weight: number;
  origin: string;
  generation: number;
  parents: string[];
  suffix_chars: number;
}

export interface CamouflageAutoRetireStats {
  total_ticks: number;
  total_steps: number;
  total_retired: number;
  total_boosted: number;
  last_step_at: number;
  last_step_summary: string;
}

export interface CamouflageStatus {
  enabled: boolean;
  running: boolean;
  tick_count?: number;
  interval_sec?: number;
  last_error?: string;
  last_step_summary?: string;
  variants?: CamouflageVariantInfo[];
  auto_retire?: CamouflageAutoRetireStats;
  error?: string;
}

export interface RegenerationStatus {
  scheduler: RegenerationSchedulerStatus;
  camouflage?: CamouflageStatus;
  learned_rules: LearnedRulesPayload | null;
  learned_memories: Record<string, unknown> | null;
  workflow_proposals: Record<string, unknown> | null;
  recipe_scores: RecipeScoresPayload | null;
  gepa_proposals: GepaProposalsPayload | null;
  forged_skills: Record<string, unknown> | null;
}

export async function getRegenerationStatus(): Promise<RegenerationStatus> {
  const res = await fetch(`${getBackendBaseURL()}/api/regeneration/status`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed: ${res.statusText}`);
  return (await res.json()) as RegenerationStatus;
}
