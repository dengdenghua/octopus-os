/**
 * Variant performance panel · shows live A/B stats for every
 * recipe that has a RecipeForge manifest active.
 *
 * Closes the loop: operator spawns N variants via the main Forge
 * panel (apply with variant_id), real traffic accumulates in the
 * journal (trajectories tagged with recipe#variant), this panel
 * reads the stats and offers a one-click "auto-promote the
 * winner" once the data is strong enough (Wilson lower-bound
 * lead ≥ 10pp by default · thresholds are tunable).
 *
 * Why a separate component
 * ------------------------
 *
 * Keeping this out of gepa-panel.tsx · that file already handles
 * a lot (optimizer run, addendum apply, convergence chart,
 * addendum list, past runs, CSV exports). Variants are the
 * multi-armed-bandit layer on top of addendums · conceptually
 * distinct, deserves its own card.
 *
 * Backend endpoints used
 * ----------------------
 *
 *   GET  /api/evolution/forge/recipes
 *     → list of recipe_ids that have manifests
 *   GET  /api/evolution/forge/variants/<recipe>/stats
 *     → per-variant uses / success_rate / wilson_lower
 *   POST /api/evolution/forge/variants/<recipe>/auto-promote
 *     → propose (or apply) new weights based on the stats
 */

import { swallow } from "@/core/utils/log";
import {
  BarChart3Icon,
  CheckCheckIcon,
  PauseIcon,
  PlayIcon,
  RefreshCwIcon,
  Sparkles,
  TimerIcon,
  TrophyIcon,
  ZapIcon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

import { reflexFetch } from "./api";

type RecipeSummary = {
  recipe_id: string;
  variant_count: number;
  total_weight: number;
  default_weight: number;
  updated_at: number;
};

type VariantStatRow = {
  variant_id: string;
  uses: number;
  successes: number;
  success_rate: number;
  wilson_lower: number;
  avg_step_count: number;
  avg_cost_usd: number;
};

type VariantStatsResp = {
  recipe_id: string;
  total_uses: number;
  variants: VariantStatRow[];
};

type Proposal = {
  base_recipe_id: string;
  winner_variant_id: string | null;
  winner_lower_bound: number;
  runner_up_lower_bound: number;
  weights: Record<string, number>;
  rationale: string;
};

type PromoteResp = {
  ok: boolean;
  error?: string;
  skipped?: boolean;
  reason?: string;
  proposal?: Proposal;
  applied?: boolean;
  current_stats?: Array<{
    variant_id: string;
    uses: number;
    success_rate: number;
    wilson_lower: number;
  }>;
};

type AutoTickStatus = {
  enabled: boolean;
  interval_hours: number;
  min_uses: number;
  min_lead: number;
  started_at: number | null;
  next_tick_at: number | null;
  ticks_done: number;
  last_tick: {
    ts: number;
    elapsed_s: number;
    recipes_scanned: number;
    recipes_promoted: number;
    results: Array<{
      recipe_id?: string;
      ok: boolean;
      applied?: boolean;
      skipped?: boolean;
      winner?: string | null;
      rationale?: string;
      reason?: string;
      error?: string;
    }>;
  } | null;
};

export function VariantPerformancePanel() {
  const [recipes, setRecipes] = useState<RecipeSummary[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [tickAt, setTickAt] = useState<Date | null>(null);
  const [autoTick, setAutoTick] = useState<AutoTickStatus | null>(null);
  const [autoBusy, setAutoBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([
        reflexFetch<{ recipes: RecipeSummary[] }>(
          "/api/evolution/forge/recipes",
        ),
        reflexFetch<AutoTickStatus>(
          "/api/evolution/forge/auto-tick/status",
        ).catch(() => null),
      ]);
      setRecipes(r.recipes ?? []);
      setAutoTick(s);
      setErr(null);
      setTickAt(new Date());
    } catch (e) {
      swallow(e);
      setErr(e instanceof Error ? e.message : "fetch failed");
    }
  }, []);

  useEffect(() => {
    void reload();
    const tid = window.setInterval(() => void reload(), 10000);
    return () => window.clearInterval(tid);
  }, [reload]);

  const toggleAuto = useCallback(async () => {
    setAutoBusy(true);
    try {
      const path = autoTick?.enabled
        ? "/api/evolution/forge/auto-tick/disable"
        : "/api/evolution/forge/auto-tick/enable?interval_hours=24";
      await reflexFetch<unknown>(path, { method: "POST" });
      void reload();
    } finally {
      setAutoBusy(false);
    }
  }, [autoTick?.enabled, reload]);

  const tickNow = useCallback(async () => {
    setAutoBusy(true);
    try {
      await reflexFetch<unknown>(
        "/api/evolution/forge/auto-tick/run-now?apply=true",
        { method: "POST" },
      );
      void reload();
    } finally {
      setAutoBusy(false);
    }
  }, [reload]);

  // Hide the card entirely when there's nothing to show AND the
  // auto-tick daemon is off · fresh deployments stay clean. Once
  // the operator either applies a variant OR enables the daemon,
  // the card appears (auto-tick bar is informative even with no
  // manifests · "we're waiting but haven't run yet").
  if (recipes.length === 0 && !autoTick?.enabled) return null;

  return (
    <Card className="workspace-panel border-white/40 shadow-none dark:border-white/10">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <BarChart3Icon className="size-4" />
          Variant performance · {recipes.length} recipe
          {recipes.length !== 1 ? "s" : ""} with A/B running
          {tickAt && (
            <span className="ml-auto text-xs font-normal text-muted-foreground">
              {tickAt.toLocaleTimeString()}
            </span>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="h-6 text-xs"
            onClick={reload}
          >
            <RefreshCwIcon className="size-3" />
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {err && (
          <div className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {err}
          </div>
        )}

        {/* Auto-tick status bar · single row showing daemon state
            + actions. Collapsed to two lines when enabled (status
            + last-tick summary), one line when disabled. */}
        <AutoTickBar
          status={autoTick}
          busy={autoBusy}
          onToggle={toggleAuto}
          onTickNow={tickNow}
        />

        {recipes.map((r) => (
          <RecipeRow key={r.recipe_id} summary={r} onChange={reload} />
        ))}
      </CardContent>
    </Card>
  );
}

/**
 * One row per recipe · lazily fetches its per-variant stats on
 * mount (so we don't fire N parallel requests when the recipe
 * list is long · each row is independent), shows the bandit
 * table + the promote-preview/apply controls.
 */
function RecipeRow({
  summary,
  onChange,
}: {
  summary: RecipeSummary;
  onChange: () => void;
}) {
  const [stats, setStats] = useState<VariantStatsResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [promote, setPromote] = useState<PromoteResp | null>(null);
  const [promoting, setPromoting] = useState(false);
  const [confirmApply, setConfirmApply] = useState(false);

  const loadStats = useCallback(async () => {
    setLoading(true);
    try {
      const r: VariantStatsResp = await reflexFetch<VariantStatsResp>(
        `/api/evolution/forge/variants/${encodeURIComponent(
          summary.recipe_id,
        )}/stats`,
      );
      setStats(r);
    } catch (e) {
      swallow(e);
    } finally {
      setLoading(false);
    }
  }, [summary.recipe_id]);

  useEffect(() => {
    void loadStats();
  }, [loadStats]);

  const previewPromote = useCallback(async () => {
    setPromoting(true);
    try {
      const r: PromoteResp = await reflexFetch<PromoteResp>(
        `/api/evolution/forge/variants/${encodeURIComponent(
          summary.recipe_id,
        )}/auto-promote?apply=false`,
        { method: "POST" },
      );
      setPromote(r);
      setConfirmApply(false);
    } catch (e) {
      swallow(e);
      setPromote({
        ok: false,
        error: e instanceof Error ? e.message : "preview failed",
      });
    } finally {
      setPromoting(false);
    }
  }, [summary.recipe_id]);

  const applyPromote = useCallback(async () => {
    setPromoting(true);
    try {
      const r: PromoteResp = await reflexFetch<PromoteResp>(
        `/api/evolution/forge/variants/${encodeURIComponent(
          summary.recipe_id,
        )}/auto-promote?apply=true`,
        { method: "POST" },
      );
      setPromote(r);
      if (r.applied) {
        void loadStats();
        onChange();
      }
    } catch (e) {
      swallow(e);
      setPromote({
        ok: false,
        error: e instanceof Error ? e.message : "apply failed",
      });
    } finally {
      setPromoting(false);
      setConfirmApply(false);
    }
  }, [summary.recipe_id, loadStats, onChange]);

  return (
    <div className="rounded-lg border border-border-default bg-background/60 px-4 py-3">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-mono text-xs text-muted-foreground">recipe:</span>
        <span className="font-mono">{summary.recipe_id}</span>
        <Badge variant="outline" className="text-xs">
          {summary.variant_count} variants · total weight {summary.total_weight}
        </Badge>
        {summary.default_weight > 0 && (
          <Badge className="bg-muted-foreground/15 text-xs text-muted-foreground hover:bg-muted-foreground/15">
            control branch w={summary.default_weight}
          </Badge>
        )}
      </div>

      {loading && (
        <div className="text-xs text-muted-foreground">loading stats…</div>
      )}

      {stats && stats.variants.length > 0 && (
        <table className="w-full text-xs">
          <thead className="text-xs uppercase tracking-wide text-muted-foreground">
            <tr className="border-b border-border-subtle">
              <th className="pb-1 text-left font-medium">variant</th>
              <th className="pb-1 text-right font-medium">uses</th>
              <th className="pb-1 text-right font-medium">✓</th>
              <th className="pb-1 text-right font-medium">rate</th>
              <th className="pb-1 text-right font-medium">wilson₉₅%</th>
              <th className="pb-1 text-right font-medium">steps</th>
              <th className="pb-1 text-right font-medium">cost</th>
            </tr>
          </thead>
          <tbody>
            {/* Sort by wilson_lower descending · puts the current
                leader at the top for easy visual scan. */}
            {[...stats.variants]
              .sort((a, b) => b.wilson_lower - a.wilson_lower)
              .map((v, i) => (
                <VariantStatRow
                  key={v.variant_id || `legacy-${i}`}
                  row={v}
                  isLeader={i === 0 && v.uses > 0}
                />
              ))}
          </tbody>
        </table>
      )}
      {stats && stats.variants.length === 0 && (
        <div className="text-xs text-muted-foreground">
          no trajectories tagged with this recipe yet · accumulating…
        </div>
      )}

      {/* Promote controls · preview first, confirm to apply. */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          onClick={previewPromote}
          disabled={promoting}
        >
          <Sparkles className="mr-1 size-3" />
          {promoting ? "analyzing…" : "Preview auto-promote"}
        </Button>
        {promote?.proposal && !confirmApply && (
          <Button
            size="sm"
            className="h-7 bg-success text-xs hover:bg-success"
            onClick={() => setConfirmApply(true)}
          >
            <TrophyIcon className="mr-1 size-3" />
            Apply proposal…
          </Button>
        )}
        {confirmApply && (
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              className="h-7 bg-success text-xs hover:bg-success"
              onClick={applyPromote}
              disabled={promoting}
            >
              <CheckCheckIcon className="mr-1 size-3" />
              Confirm apply
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs"
              onClick={() => setConfirmApply(false)}
            >
              Cancel
            </Button>
          </div>
        )}
      </div>

      {/* Preview/apply result · rationale + weight diff. */}
      {promote && (
        <div
          className={cn(
            "mt-2 rounded-md px-3 py-2 text-xs",
            promote.applied
              ? "bg-success/10 text-success"
              : promote.skipped
                ? "bg-muted-foreground/10 text-muted-foreground"
                : promote.ok
                  ? "bg-warning/10 text-warning"
                  : "bg-destructive/10 text-destructive",
          )}
        >
          {promote.error && <div>✗ {promote.error}</div>}
          {promote.skipped && <div>ℹ︎ no winner yet · {promote.reason}</div>}
          {promote.proposal && (
            <>
              <div className="font-medium">
                {promote.applied ? "✓ Applied" : "Proposed"}: winner{" "}
                <span className="font-mono">
                  {promote.proposal.winner_variant_id}
                </span>
              </div>
              <div className="mt-1 italic">"{promote.proposal.rationale}"</div>
              <div className="mt-1 font-mono text-xs">
                new weights:{" "}
                {Object.entries(promote.proposal.weights)
                  .map(([k, v]) => `${k}=${v}`)
                  .join(", ")}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Top-of-panel status bar for the auto-tick scheduler.
 *
 * Shows one of three states:
 *   - disabled · "Auto-promote: OFF [Enable (24h)] [Run once]"
 *   - enabled + never ticked · "Auto-promote: ON · next tick in …"
 *   - enabled + has ticked · "Auto-promote: ON · last ran HH:MM
 *     · promoted N/M · [Disable] [Run now]"
 *
 * The "Run now" button is always available regardless of scheduler
 * state · that's the "I want to apply every pending proposal right
 * now" knob for impatient operators.
 */
function AutoTickBar({
  status,
  busy,
  onToggle,
  onTickNow,
}: {
  status: AutoTickStatus | null;
  busy: boolean;
  onToggle: () => void;
  onTickNow: () => void;
}) {
  if (!status) {
    // Endpoint missing · probably older backend. Hide the bar
    // entirely rather than show a broken placeholder.
    return null;
  }
  const last = status.last_tick;
  const nextStr = status.next_tick_at
    ? new Date(status.next_tick_at * 1000).toLocaleString()
    : null;
  const lastStr = last ? new Date(last.ts * 1000).toLocaleString() : null;
  return (
    <div
      className={cn(
        "rounded-lg border px-4 py-3",
        status.enabled
          ? "border-success/30 bg-success/5"
          : "border-border-default bg-background/60",
      )}
    >
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <TimerIcon
          className={cn(
            "size-4",
            status.enabled ? "text-success" : "text-muted-foreground",
          )}
        />
        <span className="font-medium">Auto-promote daemon:</span>
        <Badge
          className={cn(
            "text-xs",
            status.enabled
              ? "bg-success/15 text-success"
              : "bg-muted-foreground/15 text-muted-foreground",
            "hover:" +
              (status.enabled ? "bg-success/15" : "bg-muted-foreground/15"),
          )}
        >
          {status.enabled ? "ON" : "OFF"}
        </Badge>
        {status.enabled && (
          <span className="text-xs text-muted-foreground">
            every {status.interval_hours.toFixed(0)}h · min_uses{" "}
            {status.min_uses} · min_lead {(status.min_lead * 100).toFixed(0)}pp
          </span>
        )}
        <div className="flex-1" />
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs"
          onClick={onTickNow}
          disabled={busy}
          title="Run one tick right now (ignores interval)"
        >
          <ZapIcon className="mr-1 size-3" />
          Run now
        </Button>
        <Button
          size="sm"
          variant={status.enabled ? "ghost" : "default"}
          className="h-7 text-xs"
          onClick={onToggle}
          disabled={busy}
        >
          {status.enabled ? (
            <>
              <PauseIcon className="mr-1 size-3" />
              Disable
            </>
          ) : (
            <>
              <PlayIcon className="mr-1 size-3" />
              Enable (24h)
            </>
          )}
        </Button>
      </div>
      {(last || nextStr) && (
        <div className="mt-1 text-xs text-muted-foreground">
          {last && (
            <>
              last ran {lastStr} · scanned {last.recipes_scanned} recipe
              {last.recipes_scanned !== 1 ? "s" : ""} · promoted{" "}
              <span className="text-success">{last.recipes_promoted}</span>
              {last.elapsed_s > 0 && ` · ${last.elapsed_s.toFixed(2)}s`}
              {" · "}
            </>
          )}
          {status.enabled && nextStr && <>next tick {nextStr}</>}
        </div>
      )}
      {last && last.results.length > 0 && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-muted-foreground">
            last tick actions ({last.results.length})
          </summary>
          <div className="mt-1 space-y-1 font-mono">
            {last.results.map((r, i) => (
              <div
                key={i}
                className={cn(
                  "truncate",
                  r.applied
                    ? "text-success"
                    : r.skipped
                      ? "text-muted-foreground"
                      : r.error
                        ? "text-destructive"
                        : "text-warning",
                )}
              >
                {r.applied && "✓ "}
                {r.skipped && "⋯ "}
                {r.error && "✗ "}
                {r.recipe_id ?? "?"}
                {r.applied && r.winner && ` → winner=${r.winner}`}
                {r.skipped && r.reason && ` · ${r.reason}`}
                {r.error && ` · ${r.error}`}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function VariantStatRow({
  row,
  isLeader,
}: {
  row: VariantStatRow;
  isLeader: boolean;
}) {
  // Rename display for the two special "pseudo-variants" so the
  // operator doesn't get confused by the machine-shaped ids:
  //   ""              → "(legacy / pre-manifest)"
  //   "__default__"   → "(control · no addendum)"
  const label =
    row.variant_id === ""
      ? "(legacy)"
      : row.variant_id === "__default__"
        ? "(control)"
        : row.variant_id;
  return (
    <tr
      className={cn(
        "border-b border-border-subtle",
        isLeader && "bg-success/5",
      )}
    >
      <td className="py-1 font-mono">
        {isLeader && (
          <TrophyIcon className="mr-1 inline size-3 text-success" />
        )}
        {label}
      </td>
      <td className="py-1 text-right font-mono">{row.uses}</td>
      <td className="py-1 text-right font-mono">{row.successes}</td>
      <td
        className={cn(
          "py-1 text-right font-mono",
          row.success_rate >= 0.7
            ? "text-success"
            : row.success_rate >= 0.4
              ? "text-warning"
              : "text-destructive",
        )}
      >
        {(row.success_rate * 100).toFixed(0)}%
      </td>
      <td className="py-1 text-right font-mono text-muted-foreground">
        {row.wilson_lower.toFixed(3)}
      </td>
      <td className="py-1 text-right font-mono text-muted-foreground">
        {row.avg_step_count.toFixed(1)}
      </td>
      <td className="py-1 text-right font-mono text-muted-foreground">
        {row.avg_cost_usd > 0 ? `$${row.avg_cost_usd.toFixed(4)}` : "—"}
      </td>
    </tr>
  );
}
