import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

export const E2E_SURPASS_TARGET_SCORE = 95;

export interface AgentTraceTokenTotals {
  input_tokens: number;
  output_tokens: number;
  thinking_tokens: number;
  cached_tokens: number;
  cost_usd: number;
}

export interface AgentTraceStats {
  messages: number;
  events: number;
  approvals: number;
  checkpoints: number;
  resume_requests?: number;
  token_usage: number;
  token_totals: AgentTraceTokenTotals;
}

export interface AgentTraceEvent {
  id: number;
  ts: string;
  thread_id?: string | null;
  turn_id?: string | null;
  task_id?: string | null;
  agent_id?: string | null;
  item_id?: string | null;
  event_type: string;
  payload: Record<string, unknown>;
}

export interface AgentTraceApproval {
  id: number;
  requested_at: string;
  decided_at?: string | null;
  thread_id?: string | null;
  turn_id?: string | null;
  task_id?: string | null;
  agent_id?: string | null;
  tool_name: string;
  tool_call_id: string;
  args_preview: string;
  decision: string;
  reason: string;
  metadata: Record<string, unknown>;
}

export interface AgentTraceCheckpoint {
  id: number;
  ts: string;
  task_id: string;
  thread_id?: string | null;
  turn_id?: string | null;
  agent_id?: string | null;
  checkpoint_type: string;
  iteration: number;
  summary: string;
  state: Record<string, unknown>;
}

export interface AgentTraceResumeProposal {
  checkpoint: {
    id: number;
    task_id?: string;
    taskId?: string;
    thread_id?: string | null;
    agent_id?: string | null;
    type: string;
    iteration: number;
    timestamp: string;
  };
  recovery_hints?: {
    phase: string | null;
    progress: string | null;
    message_count: number;
    step_count: number;
    working_set: string[];
    recent_tool_calls?: AgentTraceRecentToolCall[];
  };
  recoveryHints?: {
    phase: string | null;
    progress: string | null;
    messageCount: number;
    stepCount: number;
    workingSet: string[];
    recentToolCalls?: AgentTraceRecentToolCall[];
  };
  resume_plan?: {
    title: string;
    steps: string[];
  };
  resumePlan?: {
    title: string;
    steps: string[];
  };
  safety: {
    raw_state_included?: boolean;
    raw_message_snapshots_included?: boolean;
    rawStateIncluded?: boolean;
    rawMessageSnapshotsIncluded?: boolean;
  };
}

export interface AgentTraceRecentToolCall {
  iteration: number;
  tool: string;
  input_preview?: string;
  inputPreview?: string;
  observation_preview?: string;
  observationPreview?: string;
}

export interface AgentTraceResumeRequest {
  id: number;
  ts: string;
  thread_id: string;
  checkpoint_id: number;
  task_id?: string | null;
  status: "pending" | "confirmed" | "consumed" | string;
  intent: {
    schema?: string;
    requires_confirmation?: boolean;
    confirmed?: boolean;
    source?: string;
    checkpoint_id?: number;
    task_id?: string | null;
    checkpoint_type?: string;
    iteration?: number;
    continue_from_iteration?: number;
    phase?: string | null;
    working_set?: string[];
    resume_plan?: string[];
    recent_tool_calls?: AgentTraceRecentToolCall[];
    safety?: {
      raw_state_included?: boolean;
      raw_message_snapshots_included?: boolean;
    };
  };
  confirmed_at?: string | null;
  consumed_at?: string | null;
}

export interface AgentTraceTaskRun {
  task_id: string;
  thread_id?: string | null;
  turn_id?: string | null;
  agent_id?: string | null;
  title?: string | null;
  mode?: string | null;
  status?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
  summary?: string | null;
  tool_calls_started?: number;
  tool_calls_finished?: number;
  tool_errors?: number;
  tool_names?: string[];
  token_totals?: Partial<AgentTraceTokenTotals>;
}

export interface AgentTraceTaskLeaseHealth {
  state?: string;
  holder_id?: string | null;
  holder_heartbeat_at?: string | null;
  lease_expires_at?: string | null;
  recommended_action?: string;
  can_takeover?: boolean;
  can_resume?: boolean;
  recovery?: AgentTraceTaskRecoveryPlan;
  [key: string]: unknown;
}

export interface AgentTraceTaskRecoveryPlan {
  can_takeover?: boolean;
  can_resume?: boolean;
  has_checkpoint?: boolean;
  recommended_action?: string;
  operation?: string | null;
  steps?: string[];
  reason?: string;
  latest_checkpoint_id?: string | number | null;
  resume_checkpoint_id?: string | number | null;
  checkpoint_id?: string | number | null;
  [key: string]: unknown;
}

export interface AgentTraceTaskRecoveryQueueItem {
  task_id: string;
  status?: string | null;
  kind?: string | null;
  title?: string | null;
  owner_id?: string | null;
  thread_id?: string | null;
  workspace_path?: string | null;
  recommended_action: string;
  priority: number;
  can_takeover: boolean;
  can_resume: boolean;
  has_checkpoint: boolean;
  latest_checkpoint_id?: string | number | null;
  resume_checkpoint_id?: string | number | null;
  checkpoint_id?: string | number | null;
  operation?: string | null;
  steps?: string[];
  recovery_plan?: AgentTraceTaskRecoveryPlan;
  lease_health?: AgentTraceTaskLeaseHealth;
  updated_at?: string | null;
  created_at?: string | null;
}

export interface AgentTraceTaskRecoveryQueue {
  schema: "echo.task_recovery_queue.v1" | string;
  total: number;
  count: number;
  limit: number;
  items: AgentTraceTaskRecoveryQueueItem[];
  generated_at?: string;
  filters?: Record<string, unknown>;
}

export interface AgentTraceTaskRunMutationResult {
  schema: string;
  task_run: Record<string, unknown>;
  lease_health: AgentTraceTaskLeaseHealth;
}

export interface AgentTraceProcessTimelineNode {
  id?: string;
  lane: string;
  kind: string;
  ts?: string | null;
  title?: string;
  text?: string;
  severity?: string;
  status?: string;
  tool?: string;
  metadata?: Record<string, unknown>;
}

export interface AgentTraceProcessTimeline {
  schema: string;
  task_id: string;
  overview: {
    status?: string | null;
    score?: number | null;
    approval_count?: number;
    experience_record_count?: number;
    tool_error_count?: number;
    [key: string]: unknown;
  };
  timeline: AgentTraceProcessTimelineNode[];
  capabilities?: Array<Record<string, unknown>>;
  safety?: Record<string, unknown>;
}

export type AgentTraceReviewQueueStatus =
  | "pending"
  | "promoted"
  | "rejected"
  | "archived"
  | string;

export interface AgentTraceReviewQueueItem {
  id: string;
  created_at?: string;
  updated_at?: string;
  decided_at?: string;
  source: string;
  source_kind: string;
  candidate_kind: string;
  priority: "P0" | "P1" | "P2" | string;
  target_bucket: string;
  title: string;
  text: string;
  status: AgentTraceReviewQueueStatus;
  decision_reason?: string;
  promoted_to?: string;
  occurrences: number;
  last_seen_at?: string;
  source_task_ids?: string[];
  thread_ids?: string[];
  turn_ids?: string[];
  agent_ids?: string[];
  tags?: string[];
  metadata?: Record<string, unknown>;
  source_hash?: string;
}

export interface AgentTraceReviewQueueSummary {
  schema: string;
  total: number;
  pending_count: number;
  by_status: Record<string, number>;
  by_priority: Record<string, number>;
  by_target_bucket: Record<string, number>;
  next_actions: Array<{
    priority: string;
    item_id: string;
    target_bucket: string;
    action: string;
  }>;
}

export interface BrowserReplayQueueResult {
  ok: boolean;
  schema: string;
  queue: {
    created: number;
    updated: number;
    total: number;
    items: AgentTraceReviewQueueItem[];
  };
}

export interface ReplayEvidenceHint {
  schema?: string;
  case_id?: string;
  fingerprint?: string;
  replay_ready?: boolean;
  replay_case_url?: string;
  queue_url?: string;
  queue_body?: Record<string, unknown>;
}

export interface AgentScorecardGapQueueResult {
  ok: boolean;
  schema: "echo.agent_scorecard_gap_queue.v1" | string;
  created: number;
  updated: number;
  total: number;
  items: AgentTraceReviewQueueItem[];
  scorecard?: {
    overall?: Record<string, number>;
    verdict?: string;
    evidence_adjusted_overall?: Record<string, number>;
    below_target_count?: number;
  };
}

export interface AgentTracePromotionApplyResult {
  schema: string;
  dry_run: boolean;
  applied: number;
  failed: number;
  skipped: number;
  results: Array<Record<string, unknown>>;
  replay_gate?: AgentTraceReplayGate;
  override_replay_gate?: boolean;
}

export interface AgentTraceReplayGate {
  schema: string;
  passed: boolean;
  reason: string;
  thresholds: {
    min_cases: number;
    min_score: number;
  };
  summary: {
    total: number;
    passed: number;
    failed: number;
    below_min_score: number;
  };
  failing_cases: Array<Record<string, unknown>>;
  filters?: Record<string, unknown>;
}

export interface AgentTraceReplayCase {
  schema: string;
  case_id: string;
  fingerprint: string;
  source: {
    task_id?: string | null;
    thread_id?: string | null;
    turn_id?: string | null;
    agent_id?: string | null;
    status?: string | null;
  };
  replay: {
    case_id?: string;
    fingerprint?: string;
    replayable?: boolean;
    step_count?: number;
    steps?: Array<Record<string, unknown>>;
  };
  expectations: {
    status?: string | null;
    score?: number | null;
    finding_types?: string[];
    tool_error_count?: number;
  };
  resume: {
    available: boolean;
    source?: string | null;
    latest_checkpoint_id?: string | null;
  };
  safety: {
    raw_messages_included: boolean;
    raw_checkpoint_state_included: boolean;
    tool_outputs_truncated: boolean;
  };
}

export interface AgentTraceReplayEvaluationCheck {
  name: string;
  passed: boolean;
  description: string;
}

export interface AgentTraceReplayEvaluation {
  schema: string;
  case_id: string;
  fingerprint: string;
  passed: boolean;
  score: number;
  checks: AgentTraceReplayEvaluationCheck[];
  source: {
    task_id?: string | null;
    thread_id?: string | null;
    turn_id?: string | null;
    agent_id?: string | null;
    status?: string | null;
  };
}

export interface AgentTraceReplayCaseCorpus {
  schema: string;
  cases: AgentTraceReplayCase[];
  total: number;
  limit: number;
  offset: number;
}

export interface AgentTraceReplayEvaluationCorpus {
  schema: string;
  passed: number;
  failed: number;
  total: number;
  limit: number;
  offset: number;
  evaluations: AgentTraceReplayEvaluation[];
}

export interface AgentTracePromotionAuditSummary {
  schema: string;
  total: number;
  by_status: Record<string, number>;
  by_target: Record<string, number>;
  by_event_type?: Record<string, number>;
  integrity?: {
    schema: string;
    path: string;
    ok: boolean;
    entries_checked: number;
    broken_at?: number | null;
    error?: string;
    details?: string[];
  };
  override_count: number;
  gate_failed_count: number;
  gate_blocked_override_count: number;
  topology_policy_block_count?: number;
  latest: Array<Record<string, unknown>>;
}

export interface AgentTraceExperienceQualitySummary {
  schema: string;
  total: number;
  active_count: number;
  contradicted_count: number;
  stale_count: number;
  low_reliability_count: number;
  avg_reliability: number;
  by_bucket: Record<string, number>;
  top_risks: Array<Record<string, unknown>>;
  next_actions: string[];
}

export interface AgentTraceTrustDenialItem {
  id?: number | string | null;
  ts?: string | null;
  thread_id?: string | null;
  turn_id?: string | null;
  task_id?: string | null;
  agent_id?: string | null;
  tool_name: string;
  decision: string;
  action: string;
  reason?: string | null;
  risk_level?: string | null;
}

export interface AgentTraceTrustDenialSummary {
  schema: "echo.trust_denial_summary.v1" | string;
  total: number;
  by_tool: Record<string, number>;
  by_action: Record<string, number>;
  recent: AgentTraceTrustDenialItem[];
  queue?: {
    schema: string;
    min_occurrences: number;
    created: number;
    updated: number;
    items: AgentTraceReviewQueueItem[];
  };
}

export interface AgentTracePolicyReviewRuleDraft {
  schema: "echo.policy_review_rule_draft.v1" | string;
  draft_id: string;
  signed_payload: {
    schema?: string;
    proposal_id?: string;
    proposal_kind?: string;
    review_queue_item_id?: string;
    rule?: {
      effect?: string;
      tool?: string;
      args_contains?: string;
      reason?: string;
    };
    evidence?: Record<string, unknown>;
    review_required?: boolean;
  };
  signature: {
    schema?: string;
    algorithm?: string;
    digest?: string;
  };
}

export interface AgentTracePolicyReviewRuleDrafts {
  schema: "echo.policy_review_rule_drafts.v1" | string;
  total: number;
  verified: number;
  drafts: AgentTracePolicyReviewRuleDraft[];
}

export interface AgentTracePolicyReviewRuleInstallResult {
  schema: "echo.policy_review_rule_install.v1" | string;
  installed: boolean;
  draft_id?: string;
  rule: {
    effect: string;
    tool: string;
    args_contains: string;
    reason: string;
  };
  policy_rule_count: number;
  signature?: Record<string, unknown>;
}

export interface SubagentFitnessRole {
  role: string;
  score: number;
  confidence: number;
  sample_count: number;
  by_status: Record<string, number>;
  promoted_count: number;
  rejected_count: number;
  pending_count: number;
  routing_evidence_count?: number;
  by_evidence_source?: Record<string, number>;
  verdict: "strong" | "developing" | "watch" | "retire_candidate" | string;
  recommendation: string;
  evidence_item_ids: string[];
}

export interface SubagentFitnessReport {
  schema: "echo.subagent_fitness.v1" | string;
  role?: string | null;
  roles: SubagentFitnessRole[];
  role_count: number;
  top_risks: SubagentFitnessRole[];
  next_actions: Array<{
    role: string;
    verdict: string;
    action: string;
  }>;
}

export interface SubagentPolicyDecisionResult {
  schema: "echo.subagent_policy.v1" | string;
  role: string;
  action: "watch" | "retire" | "clear" | string;
  policy: Record<string, unknown> | null;
  summary: {
    schema: string;
    policies: Record<string, Record<string, unknown>>;
    policy_count: number;
    retired_count: number;
    watch_count: number;
    lastUpdated: string;
  };
}

export interface SubagentPolicyImpactItem {
  role: string;
  agent_id: string;
  status: string;
  reason?: string;
  actor?: string;
  updated_at?: string;
  evidence_item_ids?: string[];
}

export interface SubagentPolicyImpact {
  status: "blocked" | "watch" | "clear" | string;
  blocked: boolean;
  retired: SubagentPolicyImpactItem[];
  watch: SubagentPolicyImpactItem[];
  retired_count: number;
  watch_count: number;
  policy_count: number;
  lastUpdated: string;
}

export interface OrganizationTopology {
  name: string;
  protocol: string;
  task_bucket: string;
  fingerprint: string;
  agents: Record<string, { agent_id: string } & Record<string, unknown>>;
  subagent_policy?: SubagentPolicyImpact;
  metadata?: Record<string, unknown>;
}

export interface OrganizationTopologyProposal {
  kind: string;
  base_topology: string;
  bucket: string;
  detail: Record<string, unknown> & {
    historical_lift?: {
      matched_promotions: number;
      improved_count: number;
      regressed_count: number;
      avg_success_rate_delta?: number | null;
      avg_quality_score_delta?: number | null;
      rank_adjustment: number;
    };
  };
  confidence: number;
  rank_score?: number;
  rationale: string;
}

export interface OrganizationTopologyProposalsReport {
  schema: string;
  count: number;
  persisted_count: number;
  subagent_promotion_count: number;
  proposals: OrganizationTopologyProposal[];
  subagent_promotion?: Record<string, unknown>;
}

export interface OrganizationTopologyLiftReport {
  schema: string;
  count: number;
  reports: Array<{
    topology: string;
    fingerprint: string;
    base_fingerprint: string;
    bucket: string;
    mutation: string;
    promotion_source: string;
    before: Record<string, unknown>;
    after: Record<string, unknown>;
    lift: Record<string, unknown>;
    verdict: string;
  }>;
}

export interface AutoVerifierFamilySummary {
  family: string;
  total: number;
  pass_count: number;
  fail_count: number;
  pass_rate: number;
  avg_duration_ms: number;
  latest_ts?: string;
  commands: Array<{
    command: string;
    count: number;
  }>;
}

export interface AutoVerifierDecisionCandidate {
  rank: number;
  command: string;
  kind: string;
  priority: number;
  family: string;
  history_count: number;
  pass_rate: number;
  avg_duration_ms: number;
  reason: string;
  original_index?: number;
}

export interface AutoVerifierDecision {
  schema: "echo.auto_verifier_decision.v1" | string;
  ts: string;
  selected_command: string;
  candidates: AutoVerifierDecisionCandidate[];
}

export interface AutoVerifierAlert {
  family: string;
  severity: "warning" | "critical" | string;
  total: number;
  fail_count: number;
  pass_rate: number;
  latest_ts?: string;
  top_command?: string;
  message: string;
}

export interface AutoVerifierMetricsReport {
  ok?: boolean;
  schema: "echo.auto_verifier_metrics.v1" | string;
  total: number;
  pass_count: number;
  fail_count: number;
  pass_rate: number;
  avg_duration_ms: number;
  families: AutoVerifierFamilySummary[];
  alerts?: AutoVerifierAlert[];
  top_failures: Array<{
    command: string;
    count: number;
  }>;
  recent_decisions: AutoVerifierDecision[];
}

export interface RepairRoutePromotionCandidate {
  schema: "echo.repair_route_promotion_candidate.v1" | string;
  route: string;
  priority: "P0" | "P1" | "P2" | string;
  status: string;
  evidence: {
    count: number;
    share: number;
    failed_verification_count: number;
    unverified_code_changes: number;
    failure_sources?: Array<Record<string, unknown>>;
    recommended_commands?: Array<{ command: string; count: number }>;
    example_proposal_ids?: string[];
  };
  promotion_gate: {
    schema: "echo.repair_route_promotion_gate.v1" | string;
    requires_operator_review: boolean;
    requires_passing_rerun: boolean;
    blocks_auto_promotion: boolean;
  };
}

export interface RepairRouteQualityReport {
  ok?: boolean;
  schema: "echo.repair_route_quality.v1" | string;
  score: number;
  ready: boolean;
  quality_gate: {
    schema: "echo.repair_route_quality_gate.v1" | string;
    score: number;
    ready: boolean;
    blockers: string[];
    signals: Record<string, unknown>;
  };
  total_failures: number;
  route_count: number;
  routes: Array<Record<string, unknown>>;
  promotion_candidates: RepairRoutePromotionCandidate[];
  summary: Record<string, unknown>;
  recommendations: string[];
}

export interface RepairRoutePromotionQueueResult {
  ok?: boolean;
  schema: "echo.repair_route_promotion_queue.v1" | string;
  created: number;
  updated: number;
  candidates: RepairRoutePromotionCandidate[];
  items: AgentTraceReviewQueueItem[];
  summary?: Record<string, unknown>;
}

export interface BrowserDesktopQualityReport {
  ok?: boolean;
  schema: "echo.browser_desktop_quality.v1" | string;
  score: number;
  passed: number;
  total: number;
  ready: boolean;
  checks: Array<Record<string, unknown>>;
  replay_trends: {
    schema: "echo.browser_desktop_replay_trends.v1" | string;
    total: number;
    pending_count: number;
    reviewed_count: number;
    promoted_count: number;
    rejected_count: number;
    review_rate: number;
    stale_source_artifact_count?: number;
    by_status: Record<string, number>;
    by_candidate_kind: Record<string, number>;
    repair_recipe_summary?: Record<string, unknown>;
    latest: Array<Record<string, unknown>>;
    next_actions: string[];
  };
  next_actions: string[];
}

export interface BrowserDesktopRepairRecipe {
  schema: "echo.browser_desktop_repair_recipe.v1" | string;
  recipe_id: string;
  cluster_key: string;
  candidate_kind: string;
  title: string;
  priority: "P0" | "P1" | "P2" | string;
  occurrences: number;
  source_item_ids: string[];
  case_ids: string[];
  fingerprints: string[];
  evidence_summary: Record<string, unknown>;
  recommended_steps: string[];
  verification_plan: Record<string, unknown>;
  promotion_gate: Record<string, unknown>;
}

export interface BrowserDesktopRepairRecipesReport {
  ok?: boolean;
  schema: "echo.browser_desktop_repair_recipes.v1" | string;
  total_pending_cases: number;
  recipe_count: number;
  recipes: BrowserDesktopRepairRecipe[];
  ready: boolean;
  next_actions: string[];
}

export interface BrowserDesktopRepairRecipeQueueResult {
  ok?: boolean;
  schema: "echo.browser_desktop_repair_recipe_queue.v1" | string;
  created: number;
  updated: number;
  recipes: BrowserDesktopRepairRecipe[];
  items: AgentTraceReviewQueueItem[];
  summary?: Record<string, unknown>;
}

export interface BrowserDesktopStaleArtifactRejectionResult {
  ok?: boolean;
  schema: "echo.browser_desktop_stale_replay_artifact_rejection.v1" | string;
  inspected: number;
  rejected_count: number;
  archived_recipe_count?: number;
  skipped_count: number;
  rejected: Array<Record<string, unknown>>;
  archived_recipes?: Array<Record<string, unknown>>;
}

export interface BrowserDesktopRepairRecipeVerificationsReport {
  ok?: boolean;
  schema: "echo.browser_desktop_repair_recipe_verifications.v1" | string;
  total: number;
  verified_count: number;
  blocked_count: number;
  ready: boolean;
  verifications: Array<{
    schema: "echo.browser_desktop_repair_recipe_verification.v1" | string;
    item_id?: string;
    recipe_id?: string;
    title?: string;
    priority?: string;
    status: "verified" | "needs_rerun_evidence" | string;
    blockers: string[];
    source_status_counts: Record<string, number>;
    missing_evidence: string[];
    verification_evidence: Record<string, unknown>;
  }>;
  next_actions: string[];
}

export interface BrowserDesktopRepairRecipeEvidenceAttachment {
  ok?: boolean;
  schema:
    | "echo.browser_desktop_repair_recipe_evidence_attachment.v1"
    | string;
  item: AgentTraceReviewQueueItem;
  evidence: {
    schema: "echo.browser_desktop_repair_recipe_evidence.v1" | string;
    attached_at: string;
    actor: string;
    passed: boolean;
    provided: string[];
    artifacts: Array<Record<string, unknown>>;
    notes: string;
  };
  verification?:
    | BrowserDesktopRepairRecipeVerificationsReport["verifications"][number]
    | null;
}

export interface BrowserDesktopRepairRecipeRerunResult {
  ok?: boolean;
  schema: "echo.browser_desktop_repair_recipe_rerun.v1" | string;
  item_id: string;
  passed: boolean;
  provided: string[];
  missing: string[];
  promoted_source_count: number;
  artifacts: Array<Record<string, unknown>>;
  attachment: BrowserDesktopRepairRecipeEvidenceAttachment;
}

export interface BrowserDesktopRepairRecipeRerunBatchResult {
  ok?: boolean;
  schema: "echo.browser_desktop_repair_recipe_rerun_batch.v1" | string;
  attempted: number;
  passed: number;
  failed: number;
  results: BrowserDesktopRepairRecipeRerunResult[];
}

export interface AgentCompetitorScorecardDimension {
  id: string;
  title: string;
  weight: number;
  why: string;
  scores: Record<string, number>;
  evidence_adjusted_scores?: Record<string, number>;
  leader: string;
  target_score?: number;
  best_external_competitor?: string;
  best_external_score?: number;
  surpass_target_score?: number;
  effective_target_score?: number;
  echo_surpasses_best_external?: boolean;
  echo_gap_to_surpass?: number;
  echo_gap_to_target: number;
  echo_gap_to_effective_target?: number;
  echo_baseline_score?: number;
  echo_score_source?: string;
  echo_evidence_adjusted_score?: number;
  echo_evidence_adjusted_gap_to_target?: number;
  echo_evidence_adjusted_gap_to_effective_target?: number;
  echo_evidence_adjusted_score_source?: string;
  echo_certified_score_floor?: number;
  echo_certification_score_applied?: boolean;
  echo_certification_adjustment_available?: boolean;
  echo_certification_evidence?: Array<{
    id?: string;
    title?: string;
    score_floor?: number;
  }>;
  echo_evidence_readiness: number;
  echo_evidence: Array<{
    id?: string;
    title?: string;
    score?: number;
    status?: string;
  }>;
  echo_evidence_checklist?: Array<{
    id?: string;
    title?: string;
    score?: number;
    status?: string;
    implementation: {
      present: number;
      total: number;
      missing_count: number;
      missing: string[];
      coverage: number;
    };
    tests: {
      present: number;
      total: number;
      missing_count: number;
      missing: string[];
      coverage: number;
    };
    next_actions: string[];
  }>;
  echo_missing_evidence_count?: number;
  operator_drilldown?: {
    schema: "echo.scorecard_operator_drilldown.v1" | string;
    dimension_id?: string;
    certified_floor?: number;
    links?: Array<{
      id?: string;
      label?: string;
      method?: string;
      href?: string;
      body?: Record<string, unknown>;
    }>;
    source_refs?: Array<Record<string, unknown>>;
  };
  echo_ecosystem_readiness?: AgentEcosystemReadiness;
  echo_next_actions: string[];
}

export interface AgentEcosystemReadiness {
  schema: "echo.ecosystem_readiness.v1" | string;
  score: number;
  passed: number;
  total: number;
  missing_count: number;
  topics: Array<Record<string, unknown>>;
  next_actions: string[];
}

export interface AgentScoreEvidenceLayers {
  schema: "echo.agent_score_evidence_layers.v1" | string;
  architecture: {
    status: "estimated" | string;
    echo_score: number;
    codex_score: number;
    source: string;
  };
  static_certification: {
    status: "certified" | "not_certified" | string;
    ready: boolean;
    passed: number;
    total: number;
  };
  behavioral_head_to_head: {
    status: "certified" | "not_certified" | string;
    ready: boolean;
    verdict?: string;
    blocker?: "infrastructure" | "evidence" | null | string;
    infrastructure?: BehavioralInfrastructureStatus;
    echo_pass_pow_k: number;
    codex_pass_pow_k: number;
  };
}

export interface BehavioralInfrastructureStatus {
  active: boolean;
  current?: boolean;
  path?: string;
  generated_at?: string;
  age_days?: number | null;
  system_id?: string;
  failures?: Array<{ case_id: string; categories: string[] }>;
}

export interface AgentCompetitorScorecard {
  ok?: boolean;
  schema: "echo.agent_competitor_scorecard.v1" | string;
  target_score: number;
  surpass_margin?: number;
  competitors: string[];
  external_competitors?: string[];
  overall: Record<string, number>;
  ranking: Array<{ competitor: string; score: number }>;
  verdict: "leading" | "competitive" | "near_parity" | "behind" | string;
  evidence_adjusted_overall?: Record<string, number>;
  evidence_adjusted_ranking?: Array<{ competitor: string; score: number }>;
  evidence_adjusted_verdict?:
    | "leading"
    | "competitive"
    | "near_parity"
    | "behind"
    | string;
  evidence_layers?: AgentScoreEvidenceLayers;
  scorecard_policy?: {
    schema?: string;
    overall?: string;
    evidence_adjusted_overall?: string;
    certification_floors_do_not_change_overall?: boolean;
    per_dimension_target?: string;
    explicit_objective?: string;
  };
  dimensions: AgentCompetitorScorecardDimension[];
  echo_below_target: AgentCompetitorScorecardDimension[];
  echo_strengths: AgentCompetitorScorecardDimension[];
  echo_external_leaders?: AgentCompetitorScorecardDimension[];
  echo_external_gap_dimensions?: AgentCompetitorScorecardDimension[];
  echo_focus_gaps?: AgentCompetitorScorecardDimension[];
  surpass_summary?: {
    schema?: "echo.agent_surpass_summary.v1" | string;
    total_dimensions: number;
    surpassed_dimensions: number;
    gap_dimensions: number;
    target_gap_dimensions?: number;
    focus_gap_dimensions?: number;
    all_dimensions_surpassed: boolean;
    largest_gap: number;
    largest_effective_gap?: number;
  };
  next_focus: string[];
  ecosystem_readiness?: AgentEcosystemReadiness;
  parity_certification?: {
    schema: "echo.parity_certification.v1" | string;
    passed: number;
    total: number;
    ready: boolean;
    by_kind?: Record<string, { passed: number; total: number }>;
    requirements: Array<Record<string, unknown>>;
    dimension_score_floors: Record<string, number>;
    dimension_evidence: Record<string, Array<Record<string, unknown>>>;
    next_actions: string[];
  };
  codex_gap?: {
    schema?: string;
    combined_score?: number;
    verdict?: string;
    next_focus?: string[];
  };
}

export interface AutomationPolicyRuleDraftsReport {
  ok?: boolean;
  schema: "echo.automation_policy_rule_drafts.v1" | string;
  total: number;
  verified: number;
  drafts: Array<{
    schema?: string;
    draft_id: string;
    signed_payload: {
      schema?: string;
      proposal_id?: string;
      proposal_kind?: string;
      review_queue_item_id?: string | null;
      automation?: {
        id?: string;
        surface?: string;
        tool?: string;
      };
      rule: {
        effect: "deny" | "allow" | string;
        tool: string;
        args_contains?: string;
        reason?: string;
      };
      evidence?: Record<string, unknown>;
      review_required?: boolean;
    };
    signature?: {
      schema?: string;
      algorithm?: string;
      digest?: string;
    };
  }>;
}

export interface AutomationRadarReport {
  ok?: boolean;
  schema: "echo.automation_radar.v1" | string;
  target_score: number;
  scope: string;
  competitors: string[];
  overall: Record<string, number>;
  ranking: Array<{ competitor: string; score: number }>;
  verdict: "leading" | "competitive" | "near_parity" | "behind" | string;
  dimensions: Array<{
    id: string;
    title: string;
    weight: number;
    why: string;
    scores: Record<string, number>;
    leader: string;
    echo_gap_to_target: number;
    echo_gap_to_codex: number;
    evidence_ready: boolean;
    evidence_checks: Array<Record<string, unknown>>;
    missing_check_ids: string[];
    operator_drilldown?: {
      schema?: string;
      dimension_id?: string;
      links?: Array<Record<string, unknown>>;
    };
    next_actions: string[];
  }>;
  echo_gaps: AutomationRadarReport["dimensions"];
  echo_strengths: AutomationRadarReport["dimensions"];
  browser_desktop_quality: {
    schema?: string;
    score?: number;
    passed?: number;
    total?: number;
    ready?: boolean;
  };
  parity_certification: {
    schema?: string;
    passed?: number;
    total?: number;
    ready?: boolean;
  };
  policy_rule_drafts: {
    schema?: string;
    total: number;
    verified: number;
    ready: boolean;
    error?: string;
  };
  next_focus: string[];
}

export interface E2ESurpassCertificationCheck {
  id: string;
  title: string;
  passed: boolean;
  score: number;
  target: number;
  next_action?: string;
}

export interface E2ESurpassCertification {
  ok?: boolean;
  schema: "echo.e2e_surpass_certification.v1" | string;
  target_score: number;
  ready: boolean;
  verdict: "surpassed" | "needs_behavioral_evidence" | "needs_work" | string;
  summary: {
    scorecard_echo: number;
    scorecard_best_external: number;
    scorecard_evidence_adjusted_echo: number;
    automation_echo: number;
    automation_codex: number;
    quality_ready: number;
    quality_total: number;
    all_dimensions_surpassed: boolean;
    scorecard_gap_dimensions: number;
    automation_gap_dimensions: number;
    behavioral_ready: boolean;
    behavioral_echo_pass_pow_k: number;
    behavioral_codex_pass_pow_k: number;
  };
  checks: E2ESurpassCertificationCheck[];
  scorecard?: {
    schema?: string;
    target_score?: number;
    overall?: Record<string, number>;
    evidence_adjusted_overall?: Record<string, number>;
    verdict?: string;
    evidence_adjusted_verdict?: string;
    evidence_layers?: AgentScoreEvidenceLayers;
    surpass_summary?: Record<string, unknown>;
    next_focus?: string[];
  };
  automation?: {
    schema?: string;
    target_score?: number;
    overall?: Record<string, number>;
    verdict?: string;
    next_focus?: string[];
    gap_count?: number;
  };
  quality?: Array<{
    schema?: string;
    ready?: boolean;
    score?: number;
    passed?: number;
    total?: number;
    next_actions?: string[];
  }>;
  behavioral?: {
    schema?: string;
    ready: boolean;
    verdict: string;
    bundle_path?: string;
    age_days?: number | null;
    infrastructure?: BehavioralInfrastructureStatus;
    systems?: Record<
      string,
      {
        aggregate_pass_pow_k?: number;
        total_cases?: number;
        valid_cases?: number;
      }
    >;
    next_actions?: string[];
  };
  next_actions: string[];
}

export interface AutomationPolicyRuleInstallResult {
  ok?: boolean;
  schema: "echo.policy_review_rule_install.v1" | string;
  installed: boolean;
  draft_id?: string;
  source_kind?: string;
  rule: {
    effect: "deny" | "allow" | string;
    tool: string;
    args_contains?: string;
    reason?: string;
  };
  policy_rule_count: number;
  signature?: Record<string, unknown>;
}

export interface AgentTraceScope {
  threadId?: string | null;
  taskId?: string | null;
  agentId?: string | null;
  turnId?: string | null;
}

export class AgentTraceRequestError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(`Agent trace request failed: ${status}`);
    this.name = "AgentTraceRequestError";
    this.status = status;
    this.detail = detail;
  }
}

function appendScope(params: URLSearchParams, scope?: AgentTraceScope) {
  if (scope?.threadId) params.set("thread_id", scope.threadId);
  if (scope?.taskId) params.set("task_id", scope.taskId);
  if (scope?.agentId) params.set("agent_id", scope.agentId);
  if (scope?.turnId) params.set("turn_id", scope.turnId);
}

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${getBackendBaseURL()}${path}`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    throw new AgentTraceRequestError(res.status, await readErrorDetail(res));
  }
  return (await res.json()) as T;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${getBackendBaseURL()}${path}`, {
    method: "POST",
    headers: {
      ...authHeaders(),
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    throw new AgentTraceRequestError(res.status, await readErrorDetail(res));
  }
  return (await res.json()) as T;
}

async function readErrorDetail(res: Response): Promise<unknown> {
  try {
    const body = await res.json();
    return body && typeof body === "object" && "detail" in body
      ? (body as { detail?: unknown }).detail
      : body;
  } catch {
    return await res.text().catch(() => "");
  }
}

export async function fetchAgentTraceStats(
  scope?: AgentTraceScope,
): Promise<AgentTraceStats> {
  const params = new URLSearchParams();
  appendScope(params, scope);
  const query = params.toString();
  return fetchJson<AgentTraceStats>(
    `/api/agent-trace/stats${query ? `?${query}` : ""}`,
  );
}

export async function fetchAgentTraceEvents(
  limit = 8,
  offset = 0,
  scope?: AgentTraceScope,
): Promise<AgentTraceEvent[]> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(Math.max(0, offset)),
  });
  appendScope(params, scope);
  const data = await fetchJson<{ events: AgentTraceEvent[] }>(
    `/api/agent-trace/events?${params.toString()}`,
  );
  return data.events;
}

export async function fetchAgentTraceTaskRuns(
  limit = 8,
  offset = 0,
  scope?: AgentTraceScope,
  status?: string,
): Promise<AgentTraceTaskRun[]> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(Math.max(0, offset)),
  });
  appendScope(params, scope);
  if (status) params.set("status", status);
  const data = await fetchJson<{ task_runs: AgentTraceTaskRun[] }>(
    `/api/agent-trace/task-runs?${params.toString()}`,
  );
  return data.task_runs;
}

export async function fetchTaskRecoveryQueue(options?: {
  limit?: number;
  status?: string;
  kind?: string;
  threadId?: string;
  includeMonitor?: boolean;
}): Promise<AgentTraceTaskRecoveryQueue> {
  const params = new URLSearchParams({
    limit: String(options?.limit ?? 8),
  });
  if (options?.status) params.set("status", options.status);
  if (options?.kind) params.set("kind", options.kind);
  if (options?.threadId) params.set("thread_id", options.threadId);
  if (options?.includeMonitor) params.set("include_monitor", "true");
  return fetchJson<AgentTraceTaskRecoveryQueue>(
    `/api/task-runs/recovery-queue?${params.toString()}`,
  );
}

export async function takeoverTaskRun(
  taskId: string,
  reason = "operator recovery queue takeover",
): Promise<AgentTraceTaskRunMutationResult> {
  return postJson<AgentTraceTaskRunMutationResult>(
    `/api/task-runs/${encodeURIComponent(taskId)}/takeover`,
    { reason },
  );
}

export async function fetchAgentTraceProcessTimeline(
  taskId: string,
): Promise<AgentTraceProcessTimeline> {
  const data = await fetchJson<{ timeline: AgentTraceProcessTimeline }>(
    `/api/agent-trace/task-runs/${encodeURIComponent(taskId)}/process-timeline`,
  );
  return data.timeline;
}

export async function queueAgentTraceTaskRunReview(taskId: string): Promise<{
  created: number;
  updated: number;
  total: number;
  items: AgentTraceReviewQueueItem[];
}> {
  const data = await postJson<{
    queue: {
      created: number;
      updated: number;
      total: number;
      items: AgentTraceReviewQueueItem[];
    };
  }>(`/api/agent-trace/task-runs/${encodeURIComponent(taskId)}/review/queue`);
  return data.queue;
}

export async function queueLatestBrowserSessionReplayCase(
  reason = "operator panel browser replay capture",
): Promise<BrowserReplayQueueResult> {
  const sessions = await fetchJson<{
    sessions: Array<{
      session_id: string;
      action_count?: number;
      last_activity?: number;
    }>;
  }>("/api/browser/sessions");
  const latest = sessions.sessions
    .filter((session) => (session.action_count ?? 0) > 0)
    .sort((lhs, rhs) => (rhs.last_activity ?? 0) - (lhs.last_activity ?? 0))[0];
  if (!latest?.session_id) {
    throw new AgentTraceRequestError(409, {
      detail: "No browser session with replay actions is available.",
    });
  }
  return postJson<BrowserReplayQueueResult>(
    "/api/browser/session/replay-case/queue",
    {
      session_id: latest.session_id,
      reason,
    },
  );
}

export async function queueComputerActivityReplayCase(
  reason = "operator panel desktop replay capture",
): Promise<BrowserReplayQueueResult> {
  return postJson<BrowserReplayQueueResult>(
    "/api/computer/activity/replay-case/queue",
    { reason },
  );
}

export async function queueReplayEvidenceHint(
  evidence: ReplayEvidenceHint,
  reason = "operator panel replay evidence drill-down",
): Promise<BrowserReplayQueueResult> {
  const queueUrl = String(evidence.queue_url || "");
  if (!queueUrl.startsWith("/api/")) {
    throw new AgentTraceRequestError(400, {
      detail: "Replay evidence is missing a trusted queue URL.",
    });
  }
  return postJson<BrowserReplayQueueResult>(queueUrl, {
    ...(evidence.queue_body ?? {}),
    reason,
  });
}

export async function queueAgentScorecardGaps(options?: {
  targetScore?: number;
  limit?: number;
  reason?: string;
  dimensionId?: string;
}): Promise<AgentScorecardGapQueueResult> {
  return postJson<AgentScorecardGapQueueResult>(
    "/api/evolution/agent-scorecard/gaps/queue",
    {
      target_score: options?.targetScore ?? E2E_SURPASS_TARGET_SCORE,
      limit: options?.limit ?? 10,
      reason: options?.reason ?? "operator panel real score gap review",
      dimension_id: options?.dimensionId ?? "",
    },
  );
}

export async function fetchRepairRouteQuality(
  limit = 1000,
): Promise<RepairRouteQualityReport> {
  const params = new URLSearchParams({ limit: String(limit) });
  return fetchJson<RepairRouteQualityReport>(
    `/api/evolution/repair-route-quality?${params.toString()}`,
  );
}

export async function queueRepairRoutePromotionCandidates(
  limit = 1000,
): Promise<RepairRoutePromotionQueueResult> {
  return postJson<RepairRoutePromotionQueueResult>(
    "/api/evolution/repair-route-quality/promotions/queue",
    { limit },
  );
}

export async function fetchBrowserDesktopQuality(): Promise<BrowserDesktopQualityReport> {
  return fetchJson<BrowserDesktopQualityReport>(
    "/api/evolution/browser-desktop-quality",
  );
}

export async function fetchAutomationRadar(
  targetScore = E2E_SURPASS_TARGET_SCORE,
): Promise<AutomationRadarReport> {
  const params = new URLSearchParams({ target_score: String(targetScore) });
  return fetchJson<AutomationRadarReport>(
    `/api/evolution/automation-radar?${params.toString()}`,
  );
}

export async function fetchE2ESurpassCertification(
  targetScore = E2E_SURPASS_TARGET_SCORE,
): Promise<E2ESurpassCertification> {
  const params = new URLSearchParams({ target_score: String(targetScore) });
  return fetchJson<E2ESurpassCertification>(
    `/api/evolution/e2e-surpass-certification?${params.toString()}`,
  );
}

export async function fetchAutomationPolicyRuleDrafts(
  limit = 100,
): Promise<AutomationPolicyRuleDraftsReport> {
  const params = new URLSearchParams({ limit: String(limit) });
  return fetchJson<AutomationPolicyRuleDraftsReport>(
    `/api/evolution/automation-policy-rule-drafts?${params.toString()}`,
  );
}

export async function installAutomationPolicyRuleDraft(
  draftId: string,
): Promise<AutomationPolicyRuleInstallResult> {
  return postJson<AutomationPolicyRuleInstallResult>(
    "/api/evolution/automation-policy-rule-drafts/install",
    {
      draft_id: draftId,
      confirm_install: true,
    },
  );
}

export async function fetchBrowserDesktopRepairRecipes(
  limit = 1000,
  minOccurrences = 1,
): Promise<BrowserDesktopRepairRecipesReport> {
  const params = new URLSearchParams({
    limit: String(limit),
    min_occurrences: String(minOccurrences),
  });
  return fetchJson<BrowserDesktopRepairRecipesReport>(
    `/api/evolution/browser-desktop-repair-recipes?${params.toString()}`,
  );
}

export async function queueBrowserDesktopRepairRecipes(
  limit = 1000,
  minOccurrences = 1,
): Promise<BrowserDesktopRepairRecipeQueueResult> {
  return postJson<BrowserDesktopRepairRecipeQueueResult>(
    "/api/evolution/browser-desktop-repair-recipes/queue",
    {
      limit,
      min_occurrences: minOccurrences,
    },
  );
}

export async function rejectStaleBrowserDesktopReplayArtifacts(
  limit = 1000,
): Promise<BrowserDesktopStaleArtifactRejectionResult> {
  return postJson<BrowserDesktopStaleArtifactRejectionResult>(
    "/api/evolution/browser-desktop-repair-recipes/stale-artifacts/reject",
    { limit },
  );
}

export async function fetchBrowserDesktopRepairRecipeVerifications(
  limit = 1000,
): Promise<BrowserDesktopRepairRecipeVerificationsReport> {
  const params = new URLSearchParams({ limit: String(limit) });
  return fetchJson<BrowserDesktopRepairRecipeVerificationsReport>(
    `/api/evolution/browser-desktop-repair-recipes/verifications?${params.toString()}`,
  );
}

export async function attachBrowserDesktopRepairRecipeEvidence(options: {
  itemId: string;
  passed: boolean;
  provided?: string[];
  artifacts?: Array<Record<string, unknown>>;
  notes?: string;
  actor?: string;
}): Promise<BrowserDesktopRepairRecipeEvidenceAttachment> {
  return postJson<BrowserDesktopRepairRecipeEvidenceAttachment>(
    "/api/evolution/browser-desktop-repair-recipes/verifications/evidence",
    {
      item_id: options.itemId,
      passed: options.passed,
      provided: options.provided ?? [],
      artifacts: options.artifacts ?? [],
      notes: options.notes ?? "",
      actor: options.actor ?? "operator_panel",
    },
  );
}

export async function rerunBrowserDesktopRepairRecipeEvidence(options: {
  itemId: string;
  apiBaseUrl?: string;
  promoteSourceCases?: boolean;
  actor?: string;
}): Promise<BrowserDesktopRepairRecipeRerunResult> {
  return postJson<BrowserDesktopRepairRecipeRerunResult>(
    "/api/evolution/browser-desktop-repair-recipes/verifications/rerun",
    {
      item_id: options.itemId,
      api_base_url: options.apiBaseUrl ?? "http://127.0.0.1:8000",
      promote_source_cases: options.promoteSourceCases ?? false,
      actor: options.actor ?? "operator_panel",
    },
  );
}

export async function rerunBrowserDesktopRepairRecipeEvidenceBatch(options?: {
  apiBaseUrl?: string;
  promoteSourceCases?: boolean;
  actor?: string;
  limit?: number;
}): Promise<BrowserDesktopRepairRecipeRerunBatchResult> {
  return postJson<BrowserDesktopRepairRecipeRerunBatchResult>(
    "/api/evolution/browser-desktop-repair-recipes/verifications/rerun-batch",
    {
      api_base_url: options?.apiBaseUrl ?? "http://127.0.0.1:8000",
      promote_source_cases: options?.promoteSourceCases ?? false,
      actor: options?.actor ?? "operator_panel",
      limit: options?.limit ?? 20,
    },
  );
}

export async function fetchAgentTraceReviewQueue(
  limit = 12,
  offset = 0,
  filters?: {
    status?: string;
    targetBucket?: string;
    priority?: string;
    sourceTaskId?: string;
  },
): Promise<AgentTraceReviewQueueItem[]> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(Math.max(0, offset)),
  });
  if (filters?.status) params.set("status", filters.status);
  if (filters?.targetBucket) params.set("target_bucket", filters.targetBucket);
  if (filters?.priority) params.set("priority", filters.priority);
  if (filters?.sourceTaskId) params.set("source_task_id", filters.sourceTaskId);
  const data = await fetchJson<{ items: AgentTraceReviewQueueItem[] }>(
    `/api/agent-trace/review-queue?${params.toString()}`,
  );
  return data.items;
}

export async function fetchAgentTraceReviewQueueSummary(): Promise<AgentTraceReviewQueueSummary> {
  return fetchJson<AgentTraceReviewQueueSummary>(
    "/api/agent-trace/review-queue/summary",
  );
}

export async function decideAgentTraceReviewQueueItem(
  itemId: string,
  decision: {
    action: "promoted" | "rejected" | "archived";
    reason?: string;
    promotedTo?: string;
  },
): Promise<AgentTraceReviewQueueItem> {
  const data = await postJson<{ item: AgentTraceReviewQueueItem }>(
    `/api/agent-trace/review-queue/${encodeURIComponent(itemId)}/decision`,
    {
      action: decision.action,
      reason: decision.reason ?? "",
      promoted_to: decision.promotedTo,
    },
  );
  return data.item;
}

export async function applyAgentTraceReviewQueuePromotions(options?: {
  itemId?: string;
  target?: string;
  limit?: number;
  overrideReplayGate?: boolean;
  overrideReason?: string;
  minReplayCases?: number;
  minReplayScore?: number;
}): Promise<AgentTracePromotionApplyResult> {
  return postJson<AgentTracePromotionApplyResult>(
    "/api/agent-trace/review-queue/promotions/apply",
    {
      item_id: options?.itemId,
      target: options?.target,
      limit: options?.limit ?? 50,
      override_replay_gate: options?.overrideReplayGate,
      override_reason: options?.overrideReason,
      min_replay_cases: options?.minReplayCases,
      min_replay_score: options?.minReplayScore,
    },
  );
}

export async function fetchAgentTraceReplayGate(
  scope?: Pick<AgentTraceScope, "threadId" | "turnId" | "agentId"> & {
    status?: string;
    minCases?: number;
    minScore?: number;
    limit?: number;
  },
): Promise<AgentTraceReplayGate> {
  const params = new URLSearchParams({
    min_cases: String(scope?.minCases ?? 1),
    min_score: String(scope?.minScore ?? 1),
    limit: String(scope?.limit ?? 100),
  });
  appendScope(params, scope);
  if (scope?.status) params.set("status", scope.status);
  return fetchJson<AgentTraceReplayGate>(
    `/api/agent-trace/replay-gate?${params.toString()}`,
  );
}

export async function fetchAgentTraceReplayCases(
  scope?: Pick<AgentTraceScope, "threadId" | "turnId" | "agentId"> & {
    status?: string;
    limit?: number;
    offset?: number;
  },
): Promise<AgentTraceReplayCaseCorpus> {
  const params = new URLSearchParams({
    limit: String(scope?.limit ?? 100),
    offset: String(Math.max(0, scope?.offset ?? 0)),
  });
  appendScope(params, scope);
  if (scope?.status) params.set("status", scope.status);
  return fetchJson<AgentTraceReplayCaseCorpus>(
    `/api/agent-trace/replay-cases?${params.toString()}`,
  );
}

export async function fetchAgentTraceReplayEvaluations(
  scope?: Pick<AgentTraceScope, "threadId" | "turnId" | "agentId"> & {
    status?: string;
    limit?: number;
    offset?: number;
  },
): Promise<AgentTraceReplayEvaluationCorpus> {
  const params = new URLSearchParams({
    limit: String(scope?.limit ?? 100),
    offset: String(Math.max(0, scope?.offset ?? 0)),
  });
  appendScope(params, scope);
  if (scope?.status) params.set("status", scope.status);
  return fetchJson<AgentTraceReplayEvaluationCorpus>(
    `/api/agent-trace/replay-evaluations?${params.toString()}`,
  );
}

export async function fetchAgentTracePromotionAuditSummary(): Promise<AgentTracePromotionAuditSummary> {
  return fetchJson<AgentTracePromotionAuditSummary>(
    "/api/agent-trace/review-queue/promotions/audit/summary",
  );
}

export async function fetchAgentTraceExperienceQualitySummary(): Promise<AgentTraceExperienceQualitySummary> {
  return fetchJson<AgentTraceExperienceQualitySummary>(
    "/api/agent-trace/experience-ledger/quality-summary",
  );
}

export async function fetchAgentTraceTrustDenialSummary(
  limit = 1000,
  options?: {
    queueRepeated?: boolean;
    minOccurrences?: number;
  },
): Promise<AgentTraceTrustDenialSummary> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (options?.queueRepeated) params.set("queue_repeated", "true");
  if (options?.minOccurrences) {
    params.set("min_occurrences", String(options.minOccurrences));
  }
  return fetchJson<AgentTraceTrustDenialSummary>(
    `/api/agent-trace/trust-denials/summary?${params.toString()}`,
  );
}

export async function fetchAgentTracePolicyReviewRuleDrafts(
  limit = 100,
): Promise<AgentTracePolicyReviewRuleDrafts> {
  const params = new URLSearchParams({ limit: String(limit) });
  return fetchJson<AgentTracePolicyReviewRuleDrafts>(
    `/api/agent-trace/policy-review/rule-drafts?${params.toString()}`,
  );
}

export async function installAgentTracePolicyReviewRuleDraft(
  draftId: string,
): Promise<AgentTracePolicyReviewRuleInstallResult> {
  return postJson<AgentTracePolicyReviewRuleInstallResult>(
    "/api/agent-trace/policy-review/rule-drafts/install",
    {
      draft_id: draftId,
      confirm_install: true,
    },
  );
}

export async function fetchSubagentFitness(
  limit = 2000,
): Promise<SubagentFitnessReport> {
  const params = new URLSearchParams({ limit: String(limit) });
  const data = await fetchJson<SubagentFitnessReport & { ok?: boolean }>(
    `/api/evolution/subagent-fitness?${params.toString()}`,
  );
  return data;
}

export async function decideSubagentPolicy(
  role: string,
  decision: {
    action: "watch" | "retire" | "clear";
    reason?: string;
    evidenceItemIds?: string[];
  },
): Promise<SubagentPolicyDecisionResult> {
  return postJson<SubagentPolicyDecisionResult>(
    `/api/evolution/subagent-policy/${encodeURIComponent(role)}/decision`,
    {
      action: decision.action,
      reason: decision.reason ?? "",
      evidence_item_ids: decision.evidenceItemIds ?? [],
      actor: "operator_panel",
    },
  );
}

export async function fetchOrganizationTopologies(): Promise<
  OrganizationTopology[]
> {
  const data = await fetchJson<{ topologies: OrganizationTopology[] }>(
    "/api/organizations/topologies",
  );
  return data.topologies;
}

export async function fetchOrganizationTopologyProposals(): Promise<OrganizationTopologyProposalsReport> {
  return fetchJson<OrganizationTopologyProposalsReport>(
    "/api/organizations/topology-proposals",
  );
}

export async function fetchOrganizationTopologyLift(): Promise<OrganizationTopologyLiftReport> {
  return fetchJson<OrganizationTopologyLiftReport>(
    "/api/organizations/topology-promotion-lift",
  );
}

export async function fetchAutoVerifierMetrics(
  limit = 20,
): Promise<AutoVerifierMetricsReport> {
  const params = new URLSearchParams({ limit: String(limit) });
  return fetchJson<AutoVerifierMetricsReport>(
    `/api/evolution/auto-verifier-metrics?${params.toString()}`,
  );
}

export async function fetchAgentCompetitorScorecard(
  targetScore = E2E_SURPASS_TARGET_SCORE,
): Promise<AgentCompetitorScorecard> {
  const params = new URLSearchParams({ target_score: String(targetScore) });
  return fetchJson<AgentCompetitorScorecard>(
    `/api/evolution/agent-scorecard?${params.toString()}`,
  );
}

export async function fetchAgentTraceApprovals(
  limit = 5,
  offset = 0,
  scope?: Pick<AgentTraceScope, "threadId" | "turnId">,
): Promise<AgentTraceApproval[]> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(Math.max(0, offset)),
  });
  appendScope(params, scope);
  const data = await fetchJson<{ approvals: AgentTraceApproval[] }>(
    `/api/agent-trace/approvals?${params.toString()}`,
  );
  return data.approvals;
}

export async function fetchAgentTraceCheckpoints(
  limit = 3,
  offset = 0,
  scope?: AgentTraceScope,
): Promise<AgentTraceCheckpoint[]> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(Math.max(0, offset)),
  });
  appendScope(params, scope);
  const data = await fetchJson<{ checkpoints: AgentTraceCheckpoint[] }>(
    `/api/agent-trace/checkpoints?${params.toString()}`,
  );
  return data.checkpoints;
}

export async function fetchAgentTraceResumeProposal(
  checkpointId: number,
): Promise<AgentTraceResumeProposal> {
  const data = await fetchJson<{ proposal: AgentTraceResumeProposal }>(
    `/api/agent-trace/checkpoints/${encodeURIComponent(String(checkpointId))}/resume-proposal`,
  );
  return data.proposal;
}

export async function fetchAgentTraceResumeProposals(
  limit = 3,
  offset = 0,
  scope?: AgentTraceScope,
): Promise<AgentTraceResumeProposal[]> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(Math.max(0, offset)),
  });
  appendScope(params, scope);
  const data = await fetchJson<{ proposals: AgentTraceResumeProposal[] }>(
    `/api/agent-trace/resume-proposals?${params.toString()}`,
  );
  return data.proposals;
}

export async function fetchAgentTraceResumeRequests(
  limit = 5,
  offset = 0,
  scope?: Pick<AgentTraceScope, "threadId">,
  status?: string,
): Promise<AgentTraceResumeRequest[]> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(Math.max(0, offset)),
  });
  appendScope(params, scope);
  if (status) params.set("status", status);
  const data = await fetchJson<{ requests: AgentTraceResumeRequest[] }>(
    `/api/agent-trace/resume-requests?${params.toString()}`,
  );
  return data.requests;
}
