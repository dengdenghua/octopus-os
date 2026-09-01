/**
 * RecipeForge panel · embedded in the reflex monitor page.
 *
 * Provides 3 things in one card:
 *   1. "Currently applied addendum" status (read-only)
 *   2. Run controls · n_iter / eval_tasks knobs + trigger
 *   3. Last run results · Pareto front + per-candidate
 *      "Apply" button so the operator can promote a winner
 *
 * Backend endpoints used:
 *   GET  /api/evolution/forge/applied         · current addendum
 *   POST /api/evolution/forge/run             · run optimization
 *   POST /api/evolution/forge/apply           · persist winner
 *
 * Design choices
 * --------------
 * * No auto-poll · Forge runs are explicit, expensive (10s of seconds
 *   of LLM time), and the operator should kick them off intentionally
 * * Apply is two-click (button → confirm via inline button reveal)
 *   because the addendum directly affects production planner output
 * * Candidates show their RATIONALE prominently · the real value of
 *   the forge over plain prompt tweaking is the LLM's reasoning chain
 */

import { swallow } from "@/core/utils/log";
import {
  CheckCircleIcon,
  DownloadIcon,
  HistoryIcon,
  PlayIcon,
  Sparkles,
  ShieldCheckIcon,
  Wand2Icon,
  TrendingUpIcon,
  XCircleIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";

import { reflexFetch } from "./api";

type AppliedResp = {
  applied: boolean;
  path?: string;
  size?: number;
  mtime?: number;
  content_preview?: string;
  error?: string;
};

type AddendumEntry = {
  scope: "global" | "per_recipe";
  recipe_id: string | null;
  path: string;
  size: number;
  mtime: number;
  preview: string;
};
type AddendumsResp = { addendums: AddendumEntry[] };

type ApplyResp = {
  ok: boolean;
  error: string;
  scope: string;
  size: number;
  path: string;
};

type DeleteAddendumResp = {
  ok: boolean;
  error: string;
  deleted: boolean;
};

type CanaryEntry = {
  skill_name: string;
  phase: string;
  sample_count: number;
  success_count: number;
  failure_count: number;
  current_rate: number;
  entered_ts: string;
  metadata?: Record<string, unknown>;
  proposal_id?: string | null;
  proposal_kind?: string | null;
  candidate_id?: string | null;
  recipe_id?: string | null;
  avg_score?: number | null;
  last_rollback_reason?: string | null;
};

type CanaryResp = {
  ok: boolean;
  total?: number;
  active_count?: number;
  rolled_back_count?: number;
  full_count?: number;
  canaries?: CanaryEntry[];
  error?: string;
};

type Candidate = {
  candidate_id: string;
  avg_score: number;
  task_scores: number[];
  rationale: string;
  prompt_preview: string;
};

type HistoryEntry = {
  iter?: number;
  parent_id?: string;
  child_id?: string;
  child_avg?: number;
  improved?: boolean;
  rationale?: string;
  skipped?: boolean;
  reason?: string;
  early_stop?: boolean;
  front_size?: number;
};

type WinnerProposal = {
  ok: boolean;
  skipped?: boolean;
  reason?: string;
  proposal_id?: string;
  proposal_kind?: string;
  proposal_status?: string;
  canary_key?: string;
  canary_phase?: string;
  candidate_id?: string;
  avg_score?: number;
};

type NativeEvaluation = {
  candidate_id?: string | null;
  total?: number | null;
  verdict?: string | null;
  task_score?: number | null;
  constraint_score?: number | null;
  failure_coverage?: number | null;
  positive_preservation?: number | null;
  efficiency?: number | null;
  reasons?: string[];
};

type NativeReplayWeakCase = {
  case_id?: string | null;
  kind?: string | null;
  score?: number | null;
  reason?: string | null;
  missing_signals?: string[];
};

type NativeReplayCandidate = {
  candidate_id?: string | null;
  total?: number | null;
  reasons?: string[];
  weak_cases?: NativeReplayWeakCase[];
};

type NativeReplay = {
  case_count?: number;
  candidates?: NativeReplayCandidate[];
};

type NativeSandboxReplayCandidate = {
  candidate_id?: string | null;
  total?: number | null;
  passed?: boolean;
  reasons?: string[];
  weak_cases?: NativeReplayWeakCase[];
};

type NativeSandboxReplay = {
  case_count?: number;
  candidates?: NativeSandboxReplayCandidate[];
};

type NativeTurnReplayCandidate = {
  candidate_id?: string | null;
  total?: number | null;
  passed?: boolean;
  reasons?: string[];
  weak_cases?: NativeReplayWeakCase[];
};

type NativeTurnReplay = {
  case_count?: number;
  candidates?: NativeTurnReplayCandidate[];
};

type NativeLLMReplayCandidate = {
  candidate_id?: string | null;
  total?: number | null;
  passed?: boolean;
  reasons?: string[];
  weak_cases?: NativeReplayWeakCase[];
};

type NativeLLMReplay = {
  case_count?: number;
  candidates?: NativeLLMReplayCandidate[];
};

type RunResp = {
  ok: boolean;
  error?: string;
  iterations_run?: number;
  elapsed_s?: number;
  front_size?: number;
  ts?: number;
  recipe_id?: string | null; // echoed from the run query param
  best?: Candidate;
  history?: HistoryEntry[];
  winner_proposal?: WinnerProposal | null;
  native_evaluation?: NativeEvaluation[];
  native_replay?: NativeReplay;
  native_sandbox_replay?: NativeSandboxReplay;
  native_turn_replay?: NativeTurnReplay;
  native_llm_replay?: NativeLLMReplay;
  optimizer_backend?: string | null;
};

type StoredRun = {
  ts: number;
  trigger: string;
  recipe_id: string | null;
  iterations_run: number;
  elapsed_s: number;
  front_size: number;
  best_candidate_id: string | null;
  best_avg_score: number | null;
  best_rationale: string;
  best_prompt: string;
  applied: boolean;
  applied_at: number | null;
  winner_proposal_id?: string | null;
  winner_proposal_status?: string | null;
  winner_proposal_kind?: string | null;
  winner_canary_key?: string | null;
  winner_canary_phase?: string | null;
  winner_rollback_reason?: string | null;
  winner_lifecycle_state?: string | null;
  history_summary: HistoryEntry[];
  native_evaluation?: NativeEvaluation[];
  native_replay?: NativeReplay;
  native_sandbox_replay?: NativeSandboxReplay;
  native_turn_replay?: NativeTurnReplay;
  native_llm_replay?: NativeLLMReplay;
  winner_proposal?: WinnerProposal | null;
  optimizer_backend?: string | null;
};

type RunsResp = { runs: StoredRun[] };

type ProposalDetail = {
  id: string;
  kind: string;
  description: string;
  status: string;
  proposer: string;
  ts: string;
  fitness_before?: number | null;
  fitness_after?: number | null;
  model?: string | null;
  cost_tokens?: number;
  cost_usd?: number;
  metadata?: Record<string, unknown>;
  applied_ts?: string | null;
  rolled_back_ts?: string | null;
  rejection_reason?: string | null;
};

type ProposalDetailResp = {
  ok: boolean;
  proposal?: ProposalDetail;
  canaries?: CanaryEntry[];
  rollbacks?: Array<{
    id: string;
    description: string;
    ts: string;
    rolled_back_ts?: string | null;
    metadata?: Record<string, unknown>;
  }>;
  error?: string;
};

type AutoProposeResp = {
  ok: boolean;
  error?: string;
  proposals_generated?: number;
  results?: Array<{
    ok: boolean;
    skipped?: boolean;
    reason?: string;
    recipe_id?: string;
    ts?: number;
    iterations_run?: number;
    best_avg_score?: number;
    front_size?: number;
    error?: string;
  }>;
};

export function GepaPanel() {
  const { t } = useI18n();
  const [applied, setApplied] = useState<AppliedResp | null>(null);
  const [nIter, setNIter] = useState(8);
  const [evalTasks, setEvalTasks] = useState(4);
  const [optimizerBackend, setOptimizerBackend] = useState("native_gepa");
  const [running, setRunning] = useState(false);
  const [autoRunning, setAutoRunning] = useState(false);
  const [run, setRun] = useState<RunResp | null>(null);
  const [history, setHistory] = useState<StoredRun[]>([]);
  const [addendums, setAddendums] = useState<AddendumEntry[]>([]);
  const [canaries, setCanaries] = useState<CanaryEntry[]>([]);
  const [canarySummary, setCanarySummary] = useState({
    active: 0,
    rolledBack: 0,
    total: 0,
  });
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [appliedLoading, setAppliedLoading] = useState(true);
  const [appliedLoadFailed, setAppliedLoadFailed] = useState(false);
  const [canariesLoaded, setCanariesLoaded] = useState(false);
  const [canaryLoadFailed, setCanaryLoadFailed] = useState(false);

  const loadApplied = useCallback(async () => {
    setAppliedLoading(true);
    try {
      const r: AppliedResp = await reflexFetch<AppliedResp>(
        "/api/evolution/forge/applied",
      );
      setApplied(r);
      setAppliedLoadFailed(false);
    } catch (e) {
      swallow(e);
      setAppliedLoadFailed(true);
    } finally {
      setAppliedLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const r: RunsResp = await reflexFetch<RunsResp>(
        "/api/evolution/forge/runs?limit=20",
      );
      setHistory(r.runs ?? []);
    } catch (e) {
      swallow(e);
      // History is best-effort · don't surface a fetch error here
      // since the rest of the panel still works without it.
    }
  }, []);

  const loadAddendums = useCallback(async () => {
    try {
      const r: AddendumsResp = await reflexFetch<AddendumsResp>(
        "/api/evolution/forge/addendums",
      );
      setAddendums(r.addendums ?? []);
    } catch (e) {
      swallow(e);
    }
  }, []);

  const loadCanaries = useCallback(async () => {
    try {
      const r: CanaryResp = await reflexFetch<CanaryResp>(
        "/api/evolution/canary?include_all=true&limit=20",
      );
      setCanaries(r.canaries ?? []);
      setCanarySummary({
        active: r.active_count ?? 0,
        rolledBack: r.rolled_back_count ?? 0,
        total: r.total ?? r.canaries?.length ?? 0,
      });
      setCanariesLoaded(true);
      setCanaryLoadFailed(false);
    } catch (e) {
      swallow(e);
      setCanaryLoadFailed(true);
    }
  }, []);

  useEffect(() => {
    void loadApplied();
    void loadHistory();
    void loadAddendums();
    void loadCanaries();
  }, [loadApplied, loadHistory, loadAddendums, loadCanaries]);

  const triggerRun = useCallback(async () => {
    setRunning(true);
    setStatusMsg(t.recipeForge.statusRunInProgress(nIter * 2, nIter * 12));
    setRun(null);
    try {
      const r: RunResp = await reflexFetch<RunResp>(
        `/api/evolution/forge/run?n_iter=${nIter}&eval_tasks=${evalTasks}&optimizer_backend=${encodeURIComponent(optimizerBackend)}`,
        { method: "POST" },
      );
      setRun(r);
      if (!r.ok) {
        setStatusMsg(t.recipeForge.statusRunError(r.error ?? "unknown"));
      } else if ((r.iterations_run ?? 0) === 0) {
        setStatusMsg(
          t.recipeForge.statusNoRun(
            r.history?.[0]?.reason ?? "insufficient data",
          ),
        );
      } else {
        setStatusMsg(
          t.recipeForge.statusRunSuccess(
            String(r.iterations_run),
            r.elapsed_s?.toFixed(1) ?? "?",
            r.front_size ?? 0,
          ),
        );
      }
      void loadHistory();
      void loadCanaries();
    } catch (e) {
      swallow(e);
      setStatusMsg(
        e instanceof Error ? e.message : t.recipeForge.statusRunFailed,
      );
    } finally {
      setRunning(false);
    }
  }, [nIter, evalTasks, optimizerBackend, loadHistory, loadCanaries, t]);

  const triggerAutoPropose = useCallback(async () => {
    setAutoRunning(true);
    setStatusMsg(t.recipeForge.statusAutoProposeInProgress(nIter * 12));
    try {
      const r: AutoProposeResp = await reflexFetch<AutoProposeResp>(
        `/api/evolution/forge/auto-propose?n_iter=${nIter}&eval_tasks=${evalTasks}`,
        { method: "POST" },
      );
      if (!r.ok) {
        setStatusMsg(
          t.recipeForge.statusAutoProposeError(r.error ?? "unknown"),
        );
      } else if ((r.proposals_generated ?? 0) === 0) {
        const skip = r.results?.[0];
        setStatusMsg(
          skip?.skipped
            ? t.recipeForge.statusProposeSkipped(skip.reason ?? "unknown")
            : t.recipeForge.statusNoPropose,
        );
      } else {
        setStatusMsg(
          t.recipeForge.statusProposeSuccess(r.proposals_generated ?? 0),
        );
      }
      void loadHistory();
      void loadCanaries();
    } catch (e) {
      swallow(e);
      setStatusMsg(
        e instanceof Error
          ? e.message
          : t.recipeForge.statusDeleteFailedGeneric,
      );
    } finally {
      setAutoRunning(false);
    }
  }, [nIter, evalTasks, loadHistory, loadCanaries, t]);

  const apply = useCallback(
    async (
      c: Candidate,
      fullPrompt: string,
      opts?: {
        runTs?: number;
        targetRecipeId?: string | null;
        winnerProposal?: WinnerProposal | null;
      },
    ) => {
      const target = opts?.targetRecipeId;
      const where = target
        ? t.recipeForge.recipePrefix + " " + target
        : t.recipeForge.globalScope;
      setStatusMsg(t.recipeForge.statusApplying(c.candidate_id, where));
      try {
        const r = await reflexFetch<ApplyResp>("/api/evolution/forge/apply", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prompt: fullPrompt,
            candidate_id: c.candidate_id,
            avg_score: c.avg_score,
            rationale: c.rationale,
            run_ts: opts?.runTs,
            target_recipe_id: target ?? undefined,
            winner_proposal: opts?.winnerProposal ?? undefined,
          }),
        });
        if (!r.ok) {
          setStatusMsg(t.recipeForge.statusApplyFailed(r.error));
          return;
        }
        setStatusMsg(t.recipeForge.statusApplied(r.scope, r.size, r.path));
        void loadApplied();
        void loadHistory();
        void loadAddendums();
        void loadCanaries();
      } catch (e) {
        swallow(e);
        setStatusMsg(
          e instanceof Error ? e.message : t.recipeForge.statusApplyFailed(""),
        );
      }
    },
    [loadApplied, loadHistory, loadAddendums, loadCanaries, t],
  );

  const applyFromHistory = useCallback(
    (run: StoredRun, scope: "global" | "per_recipe") => {
      if (!run.best_candidate_id || !run.best_prompt) return;
      const candidate: Candidate = {
        candidate_id: run.best_candidate_id,
        avg_score: run.best_avg_score ?? 0,
        task_scores: [],
        rationale: run.best_rationale,
        prompt_preview: run.best_prompt,
      };
      void apply(candidate, run.best_prompt, {
        runTs: run.ts,
        targetRecipeId: scope === "per_recipe" ? run.recipe_id : null,
      });
    },
    [apply],
  );

  const deleteAddendum = useCallback(
    async (entry: AddendumEntry) => {
      const id = entry.scope === "global" ? "__global__" : entry.recipe_id;
      if (!id) return;
      setStatusMsg(t.recipeForge.statusDeleteAddendum);
      try {
        const r = await reflexFetch<DeleteAddendumResp>(
          `/api/evolution/forge/addendums/${encodeURIComponent(id)}`,
          { method: "DELETE" },
        );
        if (!r.ok) {
          setStatusMsg(t.recipeForge.statusDeleteFailed(r.error));
          return;
        }
        setStatusMsg(
          r.deleted
            ? t.recipeForge.statusDeleted(entry.path)
            : t.recipeForge.statusNothingToDelete,
        );
        void loadApplied();
        void loadAddendums();
        void loadCanaries();
      } catch (e) {
        swallow(e);
        setStatusMsg(
          e instanceof Error
            ? e.message
            : t.recipeForge.statusDeleteFailedGeneric,
        );
      }
    },
    [loadApplied, loadAddendums, loadCanaries, t],
  );

  const clearApplied = useCallback(async () => {
    setStatusMsg(
      t.recipeForge.clearAddendumPath(
        applied?.path ?? "data/forge_planner_addendum.md",
      ),
    );
  }, [applied, t]);

  return (
    <Card className="workspace-panel border-white/40 shadow-none dark:border-white/10">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Sparkles className="size-4" />
          {t.recipeForge.panelTitle}
          <Badge variant="outline" className="text-xs font-normal">
            {t.recipeForge.reflectionPathBadge}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {/* Currently applied */}
        <div className="rounded-lg border border-border-default bg-background/60 px-4 py-3">
          <div className="flex items-center justify-between">
            <div className="font-medium">
              {t.recipeForge.addendumAppliedTitle}
            </div>
            {appliedLoading && !applied ? (
              <Badge variant="outline">{t.recipeForge.stateLoading}</Badge>
            ) : appliedLoadFailed && !applied ? (
              <Badge variant="outline">{t.recipeForge.stateUnavailable}</Badge>
            ) : applied?.applied ? (
              <Badge className="bg-success/15 text-success hover:bg-success/15">
                <CheckCircleIcon className="mr-1 size-3" />
                {t.recipeForge.addendumLive}
              </Badge>
            ) : (
              <Badge variant="outline">{t.recipeForge.addendumNone}</Badge>
            )}
          </div>
          {appliedLoadFailed && (
            <div
              role="alert"
              className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive"
            >
              <span>{t.recipeForge.addendumUnavailable}</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-xs"
                onClick={() => void loadApplied()}
              >
                {t.reflexPage.retryButton}
              </Button>
            </div>
          )}
          {applied?.applied && (
            <>
              <div className="mt-2 text-xs text-muted-foreground">
                {applied.path} ·{" "}
                {applied.size ? t.recipeForge.addendumBytes(applied.size) : "?"}{" "}
                ·{" "}
                {applied.mtime
                  ? new Date(applied.mtime * 1000).toLocaleString()
                  : "?"}
              </div>
              {applied.content_preview && (
                <pre className="mt-2 max-h-24 overflow-auto whitespace-pre-wrap break-words rounded bg-background/60 p-2 font-mono text-xs text-muted-foreground">
                  {applied.content_preview}
                </pre>
              )}
              <Button
                variant="ghost"
                size="sm"
                className="mt-2 h-7 text-xs"
                onClick={clearApplied}
              >
                <XCircleIcon className="mr-1 size-3" />
                {t.recipeForge.addendumClearButton}
              </Button>
            </>
          )}
        </div>

        {/* Run controls */}
        <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border-default bg-background/60 px-4 py-3">
          <NumberKnob
            label={t.recipeForge.knobIterations}
            value={nIter}
            onChange={setNIter}
            min={1}
            max={30}
          />
          <NumberKnob
            label={t.recipeForge.knobEvalTasks}
            value={evalTasks}
            onChange={setEvalTasks}
            min={1}
            max={10}
          />
          <label className="flex min-w-[180px] flex-col gap-1 text-xs text-muted-foreground">
            <span>Optimizer</span>
            <select
              className="h-9 rounded-md border border-border-default bg-background px-2 text-xs text-foreground"
              value={optimizerBackend}
              onChange={(event) => setOptimizerBackend(event.target.value)}
            >
              <option value="native_gepa">native_gepa</option>
              <option value="dspy_gepa">dspy_gepa</option>
              <option value="external_gepa">external_gepa</option>
            </select>
          </label>
          <div className="flex-1" />
          <Button
            variant="outline"
            onClick={triggerAutoPropose}
            disabled={autoRunning || running}
            size="sm"
            title={t.recipeForge.autoProposeTitle}
          >
            <Wand2Icon className="mr-2 size-4" />
            {autoRunning
              ? t.recipeForge.autoProposeRunning
              : t.recipeForge.autoProposeButton}
          </Button>
          <Button
            onClick={triggerRun}
            disabled={running || autoRunning}
            size="sm"
          >
            <PlayIcon className="mr-2 size-4" />
            {running
              ? t.recipeForge.runForgeRunning
              : t.recipeForge.runForgeButton}
          </Button>
        </div>

        {statusMsg && (
          <div className="text-xs text-muted-foreground">{statusMsg}</div>
        )}

        {/* Last run results · renders even on 0-iter runs (the chart's
            "no iterations yet" placeholder is informative on its own;
            and skipped runs still tell the operator WHY they were
            skipped via the history list). */}
        {run && run.ok && (
          <div className="space-y-3 rounded-lg border border-border-default bg-background/60 px-4 py-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 font-medium">
                <TrendingUpIcon className="size-4 text-success" />
                {t.recipeForge.paretoFrontTitle(run.front_size ?? 0)}
              </div>
              <span className="text-xs text-muted-foreground">
                {t.recipeForge.iterCount(run.iterations_run ?? 0)} ·{" "}
                {t.recipeForge.elapsedSeconds(run.elapsed_s ?? 0)}
              </span>
            </div>
            <Badge className="w-fit bg-muted-foreground/15 text-xs text-muted-foreground hover:bg-muted-foreground/15">
              {run.optimizer_backend || "native_gepa"}
            </Badge>

            {run.best && (
              <CandidateRow
                candidate={run.best}
                isBest
                runRecipeId={run.recipe_id ?? null}
                onApply={(text, scope) =>
                  apply(run.best!, text, {
                    runTs: run.ts,
                    targetRecipeId:
                      scope === "per_recipe" ? (run.recipe_id ?? null) : null,
                    winnerProposal: run.winner_proposal ?? null,
                  })
                }
                t={t}
              />
            )}

            {/* Convergence chart · always shown when run has any
                history rows · helps the operator decide whether to
                bump n_iter (still climbing) or stop (plateaued). */}
            <ConvergenceChart history={run.history ?? []} t={t} />

            {/* This-run history */}
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground">
                {t.recipeForge.thisRunHistory(run.history?.length ?? 0)}
              </summary>
              <div className="mt-2 max-h-64 space-y-1 overflow-y-auto font-mono text-xs">
                {(run.history ?? []).map((h, i) => (
                  <HistoryRow key={i} entry={h} t={t} />
                ))}
              </div>
            </details>
          </div>
        )}

        {/* Active addendums · global + per-recipe map */}
        {addendums.length > 0 && (
          <div className="rounded-lg border border-border-default bg-background/60 px-4 py-3">
            <div className="mb-2 flex items-center justify-between">
              <div className="font-medium">
                {t.recipeForge.addendumsByScope(addendums.length)}
              </div>
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-xs"
                  asChild
                  title={t.recipeForge.addendumCsvTooltip}
                >
                  <a href="/api/evolution/forge/addendums.csv" download>
                    <DownloadIcon className="mr-1 size-3" />
                    CSV
                  </a>
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-xs"
                  onClick={loadAddendums}
                >
                  {t.recipeForge.addendumRefresh}
                </Button>
              </div>
            </div>
            <div className="space-y-2">
              {addendums.map((a) => (
                <AddendumRow
                  key={a.path}
                  entry={a}
                  onDelete={() => deleteAddendum(a)}
                  t={t}
                />
              ))}
            </div>
            <div className="mt-2 text-xs text-muted-foreground">
              {t.recipeForge.addendumGlobalHint}
            </div>
          </div>
        )}

        {/* Canary states · active / full / rolled back */}
        <div className="rounded-lg border border-border-default bg-background/60 px-4 py-3">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2 font-medium">
              <ShieldCheckIcon className="size-4" />
              {t.recipeForge.canaryTitle}
            </div>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                className="h-6 text-xs"
                onClick={loadCanaries}
              >
                {t.recipeForge.canaryRefresh}
              </Button>
            </div>
          </div>
          {canaryLoadFailed && (
            <div
              role="alert"
              className="mb-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive"
            >
              {t.recipeForge.canaryUnavailable}
            </div>
          )}
          <div className="mb-2 text-xs text-muted-foreground">
            {!canariesLoaded && canaryLoadFailed
              ? t.recipeForge.canaryCountsUnavailable
              : !canariesLoaded
                ? t.recipeForge.stateLoading
                : t.recipeForge.canaryCounts(
                    canarySummary.active,
                    canarySummary.rolledBack,
                    canarySummary.total,
                  )}
          </div>
          <div className="space-y-2">
            {!canariesLoaded ? (
              <div className="rounded-md border border-dashed border-border-default px-3 py-2 text-xs text-muted-foreground">
                {canaryLoadFailed
                  ? t.recipeForge.canaryUnavailable
                  : t.recipeForge.stateLoading}
              </div>
            ) : canaries.length === 0 ? (
              <div className="rounded-md border border-dashed border-border-default px-3 py-2 text-xs text-muted-foreground">
                {t.recipeForge.canaryEmpty}
              </div>
            ) : (
              canaries.map((c) => (
                <CanaryRow key={c.skill_name} entry={c} t={t} />
              ))
            )}
          </div>
        </div>

        {/* Past runs · cross-session history of all Forge runs */}
        {history.length > 0 && (
          <div className="rounded-lg border border-border-default bg-background/60 px-4 py-3">
            <div className="mb-2 flex items-center justify-between">
              <div className="flex items-center gap-2 font-medium">
                <HistoryIcon className="size-4" />
                {t.recipeForge.pastRunsTitle(history.length)}
              </div>
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-xs"
                  asChild
                  title={t.recipeForge.pastRunsCsvTooltip}
                >
                  <a href="/api/evolution/forge/runs.csv" download>
                    <DownloadIcon className="mr-1 size-3" />
                    CSV
                  </a>
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-xs"
                  onClick={loadHistory}
                >
                  {t.recipeForge.pastRunsRefresh}
                </Button>
              </div>
            </div>
            <div className="max-h-72 space-y-2 overflow-y-auto">
              {history.map((h) => (
                <PastRunRow
                  key={h.ts}
                  run={h}
                  canary={findRunCanary(h, canaries)}
                  onApplyGlobal={() => applyFromHistory(h, "global")}
                  onApplyRecipe={
                    h.recipe_id ? () => applyFromHistory(h, "per_recipe") : null
                  }
                  t={t}
                />
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/* Implementation note. */
function ConvergenceChart({
  history,
  t,
}: {
  history: HistoryEntry[];
  t: Translations;
}) {
  const ref = useRef<HTMLCanvasElement>(null);

  // Project history into (iter, child_avg, improved) triples,
  // skipping seed-row + skipped/early-stop markers (those have
  // no child_avg). Memoising would be nice but the array is
  // tiny · O(N) per render is fine.
  const points = history
    .filter(
      (h) =>
        typeof h.iter === "number" &&
        typeof h.child_avg === "number" &&
        h.iter > 0,
    )
    .map((h) => ({
      iter: h.iter as number,
      score: h.child_avg as number,
      improved: !!h.improved,
    }));

  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const w = c.clientWidth;
    const h = c.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== w * dpr) {
      c.width = w * dpr;
      c.height = h * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    // Padding for the in-canvas axis labels.
    const pad = { l: 28, r: 8, t: 8, b: 18 };
    const innerW = w - pad.l - pad.r;
    const innerH = h - pad.t - pad.b;

    // Always draw axes + baseline so the chart slot doesn't look
    // empty between runs.
    ctx.strokeStyle = "rgba(148, 163, 184, 0.25)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.l, pad.t);
    ctx.lineTo(pad.l, pad.t + innerH);
    ctx.lineTo(pad.l + innerW, pad.t + innerH);
    ctx.stroke();

    // Axis labels (hand-rolled · no chart lib so the canvas stays
    // 100 LOC. Two y-grid ticks at 0.0 / 0.5 / 1.0 is enough for
    // a sparkline-sized chart).
    ctx.fillStyle = "rgba(148, 163, 184, 0.7)";
    ctx.font = "10px ui-sans-serif, system-ui";
    ctx.textBaseline = "middle";
    for (const v of [0, 0.5, 1]) {
      const y = pad.t + innerH - v * innerH;
      ctx.fillText(v.toFixed(1), 4, y);
      ctx.strokeStyle = "rgba(148, 163, 184, 0.08)";
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(pad.l + innerW, y);
      ctx.stroke();
    }

    if (points.length === 0) {
      ctx.fillStyle = "rgba(148, 163, 184, 0.6)";
      ctx.textBaseline = "middle";
      ctx.textAlign = "center";
      ctx.fillText(
        t.recipeForge.noIterationsYet,
        pad.l + innerW / 2,
        pad.t + innerH / 2,
      );
      return;
    }

    const maxIter = Math.max(...points.map((p) => p.iter));
    const xFor = (it: number) =>
      pad.l + (maxIter > 0 ? (it / maxIter) * innerW : innerW / 2);
    const yFor = (s: number) =>
      pad.t + innerH - Math.max(0, Math.min(1, s)) * innerH;

    // Best-so-far step line · monotone non-decreasing.
    ctx.strokeStyle = "#34d399"; // emerald
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    let bestSoFar = -Infinity;
    points.forEach((p, i) => {
      bestSoFar = Math.max(bestSoFar, p.score);
      const x = xFor(p.iter);
      const y = yFor(bestSoFar);
      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        // Step shape · horizontal then vertical · matches the
        // "best-so-far" step semantics better than a smooth line.
        const prevX = xFor(points[i - 1]!.iter);
        ctx.lineTo(prevX, y);
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();

    // Per-iter dots · green = improved front, gray = dominated.
    for (const p of points) {
      ctx.fillStyle = p.improved ? "#34d399" : "rgba(148, 163, 184, 0.5)";
      ctx.beginPath();
      ctx.arc(xFor(p.iter), yFor(p.score), 3, 0, Math.PI * 2);
      ctx.fill();
    }

    // X-axis end label · just the max iter so we know the run length.
    ctx.fillStyle = "rgba(148, 163, 184, 0.7)";
    ctx.textBaseline = "top";
    ctx.textAlign = "right";
    ctx.fillText(`iter ${maxIter}`, pad.l + innerW, pad.t + innerH + 4);
  }, [points, t.recipeForge.noIterationsYet]);

  return (
    <div className="rounded-md border border-border-subtle bg-background/40 p-2">
      <div className="mb-1 flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
        <TrendingUpIcon className="size-3" />
        Convergence · best-so-far (line) + per-iter score (dots)
      </div>
      <canvas ref={ref} className="h-[120px] w-full" />
    </div>
  );
}

function CanaryRow({ entry, t }: { entry: CanaryEntry; t: Translations }) {
  const phaseColor =
    entry.phase === "rolled_back"
      ? "bg-destructive/15 text-destructive"
      : entry.phase === "full"
        ? "bg-success/15 text-success"
        : "bg-warning/15 text-warning";
  const dt = entry.entered_ts ? new Date(entry.entered_ts) : null;
  const shortSkill =
    entry.skill_name.length > 72
      ? `${entry.skill_name.slice(0, 72)}...`
      : entry.skill_name;
  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2 text-xs",
        entry.phase === "rolled_back"
          ? "border-destructive/30 bg-destructive/5"
          : "border-border-default bg-background/40",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge className={cn("text-xs", phaseColor, "hover:" + phaseColor)}>
          {t.recipeForge.canaryPhase(entry.phase)}
        </Badge>
        <span className="min-w-0 max-w-full truncate font-mono text-xs text-muted-foreground">
          {shortSkill}
        </span>
        <span className="font-mono text-xs text-success">
          {t.recipeForge.canaryRate(entry.current_rate ?? 0)}
        </span>
        <span className="text-muted-foreground">
          {t.recipeForge.canarySamples(
            entry.sample_count ?? 0,
            entry.success_count ?? 0,
            entry.failure_count ?? 0,
          )}
        </span>
        {dt && !Number.isNaN(dt.getTime()) && (
          <span className="text-muted-foreground">{dt.toLocaleString()}</span>
        )}
      </div>
      <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
        {entry.candidate_id && (
          <span>{t.recipeForge.canaryCandidate(entry.candidate_id)}</span>
        )}
        {entry.recipe_id && (
          <span>{t.recipeForge.canaryRecipe(entry.recipe_id)}</span>
        )}
        {entry.proposal_id && (
          <span>{t.recipeForge.canaryProposal(entry.proposal_id)}</span>
        )}
        {typeof entry.avg_score === "number" && (
          <span>{t.recipeForge.bestAvg(entry.avg_score)}</span>
        )}
        {entry.last_rollback_reason && (
          <span className="basis-full text-destructive">
            {t.recipeForge.canaryRollbackReason(entry.last_rollback_reason)}
          </span>
        )}
      </div>
    </div>
  );
}

function findRunCanary(
  run: StoredRun,
  canaries: CanaryEntry[],
): CanaryEntry | null {
  const recipeKey = run.recipe_id ?? null;
  const candidateKey = run.best_candidate_id ?? null;
  if (!candidateKey) return null;
  const exact = canaries.find(
    (c) =>
      (c.recipe_id ?? null) === recipeKey &&
      (c.candidate_id ?? null) === candidateKey,
  );
  if (exact) return exact;
  return (
    canaries.find((c) => (c.candidate_id ?? null) === candidateKey) ?? null
  );
}

function PastRunRow({
  run,
  canary,
  onApplyGlobal,
  onApplyRecipe,
  t,
}: {
  run: StoredRun;
  canary?: CanaryEntry | null;
  onApplyGlobal: () => void;
  onApplyRecipe: (() => void) | null;
  t: Translations;
}) {
  const triggerColor =
    run.trigger === "auto_propose"
      ? "bg-chart-1/15 text-chart-1"
      : "bg-info/15 text-info";
  const dt = new Date(run.ts * 1000);
  const canApply = !!(run.best_candidate_id && run.best_prompt && !run.applied);
  const lifecyclePhase =
    run.winner_canary_phase ??
    canary?.phase ??
    run.winner_lifecycle_state ??
    run.winner_proposal_status ??
    null;
  const lifecycleColor =
    lifecyclePhase === "rolled_back"
      ? "bg-destructive/15 text-destructive"
      : lifecyclePhase === "full" || lifecyclePhase === "applied"
        ? "bg-success/15 text-success"
        : lifecyclePhase
          ? "bg-warning/15 text-warning"
          : "";
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detail, setDetail] = useState<ProposalDetailResp | null>(null);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const replayCandidate =
    run.native_replay?.candidates?.find(
      (candidate) => candidate.candidate_id === run.best_candidate_id,
    ) ?? run.native_replay?.candidates?.[0];
  const nativeEvaluation =
    run.native_evaluation?.find(
      (candidate) => candidate.candidate_id === run.best_candidate_id,
    ) ?? run.native_evaluation?.[0];
  const sandboxReplayCandidate =
    run.native_sandbox_replay?.candidates?.find(
      (candidate) => candidate.candidate_id === run.best_candidate_id,
    ) ?? run.native_sandbox_replay?.candidates?.[0];
  const turnReplayCandidate =
    run.native_turn_replay?.candidates?.find(
      (candidate) => candidate.candidate_id === run.best_candidate_id,
    ) ?? run.native_turn_replay?.candidates?.[0];
  const llmReplayCandidate =
    run.native_llm_replay?.candidates?.find(
      (candidate) => candidate.candidate_id === run.best_candidate_id,
    ) ?? run.native_llm_replay?.candidates?.[0];
  const loadDetail = useCallback(async () => {
    if (!run.winner_proposal_id) return;
    if (detail || detailLoading) return;
    setDetailLoading(true);
    try {
      const r: ProposalDetailResp = await reflexFetch<ProposalDetailResp>(
        `/api/evolution/ledger/${encodeURIComponent(run.winner_proposal_id)}`,
      );
      setDetail(r);
      setDetailOpen(true);
    } catch (e) {
      swallow(e);
    } finally {
      setDetailLoading(false);
    }
  }, [detail, detailLoading, run.winner_proposal_id]);
  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2 text-xs",
        lifecyclePhase === "rolled_back"
          ? "border-destructive/30 bg-destructive/5"
          : run.applied
            ? "border-success/30 bg-success/5"
            : "border-border-default",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge className={cn("text-xs", triggerColor, "hover:" + triggerColor)}>
          {run.trigger === "auto_propose"
            ? t.recipeForge.triggerAutoPropose
            : t.recipeForge.triggerManual}
        </Badge>
        {run.recipe_id && (
          <span className="font-mono text-xs text-muted-foreground">
            {t.recipeForge.recipePrefix}: {run.recipe_id.slice(0, 12)}
          </span>
        )}
        <Badge className="bg-muted-foreground/15 text-xs text-muted-foreground hover:bg-muted-foreground/15">
          {run.optimizer_backend || "native_gepa"}
        </Badge>
        {lifecyclePhase && (
          <Badge
            className={cn("text-xs", lifecycleColor, "hover:" + lifecycleColor)}
          >
            {t.recipeForge.canaryPhase(lifecyclePhase)}
          </Badge>
        )}
        <span className="text-muted-foreground">{dt.toLocaleString()}</span>
        <span className="text-muted-foreground">
          · {t.recipeForge.iterCount(run.iterations_run)} ·{" "}
          {t.recipeForge.elapsedSeconds(run.elapsed_s)}
        </span>
        <span className="text-muted-foreground">· front {run.front_size}</span>
        {run.best_avg_score !== null && (
          <span className="font-mono text-success">
            {t.recipeForge.bestAvg(run.best_avg_score)}
          </span>
        )}
        <div className="flex-1" />
        {run.applied && (
          <Badge className="bg-success/15 text-xs text-success hover:bg-success/15">
            <CheckCircleIcon className="mr-1 size-3" />
            {t.recipeForge.addendumLive}
          </Badge>
        )}
        {run.winner_proposal_id && (
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-2 text-xs"
            onClick={() => {
              setDetailOpen((v) => !v);
              void loadDetail();
            }}
            title={t.recipeForge.proposalDetailsButton}
          >
            {t.recipeForge.canaryProposal(run.winner_proposal_id)}
          </Button>
        )}
        {canApply && (
          <div className="flex items-center gap-1">
            {onApplyRecipe && (
              <Button
                size="sm"
                variant="outline"
                className="h-6 text-xs"
                onClick={onApplyRecipe}
                title={`${t.recipeForge.recipePrefix} ${run.recipe_id}`}
              >
                {t.recipeForge.applyRecipeButton(run.recipe_id!.slice(0, 12))}
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              className="h-6 text-xs"
              onClick={onApplyGlobal}
              title={t.recipeForge.applyGlobalButton}
            >
              {t.recipeForge.applyGlobalButton}
            </Button>
          </div>
        )}
      </div>
      {run.winner_rollback_reason && (
        <div className="mt-1 text-xs text-destructive">
          {t.recipeForge.canaryRollbackReason(run.winner_rollback_reason)}
        </div>
      )}
      {(nativeEvaluation ||
        replayCandidate ||
        sandboxReplayCandidate ||
        turnReplayCandidate ||
        llmReplayCandidate) && (
        <div className="mt-2 rounded-md border border-border-subtle bg-background/40 px-2 py-1.5 text-xs text-muted-foreground">
          <button
            type="button"
            className="flex w-full items-center gap-2 text-left"
            aria-expanded={evidenceOpen}
            onClick={() => setEvidenceOpen((value) => !value)}
          >
            <ShieldCheckIcon className="size-3 text-success" />
            <span className="font-medium text-foreground">
              {t.recipeForge.nativeEvidenceTitle}
            </span>
            {nativeEvaluation?.verdict && (
              <Badge className="bg-success/10 text-xs text-success hover:bg-success/10">
                {nativeEvaluation.verdict}
              </Badge>
            )}
            {typeof replayCandidate?.total === "number" && (
              <span>
                {t.recipeForge.nativeEvidenceReplay(replayCandidate.total)}
              </span>
            )}
            {typeof sandboxReplayCandidate?.total === "number" && (
              <span>
                {t.recipeForge.nativeEvidenceSandboxReplay(
                  sandboxReplayCandidate.total,
                  sandboxReplayCandidate.passed,
                )}
              </span>
            )}
            {typeof turnReplayCandidate?.total === "number" && (
              <span>
                {t.recipeForge.nativeEvidenceTurnReplay(
                  turnReplayCandidate.total,
                  turnReplayCandidate.passed,
                )}
              </span>
            )}
            {typeof llmReplayCandidate?.total === "number" && (
              <span>
                {t.recipeForge.nativeEvidenceLLMReplay(
                  llmReplayCandidate.total,
                  llmReplayCandidate.passed,
                )}
              </span>
            )}
            <span>
              {t.recipeForge.nativeEvidenceCases(
                Math.max(
                  run.native_replay?.case_count ?? 0,
                  run.native_sandbox_replay?.case_count ?? 0,
                  run.native_turn_replay?.case_count ?? 0,
                  run.native_llm_replay?.case_count ?? 0,
                ),
              )}
            </span>
          </button>
          {evidenceOpen && (
            <div className="mt-2 space-y-1 border-t border-border-subtle pt-2">
              {nativeEvaluation && (
                <div className="flex flex-wrap gap-x-3 gap-y-1">
                  <span>
                    {t.recipeForge.nativeEvidenceMetric(
                      "task",
                      nativeEvaluation.task_score,
                    )}
                  </span>
                  <span>
                    {t.recipeForge.nativeEvidenceMetric(
                      "constraints",
                      nativeEvaluation.constraint_score,
                    )}
                  </span>
                  <span>
                    {t.recipeForge.nativeEvidenceMetric(
                      "failures",
                      nativeEvaluation.failure_coverage,
                    )}
                  </span>
                  <span>
                    {t.recipeForge.nativeEvidenceMetric(
                      "preservation",
                      nativeEvaluation.positive_preservation,
                    )}
                  </span>
                  <span>
                    {t.recipeForge.nativeEvidenceMetric(
                      "efficiency",
                      nativeEvaluation.efficiency,
                    )}
                  </span>
                </div>
              )}
              {(llmReplayCandidate?.weak_cases?.length ?? 0) > 0 ||
              (turnReplayCandidate?.weak_cases?.length ?? 0) > 0 ||
              (sandboxReplayCandidate?.weak_cases?.length ?? 0) > 0 ||
              (replayCandidate?.weak_cases?.length ?? 0) > 0 ? (
                (
                  llmReplayCandidate?.weak_cases ??
                  turnReplayCandidate?.weak_cases ??
                  sandboxReplayCandidate?.weak_cases ??
                  replayCandidate?.weak_cases
                )?.map((weakCase) => (
                  <div
                    key={weakCase.case_id ?? weakCase.reason}
                    className="text-warning"
                  >
                    {t.recipeForge.nativeEvidenceWeakCase(
                      weakCase.case_id ?? "case",
                      weakCase.reason ?? "weak coverage",
                    )}
                    {(weakCase.missing_signals?.length ?? 0) > 0 && (
                      <span>
                        {" · "}
                        {t.recipeForge.nativeEvidenceMissing(
                          weakCase.missing_signals?.join(", ") ?? "",
                        )}
                      </span>
                    )}
                  </div>
                ))
              ) : (
                <div className="text-success">
                  {t.recipeForge.nativeEvidenceNoWeakCases}
                </div>
              )}
            </div>
          )}
        </div>
      )}
      {detailOpen && (
        <div className="mt-2 rounded-md border border-border-subtle bg-background/50 p-2 text-xs text-muted-foreground">
          {!detail ? (
            <div>{t.recipeForge.proposalDetailsLoading}</div>
          ) : detail.ok && detail.proposal ? (
            <div className="space-y-1">
              <div className="flex flex-wrap gap-2">
                <span className="font-mono text-foreground">
                  {detail.proposal.id}
                </span>
                <span>{detail.proposal.kind}</span>
                <span>
                  {t.recipeForge.proposalDetailsStatus(detail.proposal.status)}
                </span>
                <span>
                  {t.recipeForge.proposalDetailsCanaries(
                    detail.canaries?.length ?? 0,
                  )}
                </span>
                <span>
                  {t.recipeForge.proposalDetailsRollbacks(
                    detail.rollbacks?.length ?? 0,
                  )}
                </span>
              </div>
              <div>{detail.proposal.description}</div>
              {detail.proposal.rejection_reason && (
                <div className="text-destructive">
                  {detail.proposal.rejection_reason}
                </div>
              )}
              {detail.proposal.metadata &&
                Object.keys(detail.proposal.metadata).length > 0 && (
                  <details>
                    <summary className="cursor-pointer">
                      {t.recipeForge.proposalDetailsMetadata}
                    </summary>
                    <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded bg-background/60 p-2 font-mono text-xs">
                      {JSON.stringify(detail.proposal.metadata, null, 2)}
                    </pre>
                  </details>
                )}
              {(detail.canaries?.length ?? 0) > 0 && (
                <div className="space-y-1">
                  {detail.canaries?.map((c) => (
                    <div key={c.skill_name} className="font-mono">
                      {c.skill_name} · {c.phase} · {c.current_rate.toFixed(3)}
                    </div>
                  ))}
                </div>
              )}
              {(detail.rollbacks?.length ?? 0) > 0 && (
                <div className="space-y-1">
                  {detail.rollbacks?.map((r) => (
                    <div key={r.id} className="font-mono text-destructive">
                      {r.id} · {r.description}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div>{detail.error ?? t.recipeForge.proposalDetailsLoading}</div>
          )}
        </div>
      )}
      {run.best_rationale && (
        <div className="mt-1 italic text-muted-foreground">
          “{run.best_rationale.slice(0, 200)}”
        </div>
      )}
    </div>
  );
}

function NumberKnob({
  label,
  value,
  onChange,
  min,
  max,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <input
        type="number"
        aria-label={label}
        className="w-20 rounded-md border border-border-default bg-background/60 px-2 py-1 text-sm tabular-nums"
        value={value}
        min={min}
        max={max}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (!Number.isNaN(v)) onChange(Math.max(min, Math.min(max, v)));
        }}
      />
    </div>
  );
}

function CandidateRow({
  candidate,
  isBest,
  runRecipeId,
  onApply,
  t,
}: {
  candidate: Candidate;
  isBest?: boolean;
  runRecipeId?: string | null;
  onApply: (fullPrompt: string, scope: "global" | "per_recipe") => void;
  t: Translations;
}) {
  const [confirming, setConfirming] = useState(false);
  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2",
        isBest
          ? "border-success/30 bg-success/5"
          : "border-border-default bg-background/40",
      )}
    >
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs">{candidate.candidate_id}</span>
        {isBest && (
          <Badge className="bg-success/15 text-xs text-success hover:bg-success/15">
            {t.recipeForge.bestBadge}
          </Badge>
        )}
        <span className="font-mono text-sm tabular-nums text-success">
          {candidate.avg_score.toFixed(3)}
        </span>
        <span className="font-mono text-xs text-muted-foreground">
          [{candidate.task_scores.map((s) => s.toFixed(2)).join(", ")}]
        </span>
        <div className="flex-1" />
        {!confirming ? (
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs"
            onClick={() => setConfirming(true)}
          >
            {t.recipeForge.applyButton}
          </Button>
        ) : (
          <div className="flex items-center gap-1">
            {runRecipeId && (
              <Button
                size="sm"
                className="h-7 bg-success text-xs hover:bg-success"
                onClick={() => {
                  onApply(candidate.prompt_preview, "per_recipe");
                  setConfirming(false);
                }}
                title={`${t.recipeForge.perRecipeScope} ${runRecipeId}`}
              >
                {t.recipeForge.applyRecipeButton(runRecipeId.slice(0, 12))}
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => {
                onApply(candidate.prompt_preview, "global");
                setConfirming(false);
              }}
              title={t.recipeForge.applyGlobalButton}
            >
              {t.recipeForge.applyGlobalButton}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs"
              onClick={() => setConfirming(false)}
            >
              {t.recipeForge.cancelButton}
            </Button>
          </div>
        )}
      </div>
      {candidate.rationale && (
        <div className="mt-1 text-xs italic text-muted-foreground">
          “{candidate.rationale}”
        </div>
      )}
      <details className="mt-1 text-xs">
        <summary className="cursor-pointer text-muted-foreground">
          {t.recipeForge.promptPreview}
        </summary>
        <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded bg-background/60 p-2 font-mono text-xs text-muted-foreground">
          {candidate.prompt_preview}
        </pre>
      </details>
    </div>
  );
}

/**
 * One row in the "Addendums by scope" list. Renders the scope as
 * a colored badge (cyan = global, violet = per-recipe), shows the
 * recipe_id (when scoped), file size + mtime, content preview,
 * and an inline two-step delete button.
 */
function AddendumRow({
  entry,
  onDelete,
  t,
}: {
  entry: AddendumEntry;
  onDelete: () => void;
  t: Translations;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const dt = new Date(entry.mtime * 1000);
  const scopeColor =
    entry.scope === "global"
      ? "bg-info/15 text-info"
      : "bg-chart-1/15 text-chart-1";
  return (
    <div className="rounded-md border border-border-default bg-background/40 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge className={cn("text-xs", scopeColor, "hover:" + scopeColor)}>
          {entry.scope === "global"
            ? t.recipeForge.globalScope
            : t.recipeForge.perRecipeScope}
        </Badge>
        {entry.recipe_id && (
          <span className="font-mono text-xs text-muted-foreground">
            {t.recipeForge.recipePrefix}: {entry.recipe_id}
          </span>
        )}
        <span className="text-muted-foreground">
          {entry.size}b · {dt.toLocaleString()}
        </span>
        <div className="flex-1" />
        {!confirmDelete ? (
          <Button
            size="sm"
            variant="ghost"
            className="h-6 text-xs"
            onClick={() => setConfirmDelete(true)}
          >
            <XCircleIcon className="mr-1 size-3" />
            {t.recipeForge.deleteButton}
          </Button>
        ) : (
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              className="h-6 bg-destructive text-xs hover:bg-destructive"
              onClick={() => {
                onDelete();
                setConfirmDelete(false);
              }}
            >
              {t.recipeForge.confirmDeleteButton}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-6 text-xs"
              onClick={() => setConfirmDelete(false)}
            >
              {t.recipeForge.cancelButton}
            </Button>
          </div>
        )}
      </div>
      <details className="mt-1">
        <summary className="cursor-pointer text-xs text-muted-foreground">
          {t.recipeForge.previewSummary}
        </summary>
        <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded bg-background/60 p-2 font-mono text-xs text-muted-foreground">
          {entry.preview}
        </pre>
      </details>
    </div>
  );
}

function HistoryRow({ entry, t }: { entry: HistoryEntry; t: Translations }) {
  if (entry.skipped) {
    return (
      <div className="text-warning">
        {t.recipeForge.historySkipped(
          String(entry.iter ?? "0"),
          entry.reason ?? "",
        )}
      </div>
    );
  }
  if (entry.early_stop) {
    return (
      <div className="text-chart-1">
        {t.recipeForge.historyEarlyStop(String(entry.iter))}
      </div>
    );
  }
  if (entry.iter === 0) {
    return (
      <div className="text-muted-foreground">
        {t.recipeForge.historySeed(String(entry.front_size))}
      </div>
    );
  }
  return (
    <div
      className={cn(
        "flex flex-wrap gap-2",
        entry.improved ? "text-success" : "text-muted-foreground",
      )}
    >
      <span>{t.recipeForge.historyIter(String(entry.iter))}</span>
      <span>
        {entry.parent_id}→{entry.child_id}
      </span>
      <span>avg={entry.child_avg?.toFixed(3) ?? "?"}</span>
      <span>front={entry.front_size}</span>
      {entry.improved && <span>{t.recipeForge.historyImproved}</span>}
      {entry.rationale && (
        <span className="basis-full pl-12 text-xs italic">
          “{entry.rationale.slice(0, 120)}”
        </span>
      )}
    </div>
  );
}
