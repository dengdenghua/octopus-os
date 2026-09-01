import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ActivityIcon,
  CheckCircle2Icon,
  DnaIcon,
  GitCompareArrowsIcon,
  RefreshCwIcon,
  RocketIcon,
  RotateCcwIcon,
  ShieldCheckIcon,
  SparklesIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  coderUpstreamUpdateQueryKey,
  getCoderUpstreamUpdate,
} from "@/core/coder/api";
import {
  getAgentBenchmarkReport,
  getCodexGapReport,
  getDualHelixEvidence,
  getDualHelixShadowStatus,
  getEvolutionCandidates,
  registerCandidateCanary,
  rollbackEvolutionCandidate,
} from "@/core/evolution/api";
import type { EvolutionCandidateList } from "@/core/evolution/api";
import { useLedger } from "@/core/evolution/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

const helixQueryKey = ["evolution", "dual-helix"] as const;
const shadowQueryKey = [...helixQueryKey, "shadow"] as const;

function percent(value?: number) {
  return `${Math.round(Math.max(0, Math.min(1, value ?? 0)) * 100)}%`;
}

const ZH_ACTIONS: Record<string, string> = {
  "Add automatic repair-route promotion evidence for repeated verifier drift.":
    "为重复出现的验证偏移补充自动修复路线晋升证据。",
  "Add threat-model regression cases for every high-risk tool class.":
    "为每类高风险工具补齐威胁模型回归用例。",
  "Surface signed policy-review rule drafts in the operator panel.":
    "在运行监控中展示已签名的策略评审规则草案。",
};

function localizeAction(action: string, zh: boolean) {
  return zh ? (ZH_ACTIONS[action] ?? action) : action;
}

function localizeVerdict(verdict: string | undefined, zh: boolean) {
  if (!verdict) return "—";
  if (!zh) return verdict;
  return verdict === "differentiated" ? "已形成差异化" : verdict;
}

function formatLedgerDescription(description: string, zh: boolean) {
  const value = description.trim();
  const completed = value.match(/^turn_success\s*\|\s*goal=(.*)$/i);
  if (completed) {
    return zh
      ? `任务完成 · ${completed[1]}`
      : `Task completed · ${completed[1]}`;
  }

  const payloadStart = value.search(/:\s*[\[{]/);
  if (payloadStart >= 0) {
    const eventName = value.slice(0, payloadStart).trim();
    if (/(?:failed|error)/i.test(eventName)) {
      const label =
        eventName === "react_failed"
          ? zh
            ? "任务执行失败"
            : "Task execution failed"
          : eventName.replaceAll("_", " ");
      return zh
        ? `${label} · 内部错误详情已收起`
        : `${label} · internal error details hidden`;
    }
  }

  return value;
}

function ScoreBar({
  value,
  tone,
}: {
  value?: number;
  tone: "cyan" | "violet";
}) {
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-muted">
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-500",
          tone === "cyan" ? "bg-cyan-500" : "bg-violet-500",
        )}
        style={{ width: percent(value) }}
      />
    </div>
  );
}

function EngineCard({
  name,
  label,
  value,
  score,
  tone,
  detail,
}: {
  name: string;
  label: string;
  value: string;
  score?: number;
  tone: "cyan" | "violet";
  detail: string;
}) {
  return (
    <article
      className={cn(
        "border-l-2 bg-transparent px-4 py-3",
        tone === "cyan" ? "border-cyan-500/55" : "border-violet-500/55",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
            {label}
          </div>
          <h3 className="mt-1 text-sm font-semibold">{name}</h3>
        </div>
        <span
          className={cn(
            "font-mono text-[10px]",
            tone === "cyan"
              ? "text-cyan-600 dark:text-cyan-300"
              : "text-violet-600 dark:text-violet-300",
          )}
        >
          {value}
        </span>
      </div>
      <div className="mt-4 flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{detail}</span>
        <span className="font-mono font-semibold">
          {score === undefined ? "—" : percent(score)}
        </span>
      </div>
      <div className="mt-2">
        <ScoreBar value={score} tone={tone} />
      </div>
    </article>
  );
}

function EvidenceLoadAlert({
  onRetry,
  retrying,
}: {
  onRetry: () => void;
  retrying: boolean;
}) {
  return (
    <div
      role="alert"
      className="mt-4 flex flex-wrap items-center justify-between gap-3 border-y border-destructive/25 bg-destructive/5 px-3 py-2.5"
    >
      <div>
        <p className="text-xs font-medium text-destructive">
          部分进化证据暂时无法加载
        </p>
        <p className="mt-0.5 text-[10px] text-muted-foreground">
          缺失指标将显示为“—”，不会用零值或健康状态代替。
        </p>
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={retrying}
        onClick={onRetry}
      >
        <RefreshCwIcon
          className={cn("mr-1.5 size-3.5", retrying && "animate-spin")}
        />
        重试
      </Button>
    </div>
  );
}

function HelixBridge() {
  return (
    <div
      className="relative hidden min-h-40 items-center justify-center md:flex"
      aria-hidden="true"
    >
      <div className="absolute h-[82%] w-px -rotate-[16deg] bg-gradient-to-b from-cyan-500 via-foreground/25 to-violet-500" />
      <div className="absolute h-[82%] w-px rotate-[16deg] bg-gradient-to-b from-violet-500 via-foreground/25 to-cyan-500" />
      <div className="z-10 grid size-10 place-items-center bg-background">
        <DnaIcon className="size-5 text-primary" />
      </div>
    </div>
  );
}

type CandidateRow = EvolutionCandidateList["candidates"][number];

const STATUS_LABELS: Record<string, string> = {
  proposed: "待验证",
  validated: "已验证",
  shadow: "影子通过",
  canary: "灰度中",
  promoted: "已上线",
  rejected: "已拒绝",
  rolled_back: "已回滚",
};

const CANARY_PHASE_LABELS: Record<string, string> = {
  canary_5: "5%",
  canary_25: "25%",
  canary_50: "50%",
  full: "全量",
  rolled_back: "已停止",
};

function CandidateControlView({
  mode,
  rows,
  onCanary,
  onRollback,
  pending,
  showEmpty = true,
}: {
  mode: "candidates" | "deployments";
  rows: CandidateRow[];
  onCanary: (candidateId: string) => void;
  onRollback: (candidateId: string) => void;
  pending: boolean;
  showEmpty?: boolean;
}) {
  const visible =
    mode === "deployments"
      ? rows.filter((row) =>
          ["shadow", "canary", "promoted", "rolled_back"].includes(row.status),
        )
      : rows;
  return (
    <section aria-label={mode === "candidates" ? "候选基因" : "部署与回滚"}>
      <div className="flex flex-wrap items-end justify-between gap-3 border-b pb-3">
        <div>
          <h2 className="text-sm font-semibold">
            {mode === "candidates" ? "候选基因" : "部署与回滚"}
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            {mode === "candidates"
              ? "Prompt、Skill、路由、工作流和角色共用同一套证据门禁。"
              : "只展示已经通过影子验证、正在灰度或已经上线的候选。"}
          </p>
        </div>
        <div className="text-xs text-muted-foreground">
          共 {visible.length} 项 · 上线{" "}
          {rows.filter((r) => r.status === "promoted").length}· 回滚{" "}
          {rows.filter((r) => r.status === "rolled_back").length}
        </div>
      </div>

      <div className="divide-y divide-border-subtle">
        {visible.map((row) => (
          <div
            key={row.candidate_id}
            className="grid gap-2 py-3 text-xs md:grid-cols-[minmax(0,1.6fr)_120px_150px_auto] md:items-center"
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    "size-1.5 shrink-0 rounded-full",
                    row.status === "promoted"
                      ? "bg-success"
                      : row.status === "rolled_back" ||
                          row.status === "rejected"
                        ? "bg-destructive"
                        : row.status === "canary"
                          ? "bg-primary"
                          : "bg-muted-foreground/50",
                  )}
                />
                <span className="truncate font-medium">{row.scope}</span>
              </div>
              <div className="mt-1 truncate pl-3.5 text-[10px] text-muted-foreground">
                {row.candidate_id} · {row.proposer}
              </div>
            </div>
            <div className="text-muted-foreground">
              {row.gene_type.toUpperCase()}
            </div>
            <div>
              {STATUS_LABELS[row.status] ?? row.status}
              <span className="ml-1 text-[10px] text-muted-foreground">
                · {row.hard_gate_passed ? "门禁通过" : "证据未齐"}
              </span>
              {row.canary ? (
                <div className="mt-1 text-[10px] text-muted-foreground">
                  {CANARY_PHASE_LABELS[row.canary.phase] ?? row.canary.phase}
                  {row.canary.sample_count > 0
                    ? ` · ${row.canary.sample_count} 次 · ${percent(row.canary.current_rate)}`
                    : " · 等待真实任务"}
                </div>
              ) : null}
            </div>
            <div className="flex justify-end gap-1">
              {row.status === "shadow" && row.runtime_consumer_ready ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={pending}
                  onClick={() => onCanary(row.candidate_id)}
                >
                  <RocketIcon className="mr-1 size-3.5" />
                  进入灰度
                </Button>
              ) : null}
              {row.status === "shadow" && !row.runtime_consumer_ready ? (
                <span className="px-2 text-[10px] text-muted-foreground">
                  待接入运行时
                </span>
              ) : null}
              {row.status === "canary" || row.status === "promoted" ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={pending}
                  onClick={() => onRollback(row.candidate_id)}
                >
                  <RotateCcwIcon className="mr-1 size-3.5" />
                  回滚
                </Button>
              ) : null}
            </div>
          </div>
        ))}
        {!visible.length && showEmpty ? (
          <div className="py-16 text-center text-xs text-muted-foreground">
            {mode === "candidates"
              ? "还没有候选。GEPA、深度进化和自动 SkillForge 产生的改进会出现在这里。"
              : "还没有进入部署阶段的候选。"}
          </div>
        ) : null}
      </div>
    </section>
  );
}

export function DualHelixEvolutionPanel({
  view = "overview",
}: {
  view?: "overview" | "evidence" | "experiments" | "candidates" | "deployments";
}) {
  const queryClient = useQueryClient();
  const { locale } = useI18n();
  const zh = locale.toLowerCase().startsWith("zh");
  const gap = useQuery({
    queryKey: [...helixQueryKey, "gap"],
    queryFn: getCodexGapReport,
    staleTime: 60_000,
  });
  const benchmark = useQuery({
    queryKey: [...helixQueryKey, "benchmark"],
    queryFn: getAgentBenchmarkReport,
    staleTime: 60_000,
  });
  const paired = useQuery({
    queryKey: [...helixQueryKey, "paired-evidence"],
    queryFn: getDualHelixEvidence,
    staleTime: 30_000,
  });
  const shadow = useQuery({
    queryKey: shadowQueryKey,
    queryFn: getDualHelixShadowStatus,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
  const candidates = useQuery({
    queryKey: [...helixQueryKey, "candidates"],
    queryFn: getEvolutionCandidates,
    staleTime: 30_000,
    refetchInterval:
      view === "candidates" || view === "deployments" ? 10_000 : false,
  });
  const upstream = useQuery({
    queryKey: coderUpstreamUpdateQueryKey,
    queryFn: ({ signal }) => getCoderUpstreamUpdate(signal),
    staleTime: 60_000,
  });
  const ledger = useLedger({ limit: 8 });
  const refreshing =
    gap.isFetching ||
    benchmark.isFetching ||
    paired.isFetching ||
    shadow.isFetching ||
    candidates.isFetching ||
    upstream.isFetching ||
    ledger.isFetching;
  const refresh = () => {
    void Promise.all([
      gap.refetch(),
      benchmark.refetch(),
      paired.refetch(),
      shadow.refetch(),
      candidates.refetch(),
      upstream.refetch(),
      ledger.refetch(),
    ]);
  };
  const capabilities = gap.data?.capabilities ?? [];
  const gaps = capabilities
    .filter((item) => item.score < item.target_score)
    .sort((a, b) => a.score - b.score)
    .slice(0, 3);
  const nextActions = (gaps.length ? gaps : capabilities)
    .flatMap((item) => item.next_actions ?? [])
    .slice(0, 3);
  const error =
    gap.error ??
    benchmark.error ??
    paired.error ??
    shadow.error ??
    candidates.error ??
    upstream.error ??
    ledger.error;

  const candidateRollout = useMutation({
    mutationFn: registerCandidateCanary,
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: [...helixQueryKey, "candidates"],
      }),
  });
  const candidateRollback = useMutation({
    mutationFn: (candidateId: string) =>
      rollbackEvolutionCandidate(
        candidateId,
        "operator rollback from evolution panel",
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: [...helixQueryKey, "candidates"],
      }),
  });

  if (view === "candidates" || view === "deployments") {
    const hasInitialLoadError = Boolean(candidates.error) && !candidates.data;
    const hasRefreshError =
      Boolean(candidates.error) && Boolean(candidates.data);
    return (
      <>
        {candidates.isLoading ? (
          <div className="border-y py-16 text-center text-xs text-muted-foreground">
            正在读取候选谱系…
          </div>
        ) : (
          <CandidateControlView
            mode={view}
            rows={candidates.data?.candidates ?? []}
            pending={candidateRollout.isPending || candidateRollback.isPending}
            onCanary={(candidateId) => candidateRollout.mutate(candidateId)}
            onRollback={(candidateId) => {
              if (window.confirm("确认回滚这个候选并停止继续放量？")) {
                candidateRollback.mutate(candidateId);
              }
            }}
            showEmpty={!hasInitialLoadError}
          />
        )}
        {hasInitialLoadError ? (
          <div
            role="alert"
            className="border-y border-destructive/25 bg-destructive/5 px-4 py-10 text-center"
          >
            <p className="text-sm font-medium text-destructive">
              {view === "candidates"
                ? "候选谱系暂时无法加载"
                : "部署状态暂时无法加载"}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              请检查服务连接后重试；这不会修改现有候选或部署状态。
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-4"
              disabled={candidates.isFetching}
              onClick={() => void candidates.refetch()}
            >
              <RefreshCwIcon
                className={cn(
                  "mr-1.5 size-3.5",
                  candidates.isFetching && "animate-spin",
                )}
              />
              重试
            </Button>
          </div>
        ) : null}
        {hasRefreshError ? (
          <div
            role="alert"
            className="mt-3 flex flex-wrap items-center justify-between gap-2 border-y border-destructive/25 bg-destructive/5 px-3 py-2 text-xs"
          >
            <span className="text-destructive">
              刷新失败，当前显示的是上次成功加载的数据。
            </span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={candidates.isFetching}
              onClick={() => void candidates.refetch()}
            >
              重试
            </Button>
          </div>
        ) : null}
        {candidateRollout.error || candidateRollback.error ? (
          <p role="alert" className="mt-3 text-xs text-destructive">
            候选操作未完成，请稍后重试。
          </p>
        ) : null}
      </>
    );
  }

  if (view === "evidence" || view === "experiments") {
    const controlled = paired.data?.controlled;
    const pairs = controlled?.pairs ?? [];
    const runs = shadow.data?.runs ?? [];
    return (
      <section className="space-y-3" aria-label="实验证据">
        <div className="border-b pb-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="flex items-center gap-2 text-sm font-semibold">
                <GitCompareArrowsIcon className="size-4 text-primary" />
                双引擎实验证据
              </h2>
              <p className="mt-1 text-xs text-muted-foreground">
                只展示真实任务配对、隔离影子复核和可追溯的进化记录。
              </p>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={refresh}
              disabled={refreshing}
            >
              <RefreshCwIcon
                className={cn("mr-1.5 size-3.5", refreshing && "animate-spin")}
              />
              刷新证据
            </Button>
          </div>
          <div className="mt-4 grid border-y sm:grid-cols-2 xl:grid-cols-4">
            {[
              [
                "受控同题配对",
                paired.data ? (controlled?.paired_count ?? 0) : "—",
              ],
              [
                "Echo 胜出",
                paired.data ? (controlled?.echo_wins ?? 0) : "—",
              ],
              ["Codex 胜出", paired.data ? (controlled?.codex_wins ?? 0) : "—"],
              ["影子复核", shadow.data ? runs.length : "—"],
            ].map(([label, value]) => (
              <div
                key={String(label)}
                className="border-b px-3 py-2.5 last:border-b-0 sm:border-r xl:border-b-0"
              >
                <div className="text-[11px] text-muted-foreground">{label}</div>
                <div className="mt-1 font-mono text-lg font-semibold">
                  {value}
                </div>
              </div>
            ))}
          </div>
          {error ? (
            <EvidenceLoadAlert onRetry={refresh} retrying={refreshing} />
          ) : null}
        </div>

        <div className="grid gap-3 xl:grid-cols-2">
          <article className="border-t pt-4">
            <h3 className="text-sm font-semibold">同任务双引擎对照</h3>
            <div className="mt-3 divide-y divide-border-subtle">
              {pairs.length ? (
                pairs.map((pair) => (
                  <div key={pair.pair_key} className="px-1 py-2.5">
                    <div className="truncate text-xs font-medium">
                      {pair.goal}
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
                      <span>{pair.case_id}</span>
                      <span>· 第 {pair.trial_index + 1} 次</span>
                      <span className="ml-auto">
                        胜出：{pair.winner === "tie" ? "平局" : pair.winner}
                      </span>
                    </div>
                  </div>
                ))
              ) : (
                <div className="border-y border-dashed px-3 py-8 text-center text-xs text-muted-foreground">
                  {paired.error && !paired.data
                    ? "受控实验数据暂时无法加载。"
                    : "暂无受控同题实验。普通回合和影子复核不会冒充受控配对。"}
                </div>
              )}
            </div>
          </article>

          <article className="border-t pt-4">
            <h3 className="text-sm font-semibold">影子复核记录</h3>
            <div className="mt-3 divide-y divide-border-subtle">
              {runs.length ? (
                runs.map((run) => (
                  <div key={run.run_id} className="px-1 py-2.5">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-xs font-medium">
                          {run.goal}
                        </div>
                        <div className="mt-1 text-[10px] text-muted-foreground">
                          {run.primary_engine} → {run.shadow_engine} ·
                          只读隔离副本
                        </div>
                      </div>
                      <span
                        className={cn(
                          "shrink-0 rounded-full px-2 py-0.5 text-[10px]",
                          run.status === "completed"
                            ? "bg-success/10 text-success"
                            : run.status === "failed"
                              ? "bg-destructive/10 text-destructive"
                              : "bg-primary/10 text-primary",
                        )}
                      >
                        {run.status}
                      </span>
                    </div>
                    {run.result || run.error ? (
                      <p className="mt-2 line-clamp-2 text-[11px] leading-5 text-muted-foreground">
                        {run.result || run.error}
                      </p>
                    ) : null}
                  </div>
                ))
              ) : (
                <div className="border-y border-dashed px-3 py-8 text-center text-xs text-muted-foreground">
                  {shadow.error && !shadow.data
                    ? "影子复核数据暂时无法加载。"
                    : "暂无影子复核记录。"}
                </div>
              )}
            </div>
          </article>
        </div>

        <article className="border-t pt-4">
          <h3 className="text-sm font-semibold">进化账本</h3>
          <div className="mt-3 divide-y divide-border-subtle">
            {(ledger.data?.records ?? []).map((record) => (
              <div
                key={record.id}
                className="flex items-center gap-3 py-2 text-xs"
              >
                <span className="size-1.5 shrink-0 rounded-full bg-primary" />
                <span className="min-w-0 flex-1 truncate">
                  {formatLedgerDescription(record.description, zh)}
                </span>
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  {record.status}
                </span>
              </div>
            ))}
            {!ledger.data?.records?.length ? (
              <div className="py-6 text-center text-xs text-muted-foreground">
                {ledger.error ? "进化账本暂时无法加载。" : "暂无进化账本记录。"}
              </div>
            ) : null}
          </div>
        </article>
      </section>
    );
  }

  return (
    <section
      className="space-y-3"
      aria-label={zh ? "双螺旋进化" : "Dual-helix evolution"}
    >
      <div className="border-b pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="grid size-8 place-items-center text-primary">
                <DnaIcon className="size-4" />
              </span>
              <div>
                <h2 className="text-sm font-semibold">
                  {zh ? "双引擎螺旋进化" : "Dual-engine helix evolution"}
                </h2>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {zh
                    ? "Codex 提供能力基线，Echo 沉淀可验证的行为基因。"
                    : "Codex supplies the capability baseline; Echo promotes verified behavior genes."}
                </p>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  shadow.data?.enabled
                    ? "bg-success"
                    : "bg-muted-foreground/40",
                )}
              />
              {shadow.data
                ? shadow.data.enabled
                  ? "保护模式已开启"
                  : "保护模式已关闭"
                : "保护状态暂不可用"}
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={refresh}
              disabled={refreshing}
            >
              <RefreshCwIcon
                className={cn("mr-1.5 size-3.5", refreshing && "animate-spin")}
              />
              {zh ? "刷新进化证据" : "Refresh evidence"}
            </Button>
          </div>
        </div>

        {error ? (
          <EvidenceLoadAlert onRetry={refresh} retrying={refreshing} />
        ) : null}

        <div className="mt-4 grid items-stretch gap-3 md:grid-cols-[1fr_80px_1fr]">
          <EngineCard
            name="Echo Native"
            label={zh ? "行为基因链" : "Behavior gene strand"}
            value={localizeVerdict(gap.data?.verdict, zh)}
            score={gap.data?.advantage_score}
            tone="cyan"
            detail={zh ? "差异化能力" : "Differentiated capability"}
          />
          <HelixBridge />
          <EngineCard
            name="OpenAI Codex"
            label={zh ? "能力基准链" : "Capability baseline strand"}
            value={`v${upstream.data?.current_version ?? "—"}`}
            score={gap.data?.parity_score}
            tone="violet"
            detail={zh ? "能力对齐度" : "Capability parity"}
          />
        </div>
      </div>

      <div className="grid border-y sm:grid-cols-2 xl:grid-cols-4">
        {[
          [
            ShieldCheckIcon,
            zh ? "已验证能力" : "Verified capabilities",
            benchmark.data
              ? `${benchmark.data.passed}/${benchmark.data.total}`
              : "—",
          ],
          [
            DnaIcon,
            zh ? "受控同题配对" : "Controlled task pairs",
            paired.data
              ? String(paired.data.controlled?.paired_count ?? 0)
              : "—",
          ],
          [
            ActivityIcon,
            zh ? "待验证候选" : "Pending candidates",
            candidates.data
              ? String(
                  (candidates.data.by_status?.proposed ?? 0) +
                    (candidates.data.by_status?.validated ?? 0) +
                    (candidates.data.by_status?.shadow ?? 0),
                )
              : "—",
          ],
          [
            SparklesIcon,
            zh ? "当前状态" : "Current status",
            benchmark.data
              ? benchmark.data.passed === benchmark.data.total
                ? zh
                  ? "稳定"
                  : "Stable"
                : zh
                  ? "观察中"
                  : "Watching"
              : "—",
          ],
        ].map(([Icon, label, value]) => {
          const MetricIcon = Icon as typeof ActivityIcon;
          return (
            <div
              key={String(label)}
              className="border-b px-3 py-3 sm:border-r xl:border-b-0"
            >
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <MetricIcon className="size-3.5" />
                {String(label)}
              </div>
              <div className="mt-2 font-mono text-lg font-semibold">
                {String(value)}
              </div>
            </div>
          );
        })}
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <article className="border-t pt-4">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <GitCompareArrowsIcon className="size-4 text-primary" />
            {zh ? "能力对照产生的下一代候选" : "Next-generation candidates"}
          </h3>
          <div className="mt-3 divide-y divide-border-subtle">
            {nextActions.length ? (
              nextActions.map((action, index) => (
                <div
                  key={`${action}-${index}`}
                  className="flex gap-2 px-1 py-2 text-xs"
                >
                  <span className="mt-0.5 grid size-4 shrink-0 place-items-center rounded-full bg-primary/10 font-mono text-[9px] text-primary">
                    {index + 1}
                  </span>
                  <span className="leading-5">
                    {localizeAction(action, zh)}
                  </span>
                </div>
              ))
            ) : (
              <p
                className={cn(
                  "border-l-2 px-3 py-2 text-xs",
                  gap.data
                    ? "border-success text-success"
                    : "border-muted-foreground/40 text-muted-foreground",
                )}
              >
                {gap.data
                  ? zh
                    ? "当前能力基线没有未达标项，继续从真实任务中采集差异。"
                    : "No baseline gaps; continue collecting differences from live tasks."
                  : zh
                    ? "能力基线暂时无法加载，恢复后将继续生成候选。"
                    : "The capability baseline is temporarily unavailable."}
              </p>
            )}
          </div>
        </article>

        <article className="border-t pt-4">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <ActivityIcon className="size-4 text-primary" />
            {zh ? "最近进化证据" : "Recent evolution evidence"}
          </h3>
          <div className="mt-3 space-y-2">
            {(ledger.data?.records ?? []).slice(0, 4).map((record) => {
              const codex = /codex/i.test(
                `${record.description} ${record.proposer}`,
              );
              return (
                <div key={record.id} className="flex items-start gap-2 text-xs">
                  <span
                    className={cn(
                      "mt-1.5 size-1.5 shrink-0 rounded-full",
                      codex ? "bg-violet-500" : "bg-cyan-500",
                    )}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate">
                      {formatLedgerDescription(record.description, zh)}
                    </div>
                    <div className="mt-0.5 text-[10px] text-muted-foreground">
                      {codex ? "Codex" : "Echo"} ·{" "}
                      {zh && record.status === "proposed"
                        ? "候选"
                        : record.status}
                    </div>
                  </div>
                </div>
              );
            })}
            {!ledger.data?.records?.length ? (
              <p className="text-xs text-muted-foreground">
                {ledger.error
                  ? zh
                    ? "进化账本暂时无法加载。"
                    : "The evolution ledger is temporarily unavailable."
                  : zh
                    ? "暂无任务证据。"
                    : "No task evidence yet."}
              </p>
            ) : null}
          </div>
        </article>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t px-1 pt-3 text-[10px] text-muted-foreground">
        {[
          zh ? "观察" : "Observe",
          zh ? "双引擎对照" : "Compare",
          zh ? "生成候选" : "Forge",
          zh ? "影子验证" : "Shadow",
          zh ? "灰度晋升" : "Promote",
        ].map((step, index) => (
          <div key={step} className="flex items-center gap-2">
            {index > 0 ? <span aria-hidden>→</span> : null}
            <span className="flex items-center gap-1.5">
              {index === 4 ? (
                <CheckCircle2Icon className="size-3 text-success" />
              ) : (
                <span className="size-1.5 rounded-full bg-primary/70" />
              )}
              {step}
            </span>
          </div>
        ))}
        <span className="ml-auto">
          {paired.data
            ? paired.data.controlled?.paired_count
              ? zh
                ? `${paired.data.controlled.paired_count} 对受控任务已完成同题实验 · `
                : `${paired.data.controlled.paired_count} controlled task pairs completed · `
              : zh
                ? `等待受控同题实验（另有 ${paired.data.paired_count ?? 0} 对观察性记录） · `
                : `Awaiting controlled experiments (${paired.data.paired_count ?? 0} observational pairs) · `
            : zh
              ? "受控实验数据暂不可用 · "
              : "Controlled experiment data unavailable · "}
          {upstream.data
            ? upstream.data.update_available
              ? zh
                ? `Codex v${upstream.data.latest_version} 待审核`
                : `Codex v${upstream.data.latest_version} awaiting review`
              : zh
                ? "Codex 上游已同步"
                : "Codex upstream synced"
            : zh
              ? "Codex 上游状态暂不可用"
              : "Codex upstream status unavailable"}
        </span>
      </div>

      <p className="px-1 text-[10px] text-muted-foreground">
        {shadow.data?.enabled
          ? zh
            ? "影子模式已授权：仅在明确提交影子任务时运行，使用隔离快照和只读权限。"
            : "Shadow mode is authorized only for explicitly submitted reviews, using isolated snapshots and read-only permissions."
          : zh
            ? "影子模式默认关闭；开启开关本身不会调用模型或产生费用。"
            : "Shadow mode is off by default; enabling it alone does not call a model or incur cost."}
      </p>
    </section>
  );
}
