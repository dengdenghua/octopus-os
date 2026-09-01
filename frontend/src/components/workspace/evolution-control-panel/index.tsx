/**
 * Evolution control panel — companion to EvolutionDashboard.
 *
 * The dashboard shows "what the agent learned". This panel surfaces
 * the seven new backend control surfaces added in the recent evolution
 * overhaul:
 *
 *  1. Budget + circuit breakers   (GET /api/evolution/budget/snapshot)
 *  2. Skill proposals from intel  (GET /api/intel-evolution/skills/proposals)
 *  3. Model proposals             (GET /api/intel-evolution/models/proposals)
 *  4. MCP proposals               (GET /api/intel-evolution/mcp/proposals)
 *  5. Curriculum goals            (GET /api/evolution/curriculum/goals)
 *  6. Framework benchmarks        (GET /api/intel-evolution/frameworks/benchmarks)
 *  7. Protocol drift + repair     (GET /api/intel-evolution/protocols/drift,
 *                                  GET /api/intel-evolution/protocols/repair/proposals)
 *  plus the A/B dispatch snapshot (GET /api/evolution/dispatch/snapshot)
 *
 * All UI is intentionally functional rather than polished — the
 * existing EvolutionDashboard is the primary narrative surface; this
 * panel is the operator console.
 */

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  CircleDashedIcon,
  FlaskConicalIcon,
  GaugeIcon,
  Loader2Icon,
  PackagePlusIcon,
  RefreshCwIcon,
  ShieldAlertIcon,
  SparklesIcon,
  XCircleIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { swallow } from "@/core/utils/log";
import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { RoutedWebLink } from "@/components/ui/routed-web-link";
import { cn } from "@/lib/utils";

function numberOrZero(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function fixed(value: unknown, digits: number): string {
  return numberOrZero(value).toFixed(digits);
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${getBackendBaseURL()}${path}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${getBackendBaseURL()}${path}`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Types (minimal — match the pydantic schemas)
// ---------------------------------------------------------------------------

interface BudgetComponent {
  name: string;
  budget: {
    max_calls_per_hour: number;
    max_calls_per_day: number;
    breaker_failure_threshold: number;
    breaker_cooldown_seconds: number;
  };
  usage: {
    hourly_used: number;
    hourly_limit: number;
    hourly_remaining: number;
    daily_used: number;
    daily_limit: number;
    daily_remaining: number;
  };
  breaker: {
    component: string;
    state: "closed" | "open" | "half_open";
    consecutive_failures: number;
    opened_at: string | null;
  };
  last_24h: {
    success: number;
    failure: number;
    rejected_budget: number;
    rejected_breaker: number;
  };
  cost?: {
    hourly_tokens: number;
    daily_tokens: number;
    hourly_usd: number;
    daily_usd: number;
  };
  last_reset_at?: string | null;
}

interface BudgetSnapshot {
  components: BudgetComponent[];
  source?: string;
  events?: number;
}

interface SkillProposal {
  id?: number;
  name: string;
  created_at: string;
  topic?: string | null;
  source_url?: string | null;
  status: string;
}

interface ModelProposal {
  id?: number;
  model_label: string;
  created_at: string;
  status: string;
  benchmark_notes?: string | null;
}

interface McpProposal {
  id?: number;
  server_name: string;
  created_at: string;
  suggested_cmd?: string | null;
  description?: string | null;
  status: string;
}

interface CurriculumGoal {
  id: number;
  cluster_key: string;
  category: string;
  title: string;
  description: string;
  keywords: string[] | string;
  failure_count: number;
  priority: number;
  status: string;
  covered_by?: string | null;
}

interface BenchmarkRow {
  id: number;
  strategy_a: string;
  strategy_b: string;
  base_model: string;
  a_wins: number;
  b_wins: number;
  ties: number;
  total_tasks: number;
  win_rate_b: number;
  decision: string;
}

interface DriftEvent {
  id: number;
  protocol_id: string;
  detected_at: string;
  summary: string;
  acknowledged: boolean;
}

interface RepairProposal {
  id: number;
  drift_event_id: number;
  protocol_id: string;
  created_at: string;
  suggested_diff: string;
  rationale: string;
  status: string;
  source?: string;
  repair_tasks?: RepairTask[];
}

interface RepairTask {
  id: number;
  proposal_id: number;
  protocol_id: string;
  priority: string;
  title: string;
  target_layer: string;
  target_modules: string[];
  verification_commands: string[];
}

type DispatchSnapshot = Record<
  string,
  {
    skill_name: string;
    a_assigned: number;
    b_assigned: number;
    a_reported: number;
    b_reported: number;
    outcomes: Record<string, number>;
  }
>;

// ---------------------------------------------------------------------------
// Shared UI atoms
// ---------------------------------------------------------------------------

function Pill({
  label,
  tone = "muted",
}: {
  label: string;
  tone?: "muted" | "ok" | "warn" | "bad" | "info";
}) {
  const tones: Record<string, string> = {
    muted: "bg-muted text-muted-foreground",
    ok: "bg-success/10 text-success dark:bg-success/15 dark:text-success",
    warn: "bg-warning/10 text-warning dark:bg-warning/15 dark:text-warning",
    bad: "bg-destructive/10 text-destructive dark:bg-destructive/15 dark:text-destructive",
    info: "bg-info/15 text-info",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-lg px-2 py-0.5 text-xs font-medium uppercase tracking-wider",
        tones[tone],
      )}
    >
      {label}
    </span>
  );
}

function Bar({
  pct,
  tone,
  label,
}: {
  pct: number;
  tone: "ok" | "warn" | "bad";
  label: string;
}) {
  const color =
    tone === "bad"
      ? "bg-destructive"
      : tone === "warn"
        ? "bg-warning"
        : "bg-success";
  return (
    <div
      className="h-1.5 w-full rounded-lg bg-muted"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.min(100, Math.max(0, pct))}
    >
      <div
        className={cn("h-full rounded-lg transition-all", color)}
        style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
      />
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div
      className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border-default px-6 py-10 text-center"
      role="status"
    >
      <CircleDashedIcon className="size-5 text-muted-foreground" />
      <p className="text-sm text-muted-foreground">{text}</p>
    </div>
  );
}

function LoadingRow() {
  const { t } = useI18n();
  return (
    <div
      className="flex items-center gap-2 py-3 text-sm text-muted-foreground"
      role="status"
      aria-live="polite"
    >
      <Loader2Icon className="size-3.5 animate-spin" />
      {t.evolutionControl.loadingText}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section: Budget + breakers
// ---------------------------------------------------------------------------

function BudgetSection() {
  const { t } = useI18n();
  const [snap, setSnap] = useState<BudgetSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<BudgetSnapshot>(
        "/api/evolution/budget/snapshot",
      );
      setSnap(data);
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const resetBreaker = useCallback(
    async (component: string) => {
      try {
        await apiPost("/api/evolution/budget/breaker/reset", { component });
        load();
      } catch (e) {
        swallow(e);
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [load],
  );

  return (
    <Card
      title={t.evolutionControl.budget.cardTitle}
      icon={<GaugeIcon className="size-4 text-primary" />}
      onRefresh={load}
      loading={loading}
      error={error}
      headerRight={
        snap ? (
          <span className="rounded-md bg-muted px-2 py-1 font-mono text-xs text-muted-foreground">
            {t.evolutionControl.budget.source(
              snap.source ?? "unknown",
              snap.events ?? 0,
            )}
          </span>
        ) : null
      }
    >
      {!snap && loading && <LoadingRow />}
      {snap && snap.components.length === 0 && (
        <EmptyState text={t.evolutionControl.budget.empty} />
      )}
      <div className="flex flex-col divide-y divide-border/60">
        {snap?.components.map((c) => {
          const hourly = c.usage.hourly_used;
          const hourlyLimit = c.usage.hourly_limit;
          const hourlyPct = hourlyLimit
            ? Math.round((hourly / hourlyLimit) * 100)
            : 0;
          const tone: "ok" | "warn" | "bad" =
            hourlyPct >= 90 ? "bad" : hourlyPct >= 60 ? "warn" : "ok";
          const breakerTone: "ok" | "warn" | "bad" =
            c.breaker.state === "open"
              ? "bad"
              : c.breaker.state === "half_open"
                ? "warn"
                : "ok";
          const breakerStateLabel =
            c.breaker.state === "closed"
              ? t.evolutionControl.budget.breakerStates.closed
              : c.breaker.state === "open"
                ? t.evolutionControl.budget.breakerStates.open
                : t.evolutionControl.budget.breakerStates.halfOpen;
          const dailyUsd = c.cost?.daily_usd ?? 0;
          const dailyTokens = c.cost?.daily_tokens ?? 0;
          return (
            <div
              key={c.name}
              className="flex flex-col gap-2 py-3 md:flex-row md:items-center md:justify-between"
            >
              <div className="flex min-w-[160px] flex-col">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs">{c.name}</span>
                  <Pill
                    label={breakerStateLabel}
                    tone={
                      breakerTone === "bad"
                        ? "bad"
                        : breakerTone === "warn"
                          ? "warn"
                          : "ok"
                    }
                  />
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {t.evolutionControl.budget.consecutiveFailures(
                    c.breaker.consecutive_failures,
                    c.budget.max_calls_per_hour,
                  )}
                </div>
              </div>
              <div className="flex min-w-[220px] flex-1 flex-col gap-1">
                <div className="flex justify-between text-xs text-muted-foreground">
                  <span>{t.evolutionControl.budget.perHour}</span>
                  <span className="tabular-nums">
                    {hourly}/{hourlyLimit}
                  </span>
                </div>
                <Bar
                  pct={hourlyPct}
                  tone={tone}
                  label={t.evolutionControl.budget.hourlyUsageAria(
                    c.name,
                    hourly,
                    hourlyLimit,
                  )}
                />
              </div>
              <div className="flex min-w-[180px] flex-col gap-0.5 text-xs text-muted-foreground">
                <span>
                  {t.evolutionControl.budget.last24h(
                    c.last_24h.success,
                    c.last_24h.failure,
                  )}
                </span>
                <span>
                  {t.evolutionControl.budget.rejected(
                    c.last_24h.rejected_budget,
                    c.last_24h.rejected_breaker,
                  )}
                </span>
                <span>
                  {t.evolutionControl.budget.dailyUsage(
                    c.usage.daily_used,
                    c.usage.daily_limit,
                  )}
                </span>
                <span>
                  {t.evolutionControl.budget.cost(dailyTokens, dailyUsd)}
                </span>
                {c.last_reset_at && (
                  <span>
                    {t.evolutionControl.budget.lastReset(c.last_reset_at)}
                  </span>
                )}
              </div>
              {c.breaker.state !== "closed" && (
                <button
                  type="button"
                  onClick={() => resetBreaker(c.name)}
                  className="rounded-lg border border-border-default px-2 py-1 text-xs hover:bg-muted"
                >
                  {t.evolutionControl.budget.resetButton}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section: Skill proposals (intel)
// ---------------------------------------------------------------------------

function SkillProposalsSection() {
  const { t } = useI18n();
  const [rows, setRows] = useState<SkillProposal[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<SkillProposal[]>(
        "/api/intel-evolution/skills/proposals?status=pending",
      );
      setRows(data);
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const decide = useCallback(
    async (name: string, approve: boolean) => {
      setBusy(name);
      try {
        await apiPost(
          `/api/intel-evolution/skills/proposals/${approve ? "approve" : "reject"}`,
          { name, reason: approve ? "approved" : "rejected via UI" },
        );
        await load();
      } catch (e) {
        swallow(e);
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  return (
    <Card
      title={t.evolutionControl.skillProposals.cardTitle}
      icon={<SparklesIcon className="size-4 text-primary" />}
      onRefresh={load}
      loading={loading}
      error={error}
    >
      {!rows.length && !loading && (
        <EmptyState text={t.evolutionControl.skillProposals.empty} />
      )}
      <div className="divide-y divide-border/60">
        {rows.map((p) => (
          <div
            key={p.name}
            className="flex flex-col gap-2 py-3 md:flex-row md:items-center md:justify-between"
          >
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs">{p.name}</span>
                {p.topic && <Pill label={p.topic} tone="info" />}
              </div>
              {p.source_url && (
                <RoutedWebLink
                  href={p.source_url}
                  openTargetSource="evolution-source"
                  className="text-xs text-primary underline underline-offset-2"
                >
                  {p.source_url}
                </RoutedWebLink>
              )}
              <span className="text-xs text-muted-foreground">
                {p.created_at}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={busy === p.name}
                onClick={() => decide(p.name, true)}
                className="rounded-lg bg-success/10 px-3 py-1 text-xs text-success hover:bg-success/20 disabled:opacity-50 dark:text-success"
              >
                {t.evolutionControl.skillProposals.approve}
              </button>
              <button
                type="button"
                disabled={busy === p.name}
                onClick={() => decide(p.name, false)}
                className="rounded-lg bg-destructive/10 px-3 py-1 text-xs text-destructive hover:bg-destructive/20 disabled:opacity-50 dark:text-destructive"
              >
                {t.evolutionControl.skillProposals.reject}
              </button>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section: Model proposals
// ---------------------------------------------------------------------------

function ModelProposalsSection() {
  const { t } = useI18n();
  const [rows, setRows] = useState<ModelProposal[]>([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<ModelProposal[]>(
        "/api/intel-evolution/models/proposals",
      );
      setRows(data);
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const runBenchmarks = useCallback(async () => {
    setRunning(true);
    try {
      await apiPost("/api/intel-evolution/models/benchmarks/run", {});
      await load();
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }, [load]);

  return (
    <Card
      title={t.evolutionControl.models.cardTitle}
      icon={<FlaskConicalIcon className="size-4 text-primary" />}
      onRefresh={load}
      loading={loading}
      error={error}
      headerRight={
        <button
          type="button"
          disabled={running}
          onClick={runBenchmarks}
          className="rounded-lg border border-border-default px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
        >
          {running
            ? t.evolutionControl.models.runningBenchmarks
            : t.evolutionControl.models.runBenchmarks}
        </button>
      }
    >
      {!rows.length && !loading && (
        <EmptyState text={t.evolutionControl.models.empty} />
      )}
      <div className="divide-y divide-border/60">
        {rows.map((p) => (
          <div key={p.model_label} className="py-2">
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs">{p.model_label}</span>
              <Pill
                label={p.status}
                tone={
                  p.status === "promoted"
                    ? "ok"
                    : p.status === "rejected"
                      ? "bad"
                      : "muted"
                }
              />
            </div>
            {p.benchmark_notes && (
              <details className="mt-1">
                <summary className="cursor-pointer text-xs text-muted-foreground">
                  {t.evolutionControl.models.benchmarkNotes}
                </summary>
                <pre className="mt-1 max-h-48 overflow-auto rounded-lg bg-muted p-2 text-xs">
                  {p.benchmark_notes}
                </pre>
              </details>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section: MCP proposals
// ---------------------------------------------------------------------------

function McpProposalsSection() {
  const { t } = useI18n();
  const [rows, setRows] = useState<McpProposal[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<McpProposal[]>(
        "/api/intel-evolution/mcp/proposals",
      );
      setRows(data);
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const vetAll = useCallback(async () => {
    try {
      await apiPost("/api/intel-evolution/mcp/proposals/vet", {});
      await load();
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [load]);

  const install = useCallback(
    async (server_name: string) => {
      setBusy(server_name);
      try {
        await apiPost("/api/intel-evolution/mcp/proposals/install", {
          server_name,
        });
        await load();
      } catch (e) {
        swallow(e);
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  return (
    <Card
      title={t.evolutionControl.mcp.cardTitle}
      icon={<PackagePlusIcon className="size-4 text-primary" />}
      onRefresh={load}
      loading={loading}
      error={error}
      headerRight={
        <button
          type="button"
          onClick={vetAll}
          className="rounded-lg border border-border-default px-2 py-1 text-xs hover:bg-muted"
        >
          {t.evolutionControl.mcp.vetAll}
        </button>
      }
    >
      {!rows.length && !loading && (
        <EmptyState text={t.evolutionControl.mcp.empty} />
      )}
      <div className="divide-y divide-border/60">
        {rows.map((p) => (
          <div
            key={p.server_name}
            className="flex items-start justify-between py-3"
          >
            <div className="flex flex-col gap-1">
              <span className="font-mono text-xs">{p.server_name}</span>
              {p.description && (
                <span className="text-xs text-muted-foreground">
                  {p.description}
                </span>
              )}
              {p.suggested_cmd && (
                <code className="rounded-lg bg-muted px-1 py-0.5 text-xs">
                  {p.suggested_cmd}
                </code>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Pill
                label={p.status}
                tone={
                  p.status === "installed"
                    ? "ok"
                    : p.status === "rejected"
                      ? "bad"
                      : p.status === "vetted"
                        ? "info"
                        : "muted"
                }
              />
              {p.status === "vetted" && (
                <button
                  type="button"
                  disabled={busy === p.server_name}
                  onClick={() => install(p.server_name)}
                  className="rounded-lg bg-primary/10 px-2 py-1 text-xs text-primary hover:bg-primary/20 disabled:opacity-50"
                >
                  {t.evolutionControl.mcp.installDisabled}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section: Curriculum goals
// ---------------------------------------------------------------------------

function CurriculumSection() {
  const { t } = useI18n();
  const [rows, setRows] = useState<CurriculumGoal[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<CurriculumGoal[]>(
        "/api/evolution/curriculum/goals?status=pending",
      );
      setRows(data);
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const runCycle = useCallback(async () => {
    try {
      await apiPost("/api/evolution/curriculum/cycle/run", {});
      await load();
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [load]);

  const decide = useCallback(
    async (id: number, status: string, covered_by?: string) => {
      try {
        await apiPost("/api/evolution/curriculum/goals/decide", {
          goal_id: id,
          status,
          covered_by,
        });
        await load();
      } catch (e) {
        swallow(e);
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [load],
  );

  return (
    <Card
      title={t.evolutionControl.curriculum.cardTitle}
      icon={<SparklesIcon className="size-4 text-primary" />}
      onRefresh={load}
      loading={loading}
      error={error}
      headerRight={
        <button
          type="button"
          onClick={runCycle}
          className="rounded-lg border border-border-default px-2 py-1 text-xs hover:bg-muted"
        >
          {t.evolutionControl.curriculum.runCycle}
        </button>
      }
    >
      {!rows.length && !loading && (
        <EmptyState text={t.evolutionControl.curriculum.empty} />
      )}
      <div className="divide-y divide-border/60">
        {rows.map((g) => (
          <div key={g.id} className="flex flex-col gap-1 py-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">{g.title}</span>
              <div className="flex items-center gap-2">
                <Pill label={g.category} tone="info" />
                <span className="text-xs text-muted-foreground tabular-nums">
                  p={fixed(g.priority, 1)} · n={numberOrZero(g.failure_count)}
                </span>
              </div>
            </div>
            <span className="text-xs text-muted-foreground">
              {g.description}
            </span>
            <div className="mt-1 flex items-center gap-2">
              <button
                type="button"
                onClick={() => decide(g.id, "in_progress")}
                className="rounded-lg border border-border-default px-2 py-0.5 text-xs hover:bg-muted"
              >
                {t.evolutionControl.curriculum.start}
              </button>
              <button
                type="button"
                onClick={() => decide(g.id, "dismissed")}
                className="rounded-lg border border-border-default px-2 py-0.5 text-xs hover:bg-muted"
              >
                {t.evolutionControl.curriculum.dismiss}
              </button>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section: Framework benchmarks
// ---------------------------------------------------------------------------

function FrameworkBenchmarksSection() {
  const { t } = useI18n();
  const [rows, setRows] = useState<BenchmarkRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<BenchmarkRow[]>(
        "/api/intel-evolution/frameworks/benchmarks",
      );
      setRows(data);
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <Card
      title={t.evolutionControl.frameworks.cardTitle}
      icon={<FlaskConicalIcon className="size-4 text-primary" />}
      onRefresh={load}
      loading={loading}
      error={error}
    >
      {!rows.length && !loading && (
        <EmptyState text={t.evolutionControl.frameworks.empty} />
      )}
      <div className="divide-y divide-border/60">
        {rows.map((b) => (
          <div
            key={b.id}
            className="flex flex-col gap-1 py-3 md:flex-row md:items-center md:justify-between"
          >
            <div>
              <div className="font-mono text-xs">
                #{b.id} {b.strategy_a} vs {b.strategy_b}
              </div>
              <div className="text-xs text-muted-foreground">
                {t.evolutionControl.frameworks.baseModelPrefix}
                {b.base_model}
              </div>
            </div>
            <div className="flex items-center gap-4 text-xs tabular-nums text-muted-foreground">
              <span>A: {b.a_wins}</span>
              <span>B: {b.b_wins}</span>
              <span>
                {t.evolutionControl.frameworks.tiesPrefix}
                {b.ties}
              </span>
              <span>
                {t.evolutionControl.frameworks.bWinRatePrefix}
                {fixed(numberOrZero(b.win_rate_b) * 100, 0)}%
              </span>
              <Pill
                label={b.decision}
                tone={
                  b.decision === "prefer_b"
                    ? "ok"
                    : b.decision === "prefer_a"
                      ? "info"
                      : b.decision === "rejected_budget"
                        ? "bad"
                        : "muted"
                }
              />
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section: Protocol drift + repair
// ---------------------------------------------------------------------------

function ProtocolDriftSection() {
  const { t } = useI18n();
  const [events, setEvents] = useState<DriftEvent[]>([]);
  const [repairs, setRepairs] = useState<RepairProposal[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ev, rp] = await Promise.all([
        apiGet<DriftEvent[]>(
          "/api/intel-evolution/protocols/drift?acknowledged=false",
        ),
        apiGet<RepairProposal[]>(
          "/api/intel-evolution/protocols/repair/proposals?status=pending",
        ),
      ]);
      setEvents(ev);
      setRepairs(rp);
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const scan = useCallback(async () => {
    setBusy(true);
    try {
      await apiPost("/api/intel-evolution/protocols/drift/scan", {});
      await load();
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [load]);

  const sweep = useCallback(async () => {
    setBusy(true);
    try {
      await apiPost("/api/intel-evolution/protocols/repair/sweep", {});
      await load();
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [load]);

  const acknowledge = useCallback(
    async (id: number) => {
      try {
        await apiPost(
          `/api/intel-evolution/protocols/drift/${id}/acknowledge`,
          {},
        );
        await load();
      } catch (e) {
        swallow(e);
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [load],
  );

  return (
    <Card
      title={t.evolutionControl.drift.cardTitle}
      icon={<ShieldAlertIcon className="size-4 text-primary" />}
      onRefresh={load}
      loading={loading}
      error={error}
      headerRight={
        <div className="flex gap-1">
          <button
            type="button"
            onClick={scan}
            disabled={busy}
            className="rounded-lg border border-border-default px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
          >
            {t.evolutionControl.drift.scanButton}
          </button>
          <button
            type="button"
            onClick={sweep}
            disabled={busy}
            className="rounded-lg border border-border-default px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
          >
            {t.evolutionControl.drift.sweepButton}
          </button>
        </div>
      }
    >
      <div className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {t.evolutionControl.drift.eventsHeader}
      </div>
      {!events.length && !loading && (
        <EmptyState text={t.evolutionControl.drift.emptyEvents} />
      )}
      <div className="divide-y divide-border/60">
        {events.map((ev) => (
          <div
            key={ev.id}
            className="flex items-start justify-between gap-2 py-2"
          >
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <AlertTriangleIcon className="size-3.5 text-warning" />
                <span className="font-mono text-xs">{ev.protocol_id}</span>
                <span className="text-xs text-muted-foreground">
                  {ev.detected_at}
                </span>
              </div>
              <div className="mt-0.5 text-xs text-muted-foreground">
                {ev.summary}
              </div>
            </div>
            <button
              type="button"
              onClick={() => acknowledge(ev.id)}
              className="rounded-lg border border-border-default px-2 py-1 text-xs hover:bg-muted"
            >
              {t.evolutionControl.drift.acknowledgeButton}
            </button>
          </div>
        ))}
      </div>

      <div className="mt-4 mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {t.evolutionControl.drift.repairsHeader}
      </div>
      {!repairs.length && !loading && (
        <EmptyState text={t.evolutionControl.drift.emptyRepairs} />
      )}
      <div className="divide-y divide-border/60">
        {repairs.map((r) => (
          <div key={r.id} className="flex flex-col gap-1 py-2">
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs">{r.protocol_id}</span>
              <span className="text-xs text-muted-foreground">
                {t.evolutionControl.drift.eventPrefix(r.drift_event_id)}
              </span>
            </div>
            <div className="text-xs text-muted-foreground">{r.rationale}</div>
            {r.repair_tasks?.length ? (
              <div className="mt-1 rounded-md border border-border-default bg-muted/40 px-2 py-1.5">
                {r.repair_tasks.slice(0, 2).map((task) => (
                  <div key={task.id} className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span className="font-medium text-foreground">
                        {task.title}
                      </span>
                      <span className="rounded border border-border-default px-1.5 py-0.5 font-mono text-muted-foreground">
                        {task.priority}
                      </span>
                      <span className="text-muted-foreground">
                        {task.target_layer}
                      </span>
                    </div>
                    <div className="truncate font-mono text-xs text-muted-foreground">
                      {task.target_modules.slice(0, 3).join(" · ")}
                    </div>
                  </div>
                ))}
              </div>
            ) : null}
            <details className="mt-1">
              <summary className="cursor-pointer text-xs text-primary">
                {t.evolutionControl.drift.diffSummary}
              </summary>
              <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-muted p-2 text-xs">
                {r.suggested_diff}
              </pre>
            </details>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section: A/B dispatch snapshot
// ---------------------------------------------------------------------------

function DispatchSection() {
  const { t } = useI18n();
  const [snap, setSnap] = useState<DispatchSnapshot>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<DispatchSnapshot>(
        "/api/evolution/dispatch/snapshot",
      );
      setSnap(data || {});
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const rows = useMemo(() => Object.entries(snap), [snap]);

  return (
    <Card
      title={t.evolutionControl.dispatch.cardTitle}
      icon={<FlaskConicalIcon className="size-4 text-primary" />}
      onRefresh={load}
      loading={loading}
      error={error}
    >
      {rows.length === 0 && !loading && (
        <EmptyState text={t.evolutionControl.dispatch.empty} />
      )}
      <div className="divide-y divide-border/60">
        {rows.map(([test_id, b]) => (
          <div key={test_id} className="py-3">
            <div className="flex items-center justify-between">
              <div>
                <div className="font-mono text-xs">{b.skill_name}</div>
                <div className="text-xs text-muted-foreground">
                  {t.evolutionControl.dispatch.testPrefix(test_id.slice(0, 8))}
                </div>
              </div>
              <div className="flex gap-4 text-xs tabular-nums text-muted-foreground">
                <span>
                  A: {b.a_assigned} ({b.a_reported})
                </span>
                <span>
                  B: {b.b_assigned} ({b.b_reported})
                </span>
              </div>
            </div>
            {Object.keys(b.outcomes).length > 0 && (
              <div className="mt-1 flex gap-2 text-xs text-muted-foreground">
                {Object.entries(b.outcomes).map(([k, v]) => (
                  <span key={k}>
                    {k}: {v}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Shared Card container
// ---------------------------------------------------------------------------

function Card({
  title,
  icon,
  children,
  onRefresh,
  loading,
  error,
  headerRight,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  onRefresh?: () => void;
  loading?: boolean;
  error?: string | null;
  headerRight?: React.ReactNode;
}) {
  const { t } = useI18n();
  const ariaLabelRefresh = t.evolutionControl.refreshAriaLabel;
  return (
    <section className="workspace-panel flex flex-col gap-3 rounded-lg px-5 py-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium">
          {icon}
          {title}
        </div>
        <div className="flex items-center gap-2">
          {headerRight}
          {onRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              className="rounded-lg p-1 hover:bg-muted"
              aria-label={ariaLabelRefresh}
            >
              <RefreshCwIcon
                className={cn(
                  "size-3.5 text-muted-foreground",
                  loading && "animate-spin",
                )}
              />
            </button>
          )}
        </div>
      </div>
      {error && (
        <div className="flex items-center gap-2 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <XCircleIcon className="size-3.5" />
          {error}
        </div>
      )}
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

type SectionKey =
  | "budget"
  | "skill_proposals"
  | "models"
  | "mcp"
  | "curriculum"
  | "frameworks"
  | "drift"
  | "dispatch";

const SECTION_ORDER: SectionKey[] = [
  "budget",
  "skill_proposals",
  "models",
  "mcp",
  "curriculum",
  "frameworks",
  "drift",
  "dispatch",
];

export function EvolutionControlPanel() {
  const { t } = useI18n();
  const [active, setActive] = useState<SectionKey>("budget");

  const sectionLabels: Record<SectionKey, string> = {
    budget: t.evolutionControl.sections.budget,
    skill_proposals: t.evolutionControl.sections.skillProposals,
    models: t.evolutionControl.sections.models,
    mcp: t.evolutionControl.sections.mcp,
    curriculum: t.evolutionControl.sections.curriculum,
    frameworks: t.evolutionControl.sections.frameworks,
    drift: t.evolutionControl.sections.drift,
    dispatch: t.evolutionControl.sections.dispatch,
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 border-b border-border-default pb-2 text-xs">
        <div className="flex items-center gap-1.5 font-semibold uppercase tracking-wider text-muted-foreground">
          <CheckCircle2Icon className="size-3.5" />
          {t.evolutionControl.panelTitle}
        </div>
        <div
          className="ml-4 flex flex-wrap gap-1"
          role="group"
          aria-label={t.evolutionControl.panelTitle}
        >
          {SECTION_ORDER.map((k) => (
            <button
              type="button"
              key={k}
              onClick={() => setActive(k)}
              aria-pressed={active === k}
              className={cn(
                "rounded-lg px-3 py-1 text-xs transition-colors",
                active === k
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-muted",
              )}
            >
              {sectionLabels[k]}
            </button>
          ))}
        </div>
      </div>

      {active === "budget" && <BudgetSection />}
      {active === "skill_proposals" && <SkillProposalsSection />}
      {active === "models" && <ModelProposalsSection />}
      {active === "mcp" && <McpProposalsSection />}
      {active === "curriculum" && <CurriculumSection />}
      {active === "frameworks" && <FrameworkBenchmarksSection />}
      {active === "drift" && <ProtocolDriftSection />}
      {active === "dispatch" && <DispatchSection />}
    </div>
  );
}

export default EvolutionControlPanel;
