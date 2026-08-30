import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { E2E_SURPASS_TARGET_SCORE, applyAgentTraceReviewQueuePromotions, decideAgentTraceReviewQueueItem, decideSubagentPolicy, fetchAgentCompetitorScorecard, fetchAgentTraceExperienceQualitySummary, fetchAgentTracePolicyReviewRuleDrafts, fetchAgentTraceProcessTimeline, fetchAgentTracePromotionAuditSummary, fetchAgentTraceReplayGate, fetchAgentTraceReviewQueue, fetchAgentTraceReviewQueueSummary, fetchAgentTraceTaskRuns, fetchAgentTraceTrustDenialSummary, fetchAutoVerifierMetrics, fetchAutomationPolicyRuleDrafts, fetchAutomationRadar, fetchBrowserDesktopQuality, fetchBrowserDesktopRepairRecipeVerifications, fetchBrowserDesktopRepairRecipes, fetchE2ESurpassCertification, fetchOrganizationTopologies, fetchOrganizationTopologyLift, fetchOrganizationTopologyProposals, fetchRepairRouteQuality, fetchSubagentFitness, fetchTaskRecoveryQueue, installAgentTracePolicyReviewRuleDraft, installAutomationPolicyRuleDraft, queueAgentScorecardGaps, queueAgentTraceTaskRunReview, queueBrowserDesktopRepairRecipes, queueComputerActivityReplayCase, queueLatestBrowserSessionReplayCase, queueRepairRoutePromotionCandidates, queueReplayEvidenceHint, rejectStaleBrowserDesktopReplayArtifacts, rerunBrowserDesktopRepairRecipeEvidenceBatch, takeoverTaskRun } from "@/core/agent-trace/api";
import type { AgentCompetitorScorecard, AgentTraceExperienceQualitySummary, AgentTracePolicyReviewRuleDrafts, AgentTraceProcessTimeline, AgentTracePromotionAuditSummary, AgentTraceReplayGate, AgentTraceReviewQueueItem, AgentTraceReviewQueueSummary, AgentTraceTaskRecoveryQueue, AgentTraceTaskRun, AgentTraceTrustDenialSummary, AutoVerifierMetricsReport, AutomationPolicyRuleDraftsReport, AutomationRadarReport, BrowserDesktopQualityReport, BrowserDesktopRepairRecipeVerificationsReport, BrowserDesktopRepairRecipesReport, E2ESurpassCertification, OrganizationTopology, OrganizationTopologyLiftReport, OrganizationTopologyProposalsReport, RepairRouteQualityReport, ReplayEvidenceHint, SubagentFitnessReport } from "@/core/agent-trace/api";
import { fetchPluginLifecycleHistory, fetchPluginPublisherTrust, fetchPluginSmokeSummary } from "@/core/plugins/api";
import type { PluginLifecycleHistory, PluginPublisherTrustReport, PluginSmokeSummary } from "@/core/plugins/types";
import { swallow } from "@/core/utils/log";
import { cn } from "@/lib/utils";
import { BrowserDesktopReplayReviewCard, ReplayEvidenceDrilldownCard, ReplayGateCard, StatusDot } from "../replay-panel";
import { CheckCircle2Icon, GitBranchIcon, ListChecksIcon, RefreshCwIcon } from "lucide-react";
import { AutoVerifierCard } from "./cards/AutoVerifierCard";
import { AutomationRadarCard } from "./cards/AutomationRadarCard";
import { CompetitorScorecardCard } from "./cards/CompetitorScorecardCard";
import { E2ESurpassCertificationCard } from "./cards/E2ESurpassCertificationCard";
import { MemoryQualityCard } from "./cards/MemoryQualityCard";
import { PluginHealthCard } from "./cards/PluginHealthCard";
import { PolicyReviewRuleDraftCard } from "./cards/PolicyReviewRuleDraftCard";
import { PromotionAuditSummaryCard } from "./cards/PromotionAuditSummaryCard";
import { PublisherTrustCard } from "./cards/PublisherTrustCard";
import { ReviewQueueRow } from "./cards/ReviewQueueRow";
import { SubagentRiskCard } from "./cards/SubagentRiskCard";
import { TaskRecoveryQueueCard } from "./cards/TaskRecoveryQueueCard";
import { TimelinePreview } from "./cards/TimelinePreview";
import { ToolSafetyCard } from "./cards/ToolSafetyCard";
import { TopologyPolicyCard } from "./cards/TopologyPolicyCard";
import { TopologyPromotionCard } from "./cards/TopologyPromotionCard";
import { ReplayGateOverrideDialog } from "./dialogs/ReplayGateOverrideDialog";
import { EmptyPanel, Metric, PanelTitle } from "./operator-primitives";
import { formatApplyResult, formatOperatorCopy, readRequestErrorMessage, replayEvidenceFromError, replayGateBlockFromError, shortId } from "./operator-utils";
import { EMPTY_AGENT_SCORECARD, EMPTY_AUDIT_SUMMARY, EMPTY_AUTOMATION_POLICY_RULE_DRAFTS, EMPTY_AUTOMATION_RADAR, EMPTY_AUTO_VERIFIER_METRICS, EMPTY_BROWSER_DESKTOP_QUALITY, EMPTY_BROWSER_DESKTOP_REPAIR_RECIPES, EMPTY_BROWSER_DESKTOP_REPAIR_VERIFICATIONS, EMPTY_E2E_SURPASS_CERTIFICATION, EMPTY_EXPERIENCE_QUALITY, EMPTY_PLUGIN_LIFECYCLE_HISTORY, EMPTY_PLUGIN_PUBLISHER_TRUST, EMPTY_PLUGIN_SMOKE_SUMMARY, EMPTY_POLICY_REVIEW_RULE_DRAFTS, EMPTY_REPAIR_ROUTE_QUALITY, EMPTY_SUBAGENT_FITNESS, EMPTY_SUMMARY, EMPTY_TASK_RECOVERY_QUEUE, EMPTY_TOPOLOGY_LIFT, EMPTY_TOPOLOGY_PROPOSALS, EMPTY_TRUST_DENIAL_SUMMARY } from "./shared";
import type { ReplayGateOverridePrompt } from "./shared";
import { useOperatorCopy } from "./use-operator-copy";

export function AgentOperatorPanel() {
  const to = useOperatorCopy();
  const [taskRuns, setTaskRuns] = useState<AgentTraceTaskRun[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<AgentTraceProcessTimeline | null>(
    null,
  );
  const [queueItems, setQueueItems] = useState<AgentTraceReviewQueueItem[]>([]);
  const [browserDesktopQueueItems, setBrowserDesktopQueueItems] = useState<
    AgentTraceReviewQueueItem[]
  >([]);
  const [scorecardGapQueueItems, setScorecardGapQueueItems] = useState<
    AgentTraceReviewQueueItem[]
  >([]);
  const [queueSummary, setQueueSummary] =
    useState<AgentTraceReviewQueueSummary>(EMPTY_SUMMARY);
  const [taskRecoveryQueue, setTaskRecoveryQueue] =
    useState<AgentTraceTaskRecoveryQueue>(EMPTY_TASK_RECOVERY_QUEUE);
  const [auditSummary, setAuditSummary] =
    useState<AgentTracePromotionAuditSummary>(EMPTY_AUDIT_SUMMARY);
  const [experienceQuality, setExperienceQuality] =
    useState<AgentTraceExperienceQualitySummary>(EMPTY_EXPERIENCE_QUALITY);
  const [subagentFitness, setSubagentFitness] = useState<SubagentFitnessReport>(
    EMPTY_SUBAGENT_FITNESS,
  );
  const [topologies, setTopologies] = useState<OrganizationTopology[]>([]);
  const [topologyProposals, setTopologyProposals] =
    useState<OrganizationTopologyProposalsReport>(EMPTY_TOPOLOGY_PROPOSALS);
  const [topologyLift, setTopologyLift] =
    useState<OrganizationTopologyLiftReport>(EMPTY_TOPOLOGY_LIFT);
  const [autoVerifierMetrics, setAutoVerifierMetrics] =
    useState<AutoVerifierMetricsReport>(EMPTY_AUTO_VERIFIER_METRICS);
  const [browserDesktopQuality, setBrowserDesktopQuality] =
    useState<BrowserDesktopQualityReport>(EMPTY_BROWSER_DESKTOP_QUALITY);
  const [automationRadar, setAutomationRadar] = useState<AutomationRadarReport>(
    EMPTY_AUTOMATION_RADAR,
  );
  const [automationPolicyRuleDrafts, setAutomationPolicyRuleDrafts] =
    useState<AutomationPolicyRuleDraftsReport>(
      EMPTY_AUTOMATION_POLICY_RULE_DRAFTS,
    );
  const [browserDesktopRepairRecipes, setBrowserDesktopRepairRecipes] =
    useState<BrowserDesktopRepairRecipesReport>(
      EMPTY_BROWSER_DESKTOP_REPAIR_RECIPES,
    );
  const [
    browserDesktopRepairVerifications,
    setBrowserDesktopRepairVerifications,
  ] = useState<BrowserDesktopRepairRecipeVerificationsReport>(
    EMPTY_BROWSER_DESKTOP_REPAIR_VERIFICATIONS,
  );
  const [repairRouteQuality, setRepairRouteQuality] =
    useState<RepairRouteQualityReport>(EMPTY_REPAIR_ROUTE_QUALITY);
  const [pluginSmokeSummary, setPluginSmokeSummary] =
    useState<PluginSmokeSummary>(EMPTY_PLUGIN_SMOKE_SUMMARY);
  const [pluginPublisherTrust, setPluginPublisherTrust] =
    useState<PluginPublisherTrustReport>(EMPTY_PLUGIN_PUBLISHER_TRUST);
  const [pluginLifecycleHistory, setPluginLifecycleHistory] =
    useState<PluginLifecycleHistory>(EMPTY_PLUGIN_LIFECYCLE_HISTORY);
  const [trustDenialSummary, setTrustDenialSummary] =
    useState<AgentTraceTrustDenialSummary>(EMPTY_TRUST_DENIAL_SUMMARY);
  const [policyRuleDrafts, setPolicyRuleDrafts] =
    useState<AgentTracePolicyReviewRuleDrafts>(EMPTY_POLICY_REVIEW_RULE_DRAFTS);
  const [agentScorecard, setAgentScorecard] =
    useState<AgentCompetitorScorecard>(EMPTY_AGENT_SCORECARD);
  const [scorecardError, setScorecardError] = useState<string | null>(null);
  const [e2eCertification, setE2eCertification] =
    useState<E2ESurpassCertification>(EMPTY_E2E_SURPASS_CERTIFICATION);
  const [e2eCertificationError, setE2eCertificationError] = useState<
    string | null
  >(null);
  const [replayGate, setReplayGate] = useState<AgentTraceReplayGate | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [lastApplyResult, setLastApplyResult] = useState<string | null>(null);
  const [overridePrompt, setOverridePrompt] =
    useState<ReplayGateOverridePrompt | null>(null);
  const [overrideReason, setOverrideReason] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorReplayEvidence, setErrorReplayEvidence] =
    useState<ReplayEvidenceHint | null>(null);

  const refreshQueue = useCallback(async () => {
    const [
      items,
      browserDesktopItems,
      scorecardGapItems,
      summary,
      recoveryQueue,
      gate,
      audit,
      memoryQuality,
      fitness,
      organizationTopologies,
      proposals,
      lift,
      autoVerifier,
      browserDesktopHealth,
      automationRadarReport,
      automationPolicyDrafts,
      browserRepairRecipes,
      browserRepairVerifications,
      repairRoutes,
      pluginSmoke,
      pluginLifecycle,
      publisherTrust,
      trustDenials,
      ruleDrafts,
      scorecardResult,
      e2eCertificationResult,
    ] = await Promise.all([
      fetchAgentTraceReviewQueue(12, 0, { status: "pending" }),
      fetchAgentTraceReviewQueue(6, 0, {
        status: "pending",
        targetBucket: "browser_desktop_replay",
      }),
      fetchAgentTraceReviewQueue(10, 0, {
        status: "pending",
        targetBucket: "scorecard_gap_backlog",
      }),
      fetchAgentTraceReviewQueueSummary(),
      fetchTaskRecoveryQueue({ limit: 8 }),
      fetchAgentTraceReplayGate({ status: "completed" }),
      fetchAgentTracePromotionAuditSummary(),
      fetchAgentTraceExperienceQualitySummary(),
      fetchSubagentFitness(),
      fetchOrganizationTopologies(),
      fetchOrganizationTopologyProposals(),
      fetchOrganizationTopologyLift(),
      fetchAutoVerifierMetrics(),
      fetchBrowserDesktopQuality(),
      fetchAutomationRadar(E2E_SURPASS_TARGET_SCORE),
      fetchAutomationPolicyRuleDrafts(),
      fetchBrowserDesktopRepairRecipes(),
      fetchBrowserDesktopRepairRecipeVerifications(),
      fetchRepairRouteQuality(),
      fetchPluginSmokeSummary(),
      fetchPluginLifecycleHistory(),
      fetchPluginPublisherTrust(),
      fetchAgentTraceTrustDenialSummary(),
      fetchAgentTracePolicyReviewRuleDrafts(),
      fetchAgentCompetitorScorecard(E2E_SURPASS_TARGET_SCORE)
        .then((scorecard) => ({ scorecard, error: null as string | null }))
        .catch((err: unknown) => {
          swallow(err);
          return {
            scorecard: null,
            error: err instanceof Error ? err.message : String(err),
          };
        }),
      fetchE2ESurpassCertification(E2E_SURPASS_TARGET_SCORE)
        .then((certification) => ({
          certification,
          error: null as string | null,
        }))
        .catch((err: unknown) => {
          swallow(err);
          return {
            certification: null,
            error: err instanceof Error ? err.message : String(err),
          };
        }),
    ]);
    setQueueItems(items);
    setBrowserDesktopQueueItems(browserDesktopItems);
    setScorecardGapQueueItems(scorecardGapItems);
    setQueueSummary(summary);
    setTaskRecoveryQueue(recoveryQueue);
    setReplayGate(gate);
    setAuditSummary(audit);
    setExperienceQuality(memoryQuality);
    setSubagentFitness(fitness);
    setTopologies(organizationTopologies);
    setTopologyProposals(proposals);
    setTopologyLift(lift);
    setAutoVerifierMetrics(autoVerifier);
    setBrowserDesktopQuality(browserDesktopHealth);
    setAutomationRadar(automationRadarReport);
    setAutomationPolicyRuleDrafts(automationPolicyDrafts);
    setBrowserDesktopRepairRecipes(browserRepairRecipes);
    setBrowserDesktopRepairVerifications(browserRepairVerifications);
    setRepairRouteQuality(repairRoutes);
    setPluginSmokeSummary(pluginSmoke);
    setPluginLifecycleHistory(pluginLifecycle);
    setPluginPublisherTrust(publisherTrust);
    setTrustDenialSummary(trustDenials);
    setPolicyRuleDrafts(ruleDrafts);
    if (scorecardResult.scorecard) setAgentScorecard(scorecardResult.scorecard);
    setScorecardError(scorecardResult.error);
    if (e2eCertificationResult.certification) {
      setE2eCertification(e2eCertificationResult.certification);
    }
    setE2eCertificationError(e2eCertificationResult.error);
  }, []);

  const refreshTaskRuns = useCallback(async () => {
    const rows = await fetchAgentTraceTaskRuns(8);
    setTaskRuns(rows);
    setSelectedTaskId((current) => current ?? rows[0]?.task_id ?? null);
  }, []);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    try {
      await Promise.all([refreshTaskRuns(), refreshQueue()]);
      setError(null);
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [refreshQueue, refreshTaskRuns]);

  useEffect(() => {
    void refreshAll();
    const timer = window.setInterval(refreshAll, 8000);
    return () => window.clearInterval(timer);
  }, [refreshAll]);

  useEffect(() => {
    if (!selectedTaskId) {
      setTimeline(null);
      return;
    }
    let cancelled = false;
    fetchAgentTraceProcessTimeline(selectedTaskId)
      .then((next) => {
        if (!cancelled) setTimeline(next);
      })
      .catch((err) => {
        swallow(err);
        if (!cancelled) setTimeline(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedTaskId]);

  const selectedTask = useMemo(
    () => taskRuns.find((run) => run.task_id === selectedTaskId) ?? null,
    [selectedTaskId, taskRuns],
  );

  const onQueueSelectedReview = async () => {
    if (!selectedTaskId) return;
    setBusyId(`queue:${selectedTaskId}`);
    try {
      await queueAgentTraceTaskRunReview(selectedTaskId);
      await refreshQueue();
      setError(null);
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  const onDecide = async (
    item: AgentTraceReviewQueueItem,
    action: "promoted" | "rejected" | "archived",
  ) => {
    setBusyId(item.id);
    try {
      await decideAgentTraceReviewQueueItem(item.id, {
        action,
        promotedTo: action === "promoted" ? item.target_bucket : undefined,
        reason:
          action === "promoted" ? to("Accepted from operator panel.") : "",
      });
      await refreshQueue();
      setError(null);
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  const onApplyPromoted = async () => {
    setBusyId("apply-promoted");
    try {
      const result = await applyAgentTraceReviewQueuePromotions({ limit: 50 });
      setLastApplyResult(formatApplyResult(result));
      await refreshQueue();
      setError(null);
    } catch (err) {
      swallow(err);
      const blocked = replayGateBlockFromError(err);
      if (blocked) {
        setOverridePrompt(blocked);
        setOverrideReason("");
        setError(null);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setBusyId(null);
    }
  };

  const onOverrideApply = async () => {
    const reason = overrideReason.trim();
    if (!reason) {
      setError(to("Override reason is required."));
      return;
    }
    setBusyId("override-apply");
    try {
      const result = await applyAgentTraceReviewQueuePromotions({
        limit: 50,
        overrideReplayGate: true,
        overrideReason: reason,
      });
      setLastApplyResult(formatApplyResult(result));
      setOverridePrompt(null);
      setOverrideReason("");
      await refreshQueue();
      setError(null);
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  const onSubagentPolicyDecision = async (
    role: string,
    action: "watch" | "retire",
    evidenceItemIds: string[],
  ) => {
    setBusyId(`subagent-policy:${role}:${action}`);
    try {
      await decideSubagentPolicy(role, {
        action,
        evidenceItemIds,
        reason:
          action === "retire"
            ? to(
                "Retired from operator panel using subagent fitness route evidence.",
              )
            : to(
                "Placed on watch from operator panel using subagent fitness route evidence.",
              ),
      });
      await refreshQueue();
      setError(null);
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  const onQueueTrustDenials = async () => {
    setBusyId("queue-trust-denials");
    try {
      const next = await fetchAgentTraceTrustDenialSummary(1000, {
        queueRepeated: true,
        minOccurrences: 2,
      });
      setTrustDenialSummary(next);
      await refreshQueue();
      setError(null);
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  const onQueueBrowserDesktopReplay = async (kind: "browser" | "desktop") => {
    setBusyId(`queue-${kind}-desktop-replay`);
    try {
      const result =
        kind === "browser"
          ? await queueLatestBrowserSessionReplayCase()
          : await queueComputerActivityReplayCase();
      setLastApplyResult(
        formatOperatorCopy(
          to,
          "Queued {count} {kind} replay review item(s).",
          {
            count: result.queue.created + result.queue.updated,
            kind: to(kind),
          },
        ),
      );
      await refreshQueue();
      setError(null);
      setErrorReplayEvidence(null);
    } catch (err) {
      swallow(err);
      setError(readRequestErrorMessage(err));
      setErrorReplayEvidence(replayEvidenceFromError(err));
    } finally {
      setBusyId(null);
    }
  };

  const onQueueBrowserDesktopRepairRecipes = async () => {
    setBusyId("queue-browser-desktop-repair-recipes");
    try {
      const result = await queueBrowserDesktopRepairRecipes();
      setLastApplyResult(
        formatOperatorCopy(
          to,
          "Queued {count} browser/desktop repair recipe item(s).",
          { count: result.created + result.updated },
        ),
      );
      await refreshQueue();
      setError(null);
      setErrorReplayEvidence(null);
    } catch (err) {
      swallow(err);
      setError(readRequestErrorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const onRejectStaleBrowserDesktopReplayArtifacts = async () => {
    setBusyId("reject-stale-browser-desktop-replay-artifacts");
    try {
      const result = await rejectStaleBrowserDesktopReplayArtifacts();
      setLastApplyResult(
        formatOperatorCopy(
          to,
          "Rejected {rejected} stale replay item(s); archived {archived} repair recipe item(s).",
          {
            rejected: result.rejected_count,
            archived: result.archived_recipe_count ?? 0,
          },
        ),
      );
      await refreshQueue();
      setError(null);
      setErrorReplayEvidence(null);
    } catch (err) {
      swallow(err);
      setError(readRequestErrorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const onRerunBlockedBrowserDesktopRepairRecipes = async () => {
    setBusyId("rerun-browser-desktop-repair-recipes");
    try {
      const result = await rerunBrowserDesktopRepairRecipeEvidenceBatch({
        promoteSourceCases: false,
        actor: "operator_panel",
      });
      setLastApplyResult(
        formatOperatorCopy(
          to,
          "Reran {attempted} browser/desktop repair recipe(s): {passed} passed, {failed} failed. Source cases remain operator-gated.",
          {
            attempted: result.attempted,
            passed: result.passed,
            failed: result.failed,
          },
        ),
      );
      await refreshQueue();
      setError(null);
      setErrorReplayEvidence(null);
    } catch (err) {
      swallow(err);
      setError(readRequestErrorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const onQueueRepairRoutePromotions = async () => {
    setBusyId("queue-repair-route-promotions");
    try {
      const result = await queueRepairRoutePromotionCandidates();
      setLastApplyResult(
        formatOperatorCopy(
          to,
          "Queued {count} repair-route promotion review item(s).",
          { count: result.created + result.updated },
        ),
      );
      await refreshQueue();
      setError(null);
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  const onQueueErrorReplayEvidence = async () => {
    if (!errorReplayEvidence) return;
    setBusyId("queue-error-replay-evidence");
    try {
      const result = await queueReplayEvidenceHint(errorReplayEvidence);
      setLastApplyResult(
        formatOperatorCopy(to, "Queued {count} replay evidence item(s).", {
          count: result.queue.created + result.queue.updated,
        }),
      );
      await refreshQueue();
      setError(null);
      setErrorReplayEvidence(null);
    } catch (err) {
      swallow(err);
      setError(readRequestErrorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const onQueueRealScorecardGaps = async () => {
    setBusyId("queue-scorecard-gaps");
    try {
      const result = await queueAgentScorecardGaps({
        targetScore: agentScorecard.target_score,
        limit: 10,
      });
      setLastApplyResult(
        formatOperatorCopy(
          to,
          "Queued {count} real scorecard gap review item(s).",
          { count: result.total },
        ),
      );
      await refreshQueue();
      setError(null);
    } catch (err) {
      swallow(err);
      setError(readRequestErrorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const onQueueScorecardGap = async (dimensionId: string) => {
    setBusyId(`queue-scorecard-gap:${dimensionId}`);
    try {
      const result = await queueAgentScorecardGaps({
        targetScore: agentScorecard.target_score,
        limit: 1,
        dimensionId,
        reason: "operator scorecard drill-down remediation",
      });
      setLastApplyResult(
        formatOperatorCopy(
          to,
          "Queued {count} {dimension} scorecard remediation item(s).",
          { count: result.total, dimension: dimensionId },
        ),
      );
      await refreshQueue();
      setError(null);
    } catch (err) {
      swallow(err);
      setError(readRequestErrorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const onInstallPolicyRuleDraft = async (draftId: string) => {
    setBusyId(`install-policy-rule:${draftId}`);
    try {
      const result = await installAgentTracePolicyReviewRuleDraft(draftId);
      setLastApplyResult(
        formatOperatorCopy(
          to,
          "Installed {effect} rule for {tool} · {count} policy rules",
          {
            effect: result.rule.effect,
            tool: result.rule.tool,
            count: result.policy_rule_count,
          },
        ),
      );
      await refreshQueue();
      setError(null);
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  const onInstallAutomationPolicyRuleDraft = async (draftId: string) => {
    setBusyId(`install-automation-policy-rule:${draftId}`);
    try {
      const result = await installAutomationPolicyRuleDraft(draftId);
      setLastApplyResult(
        formatOperatorCopy(
          to,
          "Installed {effect} automation rule for {tool} · {count} policy rules",
          {
            effect: result.rule.effect,
            tool: result.rule.tool,
            count: result.policy_rule_count,
          },
        ),
      );
      await refreshQueue();
      setError(null);
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  const onTakeoverTaskRun = async (taskId: string) => {
    setBusyId(`takeover-task:${taskId}`);
    try {
      await takeoverTaskRun(taskId);
      setLastApplyResult(
        formatOperatorCopy(to, "Took over task {task}.", { task: taskId }),
      );
      await Promise.all([refreshTaskRuns(), refreshQueue()]);
      setError(null);
    } catch (err) {
      swallow(err);
      setError(readRequestErrorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="workspace-panel px-5 py-4">
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-eyebrow text-muted-foreground">
            {to("Operator loop")}
          </div>
          <h2 className="mt-1 text-base font-semibold">
            {to("Agent evolution queue")}
          </h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">
            {to(
              "Task runs become review candidates first, then you decide what is promoted into memory, backlog, rules, or archive.",
            )}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-8"
            onClick={() => void onApplyPromoted()}
            disabled={
              busyId === "apply-promoted" ||
              (queueSummary.by_status.promoted ?? 0) === 0
            }
          >
            <CheckCircle2Icon className="mr-1.5 size-3.5" />
            {to("Apply promoted")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-8"
            onClick={() => void refreshAll()}
            disabled={loading}
          >
            <RefreshCwIcon
              className={cn("mr-1.5 size-3.5", loading && "animate-spin")}
            />
            {to("Refresh")}
          </Button>
        </div>
      </div>

      {error && (
        <div className="mb-3 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      )}
      {errorReplayEvidence && (
        <ReplayEvidenceDrilldownCard
          evidence={errorReplayEvidence}
          busy={busyId === "queue-error-replay-evidence"}
          onQueue={() => void onQueueErrorReplayEvidence()}
        />
      )}
      {lastApplyResult && (
        <div className="mb-3 rounded-lg border border-success/25 bg-success/10 px-3 py-2 text-xs text-success">
          {lastApplyResult}
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-4">
        <Metric
          label={to("Pending")}
          value={queueSummary.pending_count}
          tone="amber"
        />
        <Metric
          label={to("Promoted")}
          value={queueSummary.by_status.promoted ?? 0}
          tone="emerald"
        />
        <Metric
          label={to("Rejected")}
          value={queueSummary.by_status.rejected ?? 0}
          tone="rose"
        />
        <Metric label={to("Total")} value={queueSummary.total} tone="blue" />
      </div>

      <ReplayGateCard gate={replayGate} />
      <TaskRecoveryQueueCard
        queue={taskRecoveryQueue}
        busyId={busyId}
        onTakeover={(taskId) => void onTakeoverTaskRun(taskId)}
      />
      <CompetitorScorecardCard
        report={agentScorecard}
        error={scorecardError}
        auditSummary={auditSummary}
        queueItems={scorecardGapQueueItems}
        queueBusy={busyId === "queue-scorecard-gaps"}
        busyId={busyId}
        applyBusy={busyId === "apply-promoted"}
        onQueueRealGaps={() => void onQueueRealScorecardGaps()}
        onQueueGap={(dimensionId) => void onQueueScorecardGap(dimensionId)}
        onApplyPromoted={() => void onApplyPromoted()}
      />
      <E2ESurpassCertificationCard
        certification={e2eCertification}
        error={e2eCertificationError}
      />
      <AutomationRadarCard
        radar={automationRadar}
        drafts={automationPolicyRuleDrafts}
        busyId={busyId}
        onInstallDraft={(draftId) =>
          void onInstallAutomationPolicyRuleDraft(draftId)
        }
      />
      <BrowserDesktopReplayReviewCard
        items={browserDesktopQueueItems}
        total={queueSummary.by_target_bucket.browser_desktop_replay ?? 0}
        quality={browserDesktopQuality}
        repairRecipes={browserDesktopRepairRecipes}
        repairVerifications={browserDesktopRepairVerifications}
        browserBusy={busyId === "queue-browser-desktop-replay"}
        desktopBusy={busyId === "queue-desktop-desktop-replay"}
        recipeBusy={busyId === "queue-browser-desktop-repair-recipes"}
        rerunBusy={busyId === "rerun-browser-desktop-repair-recipes"}
        staleBusy={busyId === "reject-stale-browser-desktop-replay-artifacts"}
        onQueueBrowser={() => void onQueueBrowserDesktopReplay("browser")}
        onQueueDesktop={() => void onQueueBrowserDesktopReplay("desktop")}
        onQueueRepairRecipes={() => void onQueueBrowserDesktopRepairRecipes()}
        onRerunBlocked={() => void onRerunBlockedBrowserDesktopRepairRecipes()}
        onRejectStale={() => void onRejectStaleBrowserDesktopReplayArtifacts()}
      />
      <PromotionAuditSummaryCard summary={auditSummary} />
      <MemoryQualityCard summary={experienceQuality} />
      <AutoVerifierCard
        report={autoVerifierMetrics}
        repairRoutes={repairRouteQuality}
        queueBusy={busyId === "queue-repair-route-promotions"}
        onQueueRepairRoutes={() => void onQueueRepairRoutePromotions()}
      />
      <PluginHealthCard
        summary={pluginSmokeSummary}
        lifecycle={pluginLifecycleHistory}
      />
      <PublisherTrustCard
        report={pluginPublisherTrust}
        onChanged={setPluginPublisherTrust}
      />
      <ToolSafetyCard
        summary={trustDenialSummary}
        busy={busyId === "queue-trust-denials"}
        onQueuePolicyReview={() => void onQueueTrustDenials()}
      />
      <PolicyReviewRuleDraftCard
        report={policyRuleDrafts}
        busyId={busyId}
        onInstall={(draftId) => void onInstallPolicyRuleDraft(draftId)}
      />
      <SubagentRiskCard
        report={subagentFitness}
        busyId={busyId}
        onWatch={(role, evidence) =>
          void onSubagentPolicyDecision(role, "watch", evidence)
        }
        onRetire={(role, evidence) =>
          void onSubagentPolicyDecision(role, "retire", evidence)
        }
      />
      <TopologyPromotionCard
        proposals={topologyProposals}
        lift={topologyLift}
      />
      <TopologyPolicyCard topologies={topologies} />

      <div className="mt-4 grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <div className="space-y-3">
          <PanelTitle
            icon={<GitBranchIcon className="size-4" />}
            title={to("Recent task runs")}
            meta={`${taskRuns.length} ${to("loaded")}`}
          />
          <div className="overflow-hidden rounded-lg border border-border-default">
            {taskRuns.length === 0 ? (
              <EmptyPanel title={to("No task runs yet")} />
            ) : (
              taskRuns.map((run) => (
                <button
                  key={run.task_id}
                  type="button"
                  className={cn(
                    "flex w-full items-center gap-3 border-b border-border-default px-3 py-2 text-left last:border-b-0 hover:bg-muted/40",
                    selectedTaskId === run.task_id && "bg-primary/10",
                  )}
                  onClick={() => setSelectedTaskId(run.task_id)}
                >
                  <StatusDot status={run.status} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">
                      {run.title || run.summary || run.task_id}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <span className="font-mono">{shortId(run.task_id)}</span>
                      <span>
                        {run.tool_calls_started ?? 0} {to("tools")}
                      </span>
                      {(run.tool_errors ?? 0) > 0 && (
                        <span className="text-destructive">
                          {run.tool_errors} {to("errors")}
                        </span>
                      )}
                    </div>
                  </div>
                  <Badge variant="outline" className="text-xs">
                    {run.status ?? to("unknown")}
                  </Badge>
                </button>
              ))
            )}
          </div>

          <div className="rounded-lg border border-border-default bg-muted/15 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">
                  {selectedTask?.title ||
                    selectedTask?.summary ||
                    to("No task selected")}
                </div>
                {selectedTaskId && (
                  <div className="font-mono text-xs text-muted-foreground">
                    {selectedTaskId}
                  </div>
                )}
              </div>
              <Button
                size="sm"
                className="h-8 shrink-0"
                disabled={
                  !selectedTaskId || busyId === `queue:${selectedTaskId}`
                }
                onClick={() => void onQueueSelectedReview()}
              >
                <ListChecksIcon className="mr-1.5 size-3.5" />
                {to("Queue review")}
              </Button>
            </div>
            <TimelinePreview timeline={timeline} />
          </div>
        </div>

        <div className="space-y-3">
          <PanelTitle
            icon={<ListChecksIcon className="size-4" />}
            title={to("Pending review queue")}
            meta={`${queueSummary.pending_count} ${to("pending")}`}
          />
          <div className="space-y-2">
            {queueItems.length === 0 ? (
              <EmptyPanel title={to("No pending review items")} />
            ) : (
              queueItems.map((item) => (
                <ReviewQueueRow
                  key={item.id}
                  item={item}
                  busy={busyId === item.id}
                  onPromote={() => void onDecide(item, "promoted")}
                  onReject={() => void onDecide(item, "rejected")}
                  onArchive={() => void onDecide(item, "archived")}
                />
              ))
            )}
          </div>
        </div>
      </div>
      <ReplayGateOverrideDialog
        prompt={overridePrompt}
        reason={overrideReason}
        busy={busyId === "override-apply"}
        onCancel={() => setOverridePrompt(null)}
        onReasonChange={setOverrideReason}
        onConfirm={() => void onOverrideApply()}
      />
    </section>
  );
}
