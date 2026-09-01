"use client";

/**
 * 项目管理 · PM 驾驶舱（Project OS 的真实项目管理视图）
 *
 * 在里程碑引擎之上给出 PM 需要读的那一层：
 *   - 项目整体进度 / 剩余估时 / 生命周期时间戳
 *   - 里程碑健康度（正常 / 有风险 / 已逾期 / 阻塞 / 完成）
 *   - 风险与阻塞、下一步动作（按优先级排序）、人员指派、燃尽视图
 *   - 完工项目复盘（交付数 / 失败数 / 重试 / 估时 / 耗时 / 建议）
 *
 * 数据全部来自后端只读模型：GET /api/projects/{id} 一次返回
 * project + milestones + tasks + pm + retro + action_specs。
 */

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import {
  ActivityIcon,
  AlertTriangleIcon,
  ArrowRightIcon,
  CalendarRangeIcon,
  CheckCircle2Icon,
  CircleIcon,
  ClipboardListIcon,
  FlagIcon,
  ListChecksIcon,
  MessageSquareIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  TimerIcon,
  UserRoundIcon,
  UsersIcon,
} from "lucide-react";
import { toast } from "sonner";

import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import {
  WorkspaceBody,
  WorkspaceContainer,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";
import { CreateProjectDialog } from "@/components/workspace/create-project-dialog";
import { type Project, useEnsureProjectHome } from "@/core/projects/hooks";

// ─── types（与 runtime/projectos/pm.py 的返回结构对应）───────────────

type Health = "on_track" | "at_risk" | "overdue" | "blocked" | "completed";

interface MilestonePM {
  id: string;
  name: string;
  status: string;
  health: Health;
  priority: string;
  planned_start: string;
  due_at: string;
  done: number;
  total: number;
  failed: number;
  progress: number;
  total_estimate: number;
  remaining_estimate: number;
  overdue_tasks: Array<{
    id: string;
    goal: string;
    due_at: string;
    priority: string;
  }>;
  success_criteria: string[];
}

interface PmeReport {
  project_id: string;
  name: string;
  status: string;
  overall_progress: number;
  done_tasks: number;
  total_tasks: number;
  total_estimate: number;
  remaining_estimate: number;
  milestones: MilestonePM[];
  burndown: Array<{
    milestone: string;
    health: Health;
    done: number;
    total: number;
    remaining_estimate: number;
  }>;
  risks: Array<{
    type: "milestone" | "task";
    milestone?: string;
    task_id?: string;
    task?: string;
    health: string;
    detail: string;
  }>;
  blockers: string[];
  overdue: Array<{ milestone: string; tasks: MilestonePM["overdue_tasks"] }>;
  next_actions: Array<{
    milestone: string;
    task_id: string;
    task: string;
    priority: string;
    estimate: number;
    due_at: string;
  }>;
  assignments: Record<string, string[]>;
}

interface Retro {
  project_id: string;
  name: string;
  goal: string;
  status: string;
  milestone_count: number;
  task_count: number;
  done_tasks: number;
  failed_tasks: number;
  rejected_tasks: number;
  attempts_total: number;
  total_estimate: number;
  duration_days: number | null;
  blocked_milestones: string[];
  risks_hit: string[];
  recommendations: string[];
}

interface ActionSpec {
  action: string;
  label: string;
  api: { method: string; path: string; body?: Record<string, unknown> };
  realtime_command?: string;
}

interface TaskReadModel {
  id: string;
  milestone_id: string;
  type: string;
  goal: string;
  assigned_role: string;
  assigned_agent: string;
  team_mode: string;
  priority: string;
  estimate: number;
  due_at: string;
  acceptance_criteria: string[];
  status: string;
  attempts: number;
}

interface ProjectFull {
  project: {
    id: string;
    name: string;
    goal: string;
    status: string;
    owner: string;
    created_at: string;
    started_at: string;
    finished_at: string;
    execution_thread_id?: string;
  };
  milestones: Array<Record<string, unknown> & { id: string; name: string }>;
  tasks: Record<string, TaskReadModel[]>;
  pm: PmeReport | null;
  retro: Retro | null;
  available_actions: string[];
  action_specs: ActionSpec[];
}

interface ProjectSummary {
  id: string;
  name: string;
  goal?: string;
  status?: string;
  created_at?: string;
  execution_thread_id?: string;
}

const BASE = () => `${getBackendBaseURL()}/api/projects`;
const PROJECT_LOAD_ERROR_MESSAGE = "项目加载失败，请稍后重试。";
const PROJECT_ACTION_ERROR_MESSAGE = "操作失败，请稍后重试。";
const TRACE_HEADER_NAMES = [
  "x-trace-id",
  "x-request-id",
  "trace-id",
  "request-id",
] as const;

class ProjectRequestError extends Error {
  readonly traceId: string | null;

  constructor(traceId: string | null) {
    super(PROJECT_LOAD_ERROR_MESSAGE);
    this.name = "ProjectRequestError";
    this.traceId = traceId;
  }
}

function normalizeTraceId(value: string | null | undefined): string | null {
  const traceId = value?.trim();
  if (!traceId || traceId.length > 128) return null;
  return /^[A-Za-z0-9._:/-]+$/.test(traceId) ? traceId : null;
}

function traceIdFromResponse(response: Response): string | null {
  for (const header of TRACE_HEADER_NAMES) {
    const traceId = normalizeTraceId(response.headers.get(header));
    if (traceId) return traceId;
  }
  return null;
}

function traceIdFromError(error: unknown): string | null {
  return error instanceof ProjectRequestError ? error.traceId : null;
}

function traceIdFromDetail(detail: string): string | null {
  const match = detail.match(
    /\b(?:trace[_ -]?id|request[_ -]?id)\s*[:=]\s*([A-Za-z0-9._/-]{6,128})/i,
  );
  return normalizeTraceId(match?.[1]);
}

function withTraceId(message: string, traceId: string | null): string {
  return traceId ? `${message} 追踪 ID：${traceId}` : message;
}

function safeRiskDetail(risk: PmeReport["risks"][number]): string {
  if (risk.type === "milestone") {
    return "里程碑存在阻塞或延期，请检查相关任务状态。";
  }
  if (risk.health === "overdue") {
    return "任务已逾期，请重新评估排期。";
  }
  if (risk.health === "blocked" || risk.health === "failed") {
    return "任务执行失败或受阻，请检查配置后重试。";
  }
  return "任务状态需要关注，请检查任务详情。";
}

function ProjectLoadFailure({
  error,
  onRetry,
  className = "",
}: {
  error: unknown;
  onRetry: () => void;
  className?: string;
}) {
  const traceId = traceIdFromError(error);
  return (
    <div
      role="alert"
      className={`flex flex-col items-center justify-center gap-3 px-4 text-center ${className}`}
    >
      <div>
        <p className="text-sm font-medium text-foreground">
          {PROJECT_LOAD_ERROR_MESSAGE}
        </p>
        {traceId && (
          <p className="mt-1 text-xs text-muted-foreground">
            追踪 ID：<code>{traceId}</code>
          </p>
        )}
      </div>
      <Button type="button" variant="outline" size="sm" onClick={onRetry}>
        <RefreshCwIcon className="size-3.5" />
        重试
      </Button>
    </div>
  );
}

const STATUS_LABEL: Record<string, string> = {
  planning: "规划中",
  running: "进行中",
  blocked: "已阻塞",
  done: "已完成",
  failed: "失败",
};

const HEALTH_LABEL: Record<string, string> = {
  on_track: "正常",
  at_risk: "有风险",
  overdue: "已逾期",
  blocked: "阻塞",
  completed: "完成",
};

const HEALTH_TONE: Record<Health, string> = {
  on_track: "bg-emerald-500/15 text-emerald-600 border-emerald-500/30",
  at_risk: "bg-amber-500/15 text-amber-600 border-amber-500/30",
  overdue: "bg-orange-500/15 text-orange-600 border-orange-500/30",
  blocked: "bg-rose-500/15 text-rose-600 border-rose-500/30",
  completed: "bg-sky-500/15 text-sky-600 border-sky-500/30",
};

const STATUS_TONE: Record<string, string> = {
  planning: "bg-muted text-muted-foreground",
  running: "bg-emerald-500/15 text-emerald-600",
  blocked: "bg-rose-500/15 text-rose-600",
  done: "bg-sky-500/15 text-sky-600",
  failed: "bg-rose-500/15 text-rose-600",
};

const PRIORITY_TONE: Record<string, string> = {
  P0: "bg-rose-500/15 text-rose-600",
  P1: "bg-amber-500/15 text-amber-600",
  P2: "bg-muted text-muted-foreground",
  P3: "bg-muted text-muted-foreground/70",
};

function fmtDate(value: string | undefined | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value.slice(0, 10);
  return d.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function fmtDateTime(value: string | undefined | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function healthIcon(health: Health) {
  if (health === "on_track") return <CheckCircle2Icon className="size-3.5" />;
  if (health === "at_risk") return <AlertTriangleIcon className="size-3.5" />;
  if (health === "overdue") return <TimerIcon className="size-3.5" />;
  if (health === "blocked") return <FlagIcon className="size-3.5" />;
  return <CircleIcon className="size-3.5" />;
}

function MetricCard({
  label,
  value,
  sub,
  icon,
}: {
  label: string;
  value: string;
  sub?: string;
  icon: React.ReactNode;
}) {
  return (
    <Card className="bg-card/60">
      <CardContent className="flex items-start gap-3 p-4">
        <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted/60 text-muted-foreground">
          {icon}
        </div>
        <div className="min-w-0">
          <div className="text-xs text-muted-foreground">{label}</div>
          <div className="mt-0.5 truncate text-lg font-semibold">{value}</div>
          {sub && (
            <div className="mt-0.5 truncate text-xs text-muted-foreground">
              {sub}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function ProjectsPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const ensureProjectHome = useEnsureProjectHome();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const projectsQuery = useQuery<ProjectSummary[]>({
    queryKey: ["projects"],
    queryFn: async () => {
      const res = await fetch(BASE(), { headers: authHeaders() });
      if (!res.ok) throw new ProjectRequestError(traceIdFromResponse(res));
      const data = (await res.json()) as unknown;
      if (Array.isArray(data)) return data as ProjectSummary[];
      if (
        data &&
        typeof data === "object" &&
        Array.isArray((data as { projects?: unknown }).projects)
      ) {
        return (data as { projects: ProjectSummary[] }).projects;
      }
      return [];
    },
  });

  const projects = useMemo(
    () => projectsQuery.data ?? [],
    [projectsQuery.data],
  );
  useEffect(() => {
    if (!selectedId && projects.length > 0) {
      const first = projects[0];
      if (first) setSelectedId(first.id);
    }
  }, [projects, selectedId]);

  const detailQuery = useQuery<ProjectFull>({
    queryKey: ["project", selectedId],
    queryFn: async () => {
      const res = await fetch(`${BASE()}/${selectedId}`, {
        headers: authHeaders(),
      });
      if (!res.ok) throw new ProjectRequestError(traceIdFromResponse(res));
      return res.json();
    },
    enabled: !!selectedId,
  });

  const detail = detailQuery.data;
  const pm = detail?.pm ?? null;
  const retro = detail?.retro ?? null;

  const executeAction = async (
    spec: ActionSpec,
    overrides?: Record<string, unknown>,
  ) => {
    if (!spec.api) return;
    try {
      const res = await fetch(spec.api.path, {
        method: spec.api.method,
        headers: jsonAuthHeaders(),
        body:
          spec.api.body || overrides
            ? JSON.stringify({ ...(spec.api.body ?? {}), ...(overrides ?? {}) })
            : undefined,
      });
      if (!res.ok) {
        toast.error(
          withTraceId(PROJECT_ACTION_ERROR_MESSAGE, traceIdFromResponse(res)),
        );
        return;
      }
      toast.success(`${spec.label} 已执行`);
      detailQuery.refetch();
      projectsQuery.refetch();
    } catch {
      toast.error(PROJECT_ACTION_ERROR_MESSAGE);
    }
  };

  const refresh = () => {
    void detailQuery.refetch();
    void projectsQuery.refetch();
  };

  const openProjectGroup = (project: Project) => {
    ensureProjectHome.mutate(project, {
      onSuccess: ({ threadId }) =>
        navigate(`/workspace/realtime/${encodeURIComponent(threadId)}`, {
          state: { openProjectWorkbench: true },
        }),
      onError: () => toast.error("项目工作群打开失败，请重试"),
    });
  };

  return (
    <WorkspaceContainer className="!p-0 md:!px-0">
      <WorkspaceBody className="!p-0">
        <div className="flex h-full w-full min-h-0 flex-col items-stretch">
          {/* Header */}
          <div className="flex h-11 shrink-0 items-center justify-between gap-3 border-b px-4">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <h1>🗂️ {t.sidebar.navProjects}</h1>
              <span className="text-xs font-normal text-muted-foreground">
                里程碑健康度 · 风险 · 下一步 · 复盘 —— 真实 PM 视角
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                className="gap-1.5 text-xs text-muted-foreground"
                onClick={() => setCreateOpen(true)}
              >
                <PlusIcon className="size-3.5" />
                新建项目
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="gap-1.5 text-xs text-muted-foreground"
                onClick={refresh}
              >
                <RefreshCwIcon className="size-3.5" />
                刷新
              </Button>
            </div>
          </div>

          {projectsQuery.isLoading ? (
            <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
              加载中…
            </div>
          ) : projectsQuery.isError ? (
            <ProjectLoadFailure
              error={projectsQuery.error}
              onRetry={() => void projectsQuery.refetch()}
              className="flex-1"
            />
          ) : projects.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
              <ClipboardListIcon className="size-10 opacity-40" />
              <div>
                还没有项目。在实时会话里用 /project
                开启一个里程碑式项目，或先建一个项目。
              </div>
              <Link
                to="/workspace/realtime/new"
                className="text-xs text-primary underline-offset-4 hover:underline"
              >
                去新建会话
              </Link>
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 items-stretch">
              {/* Project list */}
              <aside className="hidden w-60 shrink-0 flex-col border-r md:flex">
                <div className="flex h-9 items-center justify-between border-b px-3 text-xs text-muted-foreground">
                  <span>项目列表（{projects.length}）</span>
                </div>
                <div className="flex-1 overflow-y-auto py-1">
                  {projects.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => setSelectedId(p.id)}
                      className={`flex w-full flex-col gap-0.5 border-l-2 px-3 py-2 text-left transition-colors ${
                        selectedId === p.id
                          ? "border-primary bg-muted/40"
                          : "border-transparent hover:bg-muted/30"
                      }`}
                    >
                      <span className="flex items-center justify-between gap-2 text-sm font-medium">
                        <span className="truncate">{p.name || p.id}</span>
                        {p.status && (
                          <Badge
                            variant="outline"
                            className={`shrink-0 text-[10px] ${STATUS_TONE[p.status] ?? ""}`}
                          >
                            {STATUS_LABEL[p.status] ?? p.status}
                          </Badge>
                        )}
                      </span>
                      <span className="truncate text-xs text-muted-foreground">
                        {p.goal || p.id}
                      </span>
                    </button>
                  ))}
                </div>
              </aside>

              {/* Main PM view */}
              <div className="min-w-0 flex-1 overflow-y-auto">
                {detailQuery.isLoading ? (
                  <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                    加载中…
                  </div>
                ) : detailQuery.isError ? (
                  <ProjectLoadFailure
                    error={detailQuery.error}
                    onRetry={() => void detailQuery.refetch()}
                    className="h-full"
                  />
                ) : detail ? (
                  <div className="mx-auto max-w-5xl space-y-4 p-4">
                    {/* Project header */}
                    <Card>
                      <CardContent className="space-y-3 p-4">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <h2 className="text-base font-semibold">
                                {detail.project.name || detail.project.id}
                              </h2>
                              <Badge
                                variant="outline"
                                className={
                                  STATUS_TONE[detail.project.status] ?? ""
                                }
                              >
                                {STATUS_LABEL[detail.project.status] ??
                                  detail.project.status}
                              </Badge>
                              {detail.project.owner && (
                                <Badge
                                  variant="outline"
                                  className="gap-1 bg-muted/40 text-muted-foreground"
                                >
                                  <UserRoundIcon className="size-3" />
                                  PM · {detail.project.owner}
                                </Badge>
                              )}
                            </div>
                            {detail.project.goal && (
                              <p className="mt-1 text-sm text-muted-foreground">
                                {detail.project.goal}
                              </p>
                            )}
                          </div>
                          <div className="flex items-center gap-2">
                            <Button
                              variant="outline"
                              size="sm"
                              className="gap-1.5 text-xs"
                              disabled={ensureProjectHome.isPending}
                              onClick={() => openProjectGroup(detail.project)}
                            >
                              <MessageSquareIcon className="size-3.5" />
                              进入项目群
                            </Button>
                            {detail.action_specs.map((spec) => (
                              <Button
                                key={spec.action}
                                size="sm"
                                className="gap-1.5 text-xs"
                                onClick={() => executeAction(spec)}
                              >
                                {spec.action.startsWith("recover") ? (
                                  <RotateCcwIcon className="size-3.5" />
                                ) : spec.action === "run" ? (
                                  <PlayIcon className="size-3.5" />
                                ) : (
                                  <ArrowRightIcon className="size-3.5" />
                                )}
                                {spec.label}
                              </Button>
                            ))}
                          </div>
                        </div>

                        {/* lifecycle timestamps */}
                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                          <span className="inline-flex items-center gap-1">
                            <CalendarRangeIcon className="size-3.5" />
                            创建 {fmtDateTime(detail.project.created_at)}
                          </span>
                          {detail.project.started_at && (
                            <span className="inline-flex items-center gap-1">
                              <PlayIcon className="size-3.5" />
                              启动 {fmtDateTime(detail.project.started_at)}
                            </span>
                          )}
                          {detail.project.finished_at && (
                            <span className="inline-flex items-center gap-1">
                              <CheckCircle2Icon className="size-3.5" />
                              完成 {fmtDateTime(detail.project.finished_at)}
                            </span>
                          )}
                        </div>

                        {pm && (
                          <div className="space-y-1.5">
                            <div className="flex items-center justify-between text-xs text-muted-foreground">
                              <span className="font-medium text-foreground">
                                整体进度
                              </span>
                              <span>
                                {pm.done_tasks}/{pm.total_tasks} 任务 · 剩余估时{" "}
                                {pm.remaining_estimate}d / 共{" "}
                                {pm.total_estimate}d
                              </span>
                            </div>
                            <Progress
                              value={Math.round(pm.overall_progress * 100)}
                              className="h-2"
                            />
                            <div className="text-right text-xs text-muted-foreground">
                              {Math.round(pm.overall_progress * 100)}%
                            </div>
                          </div>
                        )}
                      </CardContent>
                    </Card>

                    {pm && (
                      <>
                        {/* Metrics */}
                        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                          <MetricCard
                            label="整体进度"
                            value={`${Math.round(pm.overall_progress * 100)}%`}
                            sub={`${pm.done_tasks}/${pm.total_tasks} 任务`}
                            icon={<ActivityIcon className="size-4" />}
                          />
                          <MetricCard
                            label="剩余估时"
                            value={`${pm.remaining_estimate}d`}
                            sub={`总估时 ${pm.total_estimate}d`}
                            icon={<TimerIcon className="size-4" />}
                          />
                          <MetricCard
                            label="风险"
                            value={`${pm.risks.length}`}
                            sub={`${pm.blockers.length} 个阻塞里程碑`}
                            icon={<AlertTriangleIcon className="size-4" />}
                          />
                          <MetricCard
                            label="下一步动作"
                            value={`${pm.next_actions.length}`}
                            sub="就绪待办（按优先级）"
                            icon={<ListChecksIcon className="size-4" />}
                          />
                        </div>

                        {/* Milestones */}
                        <Card>
                          <CardHeader className="pb-2">
                            <CardTitle className="text-sm">
                              里程碑健康度
                            </CardTitle>
                          </CardHeader>
                          <CardContent className="space-y-3">
                            {pm.milestones.length === 0 && (
                              <div className="text-sm text-muted-foreground">
                                还没有里程碑 —— 先执行 Run 让引擎拆解计划。
                              </div>
                            )}
                            {pm.milestones.map((m) => (
                              <div
                                key={m.id}
                                className="rounded-lg border bg-card/50 p-3"
                              >
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                  <div className="flex items-center gap-2">
                                    <span className="text-sm font-medium">
                                      {m.name}
                                    </span>
                                    <Badge
                                      variant="outline"
                                      className={`gap-1 text-[10px] ${HEALTH_TONE[m.health] ?? ""}`}
                                    >
                                      {healthIcon(m.health)}
                                      {HEALTH_LABEL[m.health] ?? m.health}
                                    </Badge>
                                    <Badge
                                      variant="outline"
                                      className={`text-[10px] ${PRIORITY_TONE[m.priority] ?? ""}`}
                                    >
                                      优先级 {m.priority}
                                    </Badge>
                                  </div>
                                  <div className="flex items-center gap-3 text-xs text-muted-foreground">
                                    <span>
                                      {m.done}/{m.total} 任务
                                    </span>
                                    {m.due_at && (
                                      <span className="inline-flex items-center gap-1">
                                        <CalendarRangeIcon className="size-3" />
                                        截止 {fmtDate(m.due_at)}
                                      </span>
                                    )}
                                    {m.planned_start && (
                                      <span>
                                        计划 {fmtDate(m.planned_start)}
                                      </span>
                                    )}
                                    <span>剩余 {m.remaining_estimate}d</span>
                                  </div>
                                </div>
                                <div className="mt-2 flex items-center gap-2">
                                  <Progress
                                    value={Math.round(m.progress * 100)}
                                    className="h-1.5 flex-1"
                                  />
                                  <span className="w-10 text-right text-xs text-muted-foreground">
                                    {Math.round(m.progress * 100)}%
                                  </span>
                                </div>
                                {m.overdue_tasks.length > 0 && (
                                  <div className="mt-3 space-y-2 border-t border-orange-500/15 pt-2.5">
                                    <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-orange-600/90">
                                      <TimerIcon className="size-3" />
                                      逾期任务 · {m.overdue_tasks.length}
                                    </div>
                                    {m.overdue_tasks.map((o) => (
                                      <div
                                        key={o.id}
                                        className="rounded-lg bg-orange-500/[0.07] px-3 py-2"
                                      >
                                        <div className="text-xs leading-relaxed text-orange-800">
                                          {o.goal}
                                        </div>
                                        <div className="mt-1 flex items-center gap-1 text-[11px] text-orange-600/80">
                                          <CalendarRangeIcon className="size-3" />
                                          截止 {fmtDate(o.due_at)} · 已逾期
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            ))}
                          </CardContent>
                        </Card>

                        {/* Risks & Next actions */}
                        <div className="grid gap-3 lg:grid-cols-2">
                          <Card>
                            <CardHeader className="pb-2">
                              <CardTitle className="flex items-center gap-1.5 text-sm">
                                <AlertTriangleIcon className="size-4 text-rose-500" />
                                风险与阻塞
                              </CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-2">
                              {pm.risks.length === 0 && (
                                <div className="text-sm text-muted-foreground">
                                  暂无风险。
                                </div>
                              )}
                              {pm.risks.map((r, i) => {
                                const traceId = traceIdFromDetail(r.detail);
                                return (
                                  <div
                                    key={`${r.type}-${i}`}
                                    className="flex items-start gap-2 rounded-md border bg-card/50 px-2.5 py-2 text-xs"
                                  >
                                    <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0 text-amber-500" />
                                    <div className="min-w-0">
                                      <div className="font-medium">
                                        {r.type === "milestone"
                                          ? r.milestone
                                          : r.task}
                                      </div>
                                      <div className="text-muted-foreground">
                                        {safeRiskDetail(r)}
                                      </div>
                                      {traceId && (
                                        <div className="mt-0.5 text-muted-foreground">
                                          追踪 ID：<code>{traceId}</code>
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                );
                              })}
                            </CardContent>
                          </Card>

                          <Card>
                            <CardHeader className="pb-2">
                              <CardTitle className="flex items-center gap-1.5 text-sm">
                                <ListChecksIcon className="size-4 text-emerald-500" />
                                下一步动作
                              </CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-2">
                              {pm.next_actions.length === 0 && (
                                <div className="text-sm text-muted-foreground">
                                  当前没有就绪任务。
                                </div>
                              )}
                              {pm.next_actions.map((a) => (
                                <div
                                  key={a.task_id}
                                  className="flex items-start gap-2 rounded-md border bg-card/50 px-2.5 py-2 text-xs"
                                >
                                  <Badge
                                    variant="outline"
                                    className={`mt-0.5 shrink-0 text-[10px] ${PRIORITY_TONE[a.priority] ?? ""}`}
                                  >
                                    {a.priority}
                                  </Badge>
                                  <div className="min-w-0">
                                    <div className="font-medium">{a.task}</div>
                                    <div className="text-muted-foreground">
                                      {a.milestone}
                                      {a.estimate > 0 &&
                                        ` · 估时 ${a.estimate}d`}
                                      {a.due_at &&
                                        ` · 截止 ${fmtDate(a.due_at)}`}
                                    </div>
                                  </div>
                                </div>
                              ))}
                            </CardContent>
                          </Card>
                        </div>

                        {/* Assignments + Burndown */}
                        <div className="grid gap-3 lg:grid-cols-2">
                          <Card>
                            <CardHeader className="pb-2">
                              <CardTitle className="flex items-center gap-1.5 text-sm">
                                <UsersIcon className="size-4 text-sky-500" />
                                人员指派
                              </CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-2">
                              {Object.keys(pm.assignments).length === 0 && (
                                <div className="text-sm text-muted-foreground">
                                  暂无指派。
                                </div>
                              )}
                              {Object.entries(pm.assignments).map(
                                ([who, taskIds]) => (
                                  <div
                                    key={who}
                                    className="flex items-center justify-between rounded-md border bg-card/50 px-2.5 py-2 text-xs"
                                  >
                                    <span className="flex items-center gap-1.5 font-medium">
                                      <UserRoundIcon className="size-3.5 text-muted-foreground" />
                                      {who}
                                    </span>
                                    <span className="text-muted-foreground">
                                      {taskIds.length} 个任务
                                    </span>
                                  </div>
                                ),
                              )}
                            </CardContent>
                          </Card>

                          <Card>
                            <CardHeader className="pb-2">
                              <CardTitle className="text-sm">
                                燃尽视图（剩余估时）
                              </CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-2">
                              {pm.burndown.length === 0 && (
                                <div className="text-sm text-muted-foreground">
                                  暂无数据。
                                </div>
                              )}
                              {pm.burndown.map((b) => (
                                <div key={b.milestone} className="space-y-1">
                                  <div className="flex items-center justify-between text-xs">
                                    <span className="truncate pr-2">
                                      {b.milestone}
                                    </span>
                                    <span className="shrink-0 text-muted-foreground">
                                      {b.remaining_estimate}d
                                    </span>
                                  </div>
                                  <Progress
                                    value={
                                      b.total > 0
                                        ? Math.round((b.done / b.total) * 100)
                                        : 0
                                    }
                                    className="h-1.5"
                                  />
                                </div>
                              ))}
                            </CardContent>
                          </Card>
                        </div>
                      </>
                    )}

                    {/* Retro */}
                    {retro && (
                      <Card className="border-sky-500/30 bg-sky-500/[0.03]">
                        <CardHeader className="pb-2">
                          <CardTitle className="flex items-center gap-1.5 text-sm">
                            <ClipboardListIcon className="size-4 text-sky-500" />
                            项目复盘
                          </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3">
                          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                            <MetricCard
                              label="交付任务"
                              value={`${retro.done_tasks}/${retro.task_count}`}
                              icon={<CheckCircle2Icon className="size-4" />}
                            />
                            <MetricCard
                              label="失败/驳回"
                              value={`${retro.failed_tasks}/${retro.rejected_tasks}`}
                              icon={<AlertTriangleIcon className="size-4" />}
                            />
                            <MetricCard
                              label="总重试"
                              value={`${retro.attempts_total}`}
                              icon={<RefreshCwIcon className="size-4" />}
                            />
                            <MetricCard
                              label="实际耗时"
                              value={
                                retro.duration_days === null
                                  ? "—"
                                  : `${retro.duration_days} 天`
                              }
                              sub={`估时 ${retro.total_estimate}d`}
                              icon={<TimerIcon className="size-4" />}
                            />
                          </div>
                          <div>
                            <div className="mb-1.5 text-xs font-medium text-muted-foreground">
                              阻塞里程碑：
                              {retro.blocked_milestones.length > 0
                                ? retro.blocked_milestones.join("、")
                                : "无"}
                            </div>
                            <div className="text-xs font-medium text-muted-foreground">
                              建议：
                            </div>
                            <ul className="mt-1 space-y-1">
                              {retro.recommendations.map((r, i) => (
                                <li
                                  key={i}
                                  className="flex items-start gap-1.5 text-xs"
                                >
                                  <ArrowRightIcon className="mt-0.5 size-3 shrink-0 text-sky-500" />
                                  <span>{r}</span>
                                </li>
                              ))}
                            </ul>
                          </div>
                        </CardContent>
                      </Card>
                    )}
                  </div>
                ) : null}
              </div>
            </div>
          )}
        </div>
        <CreateProjectDialog open={createOpen} onOpenChange={setCreateOpen} />
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
