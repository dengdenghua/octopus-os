import {
  AlertTriangleIcon,
  BookOpenCheckIcon,
  BoxesIcon,
  CheckCircle2Icon,
  CircleDashedIcon,
  GitPullRequestArrowIcon,
  Link2Icon,
  LoaderCircleIcon,
  NetworkIcon,
  PackageSearchIcon,
  PlayIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
  SparklesIcon,
  UsersIcon,
  VoteIcon,
  XIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import type {
  NarrativeChapter,
  NarrativeContextPack,
  NarrativeExtensions,
  NarrativePipelineRun,
  NarrativeProject,
  NarrativeReviewRequest,
  NarrativeScene,
  NarrativeStateChange,
  NarrativeWorldPack,
  ReviewVoteInput,
} from "./api";
import {
  contextBudgetUsage,
  mergePipelineStages,
  reviewReadiness,
} from "./story-model";

export type InspectorTab = "world" | "continuity" | "pipeline" | "canon";

export interface NarrativeAgentCandidate {
  runId: string;
  stageId: string;
  output: string;
  model: string | null;
  inputTokens: number | null;
  outputTokens: number | null;
  totalTokens: number | null;
  promptChars: number;
  maxPromptChars: number;
  promptTruncated: boolean;
  omittedContextSources: number;
  omittedUpstreamStages: number;
  generatedAt: string;
}

export interface NarrativeAgentMessage {
  runId: string;
  stageId: string;
  kind: "error" | "cancelled";
  message: string;
}

const EMPTY_EXTENSIONS: NarrativeExtensions = {
  arcs: [],
  entities: [],
  relationships: [],
  foreshadows: [],
  contextPacks: [],
  pipelineRuns: [],
  reviewRequests: [],
  canonCommits: [],
  warnings: [],
};

function SectionTitle({
  children,
  count,
}: {
  children: string;
  count?: number;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
        {children}
      </h3>
      {typeof count === "number" ? (
        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] tabular-nums text-muted-foreground">
          {count}
        </span>
      ) : null}
    </div>
  );
}

function PanelEmpty({ children }: { children: string }) {
  return (
    <p className="rounded-xl border border-dashed border-border/70 px-3 py-5 text-center text-xs leading-5 text-muted-foreground">
      {children}
    </p>
  );
}

function StageStatus({ status }: { status: string }) {
  const completed = status === "completed" || status === "submitted";
  const failed = status === "failed" || status === "blocked";
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[10px] font-medium",
        completed && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
        failed && "bg-red-500/10 text-red-700 dark:text-red-300",
        !completed && !failed && "bg-muted text-muted-foreground",
      )}
    >
      {status === "submitted"
        ? "已提交"
        : status === "completed"
          ? "已完成"
          : status === "running"
            ? "运行中"
            : status === "ready"
              ? "可提交"
              : status === "blocked"
                ? "被阻塞"
                : status === "failed"
                  ? "失败"
                  : status === "skipped"
                    ? "已跳过"
                    : "待处理"}
    </span>
  );
}

export function ContextPackPreview({
  pack,
  loading,
  onBuild,
}: {
  pack: NarrativeContextPack | null;
  loading: boolean;
  onBuild: (tokenBudget: number) => void;
}) {
  const [budget, setBudget] = useState(12_000);
  const usage = contextBudgetUsage(pack);
  return (
    <div className="border-b border-border/60 bg-muted/15 px-4 py-3 md:px-6">
      <div className="flex flex-wrap items-center gap-2">
        <div className="mr-auto">
          <p className="flex items-center gap-2 text-xs font-medium">
            <PackageSearchIcon className="size-3.5 text-violet-500" />
            章节上下文包
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            只引用可追溯的世界资料、事实与连续性状态
          </p>
        </div>
        <label className="flex items-center gap-2 text-[11px] text-muted-foreground">
          Token 预算
          <Input
            aria-label="上下文 Token 预算"
            type="number"
            min={1000}
            max={100000}
            step={1000}
            value={budget}
            onChange={(event) => setBudget(Number(event.target.value) || 1000)}
            className="h-8 w-24 bg-background text-xs"
          />
        </label>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onBuild(budget)}
          disabled={loading}
        >
          {loading ? (
            <LoaderCircleIcon className="size-3.5 animate-spin" />
          ) : (
            <SparklesIcon className="size-3.5" />
          )}
          {pack ? "重新构建" : "构建上下文"}
        </Button>
      </div>
      {pack ? (
        <div className="mt-3 rounded-xl border border-border/60 bg-background/60 p-3">
          <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
            <span className="font-medium text-foreground">
              {usage.used.toLocaleString()} / {usage.budget.toLocaleString()}{" "}
              tokens
            </span>
            <span>
              {pack.sources.filter((source) => source.included).length} 个来源
            </span>
            {pack.omitted_count ? <span>省略 {pack.omitted_count}</span> : null}
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className={cn(
                "h-full rounded-full transition-[width]",
                usage.overBudget ? "bg-red-500" : "bg-violet-500",
              )}
              style={{ width: `${usage.percentage}%` }}
            />
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {pack.sources.slice(0, 6).map((source, index) => (
              <div
                key={`${source.id || source.reference}-${index}`}
                className={cn(
                  "min-w-0 rounded-lg bg-muted/40 p-2 text-[11px]",
                  !source.included && "opacity-55",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium">{source.title}</span>
                  <span className="shrink-0 text-muted-foreground">
                    {source.tokens} t
                  </span>
                </div>
                <p className="mt-1 truncate text-muted-foreground">
                  {source.kind} · {source.reference || "内部引用"}
                  {source.truncated ? " · 已截断" : ""}
                </p>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

interface StudioInspectorProps {
  tab: InspectorTab;
  onTabChange: (tab: InspectorTab) => void;
  project: NarrativeProject;
  worldPacks: NarrativeWorldPack[];
  stateChanges: NarrativeStateChange[];
  selectedChapter: NarrativeChapter | null;
  selectedScene: NarrativeScene | null;
  extensions: NarrativeExtensions | null;
  activeContextPack: NarrativeContextPack | null;
  loading: boolean;
  actionKey: string;
  agentActionKey: string;
  agentCandidate: NarrativeAgentCandidate | null;
  agentMessage: NarrativeAgentMessage | null;
  onRetry: () => void;
  onImportWorldPack: () => void;
  onCreatePipeline: () => void;
  onSubmitStage: (run: NarrativePipelineRun, stageId: string) => void;
  onRunAgentStage: (run: NarrativePipelineRun, stageId: string) => void;
  onCancelAgentStage: () => void;
  onSubmitAgentCandidate: (candidate: NarrativeAgentCandidate) => void;
  onCreateReview: () => void;
  onVote: (
    review: NarrativeReviewRequest,
    decision: ReviewVoteInput["decision"],
  ) => void;
  onRequestCommit: (review: NarrativeReviewRequest) => void;
}

export function StudioInspector({
  tab,
  onTabChange,
  project,
  worldPacks,
  stateChanges,
  selectedChapter,
  selectedScene,
  extensions: rawExtensions,
  activeContextPack,
  loading,
  actionKey,
  agentActionKey,
  agentCandidate,
  agentMessage,
  onRetry,
  onImportWorldPack,
  onCreatePipeline,
  onSubmitStage,
  onRunAgentStage,
  onCancelAgentStage,
  onSubmitAgentCandidate,
  onCreateReview,
  onVote,
  onRequestCommit,
}: StudioInspectorProps) {
  const extensions = rawExtensions ?? EMPTY_EXTENSIONS;
  const selectedTargetId = selectedScene?.id || selectedChapter?.id || "";
  const relatedRuns = useMemo(
    () =>
      extensions.pipelineRuns.filter(
        (run) =>
          (!selectedChapter || run.chapter_id === selectedChapter.id) &&
          (!selectedScene ||
            !run.scene_id ||
            run.scene_id === selectedScene.id),
      ),
    [extensions.pipelineRuns, selectedChapter, selectedScene],
  );
  const relatedReviews = extensions.reviewRequests.filter(
    (review) => !selectedTargetId || review.target_id === selectedTargetId,
  );
  const tabs: Array<{ id: InspectorTab; label: string }> = [
    { id: "world", label: "世界" },
    { id: "continuity", label: "连续性" },
    { id: "pipeline", label: "流水线" },
    { id: "canon", label: "正典" },
  ];

  return (
    <>
      <div className="grid grid-cols-4 border-b border-border/60 p-1.5">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => onTabChange(item.id)}
            className={cn(
              "rounded-lg px-1 py-2 text-[11px] font-medium text-muted-foreground transition-colors",
              tab === item.id && "bg-muted text-foreground",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        {loading && !rawExtensions ? (
          <div className="grid min-h-52 place-items-center">
            <LoaderCircleIcon className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <>
            {extensions.warnings.length ? (
              <div className="mb-3 rounded-xl border border-amber-400/20 bg-amber-500/8 p-3 text-[11px] leading-5 text-amber-800 dark:text-amber-200">
                <div className="flex items-center gap-2 font-medium">
                  <AlertTriangleIcon className="size-3.5" />
                  部分增强数据暂不可用
                </div>
                <p className="mt-1 line-clamp-2 text-muted-foreground">
                  {extensions.warnings.join("；")}
                </p>
                <Button
                  variant="ghost"
                  size="sm"
                  className="mt-1 h-7"
                  onClick={onRetry}
                >
                  <RefreshCwIcon className="size-3" />
                  重试
                </Button>
              </div>
            ) : null}

            {tab === "world" ? (
              <div className="space-y-5">
                <div className="grid grid-cols-2 gap-2">
                  <div className="rounded-xl bg-muted/35 p-3">
                    <BoxesIcon className="size-4 text-violet-500" />
                    <p className="mt-2 text-xl font-semibold">
                      {worldPacks.length}
                    </p>
                    <p className="text-[11px] text-muted-foreground">世界包</p>
                  </div>
                  <div className="rounded-xl bg-muted/35 p-3">
                    <NetworkIcon className="size-4 text-cyan-500" />
                    <p className="mt-2 text-xl font-semibold">
                      {extensions.entities.length}
                    </p>
                    <p className="text-[11px] text-muted-foreground">实体</p>
                  </div>
                </div>
                <div className="space-y-2">
                  <SectionTitle count={worldPacks.length}>世界包</SectionTitle>
                  {worldPacks.map((pack) => (
                    <div
                      key={pack.id}
                      className="rounded-xl border border-border/60 p-3"
                    >
                      <p className="text-sm font-medium">{pack.name}</p>
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">
                        {pack.summary || "可追溯的世界资料集合"}
                      </p>
                      <p className="mt-2 text-[11px] text-muted-foreground">
                        {pack.resources.length} 份资料
                      </p>
                    </div>
                  ))}
                  {!worldPacks.length ? (
                    <PanelEmpty>
                      尚无世界包，可从通用格式创建或导入首个示例世界包。
                    </PanelEmpty>
                  ) : null}
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full"
                    onClick={onImportWorldPack}
                  >
                    <SparklesIcon className="size-3.5" />
                    导入 ECHO 世界包
                  </Button>
                </div>
                <div className="space-y-2">
                  <SectionTitle count={extensions.arcs.length}>
                    故事弧
                  </SectionTitle>
                  {extensions.arcs.slice(0, 6).map((arc) => (
                    <div
                      key={arc.id}
                      className="rounded-lg bg-muted/35 p-2.5 text-xs"
                    >
                      <p className="font-medium">{arc.title}</p>
                      <p className="mt-1 line-clamp-2 text-muted-foreground">
                        {arc.summary || arc.status}
                      </p>
                    </div>
                  ))}
                  {!extensions.arcs.length ? (
                    <PanelEmpty>尚未定义故事弧。</PanelEmpty>
                  ) : null}
                </div>
                <div className="space-y-2">
                  <SectionTitle count={extensions.entities.length}>
                    实体
                  </SectionTitle>
                  <div className="grid gap-2">
                    {extensions.entities.slice(0, 10).map((entity) => (
                      <div
                        key={entity.id}
                        className="flex items-start gap-2 rounded-lg bg-muted/35 p-2.5"
                      >
                        <UsersIcon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                        <div className="min-w-0">
                          <p className="truncate text-xs font-medium">
                            {entity.name}
                          </p>
                          <p className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground">
                            {entity.kind} · {entity.description || "暂无描述"}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                  {!extensions.entities.length ? (
                    <PanelEmpty>尚无结构化实体。</PanelEmpty>
                  ) : null}
                </div>
                <div className="space-y-2">
                  <SectionTitle count={extensions.relationships.length}>
                    关系
                  </SectionTitle>
                  {extensions.relationships.slice(0, 8).map((relationship) => (
                    <div
                      key={relationship.id}
                      className="rounded-lg border border-border/55 p-2.5 text-[11px]"
                    >
                      <p className="flex items-center gap-1.5 font-medium">
                        <Link2Icon className="size-3" />
                        {relationship.source_entity_id} →{" "}
                        {relationship.target_entity_id}
                      </p>
                      <p className="mt-1 text-muted-foreground">
                        {relationship.kind}
                      </p>
                    </div>
                  ))}
                  {!extensions.relationships.length ? (
                    <PanelEmpty>尚无实体关系。</PanelEmpty>
                  ) : null}
                </div>
                <div className="space-y-2">
                  <SectionTitle count={extensions.foreshadows.length}>
                    伏笔
                  </SectionTitle>
                  {extensions.foreshadows.slice(0, 8).map((item) => (
                    <div
                      key={item.id}
                      className="rounded-lg border border-border/55 p-2.5 text-xs"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium">{item.title}</span>
                        <span className="text-[10px] text-muted-foreground">
                          {item.status}
                        </span>
                      </div>
                      <p className="mt-1 line-clamp-2 text-[11px] text-muted-foreground">
                        {item.setup}
                      </p>
                    </div>
                  ))}
                  {!extensions.foreshadows.length ? (
                    <PanelEmpty>尚无登记伏笔。</PanelEmpty>
                  ) : null}
                </div>
              </div>
            ) : null}

            {tab === "continuity" ? (
              <div className="space-y-5">
                <div className="rounded-xl border border-violet-400/20 bg-violet-500/8 p-3">
                  <p className="flex items-center gap-2 text-sm font-medium">
                    <PackageSearchIcon className="size-4" />
                    当前上下文包
                  </p>
                  {activeContextPack ? (
                    <>
                      <p className="mt-2 text-xs text-muted-foreground">
                        {activeContextPack.sources.length} 个来源 ·{" "}
                        {activeContextPack.token_count.toLocaleString()} /{" "}
                        {activeContextPack.token_budget.toLocaleString()} tokens
                      </p>
                      <div className="mt-3 space-y-2">
                        {activeContextPack.sources
                          .slice(0, 10)
                          .map((source, index) => (
                            <div
                              key={`${source.id}-${index}`}
                              className="rounded-lg bg-background/60 p-2 text-[11px]"
                            >
                              <div className="flex justify-between gap-2">
                                <span className="truncate font-medium">
                                  {source.title}
                                </span>
                                <span className="shrink-0 text-muted-foreground">
                                  {source.tokens} t
                                </span>
                              </div>
                              <p className="mt-1 truncate text-muted-foreground">
                                {source.reference || source.kind}
                              </p>
                            </div>
                          ))}
                      </div>
                    </>
                  ) : (
                    <p className="mt-2 text-xs leading-5 text-muted-foreground">
                      在章节编辑器中构建后，这里会显示完整来源与预算。
                    </p>
                  )}
                </div>
                <div className="space-y-2">
                  <SectionTitle count={stateChanges.length}>
                    状态变化
                  </SectionTitle>
                  {stateChanges.map((change) => (
                    <div
                      key={change.id}
                      className="rounded-xl border border-border/60 p-3 text-xs"
                    >
                      <p className="font-medium">
                        {change.entity_id} · {change.field}
                      </p>
                      <div className="mt-2 grid grid-cols-[1fr_auto_1fr] gap-2 rounded-lg bg-muted/35 p-2 text-[11px]">
                        <span className="break-words">
                          {String(change.before ?? "—")}
                        </span>
                        <span className="text-muted-foreground">→</span>
                        <span className="break-words">
                          {String(change.after ?? "—")}
                        </span>
                      </div>
                      {change.reason ? (
                        <p className="mt-2 text-[11px] text-muted-foreground">
                          {change.reason}
                        </p>
                      ) : null}
                    </div>
                  ))}
                  {!stateChanges.length ? (
                    <PanelEmpty>尚无候选状态变化。</PanelEmpty>
                  ) : null}
                </div>
              </div>
            ) : null}

            {tab === "pipeline" ? (
              <div className="space-y-4">
                <div className="rounded-xl border border-border/60 p-3">
                  <p className="text-sm font-medium">六阶段创作流水线</p>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">
                    大纲 → 初稿 → 连续性 → 文风 → 修订 →
                    编辑审阅。每个阶段必须提交真实产物才会推进。
                  </p>
                  <Button
                    size="sm"
                    className="mt-3 w-full"
                    onClick={onCreatePipeline}
                    disabled={
                      !selectedChapter || actionKey === "pipeline:create"
                    }
                  >
                    {actionKey === "pipeline:create" ? (
                      <LoaderCircleIcon className="size-3.5 animate-spin" />
                    ) : (
                      <PlayIcon className="size-3.5" />
                    )}
                    创建流水线
                  </Button>
                </div>
                {relatedRuns.map((run) => {
                  const stages = mergePipelineStages(run.stages);
                  const actionable = stages.find(
                    (stage) =>
                      !["completed", "submitted", "skipped"].includes(
                        stage.status,
                      ),
                  );
                  return (
                    <div
                      key={run.id}
                      className="rounded-xl border border-border/60 p-3"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <p className="truncate text-xs font-medium">
                          Run {run.id.slice(0, 8)}
                        </p>
                        <StageStatus status={run.status} />
                      </div>
                      <div className="mt-3 space-y-2">
                        {stages.map((stage, index) => {
                          const isActionable =
                            actionable?.id === stage.id &&
                            !["blocked", "failed"].includes(stage.status);
                          const stageActionKey = `stage:${run.id}:${stage.id}`;
                          const agentStageActionKey = `agent:${run.id}:${stage.id}`;
                          const stageCandidate =
                            agentCandidate?.runId === run.id &&
                            agentCandidate.stageId === stage.id
                              ? agentCandidate
                              : null;
                          const stageAgentMessage =
                            agentMessage?.runId === run.id &&
                            agentMessage.stageId === stage.id
                              ? agentMessage
                              : null;
                          const agentRunning =
                            agentActionKey === agentStageActionKey;
                          return (
                            <div
                              key={stage.id}
                              className="rounded-lg bg-muted/35 p-2.5"
                            >
                              <div className="flex items-center gap-2">
                                {stage.status === "completed" ||
                                stage.status === "submitted" ? (
                                  <CheckCircle2Icon className="size-3.5 text-emerald-500" />
                                ) : (
                                  <CircleDashedIcon className="size-3.5 text-muted-foreground" />
                                )}
                                <span className="mr-auto text-xs font-medium">
                                  {index + 1}. {stage.name}
                                </span>
                                <StageStatus status={stage.status} />
                              </div>
                              {stage.error ? (
                                <p className="mt-2 text-[11px] text-red-600">
                                  {stage.error}
                                </p>
                              ) : null}
                              {isActionable ? (
                                <div className="mt-2 space-y-2">
                                  <div className="grid grid-cols-2 gap-2">
                                    {agentRunning ? (
                                      <Button
                                        variant="outline"
                                        size="sm"
                                        className="h-8 text-[11px]"
                                        onClick={onCancelAgentStage}
                                      >
                                        <XIcon className="size-3" />
                                        取消 AI 运行
                                      </Button>
                                    ) : (
                                      <Button
                                        variant="outline"
                                        size="sm"
                                        className="h-8 text-[11px]"
                                        onClick={() =>
                                          onRunAgentStage(run, stage.id)
                                        }
                                        disabled={
                                          Boolean(actionKey) ||
                                          Boolean(agentActionKey) ||
                                          !activeContextPack
                                        }
                                      >
                                        <SparklesIcon className="size-3" />
                                        AI 运行当前阶段
                                      </Button>
                                    )}
                                    <Button
                                      variant="outline"
                                      size="sm"
                                      className="h-8 text-[11px]"
                                      onClick={() =>
                                        onSubmitStage(run, stage.id)
                                      }
                                      disabled={
                                        Boolean(actionKey) ||
                                        Boolean(agentActionKey)
                                      }
                                    >
                                      {actionKey === stageActionKey ? (
                                        <LoaderCircleIcon className="size-3 animate-spin" />
                                      ) : (
                                        <GitPullRequestArrowIcon className="size-3" />
                                      )}
                                      提交本阶段产物
                                    </Button>
                                  </div>
                                  {!activeContextPack ? (
                                    <p className="text-[10px] leading-4 text-amber-700 dark:text-amber-300">
                                      先构建章节上下文包，才能安全运行 AI 阶段。
                                    </p>
                                  ) : null}
                                  {agentRunning ? (
                                    <div
                                      className="flex items-center gap-2 rounded-lg border border-violet-400/20 bg-violet-500/8 p-2 text-[11px] text-violet-700 dark:text-violet-200"
                                      role="status"
                                    >
                                      <LoaderCircleIcon className="size-3 animate-spin" />
                                      AI 正在生成候选产物，尚未提交……
                                    </div>
                                  ) : null}
                                  {stageAgentMessage ? (
                                    <div
                                      className={cn(
                                        "rounded-lg border p-2 text-[11px] leading-5",
                                        stageAgentMessage.kind === "cancelled"
                                          ? "border-amber-400/25 bg-amber-500/8 text-amber-800 dark:text-amber-200"
                                          : "border-red-400/25 bg-red-500/8 text-red-700 dark:text-red-200",
                                      )}
                                      role="alert"
                                    >
                                      {stageAgentMessage.message}
                                    </div>
                                  ) : null}
                                  {stageCandidate ? (
                                    <div className="rounded-xl border border-cyan-400/25 bg-cyan-500/8 p-3">
                                      <div className="flex items-center justify-between gap-2">
                                        <p className="flex items-center gap-1.5 text-xs font-medium">
                                          <SparklesIcon className="size-3.5 text-cyan-600 dark:text-cyan-300" />
                                          AI 候选预览
                                        </p>
                                        <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] text-amber-700 dark:text-amber-300">
                                          尚未提交
                                        </span>
                                      </div>
                                      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border/50 bg-background/70 p-2.5 font-sans text-[11px] leading-5 text-foreground">
                                        {stageCandidate.output}
                                      </pre>
                                      <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px] text-muted-foreground">
                                        <span>
                                          模型：
                                          {stageCandidate.model || "未返回"}
                                        </span>
                                        <span>
                                          Token：
                                          {stageCandidate.totalTokens ??
                                            `${stageCandidate.inputTokens ?? "?"} + ${stageCandidate.outputTokens ?? "?"}`}
                                        </span>
                                        <span>
                                          提示词：
                                          {stageCandidate.promptChars.toLocaleString()}{" "}
                                          /{" "}
                                          {stageCandidate.maxPromptChars.toLocaleString()}{" "}
                                          字符
                                        </span>
                                        <span
                                          className={cn(
                                            stageCandidate.promptTruncated &&
                                              "font-medium text-amber-700 dark:text-amber-300",
                                          )}
                                        >
                                          截断审计：
                                          {stageCandidate.promptTruncated
                                            ? "已触发"
                                            : "未触发"}
                                        </span>
                                        <span>
                                          省略来源：
                                          {stageCandidate.omittedContextSources}
                                        </span>
                                        <span>
                                          省略上游：
                                          {stageCandidate.omittedUpstreamStages}
                                        </span>
                                      </div>
                                      <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
                                        这是隔离生成的候选内容，不会自动进入流水线、审核或正典。
                                      </p>
                                      <Button
                                        size="sm"
                                        className="mt-2 h-8 w-full text-[11px]"
                                        onClick={() =>
                                          onSubmitAgentCandidate(stageCandidate)
                                        }
                                        disabled={
                                          Boolean(actionKey) ||
                                          Boolean(agentActionKey)
                                        }
                                      >
                                        {actionKey === stageActionKey ? (
                                          <LoaderCircleIcon className="size-3 animate-spin" />
                                        ) : (
                                          <GitPullRequestArrowIcon className="size-3" />
                                        )}
                                        提交 AI 候选产物
                                      </Button>
                                    </div>
                                  ) : null}
                                </div>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
                {!relatedRuns.length ? (
                  <PanelEmpty>当前章节还没有流水线运行记录。</PanelEmpty>
                ) : null}
              </div>
            ) : null}

            {tab === "canon" ? (
              <div className="space-y-4">
                <div className="rounded-xl border border-amber-400/20 bg-amber-400/8 p-3">
                  <p className="flex items-center gap-2 text-sm font-medium">
                    <ShieldCheckIcon className="size-4" />
                    人工正典防线
                  </p>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">
                    Agent
                    只能生成候选内容。审核、法定票数和阻塞项全部通过后，仍需人工二次确认才能提交正典。
                  </p>
                  <p className="mt-2 text-[11px] text-muted-foreground">
                    项目规则：quorum{" "}
                    {project.governance?.quorum ?? "由服务端决定"} · 通过率{" "}
                    {project.governance
                      ? `${Math.round(project.governance.approval_ratio * 100)}%`
                      : "由服务端决定"}
                  </p>
                </div>
                <Button
                  size="sm"
                  className="w-full"
                  onClick={onCreateReview}
                  disabled={!selectedTargetId || actionKey === "review:create"}
                >
                  {actionKey === "review:create" ? (
                    <LoaderCircleIcon className="size-3.5 animate-spin" />
                  ) : (
                    <BookOpenCheckIcon className="size-3.5" />
                  )}
                  提交正典审核
                </Button>
                {relatedReviews.map((review) => {
                  const readiness = reviewReadiness(review);
                  const committed = extensions.canonCommits.some(
                    (commit) => commit.review_request_id === review.id,
                  );
                  return (
                    <div
                      key={review.id}
                      className="rounded-xl border border-border/60 p-3"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">
                            {review.title}
                          </p>
                          <p className="mt-1 text-[11px] text-muted-foreground">
                            修订 {review.revision} · {review.status}
                          </p>
                        </div>
                        {committed ? (
                          <span className="rounded-full bg-emerald-500/10 px-2 py-1 text-[10px] text-emerald-700 dark:text-emerald-300">
                            已入正典
                          </span>
                        ) : null}
                      </div>
                      <div className="mt-3 grid grid-cols-2 gap-2 text-center text-[11px]">
                        <div className="rounded-lg bg-muted/35 p-2">
                          <p className="text-base font-semibold">
                            {review.quorum_received}/{review.quorum_required}
                          </p>
                          <p className="text-muted-foreground">quorum</p>
                        </div>
                        <div className="rounded-lg bg-muted/35 p-2">
                          <p className="text-base font-semibold">
                            {Math.round(review.approval_ratio * 100)}%
                          </p>
                          <p className="text-muted-foreground">通过率</p>
                        </div>
                      </div>
                      {review.blockers.length ? (
                        <div className="mt-3 rounded-lg border border-red-400/20 bg-red-500/8 p-2 text-[11px] text-red-700 dark:text-red-300">
                          <p className="font-medium">阻塞项</p>
                          <ul className="mt-1 list-disc space-y-1 pl-4">
                            {review.blockers.map((blocker, index) => (
                              <li key={`${blocker}-${index}`}>{blocker}</li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                      {review.votes.length ? (
                        <div className="mt-3 space-y-1 text-[11px] text-muted-foreground">
                          {review.votes.slice(-4).map((vote, index) => (
                            <p key={vote.id || `${vote.actor}-${index}`}>
                              {vote.actor} · {vote.decision}
                              {vote.rationale ? ` · ${vote.rationale}` : ""}
                            </p>
                          ))}
                        </div>
                      ) : null}
                      {!committed && review.status !== "rejected" ? (
                        <div className="mt-3 grid grid-cols-2 gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => onVote(review, "approve")}
                            disabled={Boolean(actionKey)}
                          >
                            <VoteIcon className="size-3.5" />
                            赞成
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => onVote(review, "reject")}
                            disabled={Boolean(actionKey)}
                          >
                            <XIcon className="size-3.5" />
                            反对
                          </Button>
                        </div>
                      ) : null}
                      <Button
                        size="sm"
                        className="mt-2 w-full"
                        onClick={() => onRequestCommit(review)}
                        disabled={
                          !readiness.canCommit ||
                          committed ||
                          Boolean(actionKey)
                        }
                      >
                        <ShieldCheckIcon className="size-3.5" />
                        {committed
                          ? "已提交正典"
                          : readiness.hasBlockers
                            ? "解决阻塞项后可提交"
                            : !readiness.quorumMet
                              ? "达到 quorum 后可提交"
                              : "人工确认提交正典"}
                      </Button>
                    </div>
                  );
                })}
                {!relatedReviews.length ? (
                  <PanelEmpty>当前内容尚未提交正典审核。</PanelEmpty>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </div>
    </>
  );
}

export function CanonCommitDialog({
  review,
  busy,
  onClose,
  onConfirm,
}: {
  review: NarrativeReviewRequest;
  busy: boolean;
  onClose: () => void;
  onConfirm: (actor: string, rationale: string) => void;
}) {
  const [actor, setActor] = useState("human-editor");
  const [rationale, setRationale] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  return (
    <div
      className="fixed inset-0 z-[60] grid place-items-center bg-black/55 p-4 backdrop-blur-sm"
      role="presentation"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target && !busy) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="canon-confirm-title"
        className="w-full max-w-lg rounded-2xl border border-amber-400/25 bg-background p-5 shadow-2xl"
      >
        <div className="flex items-start gap-3">
          <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-amber-500/10 text-amber-600">
            <ShieldCheckIcon className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 id="canon-confirm-title" className="font-semibold">
              二次确认：提交正典
            </h2>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              这会创建不可伪造的正典提交记录。内容不会因为打开此窗口而改变。
            </p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            disabled={busy}
            aria-label="关闭正典确认"
          >
            <XIcon className="size-4" />
          </Button>
        </div>
        <div className="mt-4 rounded-xl bg-muted/40 p-3 text-xs">
          <p className="font-medium">{review.title}</p>
          <p className="mt-1 text-muted-foreground">
            审核 {review.id} · 修订 {review.revision}
          </p>
        </div>
        <div className="mt-4 space-y-3">
          <label className="block space-y-1.5 text-sm">
            操作者
            <Input
              value={actor}
              onChange={(event) => setActor(event.target.value)}
              placeholder="人工编辑标识"
            />
          </label>
          <label className="block space-y-1.5 text-sm">
            提交理由
            <Textarea
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
              placeholder="说明为什么该修订可以进入正典"
              className="min-h-24"
            />
          </label>
          <label className="flex cursor-pointer items-start gap-2 rounded-xl border border-amber-400/20 bg-amber-500/8 p-3 text-xs leading-5">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
              className="mt-0.5"
            />
            <span>
              我已核对审核票数、阻塞项和目标修订，并确认由人工执行本次正典提交。
            </span>
          </label>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            onClick={() => onConfirm(actor.trim(), rationale.trim())}
            disabled={!confirmed || !actor.trim() || !rationale.trim() || busy}
          >
            {busy ? (
              <LoaderCircleIcon className="size-4 animate-spin" />
            ) : (
              <ShieldCheckIcon className="size-4" />
            )}
            确认提交正典
          </Button>
        </div>
      </div>
    </div>
  );
}

export function GovernanceActionDialog({
  title,
  description,
  confirmLabel,
  busy,
  onClose,
  onConfirm,
}: {
  title: string;
  description: string;
  confirmLabel: string;
  busy: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  return (
    <div
      className="fixed inset-0 z-[60] grid place-items-center bg-black/55 p-4 backdrop-blur-sm"
      role="presentation"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target && !busy) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="governance-confirm-title"
        className="w-full max-w-md rounded-2xl border border-border/70 bg-background p-5 shadow-2xl"
      >
        <div className="flex items-start gap-3">
          <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-violet-500/10 text-violet-600">
            <VoteIcon className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 id="governance-confirm-title" className="font-semibold">
              {title}
            </h2>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              {description}
            </p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            disabled={busy}
            aria-label="关闭治理确认"
          >
            <XIcon className="size-4" />
          </Button>
        </div>
        <label className="mt-4 flex cursor-pointer items-start gap-2 rounded-xl border border-violet-400/20 bg-violet-500/8 p-3 text-xs leading-5">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            className="mt-0.5"
          />
          <span>
            我确认这会写入真实治理记录，但不会绕过
            quorum、阻塞项或人工正典提交。
          </span>
        </label>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button onClick={onConfirm} disabled={!confirmed || busy}>
            {busy ? (
              <LoaderCircleIcon className="size-4 animate-spin" />
            ) : (
              <VoteIcon className="size-4" />
            )}
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
