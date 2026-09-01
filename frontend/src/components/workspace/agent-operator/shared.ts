import type { AgentCompetitorScorecard, AgentTraceExperienceQualitySummary, AgentTracePolicyReviewRuleDrafts, AgentTracePromotionAuditSummary, AgentTraceReplayGate, AgentTraceReviewQueueSummary, AgentTraceTaskRecoveryQueue, AgentTraceTrustDenialSummary, AutoVerifierMetricsReport, AutomationPolicyRuleDraftsReport, AutomationRadarReport, BrowserDesktopQualityReport, BrowserDesktopRepairRecipeVerificationsReport, BrowserDesktopRepairRecipesReport, E2ESurpassCertification, OrganizationTopologyLiftReport, OrganizationTopologyProposalsReport, RepairRouteQualityReport, SubagentFitnessReport } from "@/core/agent-trace/api";
import { E2E_SURPASS_TARGET_SCORE } from "@/core/agent-trace/api";
import type { PluginLifecycleHistory, PluginPublisherTrustReport, PluginSmokeSummary } from "@/core/plugins/types";


export const EMPTY_SUMMARY: AgentTraceReviewQueueSummary = {
  schema: "echo.review_queue.v1",
  total: 0,
  pending_count: 0,
  by_status: {},
  by_priority: {},
  by_target_bucket: {},
  next_actions: [],
};

export const EMPTY_TASK_RECOVERY_QUEUE: AgentTraceTaskRecoveryQueue = {
  schema: "echo.task_recovery_queue.v1",
  total: 0,
  count: 0,
  limit: 8,
  items: [],
};

export const EMPTY_AUDIT_SUMMARY: AgentTracePromotionAuditSummary = {
  schema: "echo.promotion_audit_summary.v1",
  total: 0,
  by_status: {},
  by_target: {},
  by_event_type: {},
  integrity: {
    schema: "echo.governance_audit_chain.v1",
    path: "",
    ok: true,
    entries_checked: 0,
    broken_at: null,
    error: "",
    details: [],
  },
  override_count: 0,
  gate_failed_count: 0,
  gate_blocked_override_count: 0,
  topology_policy_block_count: 0,
  latest: [],
};

export const EMPTY_EXPERIENCE_QUALITY: AgentTraceExperienceQualitySummary = {
  schema: "echo.experience_memory_quality_summary.v1",
  total: 0,
  active_count: 0,
  contradicted_count: 0,
  stale_count: 0,
  low_reliability_count: 0,
  avg_reliability: 0,
  by_bucket: {},
  top_risks: [],
  next_actions: [],
};

export const EMPTY_SUBAGENT_FITNESS: SubagentFitnessReport = {
  schema: "echo.subagent_fitness.v1",
  role: null,
  roles: [],
  role_count: 0,
  top_risks: [],
  next_actions: [],
};

export const EMPTY_TOPOLOGY_PROPOSALS: OrganizationTopologyProposalsReport = {
  schema: "echo.topology_proposals.merged.v1",
  count: 0,
  persisted_count: 0,
  subagent_promotion_count: 0,
  proposals: [],
};

export const EMPTY_TOPOLOGY_LIFT: OrganizationTopologyLiftReport = {
  schema: "echo.topology_promotion_lift.v1",
  count: 0,
  reports: [],
};

export const EMPTY_AUTO_VERIFIER_METRICS: AutoVerifierMetricsReport = {
  schema: "echo.auto_verifier_metrics.v1",
  total: 0,
  pass_count: 0,
  fail_count: 0,
  pass_rate: 0,
  avg_duration_ms: 0,
  families: [],
  alerts: [],
  top_failures: [],
  recent_decisions: [],
};

export const EMPTY_BROWSER_DESKTOP_QUALITY: BrowserDesktopQualityReport = {
  schema: "echo.browser_desktop_quality.v1",
  score: 1,
  passed: 0,
  total: 0,
  ready: true,
  checks: [],
  replay_trends: {
    schema: "echo.browser_desktop_replay_trends.v1",
    total: 0,
    pending_count: 0,
    reviewed_count: 0,
    promoted_count: 0,
    rejected_count: 0,
    review_rate: 0,
    stale_source_artifact_count: 0,
    by_status: {},
    by_candidate_kind: {},
    latest: [],
    next_actions: [],
  },
  next_actions: [],
};

export const EMPTY_AUTOMATION_RADAR: AutomationRadarReport = {
  schema: "echo.automation_radar.v1",
  target_score: E2E_SURPASS_TARGET_SCORE,
  scope: "browser_desktop_visual_automation",
  competitors: ["codex", "claude_code", "cursor", "echo"],
  overall: {},
  ranking: [],
  verdict: "behind",
  dimensions: [],
  echo_gaps: [],
  echo_strengths: [],
  browser_desktop_quality: {
    schema: "echo.browser_desktop_quality.v1",
    score: 0,
    passed: 0,
    total: 0,
    ready: false,
  },
  parity_certification: {
    schema: "echo.parity_certification.v1",
    passed: 0,
    total: 0,
    ready: false,
  },
  policy_rule_drafts: {
    schema: "echo.automation_policy_rule_drafts.v1",
    total: 0,
    verified: 0,
    ready: false,
  },
  next_focus: [],
};

export const EMPTY_AUTOMATION_POLICY_RULE_DRAFTS: AutomationPolicyRuleDraftsReport = {
  schema: "echo.automation_policy_rule_drafts.v1",
  total: 0,
  verified: 0,
  drafts: [],
};

export const EMPTY_BROWSER_DESKTOP_REPAIR_RECIPES: BrowserDesktopRepairRecipesReport =
  {
    schema: "echo.browser_desktop_repair_recipes.v1",
    total_pending_cases: 0,
    recipe_count: 0,
    recipes: [],
    ready: true,
    next_actions: [],
  };

export const EMPTY_BROWSER_DESKTOP_REPAIR_VERIFICATIONS: BrowserDesktopRepairRecipeVerificationsReport =
  {
    schema: "echo.browser_desktop_repair_recipe_verifications.v1",
    total: 0,
    verified_count: 0,
    blocked_count: 0,
    ready: true,
    verifications: [],
    next_actions: [],
  };

export const EMPTY_REPAIR_ROUTE_QUALITY: RepairRouteQualityReport = {
  schema: "echo.repair_route_quality.v1",
  score: 1,
  ready: true,
  quality_gate: {
    schema: "echo.repair_route_quality_gate.v1",
    score: 1,
    ready: true,
    blockers: [],
    signals: {},
  },
  total_failures: 0,
  route_count: 0,
  routes: [],
  promotion_candidates: [],
  summary: {},
  recommendations: [],
};

export const EMPTY_PLUGIN_SMOKE_SUMMARY: PluginSmokeSummary = {
  schema: "echo.codex_plugin_smoke_summary.v1",
  total: 0,
  ok_count: 0,
  failed_count: 0,
  review_required_count: 0,
  warning_count: 0,
  publisher_verified_count: 0,
  unsigned_count: 0,
  invalid_signature_count: 0,
  failed: [],
  review_required: [],
  warnings: [],
  compatibility: {
    schema: "echo.codex_plugin_compatibility.v1",
    verdict: "fail",
    passed: 0,
    total: 4,
    surface_totals: {},
    requirements: [],
    next_actions: [],
  },
};

export const EMPTY_PLUGIN_PUBLISHER_TRUST: PluginPublisherTrustReport = {
  schema: "echo.plugin_publisher_trust_report.v1",
  path: "",
  exists: false,
  publisher_count: 0,
  key_count: 0,
  active_key_count: 0,
  revoked_key_count: 0,
  rotation_due_count: 0,
  ready: false,
  publishers: [],
  next_actions: [],
};

export const EMPTY_PLUGIN_LIFECYCLE_HISTORY: PluginLifecycleHistory = {
  schema: "echo.plugin_lifecycle_history.v1",
  total: 0,
  items: [],
};

export const EMPTY_TRUST_DENIAL_SUMMARY: AgentTraceTrustDenialSummary = {
  schema: "echo.trust_denial_summary.v1",
  total: 0,
  by_tool: {},
  by_action: {},
  recent: [],
};

export const EMPTY_POLICY_REVIEW_RULE_DRAFTS: AgentTracePolicyReviewRuleDrafts = {
  schema: "echo.policy_review_rule_drafts.v1",
  total: 0,
  verified: 0,
  drafts: [],
};

export const EMPTY_AGENT_SCORECARD: AgentCompetitorScorecard = {
  schema: "echo.agent_competitor_scorecard.v1",
  target_score: E2E_SURPASS_TARGET_SCORE,
  surpass_margin: 1,
  competitors: ["codex", "claude_code", "openclaw", "hermes", "echo"],
  external_competitors: ["codex", "claude_code", "openclaw", "hermes"],
  overall: {},
  ranking: [],
  verdict: "behind",
  dimensions: [],
  echo_below_target: [],
  echo_strengths: [],
  echo_external_gap_dimensions: [],
  echo_focus_gaps: [],
  next_focus: [],
};

export const EMPTY_E2E_SURPASS_CERTIFICATION: E2ESurpassCertification = {
  schema: "echo.e2e_surpass_certification.v1",
  target_score: E2E_SURPASS_TARGET_SCORE,
  ready: false,
  verdict: "needs_work",
  summary: {
    scorecard_echo: 0,
    scorecard_best_external: 0,
    scorecard_evidence_adjusted_echo: 0,
    automation_echo: 0,
    automation_codex: 0,
    quality_ready: 0,
    quality_total: 0,
    all_dimensions_surpassed: false,
    scorecard_gap_dimensions: 0,
    automation_gap_dimensions: 0,
    behavioral_ready: false,
    behavioral_echo_pass_pow_k: 0,
    behavioral_codex_pass_pow_k: 0,
  },
  checks: [],
  next_actions: [],
};

export interface ReplayGateOverridePrompt {
  gate: AgentTraceReplayGate;
  message: string;
}

