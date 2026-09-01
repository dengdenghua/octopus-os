import { useEffect, useState } from "react";
import {
  ActivityIcon,
  AlertTriangleIcon,
  BarChartIcon,
  DnaIcon,
  Loader2Icon,
  RefreshCwIcon,
  ShuffleIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  getRegenerationStatus,
  type RegenerationStatus,
} from "@/core/regeneration/api";
import { swallow } from "@/core/utils/log";
import { useI18n } from "@/core/i18n/hooks";

const SEVERITY_COLOR: Record<string, string> = {
  high: "bg-destructive/15 text-destructive border-destructive/30",
  mid: "bg-warning/15 text-warning border-warning/30",
  low: "bg-info/15 text-info dark:text-info border-info/30",
};

function formatTs(ts: number | undefined): string {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

function numberOrZero(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function fixed(value: unknown, digits: number): string {
  return numberOrZero(value).toFixed(digits);
}

export default function EvolutionSettingsPage() {
  const { t } = useI18n();
  const e = t.settings.evolution;
  const [data, setData] = useState<RegenerationStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const s = await getRegenerationStatus();
      setData(s);
      setErr(null);
    } catch (er) {
      swallow(er);
      setErr(er instanceof Error ? er.message : String(er));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), 10_000);
    return () => clearInterval(id);
  }, []);

  if (loading && !data) {
    return (
      <div
        className="flex items-center py-8 text-sm text-muted-foreground"
        role="status"
        aria-live="polite"
      >
        <Loader2Icon className="mr-2 h-4 w-4 animate-spin" />
        {e.loading}
      </div>
    );
  }
  if (err && !data) {
    return (
      <div
        className="flex flex-col items-start justify-between gap-3 rounded-lg border border-destructive/25 bg-destructive/5 px-4 py-3 text-sm text-destructive sm:flex-row sm:items-center"
        role="alert"
      >
        <span>
          {e.loadFailed}: {err}
        </span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="w-full sm:w-auto"
          onClick={() => void load()}
        >
          <RefreshCwIcon className="mr-1.5 size-3.5" aria-hidden="true" />
          {e.refresh}
        </Button>
      </div>
    );
  }
  if (!data) return null;

  const sched = data.scheduler;
  const rules = data.learned_rules;
  const recipes = data.recipe_scores;
  const gepa = data.gepa_proposals;
  const camouflage = data.camouflage;

  return (
    <div className="space-y-6" aria-busy={loading}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">{e.title}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{e.description}</p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={loading}
          onClick={() => void load()}
        >
          <RefreshCwIcon
            className={`mr-1.5 h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
            aria-hidden="true"
          />
          {e.refresh}
        </Button>
      </div>

      {err ? (
        <div
          className="flex flex-col items-start justify-between gap-3 rounded-lg border border-destructive/25 bg-destructive/5 px-4 py-3 text-sm text-destructive sm:flex-row sm:items-center"
          role="alert"
        >
          <span>
            {e.loadFailed}: {err}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="w-full sm:w-auto"
            disabled={loading}
            onClick={() => void load()}
          >
            {e.refresh}
          </Button>
        </div>
      ) : null}

      {/* Scheduler */}
      <div className="rounded-lg border border-border-default bg-card/30 p-4">
        <div className="flex items-center gap-2 text-sm font-medium">
          <ActivityIcon className="h-4 w-4" />
          {e.schedulerStatus}
        </div>
        <div className="mt-3 grid grid-cols-3 gap-3 text-xs">
          <div>
            <div className="text-muted-foreground">{e.runningLabel}</div>
            <div
              className={
                sched.running
                  ? "mt-0.5 font-medium text-success"
                  : "mt-0.5 font-medium text-destructive"
              }
            >
              {sched.running ? e.runningYes : e.runningNo}
            </div>
          </div>
          <div>
            <div className="text-muted-foreground">{e.intervalLabel}</div>
            <div className="mt-0.5 font-mono">{sched.interval_sec}s</div>
          </div>
          <div>
            <div className="text-muted-foreground">{e.tickedLabel}</div>
            <div className="mt-0.5 font-mono">
              {sched.tick_count}
              {e.tickedUnit ? ` ${e.tickedUnit}` : ""}
            </div>
          </div>
        </div>
        {sched.last_summary && Object.keys(sched.last_summary).length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {Object.entries(sched.last_summary)
              .filter(([k]) => !["tick", "ts"].includes(k))
              .map(([k, v]) => (
                <Badge
                  key={k}
                  variant="outline"
                  className="text-xs font-mono"
                >
                  {k}={String(v)}
                </Badge>
              ))}
          </div>
        )}
      </div>

      {/* RuleExtractor */}
      <div>
        <div className="flex items-center gap-2 text-sm font-medium">
          <AlertTriangleIcon className="h-4 w-4" />
          {e.learnedRulesTitle}
        </div>
        {rules ? (
          <>
            <div className="mt-2 text-xs text-muted-foreground">
              {e.scanned} {rules.trajectories_scanned} {e.trajectoryUnit}{" "}
              {rules.failure_count} · {e.clusters} {rules.clusters_formed} ·{" "}
              {e.produced} {rules.rules.length} {e.ruleUnit}· {e.lastTick}{" "}
              {formatTs(rules.ts)}
            </div>
            <div className="mt-3 space-y-2">
              {rules.rules.length === 0 ? (
                <div className="text-xs text-muted-foreground">
                  {e.noFailureData}
                </div>
              ) : (
                rules.rules.map((r) => (
                  <div
                    key={r.rule_id}
                    className="rounded-lg border border-border-default bg-card/30 p-3"
                  >
                    <div className="flex items-center gap-2">
                      <Badge
                        variant="outline"
                        className={`text-xs ${SEVERITY_COLOR[r.severity] || ""}`}
                      >
                        {r.severity}
                      </Badge>
                      <span className="text-xs font-mono text-muted-foreground">
                        {r.sucker_id}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        × {r.hit_count}
                      </span>
                    </div>
                    <div className="mt-1.5 text-xs">
                      <span className="text-muted-foreground">
                        {e.ruleTrigger}
                      </span>{" "}
                      {r.pattern}
                    </div>
                    <div className="mt-0.5 text-xs">
                      <span className="text-muted-foreground">
                        {e.ruleMitigation}
                      </span>{" "}
                      {r.mitigation}
                    </div>
                  </div>
                ))
              )}
            </div>
          </>
        ) : (
          <div className="mt-2 text-xs text-muted-foreground">
            {e.notGenerated}
          </div>
        )}
      </div>

      <Separator />

      {/* RecipeEvaluator */}
      <div>
        <div className="flex items-center gap-2 text-sm font-medium">
          <BarChartIcon className="h-4 w-4" />
          {e.recipeScoreTitle}
        </div>
        {recipes && recipes.scores.length > 0 ? (
          <div className="mt-2 overflow-x-auto max-w-full">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border-default text-left text-muted-foreground">
                  <th className="px-2 py-1">{e.colRecipe}</th>
                  <th className="px-2 py-1">{e.colUses}</th>
                  <th className="px-2 py-1">{e.colSuccessRate}</th>
                  <th className="px-2 py-1">{e.colAvgSteps}</th>
                  <th className="px-2 py-1">{e.colVerdict}</th>
                  <th className="px-2 py-1">{e.colScore}</th>
                </tr>
              </thead>
              <tbody>
                {recipes.scores.map((s) => (
                  <tr
                    key={s.recipe_id}
                    className="border-b border-border-subtle"
                  >
                    <td className="px-2 py-1.5 font-mono">{s.recipe_id}</td>
                    <td className="px-2 py-1.5">{s.uses}</td>
                    <td className="px-2 py-1.5">
                      {fixed(numberOrZero(s.success_rate) * 100, 1)}%
                    </td>
                    <td className="px-2 py-1.5">
                      {fixed(s.avg_step_count, 1)}
                    </td>
                    <td className="px-2 py-1.5">
                      <Badge variant="outline" className="text-xs">
                        {s.verdict}
                      </Badge>
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      {fixed(s.score, 2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="mt-2 text-xs text-muted-foreground">{e.noData}</div>
        )}
      </div>

      <Separator />

      {/* GEPA */}
      <div>
        <div className="flex items-center gap-2 text-sm font-medium">
          <ActivityIcon className="h-4 w-4" />
          {e.gepaTitle}
          <Badge variant="outline" className="ml-2 text-xs">
            {gepa?.auto_apply ? e.gepaAutoApplyBadge : e.gepaDryRunBadge}
          </Badge>
        </div>
        {gepa ? (
          <>
            <div className="mt-2 text-xs text-muted-foreground">
              {e.gepaScannedPrefix} {gepa.recipes_scanned} {e.gepaManifestUnit}{" "}
              {gepa.recipes_promoted} · {e.lastTick} {formatTs(gepa.ts)}
            </div>
            <div className="mt-2 space-y-1.5">
              {gepa.results.slice(0, 8).map((r, i) => (
                <div
                  key={`${r.recipe_id}-${i}`}
                  className="rounded-md border border-border-subtle bg-card/20 p-2 text-xs"
                >
                  <div className="font-mono">{r.recipe_id}</div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {r.skipped
                      ? `${e.gepaSkippedPrefix} ${r.reason}`
                      : (r.rationale ?? "ok")}
                  </div>
                </div>
              ))}
            </div>
            {!gepa.auto_apply && (
              <div className="mt-3 rounded-md border border-warning/60 bg-warning/5 p-2.5 text-xs dark:border-warning/40">
                {e.gepaDryRunHint}
              </div>
            )}
          </>
        ) : (
          <div className="mt-2 text-xs text-muted-foreground">{e.noData}</div>
        )}
      </div>

      <Separator />

      {/* Camouflage / PromptEvolver */}
      <div>
        <div className="flex items-center gap-2 text-sm font-medium">
          <ShuffleIcon className="h-4 w-4" />
          {e.camouflageTitle}
          <Badge
            variant="outline"
            className={
              camouflage?.enabled
                ? "ml-2 text-xs border-success/40 text-success"
                : "ml-2 text-xs text-muted-foreground"
            }
          >
            {camouflage?.enabled
              ? e.camouflageEnabledBadge
              : e.camouflageDisabledBadge}
          </Badge>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {e.camouflageDescription}
        </p>
        {!camouflage?.enabled ? (
          <div className="mt-3 rounded-md border border-warning/60 bg-warning/5 p-2.5 text-xs dark:border-warning/40">
            {e.camouflageDisabledHint}
            {camouflage?.last_error ? (
              <div className="mt-1 font-mono text-xs opacity-70">
                {camouflage.last_error}
              </div>
            ) : null}
          </div>
        ) : (
          <>
            <div className="mt-3 grid grid-cols-4 gap-3 text-xs">
              <div>
                <div className="text-muted-foreground">
                  {e.camouflageVariantsLabel}
                </div>
                <div className="mt-0.5 font-mono">
                  {camouflage.variants?.length ?? 0}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">
                  {e.camouflageStepsLabel}
                </div>
                <div className="mt-0.5 font-mono">
                  {camouflage.auto_retire?.total_steps ?? 0}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">
                  {e.camouflageRetiredLabel}
                </div>
                <div className="mt-0.5 font-mono">
                  {camouflage.auto_retire?.total_retired ?? 0}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">
                  {e.camouflageBoostedLabel}
                </div>
                <div className="mt-0.5 font-mono">
                  {camouflage.auto_retire?.total_boosted ?? 0}
                </div>
              </div>
            </div>
            {camouflage.auto_retire?.last_step_at ? (
              <div className="mt-2 text-xs text-muted-foreground">
                {e.camouflageLastStepLabel}{" "}
                {formatTs(camouflage.auto_retire.last_step_at)}
                {camouflage.auto_retire.last_step_summary
                  ? ` · ${camouflage.auto_retire.last_step_summary}`
                  : ""}
              </div>
            ) : null}
            <div className="mt-3 space-y-1.5">
              {!camouflage.variants || camouflage.variants.length === 0 ? (
                <div className="text-xs text-muted-foreground">
                  {e.camouflageNoVariants}
                </div>
              ) : (
                camouflage.variants.map((v) => {
                  const originLabel =
                    v.origin === "mutation"
                      ? e.camouflageOriginMutation
                      : v.origin === "crossover"
                        ? e.camouflageOriginCrossover
                        : e.camouflageOriginSeed;
                  const OriginIcon = v.origin === "mutation" ? DnaIcon : null;
                  return (
                    <div
                      key={v.name}
                      className="flex items-center gap-2 rounded-md border border-border-subtle bg-card/20 px-2 py-1.5 text-xs"
                    >
                      <span className="font-mono">{v.name}</span>
                      <Badge variant="outline" className="gap-1 text-xs">
                        {OriginIcon ? (
                          <OriginIcon className="h-3 w-3" aria-hidden />
                        ) : null}
                        {originLabel}
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        gen={v.generation} · w={fixed(v.weight, 2)} ·{" "}
                        {v.suffix_chars}ch
                      </span>
                      {v.parents.length > 0 ? (
                        <span className="ml-auto font-mono text-xs text-muted-foreground">
                          ← {v.parents.join(", ")}
                        </span>
                      ) : null}
                    </div>
                  );
                })
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
