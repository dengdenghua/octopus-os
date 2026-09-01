/**
 * /workspace/reflex · SpinalCord reflex monitor (React port).
 *
 * Replaces the inline-HTML admin panel formerly served at /admin/reflex.
 * Same backend endpoints (/api/reflex/stats, /rules, /timeseries),
 * same sparkline + stats cards + per-rule table, but rendered with
 * the workspace's design tokens so it doesn't look like a 1995 cgi
 * page next to the React app.
 *
 * Two reasons this lives in the React routes:
 *   1. Sidebar navigation · the inline-HTML page lived at a non-
 *      workspace URL so it never showed in the user's nav menu;
 *      operators had to bookmark /admin/reflex separately.
 *   2. Theming · the inline panel was hardcoded dark · breaks
 *      visually for users on the light theme.
 */

import { swallow } from "@/core/utils/log";
import {
  ActivityIcon,
  BarChart3Icon,
  ClockIcon,
  EditIcon,
  HourglassIcon,
  RefreshCwIcon,
  TargetIcon,
  ZapIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  WorkspaceBody,
  WorkspaceContainer,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

// RecipeForge prompt-evolution subsystem · lives in gepa-panel.tsx
// for historical reasons (original branch branding); the file is
// kept under that filename to avoid a noisy `git mv` in history.
// The public export name + all user-facing labels say "RecipeForge".
import { reflexFetch } from "./api";
import { GepaPanel as RecipeForgePanel } from "./gepa-panel";
import { VariantPerformancePanel } from "./variant-performance-panel";
import { GeneLockBadge } from "@/components/workspace/gene-lock-badge";

type Stats = {
  try_count: number;
  hit_count: number;
  hit_rate: number;
  by_rule: Record<
    string,
    { tries: number; hits: number; hit_rate: number; last_hit_at?: number }
  >;
  coverage?: {
    stale: string[];
    unexercised: string[];
    total_rules: number;
    stale_threshold_hours: number;
  };
};

type VariantInfo = {
  variant_id: string;
  weight: number;
  hits: number;
  preview: string;
};

type Rule = {
  rule_id: string;
  kind: string;
  priority: number;
  pattern?: string;
  intent_type?: string;
  ttl_seconds?: number;
  last_hit_at?: number;
  actions?: string[];
  variants?: VariantInfo[];
  per_actor?: Record<string, string>;
  enabled_when?: Record<string, unknown>;
};

type TimeseriesBucket = {
  ts: number;
  count: number;
  by_rule: Record<string, number>;
};
type Timeseries = {
  window_minutes: number;
  bucket_seconds: number;
  buckets: TimeseriesBucket[];
  totals_by_rule: Record<string, number>;
  total_events: number;
};

type TierInfo = {
  name: string;
  enabled: boolean;
  hits?: number;
  misses?: number;
  hit_rate?: number;
  size?: number;
  endpoint?: string;
  similarity?: number;
};

type ReloadResp = {
  ok: boolean;
  rules_loaded: number;
  stats_reset: boolean;
  error: string;
};

const POLL_INTERVAL_MS = 2000;

export function ReflexMonitorContent() {
  const { t } = useI18n();
  const [stats, setStats] = useState<Stats | null>(null);
  const [rules, setRules] = useState<Rule[]>([]);
  const [series, setSeries] = useState<Timeseries | null>(null);
  const [tiers, setTiers] = useState<TierInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reloadMsg, setReloadMsg] = useState<string | null>(null);
  const [reloadHasError, setReloadHasError] = useState(false);
  const [tickedAt, setTickedAt] = useState<Date | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [s, r, t, ti] = await Promise.all([
        reflexFetch<Stats>("/api/reflex/stats"),
        reflexFetch<{ rules: Rule[] }>("/api/reflex/rules"),
        reflexFetch<Timeseries>(
          "/api/reflex/timeseries?window_minutes=60&bucket_seconds=60",
        ),
        reflexFetch<{ tiers: TierInfo[] }>("/api/reflex/tiers")
          // The /api/reflex/tiers endpoint is opt-in (post-tier-feature
          // backends may not expose it). Treat 404 as "no tiers" rather
          // than a hard error so the page still works on older builds.
          .catch(() => ({ tiers: [] as TierInfo[] })),
      ]);
      setStats(s);
      setRules(r.rules ?? []);
      setSeries(t);
      setTiers(ti.tiers ?? []);
      setError(null);
      setTickedAt(new Date());
    } catch (e) {
      swallow(e);
      setError(t.reflexPage.fetchFailed);
    }
  }, [t.reflexPage.fetchFailed]);

  useEffect(() => {
    void fetchAll();
    const tid = window.setInterval(() => {
      void fetchAll();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(tid);
  }, [fetchAll]);

  const reload = useCallback(
    async (resetStats: boolean) => {
      setReloadMsg(t.reflexPage.reloadingStatus);
      setReloadHasError(false);
      try {
        const r = await reflexFetch<ReloadResp>(
          `/api/reflex/reload${resetStats ? "?reset_stats=true" : ""}`,
          { method: "POST" },
        );
        if (r.ok) {
          setReloadMsg(
            t.reflexPage.reloadLoaded(r.rules_loaded, r.stats_reset),
          );
        } else {
          setReloadMsg(t.reflexPage.reloadFailed);
          setReloadHasError(true);
        }
        void fetchAll();
      } catch (e) {
        swallow(e);
        setReloadMsg(t.reflexPage.reloadFailed);
        setReloadHasError(true);
      }
      window.setTimeout(() => {
        setReloadMsg(null);
        setReloadHasError(false);
      }, 4000);
    },
    [fetchAll, t],
  );

  const staleSet = useMemo(
    () => new Set(stats?.coverage?.stale ?? []),
    [stats],
  );
  const unexercisedSet = useMemo(
    () => new Set(stats?.coverage?.unexercised ?? []),
    [stats],
  );
  const sortedRules = useMemo(
    () => [...rules].sort((a, b) => b.priority - a.priority),
    [rules],
  );
  const hasReflexSnapshot = stats !== null && series !== null;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      {/* Hero / actions */}
      <section className="workspace-panel px-4 py-4 sm:px-6 sm:py-5">
        <div className="flex flex-col items-start gap-4 md:flex-row md:items-center">
          <div className="flex size-11 items-center justify-center rounded-lg bg-gradient-to-br from-success to-cyan-500 text-white shadow-[var(--shadow-md)] shadow-success/20">
            <ZapIcon className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-bold tracking-tight">
              {t.reflexPage.pageTitle}
            </h1>
            <p className="text-sm text-muted-foreground">
              {t.reflexPage.subtitle}
              {tickedAt
                ? t.reflexPage.lastRefreshPrefix(tickedAt.toLocaleTimeString())
                : ""}
            </p>
          </div>
          <div className="flex w-full flex-wrap items-center gap-2 lg:w-auto lg:justify-end">
            {/* Gene-lock badge · shows current maturity level +
                    panic state · click to drill into governance
                    controls. Auto-hides when the /api/gene-locks/
                    endpoint isn't available (older backends). */}
            <GeneLockBadge />
            <Button asChild variant="outline" size="sm">
              <Link to="/workspace/reflex/edit">
                <EditIcon className="mr-2 size-4" />
                {t.reflexPage.editRulesButton}
              </Link>
            </Button>
            <Button variant="outline" size="sm" onClick={() => reload(false)}>
              <RefreshCwIcon className="mr-2 size-4" />
              {t.reflexPage.reloadButton}
            </Button>
            <Button variant="outline" size="sm" onClick={() => reload(true)}>
              {t.reflexPage.reloadResetButton}
            </Button>
          </div>
        </div>
        {(reloadMsg || error) && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            {reloadMsg && (
              <span
                role={reloadHasError ? "alert" : "status"}
                className={cn(
                  "rounded-md px-2 py-1",
                  reloadHasError
                    ? "bg-destructive/10 text-destructive"
                    : "bg-success/10 text-success",
                )}
              >
                {reloadMsg}
              </span>
            )}
            {error && (
              <div
                role="alert"
                className="flex items-center gap-2 rounded-md bg-destructive/10 px-2 py-1 text-destructive"
              >
                <span>
                  {hasReflexSnapshot
                    ? t.reflexPage.dataRefreshFailed
                    : t.reflexPage.dataUnavailable}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs"
                  onClick={() => void fetchAll()}
                >
                  {t.reflexPage.retryButton}
                </Button>
              </div>
            )}
          </div>
        )}
      </section>

      {/* Stat cards */}
      <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatCard
          icon={<TargetIcon className="size-4" />}
          label={t.reflexPage.statTry}
          value={stats?.try_count ?? "—"}
        />
        <StatCard
          icon={<ZapIcon className="size-4" />}
          label={t.reflexPage.statHit}
          value={stats?.hit_count ?? "—"}
          tone="good"
        />
        <StatCard
          icon={<BarChart3Icon className="size-4" />}
          label={t.reflexPage.statHitRate}
          value={stats ? `${(stats.hit_rate * 100).toFixed(1)}%` : "—"}
          tone="good"
        />
        <StatCard
          icon={<ActivityIcon className="size-4" />}
          label={t.reflexPage.statRules}
          value={stats ? rules.length : "—"}
        />
        <StatCard
          icon={<HourglassIcon className="size-4" />}
          label={t.reflexPage.statStale}
          value={stats?.coverage?.stale.length ?? "—"}
          tone={(stats?.coverage?.stale.length ?? 0) > 0 ? "warn" : undefined}
        />
        <StatCard
          icon={<ClockIcon className="size-4" />}
          label={t.reflexPage.statLastHourHits}
          value={series?.total_events ?? "—"}
        />
      </div>

      {/* Sparkline */}
      <Card className="workspace-panel border-white/40 shadow-none dark:border-white/10">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">
            {t.reflexPage.sparklineTitle}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {series ? (
            <Sparkline buckets={series.buckets ?? []} />
          ) : (
            <DataPlaceholder
              text={
                error
                  ? t.reflexPage.sparklineUnavailable
                  : t.reflexPage.dataLoading
              }
            />
          )}
        </CardContent>
      </Card>

      {/* RecipeForge · 7th reflection path · prompt evolution */}
      <RecipeForgePanel />

      {/* Variant A/B performance · auto-hides when no recipes
              have manifests (fresh deployments won't see it until
              the operator applies a variant for the first time). */}
      <VariantPerformancePanel />

      {/* Tiers (fuzzy_cache + slm) */}
      {tiers.length > 0 && (
        <Card className="workspace-panel border-white/40 shadow-none dark:border-white/10">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">
              {t.reflexPage.responseTiersTitle}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 md:grid-cols-2">
              {tiers.map((t) => (
                <TierCard key={t.name} tier={t} />
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Rules table */}
      <Card className="workspace-panel border-white/40 shadow-none dark:border-white/10">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">
            {t.reflexPage.rulesTableTitle}
          </CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-wide text-muted-foreground">
              <tr className="border-b border-border-default">
                <th className="pb-2 text-left font-medium">
                  {t.reflexPage.colRule}
                </th>
                <th className="pb-2 text-left font-medium">
                  {t.reflexPage.colKind}
                </th>
                <th className="pb-2 text-left font-medium">
                  {t.reflexPage.colPatternType}
                </th>
                <th className="pb-2 text-right font-medium">
                  {t.reflexPage.colPrio}
                </th>
                <th className="pb-2 text-right font-medium">
                  {t.reflexPage.colTries}
                </th>
                <th className="pb-2 text-right font-medium">
                  {t.reflexPage.colHits}
                </th>
                <th className="pb-2 text-right font-medium">
                  {t.reflexPage.colRate}
                </th>
                <th className="pb-2 text-right font-medium">
                  {t.reflexPage.colLast}
                </th>
              </tr>
            </thead>
            <tbody>
              {sortedRules.map((r) => {
                const s = stats?.by_rule[r.rule_id] ?? {
                  tries: 0,
                  hits: 0,
                  hit_rate: 0,
                };
                const pat =
                  r.pattern ||
                  r.intent_type ||
                  (r.kind === "cache"
                    ? r.ttl_seconds != null
                      ? `ttl=${r.ttl_seconds}s`
                      : "默认 TTL"
                    : "");
                return (
                  <RuleRow
                    key={r.rule_id}
                    rule={r}
                    stats={s}
                    pat={pat}
                    stale={staleSet.has(r.rule_id)}
                    unexercised={unexercisedSet.has(r.rule_id)}
                    lastHit={formatLastHit(r.last_hit_at, t)}
                  />
                );
              })}
              {sortedRules.length === 0 && (
                <tr>
                  <td
                    colSpan={8}
                    className="py-6 text-center text-xs text-muted-foreground"
                  >
                    {error && !stats
                      ? t.reflexPage.rulesUnavailable
                      : !stats
                        ? t.reflexPage.dataLoading
                        : t.reflexPage.noRulesLoaded}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}

export default function ReflexMonitorPage() {
  return (
    <WorkspaceContainer>
      <WorkspaceBody className="px-4 pb-4">
        <ReflexMonitorContent />
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}

function StatCard({
  icon,
  label,
  value,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
  tone?: "good" | "warn";
}) {
  return (
    <div className="rounded-lg border border-border-default bg-background/60 px-3 py-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {icon}
        {label}
      </div>
      <div
        className={cn(
          "mt-1 text-2xl font-semibold tabular-nums",
          tone === "good" && "text-success",
          tone === "warn" && "text-warning",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function DataPlaceholder({ text }: { text: string }) {
  return (
    <div className="flex h-[60px] w-full items-center justify-center rounded-md border border-dashed border-border-default text-xs text-muted-foreground">
      {text}
    </div>
  );
}

function Sparkline({ buckets }: { buckets: TimeseriesBucket[] }) {
  const { t } = useI18n();
  const ref = useRef<HTMLCanvasElement>(null);
  const totalHits = buckets.reduce((acc, b) => acc + (b?.count ?? 0), 0);
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
    if (!buckets.length || totalHits === 0) return;
    const max = Math.max(1, ...buckets.map((b) => b.count));
    const bw = w / buckets.length;
    ctx.fillStyle = "#34d399";
    for (let i = 0; i < buckets.length; i += 1) {
      const bucket = buckets[i];
      if (!bucket) continue;
      const bh = (bucket.count / max) * (h - 4);
      ctx.fillRect(i * bw + 1, h - bh, Math.max(1, bw - 2), bh);
    }
    ctx.fillStyle = "rgba(148, 163, 184, 0.25)";
    ctx.fillRect(0, h - 1, w, 1);
  }, [buckets, totalHits]);
  if (totalHits === 0) {
    return (
      <div className="flex h-[60px] w-full items-center justify-center rounded-md border border-dashed border-border-default text-xs text-muted-foreground">
        {t.reflexPage.sparklineEmpty}
      </div>
    );
  }
  return (
    <canvas
      ref={ref}
      className="h-[60px] w-full"
      // Width/height attributes are set imperatively in the effect
      // (devicePixelRatio handling). Tailwind's h-[60px] ensures the
      // CSS box stays stable.
    />
  );
}

function TierCard({ tier }: { tier: TierInfo }) {
  const { t } = useI18n();
  return (
    <div className="rounded-lg border border-border-default bg-background/60 px-4 py-3">
      <div className="flex items-center justify-between">
        <div className="font-medium">{tier.name}</div>
        <Badge
          variant={tier.enabled ? "default" : "outline"}
          className="text-xs"
        >
          {tier.enabled ? t.reflexPage.tierEnabled : t.reflexPage.tierDisabled}
        </Badge>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-muted-foreground">
        {tier.size !== undefined && (
          <span>
            {t.reflexPage.tierSize}:{" "}
            <span className="text-foreground">{tier.size}</span>
          </span>
        )}
        {tier.hits !== undefined && (
          <span>
            {t.reflexPage.tierHits}:{" "}
            <span className="text-success">{tier.hits}</span>{" "}
            <span className="text-muted-foreground">
              / {t.reflexPage.tierMisses} {tier.misses ?? 0}
            </span>
          </span>
        )}
        {tier.hit_rate !== undefined && (
          <span>
            {t.reflexPage.tierRate}:{" "}
            <span className="text-success">
              {(tier.hit_rate * 100).toFixed(0)}%
            </span>
          </span>
        )}
        {tier.endpoint && (
          <span className="col-span-2 truncate font-mono">
            {t.reflexPage.tierEndpoint}: {tier.endpoint}
          </span>
        )}
        {tier.similarity !== undefined && (
          <span>
            {t.reflexPage.tierSimilarity} {tier.similarity.toFixed(2)}
          </span>
        )}
      </div>
    </div>
  );
}

function RuleRow({
  rule,
  stats,
  pat,
  stale,
  unexercised,
  lastHit,
}: {
  rule: Rule;
  stats: { tries: number; hits: number; hit_rate: number };
  pat: string;
  stale: boolean;
  unexercised: boolean;
  lastHit: string;
}) {
  const { t } = useI18n();
  const ratePct = (stats.hit_rate * 100).toFixed(0);
  return (
    <tr className="border-b border-border-subtle align-top hover:bg-background/40">
      <td className="py-2 pr-3">
        <div className="flex flex-wrap items-center gap-1 font-mono">
          <span>{rule.rule_id}</span>
          {(rule.actions ?? []).map((a) => (
            <Badge key={a} className="bg-info/15 text-info hover:bg-info/15">
              {a}
            </Badge>
          ))}
          {rule.variants && rule.variants.length > 1 && (
            <Badge className="bg-warning/15 text-warning hover:bg-warning/15">
              {t.reflexPage.badgeAB(rule.variants.length)}
            </Badge>
          )}
          {rule.enabled_when && (
            <Badge className="bg-chart-1/15 text-chart-1 hover:bg-chart-1/15">
              {t.reflexPage.badgeGated}
            </Badge>
          )}
          {stale && (
            <Badge className="bg-destructive/15 text-destructive hover:bg-destructive/15">
              {t.reflexPage.badgeStale}
            </Badge>
          )}
          {unexercised && (
            <Badge variant="outline" className="text-xs">
              {t.reflexPage.badgeUnexercised}
            </Badge>
          )}
        </div>
        {(rule.variants?.length ?? 0) > 1 && (
          <div className="mt-1 space-y-0.5 pl-2 text-xs text-muted-foreground">
            {rule.variants!.map((v) => (
              <div key={v.variant_id} className="flex items-center gap-2">
                <span className="w-16 font-mono text-warning">
                  {v.variant_id}
                </span>
                <span className="flex-1 truncate">{v.preview}</span>
                <span className="text-success">
                  {v.hits}× (w={v.weight})
                </span>
              </div>
            ))}
            {rule.per_actor && (
              <div className="text-xs">
                {t.reflexPage.perActor}:{" "}
                {Object.entries(rule.per_actor)
                  .map(([a, vid]) => `${a}→${vid}`)
                  .join(", ")}
              </div>
            )}
          </div>
        )}
      </td>
      <td className="py-2 pr-3 font-mono text-xs text-chart-3">{rule.kind}</td>
      <td className="max-w-[280px] truncate py-2 pr-3 font-mono text-xs text-info">
        {pat}
      </td>
      <td className="py-2 pr-3 text-right font-mono">{rule.priority}</td>
      <td className="py-2 pr-3 text-right font-mono">{stats.tries}</td>
      <td className="py-2 pr-3 text-right font-mono text-success">
        {stats.hits}
      </td>
      <td
        className={cn(
          "py-2 pr-3 text-right font-mono",
          stats.hits > 0 ? "text-success" : "text-muted-foreground",
        )}
      >
        {ratePct}%
      </td>
      <td className="py-2 text-right font-mono text-xs text-muted-foreground">
        {lastHit}
      </td>
    </tr>
  );
}

import type { Translations } from "@/core/i18n/locales/types";

function formatLastHit(ts: number | undefined, t: Translations): string {
  if (!ts) return "—";
  const hours = (Date.now() / 1000 - ts) / 3600;
  if (hours < 1) return t.reflexPage.minutesAgo(Math.round(hours * 60));
  if (hours < 24) return t.reflexPage.hoursAgo(hours);
  return t.reflexPage.daysAgo(hours / 24);
}
