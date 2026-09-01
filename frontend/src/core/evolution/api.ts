import { getBackendBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";

async function evolutionFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 8_000);
  const abortFromCaller = () => controller.abort();
  init?.signal?.addEventListener("abort", abortFromCaller, { once: true });
  try {
    return await fetch(`${getBackendBaseURL()}${path}`, {
      ...init,
      signal: controller.signal,
    });
  } finally {
    window.clearTimeout(timeoutId);
    init?.signal?.removeEventListener("abort", abortFromCaller);
  }
}

export interface EvolutionOverview {
  skills: {
    total: number;
    auto_extracted: number;
    manual: number;
    avg_success_rate: number;
  };
  memory: {
    total_facts: number;
    categories: {
      memories: number;
      rules: number;
      trajectories: number;
    };
  };
  knowledge_graph: { nodes: number; edges: number } | null;
  learning_events: number;
  improvement_score: number;
  proactive_learning: {
    enabled: boolean;
    is_running: boolean;
    total_reports: number;
    subscriptions: number;
    enabled_subscriptions: number;
    last_report_at: string | null;
    total_skills_created: number;
  };
  source: string;
}

export interface LearningCurvePoint {
  week: string;
  success_rate: number;
  avg_duration_ms: number;
  skills_used: number;
}

export interface SkillPerformance {
  name: string;
  usage_count: number;
  success_count: number;
  success_rate: number;
  avg_cost_usd: number;
  avg_tokens: number;
  source: string;
}

export interface MemoryGrowthPoint {
  date: string;
  fact: number;
  preference: number;
  learned_skill: number;
  relationship: number;
}

export interface Recommendation {
  type: string;
  title: string;
  description: string;
  severity: "info" | "warning" | "critical";
  action_label: string;
  meta: Record<string, unknown>;
}

export interface EvolutionStoryChange {
  kind: "rule" | "memory" | "skill";
  title: string;
  content: string;
  effect: string;
}

export interface EvolutionStoryObservation {
  task_id: string;
  thread_id: string | null;
  title: string;
  timestamp: string;
  status: string;
  success: boolean;
  step_count: number;
  tools: string[];
  learning_points: string[];
}

export interface EvolutionStory {
  has_real_change: boolean;
  observed_task_count: number;
  durable_change_count: number;
  rule_count: number;
  memory_count: number;
  skill_count: number;
  changes: EvolutionStoryChange[];
  observations: EvolutionStoryObservation[];
}

export interface FitnessReport {
  ok: boolean;
  agent_id: string;
  ts: string;
  l1: {
    score: number;
    trend: string;
    success_rate: number;
    avg_rounds: number;
  } | null;
  l2: {
    score: number;
    dominant_failure: string;
    action: string;
    confidence: number;
  } | null;
  combined: number;
  verdict: string;
}

export interface DriftReport {
  ok: boolean;
  agent_id: string;
  ts: string;
  has_drift: boolean;
  max_severity: string;
  events: Array<{ kind: string; severity: string; detail: string }>;
}

export interface LedgerRecord {
  id: string;
  kind: string;
  description: string;
  status: string;
  proposer: string;
  ts: string;
  fitness_before: number | null;
  fitness_after: number | null;
}

export interface CanaryState {
  skill_name: string;
  phase: string;
  sample_count: number;
  success_count: number;
  current_rate: number;
  entered_ts: string;
}

export interface CodexGapCapability {
  id: string;
  area: "codex_parity" | "echo_advantage";
  title: string;
  why: string;
  score: number;
  target_score: number;
  status: string;
  next_actions: string[];
}

export interface CodexGapReport {
  ok: boolean;
  schema: string;
  parity_score: number;
  advantage_score: number;
  combined_score: number;
  verdict: string;
  capabilities: CodexGapCapability[];
  top_gaps?: CodexGapCapability[];
  error?: string;
}

export interface AgentBenchmarkReport {
  ok: boolean;
  schema: string;
  score: number;
  passed: number;
  total: number;
  ready: boolean;
  cases: Array<{
    id: string;
    title: string;
    dimension: string;
    passed: boolean;
    next_action: string;
  }>;
  error?: string;
}

export interface DualHelixEvidence {
  ok: boolean;
  schema: string;
  paired_count: number;
  unpaired_count: number;
  echo_wins: number;
  codex_wins: number;
  ties: number;
  echo_win_rate: number | null;
  evidence_quality?: "controlled_same_task" | "observational";
  controlled?: ControlledExperimentEvidence;
  strands: Record<
    "echo" | "codex",
    { samples: number; successes: number; success_rate: number | null }
  >;
  pairs: Array<{
    goal_fingerprint: string;
    goal: string;
    winner: "echo" | "codex" | "tie";
    echo: {
      outcome: "success" | "failure";
      model: string | null;
      ts: string;
    };
    codex: { outcome: "success" | "failure"; model: string | null; ts: string };
  }>;
  error?: string;
}

export interface ControlledExperimentEvidence {
  ok: boolean;
  schema: "echo.evolution.pair_evidence.v1" | string;
  generated_at: string;
  trial_count: number;
  paired_count: number;
  pairable_key_count: number;
  unpaired_key_count: number;
  echo_wins: number;
  codex_wins: number;
  ties: number;
  excluded: {
    infrastructure_failed: number;
    incomplete: number;
    hard_gate_failed: number;
    duplicate_engine_trial: number;
  };
  primary_metric: string;
  pairs: Array<{
    pair_key: string;
    experiment_id: string;
    case_id: string;
    task_spec_hash: string;
    trial_index: number;
    goal: string;
    domain: string;
    winner: "echo" | "codex" | "tie";
  }>;
}

export interface EvolutionCandidateList {
  ok: boolean;
  schema: string;
  total: number;
  by_status: Record<string, number>;
  by_gene_type: Record<string, number>;
  candidates: Array<{
    candidate_id: string;
    gene_type: "prompt" | "skill" | "routing" | "workflow" | "role" | "policy";
    scope: string;
    proposer: string;
    status:
      | "proposed"
      | "validated"
      | "shadow"
      | "canary"
      | "promoted"
      | "rejected"
      | "rolled_back";
    role_id: string;
    task_domain: string;
    risk_level: string;
    hard_gate_passed: boolean;
    hard_gate_results: Record<string, boolean>;
    metric_vector: Record<string, number>;
    experiment_ids: string[];
    metadata: Record<string, unknown>;
    deployment_key: string;
    runtime_consumer_ready: boolean;
    created_at: string;
    updated_at: string;
    canary?: {
      skill_name: string;
      phase: "canary_5" | "canary_25" | "canary_50" | "full" | "rolled_back";
      sample_count: number;
      success_count: number;
      failure_count: number;
      current_rate: number;
    } | null;
  }>;
}

export interface CandidateCanaryStatus {
  ok: boolean;
  schema: string;
  candidate: EvolutionCandidateList["candidates"][number];
  canary: {
    skill_name: string;
    phase: "canary_5" | "canary_25" | "canary_50" | "full" | "rolled_back";
    sample_count: number;
    success_count: number;
    failure_count: number;
    current_rate: number;
  } | null;
}

export interface DualHelixShadowStatus {
  ok: boolean;
  schema?: string;
  enabled: boolean;
  isolation?: string;
  runs: Array<{
    run_id: string;
    goal: string;
    primary_engine: "echo" | "codex";
    shadow_engine: "echo" | "codex";
    status: string;
    created_at: string;
    updated_at?: string;
    source_thread_id?: string | null;
    source_message_id?: string | null;
    candidate_id?: string | null;
    experiment_id?: string | null;
    result?: string | null;
    verdict?: "pass" | "fail" | "inconclusive" | null;
    hard_gates?: Record<string, boolean> | null;
    evidence?: string[] | null;
    recommendations?: string[] | null;
    error?: string | null;
  }>;
  error?: string;
}

export async function getEvolutionOverview(): Promise<EvolutionOverview> {
  const res = await evolutionFetch("/api/evolution/overview", {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to load evolution overview: ${res.statusText}`);
  return (await res.json()) as EvolutionOverview;
}

export async function getCodexGapReport(): Promise<CodexGapReport> {
  const res = await evolutionFetch("/api/evolution/codex-gap", {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to load Codex gap report: ${res.statusText}`);
  return (await res.json()) as CodexGapReport;
}

export async function getAgentBenchmarkReport(): Promise<AgentBenchmarkReport> {
  const res = await evolutionFetch("/api/evolution/agent-benchmark", {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to load agent benchmark: ${res.statusText}`);
  return (await res.json()) as AgentBenchmarkReport;
}

export async function getDualHelixEvidence(): Promise<DualHelixEvidence> {
  const res = await evolutionFetch("/api/evolution/dual-helix/evidence", {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to load dual-helix evidence: ${res.statusText}`);
  return (await res.json()) as DualHelixEvidence;
}

export async function getControlledExperimentEvidence(): Promise<ControlledExperimentEvidence> {
  const res = await evolutionFetch("/api/evolution/experiments/evidence", {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(
      `Failed to load controlled experiment evidence: ${res.statusText}`,
    );
  return (await res.json()) as ControlledExperimentEvidence;
}

export async function getEvolutionCandidates(): Promise<EvolutionCandidateList> {
  const res = await evolutionFetch("/api/evolution/candidates", {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to load evolution candidates: ${res.statusText}`);
  return (await res.json()) as EvolutionCandidateList;
}

export async function registerCandidateCanary(
  candidateId: string,
): Promise<CandidateCanaryStatus> {
  const res = await evolutionFetch(
    `/api/evolution/candidates/${encodeURIComponent(candidateId)}/canary/register`,
    { method: "POST", headers: jsonAuthHeaders() },
  );
  if (!res.ok) {
    const detail = (await res.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(detail?.detail || `Failed to register candidate canary`);
  }
  return (await res.json()) as CandidateCanaryStatus;
}

export async function rollbackEvolutionCandidate(
  candidateId: string,
  reason = "operator rollback",
): Promise<CandidateCanaryStatus> {
  const res = await evolutionFetch(
    `/api/evolution/candidates/${encodeURIComponent(candidateId)}/rollback`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ reason }),
    },
  );
  if (!res.ok) {
    const detail = (await res.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(detail?.detail || `Failed to rollback candidate`);
  }
  return (await res.json()) as CandidateCanaryStatus;
}

export async function getDualHelixShadowStatus(): Promise<DualHelixShadowStatus> {
  const res = await evolutionFetch("/api/evolution/dual-helix/shadow/status", {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to load shadow status: ${res.statusText}`);
  return (await res.json()) as DualHelixShadowStatus;
}

export async function setDualHelixShadowEnabled(
  enabled: boolean,
): Promise<DualHelixShadowStatus> {
  const res = await evolutionFetch(
    "/api/evolution/dual-helix/shadow/settings",
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ enabled }),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to update shadow status: ${res.statusText}`);
  return (await res.json()) as DualHelixShadowStatus;
}

export interface DualHelixShadowRunRequest {
  goal: string;
  primary_engine: "echo" | "codex";
  primary_output: string;
  workspace_path?: string;
  source_thread_id?: string;
  source_message_id?: string;
  candidate_id?: string;
  experiment_id?: string;
}

export async function queueDualHelixShadowRun(
  body: DualHelixShadowRunRequest,
): Promise<DualHelixShadowStatus["runs"][number]> {
  const res = await evolutionFetch("/api/evolution/dual-helix/shadow/run", {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(
      detail?.detail || `Failed to queue shadow review: ${res.statusText}`,
    );
  }
  const value = (await res.json()) as DualHelixShadowStatus["runs"][number] & {
    ok?: boolean;
  };
  return value;
}

export async function getEvolutionStory(): Promise<EvolutionStory> {
  const res = await evolutionFetch("/api/evolution/story", {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to load evolution story: ${res.statusText}`);
  return (await res.json()) as EvolutionStory;
}

export async function getLearningCurve(
  weeks?: number,
): Promise<LearningCurvePoint[]> {
  const params = new URLSearchParams();
  if (weeks !== undefined) params.set("weeks", String(weeks));
  const qs = params.toString();
  const path = `/api/evolution/learning-curve${qs ? `?${qs}` : ""}`;
  const res = await evolutionFetch(path, { headers: authHeaders() });
  if (!res.ok)
    throw new Error(`Failed to load learning curve: ${res.statusText}`);
  return (await res.json()) as LearningCurvePoint[];
}

export async function getSkillPerformance(): Promise<SkillPerformance[]> {
  const res = await evolutionFetch("/api/evolution/skills/performance", {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to load skill performance: ${res.statusText}`);
  return (await res.json()) as SkillPerformance[];
}

export async function getMemoryGrowth(
  days?: number,
): Promise<MemoryGrowthPoint[]> {
  const params = new URLSearchParams();
  if (days !== undefined) params.set("days", String(days));
  const qs = params.toString();
  const path = `/api/evolution/memory/growth${qs ? `?${qs}` : ""}`;
  const res = await evolutionFetch(path, { headers: authHeaders() });
  if (!res.ok)
    throw new Error(`Failed to load memory growth: ${res.statusText}`);
  return (await res.json()) as MemoryGrowthPoint[];
}

export async function getRecommendations(): Promise<Recommendation[]> {
  const res = await evolutionFetch("/api/evolution/recommendations", {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to load recommendations: ${res.statusText}`);
  return (await res.json()) as Recommendation[];
}

export async function getFitness(
  agentId: string,
  window?: number,
): Promise<FitnessReport> {
  const params = new URLSearchParams();
  if (window !== undefined) params.set("window", String(window));
  const qs = params.toString();
  const url = `${getBackendBaseURL()}/api/evolution/fitness/${encodeURIComponent(agentId)}${qs ? `?${qs}` : ""}`;
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok)
    throw new Error(`Failed to load fitness report: ${res.statusText}`);
  return (await res.json()) as FitnessReport;
}

export async function getDrift(agentId: string): Promise<DriftReport> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/evolution/drift/${encodeURIComponent(agentId)}`,
    { headers: authHeaders() },
  );
  if (!res.ok)
    throw new Error(`Failed to load drift report: ${res.statusText}`);
  return (await res.json()) as DriftReport;
}

export async function getLedger(opts?: {
  status?: string;
  kind?: string;
  limit?: number;
}): Promise<{
  total: number;
  records: LedgerRecord[];
  stats: Record<string, unknown>;
}> {
  const params = new URLSearchParams();
  if (opts?.status) params.set("status", opts.status);
  if (opts?.kind) params.set("kind", opts.kind);
  if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const url = `${getBackendBaseURL()}/api/evolution/ledger${qs ? `?${qs}` : ""}`;
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Failed to load ledger: ${res.statusText}`);
  return (await res.json()) as {
    total: number;
    records: LedgerRecord[];
    stats: Record<string, unknown>;
  };
}

export async function getCanary(): Promise<{
  active_count: number;
  canaries: CanaryState[];
}> {
  const res = await fetch(`${getBackendBaseURL()}/api/evolution/canary`, {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to load canary state: ${res.statusText}`);
  return (await res.json()) as {
    active_count: number;
    canaries: CanaryState[];
  };
}

export async function rollbackCanary(
  skillName: string,
): Promise<{ ok: boolean; skill_name: string; phase: string }> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/evolution/canary/${encodeURIComponent(skillName)}/rollback`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
    },
  );
  if (!res.ok) throw new Error(`Failed to rollback canary: ${res.statusText}`);
  return (await res.json()) as {
    ok: boolean;
    skill_name: string;
    phase: string;
  };
}
