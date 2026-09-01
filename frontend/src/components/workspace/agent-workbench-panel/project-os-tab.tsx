"use client";

/**
 * 当前会话所绑定项目的统一右侧工作台。
 *
 * 群聊负责承载讨论，项目工作台负责承载结构化事实。这里把 Project OS
 * 的里程碑、事项、产物与协作席位收口到一个紧凑的五页签视图中，避免
 * 用户在聊天、独立项目驾驶舱与团队工作台之间反复跳转。
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ComponentType,
  type ReactNode,
} from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangleIcon,
  ArrowRightIcon,
  BotIcon,
  CalendarDaysIcon,
  CheckCircle2Icon,
  CircleDotDashedIcon,
  ClipboardListIcon,
  ExternalLinkIcon,
  FileBoxIcon,
  FileTextIcon,
  FlagIcon,
  FolderKanbanIcon,
  LayoutDashboardIcon,
  ListChecksIcon,
  ListTodoIcon,
  Loader2Icon,
  PackageOpenIcon,
  PlayIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  TimerIcon,
  UserRoundIcon,
  UserPlusIcon,
  UsersIcon,
  XCircleIcon,
} from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { RoutedWebLink } from "@/components/ui/routed-web-link";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { cn } from "@/lib/utils";

import type { WorkbenchRosterSeat } from "./helpers";

export type ProjectWorkbenchTabId =
  | "overview"
  | "milestones"
  | "tasks"
  | "assets"
  | "members";

type Health = "on_track" | "at_risk" | "overdue" | "blocked" | "completed";

export interface ProjectActionSpec {
  action: string;
  label: string;
  api: {
    method: string;
    path: string;
    body?: Record<string, unknown>;
  };
  realtime_command?: string;
  requires?: string[];
}

export interface ProjectTaskReadModel {
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
  depends_on?: string[];
  output?: unknown;
  qa_verdict?: Record<string, unknown> | null;
  available_actions?: string[];
  action_specs?: ProjectActionSpec[];
}

interface MilestonePM {
  id: string;
  name: string;
  status: string;
  health: Health;
  priority: string;
  planned_start?: string;
  due_at: string;
  done: number;
  total: number;
  failed: number;
  progress: number;
  total_estimate?: number;
  remaining_estimate?: number;
  overdue_tasks?: Array<{
    id: string;
    goal: string;
    due_at: string;
    priority: string;
  }>;
  success_criteria?: string[];
}

interface PmReport {
  project_id: string;
  name: string;
  status: string;
  overall_progress: number;
  done_tasks: number;
  total_tasks: number;
  total_estimate?: number;
  remaining_estimate: number;
  milestones: MilestonePM[];
  risks: Array<{
    type: "milestone" | "task";
    milestone?: string;
    task_id?: string;
    task?: string;
    health: string;
    detail: string;
  }>;
  blockers: string[];
  next_actions: Array<{
    milestone: string;
    task_id: string;
    task: string;
    priority: string;
    estimate: number;
    due_at: string;
  }>;
  assignments?: Record<string, string[]>;
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
  recommendations: string[];
}

export interface ProjectArtifactReadModel {
  id: string;
  name: string;
  kind?: string;
  path?: string;
  url?: string;
  summary?: string;
  task_id?: string;
  milestone_id?: string;
}

export interface ProjectDecisionReadModel {
  id?: string;
  title?: string;
  summary?: string;
  decision?: string;
  actor?: string;
  source_message_id?: string;
  milestone_id?: string;
  created_at?: string;
}

export interface ProjectMemberReadModel {
  id: string;
  name: string;
  role?: string;
  kind?: "human" | "agent" | "role";
  avatar_url?: string | null;
  status?: string;
}

export interface ProjectFullState {
  project: {
    id: string;
    name: string;
    goal: string;
    status: string;
    owner: string;
    owner_id?: string;
    current_ms?: string | null;
    execution_thread_id?: string;
    created_at: string;
    started_at: string;
    finished_at: string;
  };
  milestones: Array<{
    id: string;
    name: string;
    goal?: string;
    status: string;
    priority: string;
    planned_start?: string;
    due_at: string;
    success_criteria?: string[];
  }>;
  tasks: Record<string, ProjectTaskReadModel[]>;
  pm: PmReport | null;
  retro: Retro | null;
  available_actions: string[];
  action_specs: ProjectActionSpec[];
  /** Forward-compatible projection from the project room. */
  artifacts?: ProjectArtifactReadModel[];
  /** Forward-compatible decision log projected from project-room actions. */
  decisions?: ProjectDecisionReadModel[];
  /** Forward-compatible projection from the project room. */
  members?: ProjectMemberReadModel[];
}

interface MilestoneView extends MilestonePM {
  goal?: string;
  progressPercent: number;
}

interface ProjectAssetView extends ProjectArtifactReadModel {
  sourceTask?: string;
}

interface ProjectMemberView {
  id: string;
  name: string;
  role: string;
  kind: "human" | "agent" | "role";
  avatarUrl?: string | null;
  status?: string;
  isOwner?: boolean;
}

interface ProjectDecisionView {
  id: string;
  title: string;
  body?: string;
  actor?: string;
  milestoneId?: string;
  createdAt?: string;
}

const STATUS_LABEL: Record<string, string> = {
  planning: "规划中",
  pending: "待开始",
  active: "进行中",
  in_progress: "进行中",
  running: "进行中",
  ready: "可执行",
  blocked: "已阻塞",
  done: "已完成",
  completed: "已完成",
  failed: "失败",
  rejected: "未通过",
};

const STATUS_TONE: Record<string, string> = {
  planning: "border-border-default bg-muted text-muted-foreground",
  pending: "border-border-default bg-muted text-muted-foreground",
  ready: "border-sky-500/25 bg-sky-500/10 text-sky-600",
  active: "border-emerald-500/25 bg-emerald-500/10 text-emerald-600",
  in_progress: "border-emerald-500/25 bg-emerald-500/10 text-emerald-600",
  running: "border-emerald-500/25 bg-emerald-500/10 text-emerald-600",
  blocked: "border-rose-500/25 bg-rose-500/10 text-rose-600",
  done: "border-sky-500/25 bg-sky-500/10 text-sky-600",
  completed: "border-sky-500/25 bg-sky-500/10 text-sky-600",
  failed: "border-rose-500/25 bg-rose-500/10 text-rose-600",
  rejected: "border-orange-500/25 bg-orange-500/10 text-orange-600",
};

const HEALTH_LABEL: Record<Health, string> = {
  on_track: "正常",
  at_risk: "有风险",
  overdue: "已逾期",
  blocked: "阻塞",
  completed: "完成",
};

const HEALTH_TONE: Record<Health, string> = {
  on_track: "border-emerald-500/25 bg-emerald-500/10 text-emerald-600",
  at_risk: "border-amber-500/25 bg-amber-500/10 text-amber-700",
  overdue: "border-orange-500/25 bg-orange-500/10 text-orange-600",
  blocked: "border-rose-500/25 bg-rose-500/10 text-rose-600",
  completed: "border-sky-500/25 bg-sky-500/10 text-sky-600",
};

const HEALTH_DOT: Record<Health, string> = {
  on_track: "bg-emerald-500",
  at_risk: "bg-amber-500",
  overdue: "bg-orange-500",
  blocked: "bg-rose-500",
  completed: "bg-sky-500",
};

const PRIORITY_TONE: Record<string, string> = {
  P0: "border-rose-500/25 bg-rose-500/10 text-rose-600",
  P1: "border-amber-500/25 bg-amber-500/10 text-amber-700",
  P2: "border-border-default bg-muted/70 text-muted-foreground",
  P3: "border-border-default bg-muted/40 text-muted-foreground/80",
};

const PROJECT_ACTION_LABEL: Record<string, string> = {
  run: "开始推进",
  tick: "推进一步",
  recover: "恢复阻塞",
  recover_and_run: "恢复并推进",
  inspect: "检查状态",
  report: "生成报告",
};

const TASK_ACTION_LABEL: Record<string, string> = {
  reset: "重新开始",
  complete: "标记完成",
  skip: "跳过事项",
  reassign: "重新指派",
};

const ACTIVE_TASK_STATUSES = new Set(["pending", "ready", "running"]);
const ATTENTION_TASK_STATUSES = new Set(["blocked", "failed", "rejected"]);

/**
 * Project OS serializes progress as a ratio (0–1). Older fixtures and third
 * party projections may already send a percentage; accepting both keeps the
 * workbench resilient without ever rendering values outside 0–100.
 */
export function normalizeProjectProgress(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return 0;
  const percentage = parsed <= 1 ? parsed * 100 : parsed;
  return Math.min(100, Math.round(percentage));
}

function fmtDate(value: string | undefined | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleDateString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  });
}

function fmtDecisionTime(value: string | undefined): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatEstimate(value: number | undefined): string {
  if (!Number.isFinite(value)) return "—";
  return `${value} 天`;
}

function displayActionLabel(spec: ProjectActionSpec, task = false): string {
  return (
    (task
      ? TASK_ACTION_LABEL[spec.action]
      : PROJECT_ACTION_LABEL[spec.action]) ??
    spec.label ??
    spec.action
  );
}

function safeRiskDetail(risk: PmReport["risks"][number]): string {
  if (risk.type === "milestone") {
    return risk.health === "overdue"
      ? "里程碑已经逾期，请重新评估排期。"
      : "里程碑存在阻塞，请检查相关事项状态。";
  }
  if (risk.health === "overdue") {
    return risk.task
      ? `${risk.task}已逾期，请重新评估排期。`
      : "事项已逾期，请重新评估排期。";
  }
  if (["blocked", "failed", "rejected"].includes(risk.health)) {
    return risk.task
      ? `${risk.task}执行失败或受阻，请检查执行环境后重试。`
      : "事项执行失败或受阻，请检查执行环境后重试。";
  }
  return risk.task
    ? `${risk.task}的状态需要关注。`
    : "事项状态需要关注，请检查详情。";
}

function uniqueProjectRisks(pm: PmReport | null): PmReport["risks"] {
  if (!pm) return [];
  const blockerNames = new Set(pm.blockers ?? []);
  return (pm.risks ?? []).filter(
    (risk) =>
      !(
        risk.type === "milestone" &&
        risk.milestone &&
        blockerNames.has(risk.milestone)
      ),
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nonEmptyOutput(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (isRecord(value)) return Object.keys(value).length > 0;
  return true;
}

function firstString(
  source: Record<string, unknown>,
  keys: string[],
): string | undefined {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function projectDecisionViews(value: unknown): ProjectDecisionView[] {
  if (!Array.isArray(value)) return [];

  return value
    .map((candidate, index): ProjectDecisionView | null => {
      if (!isRecord(candidate)) return null;
      const explicitTitle = firstString(candidate, ["title", "name"]);
      const decision = firstString(candidate, ["decision", "content"]);
      const summary = firstString(candidate, ["summary", "description"]);
      const title = explicitTitle ?? summary ?? decision;
      if (!title) return null;

      const body = decision ?? (explicitTitle ? summary : undefined);
      const createdAt = firstString(candidate, [
        "created_at",
        "recorded_at",
        "timestamp",
      ]);
      return {
        id:
          firstString(candidate, ["id"]) ??
          `decision:${createdAt ?? title}:${index}`,
        title,
        body: body && body !== title ? body : undefined,
        actor: firstString(candidate, ["actor", "created_by", "author"]),
        milestoneId: firstString(candidate, ["milestone_id"]),
        createdAt,
      };
    })
    .filter((decision): decision is ProjectDecisionView => decision !== null)
    .sort((left, right) => {
      const leftTime = left.createdAt ? Date.parse(left.createdAt) : NaN;
      const rightTime = right.createdAt ? Date.parse(right.createdAt) : NaN;
      if (Number.isNaN(leftTime) && Number.isNaN(rightTime)) return 0;
      if (Number.isNaN(leftTime)) return 1;
      if (Number.isNaN(rightTime)) return -1;
      return rightTime - leftTime;
    });
}

function isExternalUrl(
  value: string | undefined,
): value is `http://${string}` | `https://${string}` {
  return !!value && /^https?:\/\//i.test(value);
}

function looksLikePath(value: string | undefined): value is string {
  if (!value || isExternalUrl(value)) return false;
  return (
    value.startsWith("/") ||
    value.startsWith("./") ||
    value.startsWith("../") ||
    /\.[a-z0-9]{1,8}(?:[#?].*)?$/i.test(value)
  );
}

function shortOutput(value: unknown): string | undefined {
  if (typeof value === "string") {
    const normalized = value.replace(/\s+/g, " ").trim();
    return normalized.length > 140
      ? `${normalized.slice(0, 140)}…`
      : normalized;
  }
  if (isRecord(value)) {
    return firstString(value, ["summary", "description", "message", "result"]);
  }
  return undefined;
}

function assetsFromTask(task: ProjectTaskReadModel): ProjectAssetView[] {
  if (task.status !== "done" || !nonEmptyOutput(task.output)) return [];

  const rawOutput = task.output;
  const candidates = (() => {
    if (Array.isArray(rawOutput)) return rawOutput;
    if (isRecord(rawOutput)) {
      for (const key of ["artifacts", "files", "outputs"]) {
        const nested = rawOutput[key];
        if (Array.isArray(nested) && nested.length > 0) return nested;
      }
    }
    return [rawOutput];
  })();

  return candidates
    .filter(nonEmptyOutput)
    .map((candidate, index): ProjectAssetView => {
      const record = isRecord(candidate) ? candidate : {};
      const path =
        firstString(record, ["path", "file_path", "filename"]) ??
        (typeof candidate === "string" && looksLikePath(candidate)
          ? candidate
          : undefined);
      const url =
        firstString(record, ["url", "href"]) ??
        (typeof candidate === "string" && isExternalUrl(candidate)
          ? candidate
          : undefined);
      const fallbackName =
        candidates.length > 1
          ? `${task.goal || task.id} · ${index + 1}`
          : `${task.goal || task.id} · 交付物`;

      return {
        id: `${task.id}:output:${index}`,
        name:
          firstString(record, ["name", "title", "filename"]) ??
          (path ? path.split("/").filter(Boolean).at(-1) : undefined) ??
          fallbackName,
        kind: firstString(record, ["kind", "type"]) ?? task.type,
        path,
        url,
        summary: shortOutput(candidate) ?? "结构化交付结果",
        task_id: task.id,
        milestone_id: task.milestone_id,
        sourceTask: task.goal || task.id,
      };
    });
}

function SectionTitle({
  icon,
  title,
  meta,
  titleId,
}: {
  icon: ReactNode;
  title: string;
  meta?: string;
  titleId?: string;
}) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="text-muted-foreground">{icon}</span>
      <h4
        id={titleId}
        className="min-w-0 flex-1 truncate text-xs font-semibold text-foreground"
      >
        {title}
      </h4>
      {meta ? (
        <span className="shrink-0 text-[10px] text-muted-foreground">
          {meta}
        </span>
      ) : null}
    </div>
  );
}

function EmptyState({
  icon,
  title,
  description,
}: {
  icon: ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="flex min-h-48 flex-col items-center justify-center px-5 py-8 text-center">
      <div className="flex size-10 items-center justify-center rounded-xl border border-border-default bg-muted/40 text-muted-foreground">
        {icon}
      </div>
      <div className="mt-3 text-sm font-medium text-foreground">{title}</div>
      <p className="mt-1 max-w-64 text-xs leading-relaxed text-muted-foreground">
        {description}
      </p>
    </div>
  );
}

/** 拉取当前线程绑定的 Project OS 项目；未绑定项目时返回 null。 */
export function boundProjectRefetchInterval(
  data: ProjectFullState | null | undefined,
): number | false {
  // A 404 is the authoritative, normal "not a Project OS thread" state.
  // Project-binding mutations invalidate this query explicitly, so polling a
  // confirmed null only creates duplicate 404 traffic and noisy backend logs.
  return data === null ? false : 15_000;
}

export function useBoundProjectState(threadId: string | undefined | null) {
  return useQuery<ProjectFullState | null>({
    queryKey: ["project", "by-thread", threadId ?? ""],
    queryFn: async () => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/projects/by-thread/${threadId}`,
        { headers: authHeaders() },
      );
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(`Failed to load project: ${res.status}`);
      return (await res.json()) as ProjectFullState;
    },
    enabled: !!threadId,
    retry: false,
    refetchInterval: (query) => boundProjectRefetchInterval(query.state.data),
  });
}

export function ProjectOsTabLoading() {
  return (
    <div
      data-testid="project-workbench-loading"
      className="flex min-h-0 flex-1 flex-col bg-background/70"
    >
      <div className="space-y-3 border-b border-border-subtle p-3">
        <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
        <div className="h-3 w-full animate-pulse rounded bg-muted/70" />
        <div className="h-3 w-4/5 animate-pulse rounded bg-muted/70" />
      </div>
      <div className="grid grid-cols-2 gap-2 p-3">
        {[0, 1, 2, 3].map((item) => (
          <div
            key={item}
            className="h-20 animate-pulse rounded-xl border border-border-subtle bg-muted/40"
          />
        ))}
      </div>
      <div className="flex items-center justify-center gap-2 py-6 text-xs text-muted-foreground">
        <Loader2Icon className="size-3.5 animate-spin" />
        正在载入项目工作台…
      </div>
    </div>
  );
}

export function ProjectOsTabError({ onRetry }: { onRetry?: () => void }) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center bg-background/70 p-5">
      <div role="alert" className="max-w-64 text-center">
        <div className="mx-auto flex size-10 items-center justify-center rounded-xl bg-rose-500/10 text-rose-600">
          <AlertTriangleIcon className="size-5" />
        </div>
        <div className="mt-3 text-sm font-medium">项目工作台加载失败</div>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          项目数据暂时不可用，群聊与其他工作台功能不受影响。
        </p>
        {onRetry ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={onRetry}
          >
            <RefreshCwIcon className="size-3.5" />
            重试
          </Button>
        ) : null}
      </div>
    </div>
  );
}

export function ProjectOsTab({
  state,
  onRefetch,
  onOpenArtifact,
  onInvitePeople,
  rosterSeats = [],
  groupTitle,
  currentThreadTitle,
}: {
  state: ProjectFullState;
  onRefetch?: () => void | Promise<void>;
  onOpenArtifact?: (path: string) => void;
  onInvitePeople?: () => void | Promise<void>;
  rosterSeats?: WorkbenchRosterSeat[];
  /** Visible room title. Used only to avoid repeating it in the workbench. */
  groupTitle?: string | null;
  /** Conversation title fallback for non-group project threads. */
  currentThreadTitle?: string | null;
}) {
  const { project, pm } = state;
  const [activeTab, setActiveTab] = useState<ProjectWorkbenchTabId>("overview");
  const [taskFilter, setTaskFilter] = useState<
    "all" | "active" | "done" | "attention"
  >("all");
  const [pendingAction, setPendingAction] = useState<string | null>(null);

  useEffect(() => {
    setActiveTab("overview");
    setTaskFilter("all");
  }, [project.id]);

  useEffect(() => {
    const handleEntityFocus = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          projectId?: string;
          kind?: string;
        }>
      ).detail;
      if (!detail || (detail.projectId && detail.projectId !== project.id)) {
        return;
      }
      const target: Partial<Record<string, ProjectWorkbenchTabId>> = {
        milestone: "milestones",
        task: "tasks",
        artifact: "assets",
        decision: "overview",
      };
      const tab = detail.kind ? target[detail.kind] : undefined;
      if (tab) setActiveTab(tab);
    };
    window.addEventListener("echo:project-entity-focus", handleEntityFocus);
    return () => {
      window.removeEventListener(
        "echo:project-entity-focus",
        handleEntityFocus,
      );
    };
  }, [project.id]);

  const tasks = useMemo(
    () =>
      Object.entries(state.tasks ?? {}).flatMap(([milestoneId, rows]) =>
        (Array.isArray(rows) ? rows : []).map((task) => ({
          ...task,
          milestone_id: task.milestone_id || milestoneId,
        })),
      ),
    [state.tasks],
  );

  const milestones = useMemo<MilestoneView[]>(() => {
    const rawMilestones = state.milestones ?? [];
    const source = pm?.milestones?.length
      ? pm.milestones
      : rawMilestones.map((milestone): MilestonePM => {
          const milestoneTasks = tasks.filter(
            (task) => task.milestone_id === milestone.id,
          );
          const done = milestoneTasks.filter(
            (task) => task.status === "done",
          ).length;
          const total = milestoneTasks.length;
          const failed = milestoneTasks.filter((task) =>
            ATTENTION_TASK_STATUSES.has(task.status),
          ).length;
          return {
            ...milestone,
            health:
              milestone.status === "done"
                ? "completed"
                : milestone.status === "blocked" || failed > 0
                  ? "blocked"
                  : "on_track",
            done,
            total,
            failed,
            progress:
              total > 0 ? done / total : milestone.status === "done" ? 1 : 0,
            total_estimate: milestoneTasks.reduce(
              (sum, task) => sum + (Number(task.estimate) || 0),
              0,
            ),
            remaining_estimate: milestoneTasks.reduce(
              (sum, task) =>
                sum + (task.status === "done" ? 0 : Number(task.estimate) || 0),
              0,
            ),
          };
        });

    const rawById = new Map(rawMilestones.map((item) => [item.id, item]));
    return source.map((milestone) => ({
      ...rawById.get(milestone.id),
      ...milestone,
      progressPercent: normalizeProjectProgress(milestone.progress),
    }));
  }, [pm?.milestones, state.milestones, tasks]);

  const assets = useMemo<ProjectAssetView[]>(() => {
    const explicit = (state.artifacts ?? []).map((asset) => ({ ...asset }));
    const derived = tasks.flatMap(assetsFromTask);
    const seen = new Set<string>();
    return [...explicit, ...derived].filter((asset) => {
      const key = asset.path || asset.url || asset.id;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [state.artifacts, tasks]);

  const members = useMemo<ProjectMemberView[]>(() => {
    const byName = new Map<string, ProjectMemberView>();
    const add = (member: ProjectMemberView) => {
      const key = member.name.trim().toLocaleLowerCase();
      if (!key) return;
      const current = byName.get(key);
      byName.set(key, current ? { ...member, ...current } : member);
    };

    if (project.owner) {
      add({
        id: project.owner_id || `owner:${project.owner}`,
        name: project.owner,
        role: "项目负责人",
        kind: "human",
        isOwner: true,
      });
    }
    for (const seat of rosterSeats) {
      const kind = seat.kind || "agent";
      add({
        id: seat.id,
        name: seat.name,
        role:
          seat.role === "tl"
            ? "协作负责人"
            : kind === "agent" && (!seat.role || seat.role === "member")
              ? "AI 成员"
              : seat.role || "项目成员",
        kind,
        avatarUrl: seat.avatarUrl,
      });
    }
    for (const member of state.members ?? []) {
      add({
        id: member.id,
        name: member.name,
        role: member.role || "项目成员",
        kind: member.kind || "human",
        avatarUrl: member.avatar_url,
        status: member.status,
      });
    }
    return [...byName.values()].sort((left, right) => {
      if (left.isOwner !== right.isOwner) return left.isOwner ? -1 : 1;
      if (left.kind !== right.kind) return left.kind === "human" ? -1 : 1;
      return left.name.localeCompare(right.name, "zh-CN");
    });
  }, [project.owner, project.owner_id, rosterSeats, state.members]);

  const overallProgress = normalizeProjectProgress(pm?.overall_progress ?? 0);
  const doneTasks =
    pm?.done_tasks ?? tasks.filter((task) => task.status === "done").length;
  const totalTasks = pm?.total_tasks ?? tasks.length;
  const riskCount = uniqueProjectRisks(pm).length + (pm?.blockers?.length ?? 0);
  const completedMilestones = milestones.filter(
    (milestone) => milestone.health === "completed",
  ).length;
  const projectGoal = project.goal?.trim();
  const projectName = project.name?.trim();
  const normalizedProjectName = projectName?.replace(/\s+/g, " ").toLowerCase();
  const titleRepeatsConversation = Boolean(
    normalizedProjectName &&
    [groupTitle, currentThreadTitle].some(
      (title) =>
        title?.trim().replace(/\s+/g, " ").toLowerCase() ===
        normalizedProjectName,
    ),
  );
  const hasPmOverviewData = Boolean(
    pm &&
    (normalizeProjectProgress(pm.overall_progress) > 0 ||
      Number(pm.done_tasks) > 0 ||
      Number(pm.total_tasks) > 0 ||
      Number(pm.remaining_estimate) > 0 ||
      pm.milestones?.length ||
      pm.risks?.length ||
      pm.blockers?.length ||
      pm.next_actions?.length),
  );
  const isEmptyProject =
    milestones.length === 0 &&
    tasks.length === 0 &&
    assets.length === 0 &&
    projectDecisionViews(state.decisions).length === 0 &&
    !state.retro &&
    !hasPmOverviewData;

  const taskCounts = useMemo(
    () => ({
      all: tasks.length,
      active: tasks.filter((task) => ACTIVE_TASK_STATUSES.has(task.status))
        .length,
      done: tasks.filter((task) => task.status === "done").length,
      attention: tasks.filter((task) =>
        ATTENTION_TASK_STATUSES.has(task.status),
      ).length,
    }),
    [tasks],
  );

  const visibleTasks = useMemo(() => {
    if (taskFilter === "all") return tasks;
    if (taskFilter === "active") {
      return tasks.filter((task) => ACTIVE_TASK_STATUSES.has(task.status));
    }
    if (taskFilter === "attention") {
      return tasks.filter((task) => ATTENTION_TASK_STATUSES.has(task.status));
    }
    return tasks.filter((task) => task.status === "done");
  }, [taskFilter, tasks]);

  const executeAction = useCallback(
    async (spec: ProjectActionSpec, actionKey: string, task = false) => {
      if (!spec.api || pendingAction) return;
      setPendingAction(actionKey);
      try {
        const res = await fetch(spec.api.path, {
          method: spec.api.method,
          headers: jsonAuthHeaders(),
          body: spec.api.body ? JSON.stringify(spec.api.body) : undefined,
        });
        if (!res.ok) {
          toast.error("操作未完成，请稍后重试");
          return;
        }
        toast.success(`${displayActionLabel(spec, task)}已执行`);
        await onRefetch?.();
      } catch {
        toast.error("操作未完成，请检查连接后重试");
      } finally {
        setPendingAction(null);
      }
    },
    [onRefetch, pendingAction],
  );

  const tabs: Array<{
    id: ProjectWorkbenchTabId;
    label: string;
    Icon: ComponentType<{ className?: string }>;
    count?: number;
  }> = [
    { id: "overview", label: "总览", Icon: LayoutDashboardIcon },
    {
      id: "milestones",
      label: "里程碑",
      Icon: FlagIcon,
      count: milestones.length,
    },
    { id: "tasks", label: "事项", Icon: ListTodoIcon, count: tasks.length },
    { id: "assets", label: "资料", Icon: FileBoxIcon, count: assets.length },
    { id: "members", label: "成员", Icon: UsersIcon, count: members.length },
  ];

  return (
    <div
      data-testid="project-workbench"
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background/70"
    >
      <header
        data-testid="project-workbench-context"
        data-project-title-collapsed={titleRepeatsConversation}
        className={cn(
          "shrink-0 border-b border-border-subtle bg-gradient-to-br from-primary/[0.07] via-background to-background px-3",
          titleRepeatsConversation ? "py-2" : "pb-3 pt-3",
        )}
      >
        <div className="flex min-w-0 items-start gap-2.5">
          <div
            className={cn(
              "flex shrink-0 items-center justify-center border border-primary/15 bg-primary/10 text-primary shadow-[var(--shadow-xs)]",
              titleRepeatsConversation
                ? "size-7 rounded-lg"
                : "size-9 rounded-xl",
            )}
          >
            <FolderKanbanIcon
              className={titleRepeatsConversation ? "size-3.5" : "size-4.5"}
            />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <h3
                className={cn(
                  "min-w-0 flex-1 truncate font-semibold",
                  titleRepeatsConversation ? "text-xs" : "text-sm",
                )}
              >
                {titleRepeatsConversation
                  ? "项目工作台"
                  : project.name || project.id}
              </h3>
              <Badge
                variant="outline"
                className={cn(
                  "h-5 shrink-0 px-1.5 text-[10px]",
                  STATUS_TONE[project.status],
                )}
              >
                {STATUS_LABEL[project.status] ?? project.status}
              </Badge>
            </div>
            {projectGoal && projectGoal !== projectName ? (
              <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
                {projectGoal}
              </p>
            ) : null}
          </div>
        </div>
        <div
          className={cn(
            "flex items-center gap-2 text-[10px] text-muted-foreground",
            titleRepeatsConversation ? "mt-1.5" : "mt-2.5",
          )}
        >
          {project.owner ? (
            <span className="inline-flex min-w-0 items-center gap-1">
              <UserRoundIcon className="size-3" />
              <span className="truncate">负责人 {project.owner}</span>
            </span>
          ) : (
            <span>尚未设置负责人</span>
          )}
          <span
            aria-hidden="true"
            className="h-3 shrink-0 border-l border-border-default"
          />
          <span>{totalTasks} 个事项</span>
          <Link
            to="/workspace/projects"
            className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-muted-foreground transition-colors hover:bg-background/80 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            title="打开完整项目管理页"
          >
            管理页
            <ExternalLinkIcon className="size-3" />
          </Link>
        </div>
      </header>

      <div
        role="tablist"
        aria-label="项目工作台"
        className="shrink-0 overflow-x-auto border-b border-border-subtle bg-background/95 px-1.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        <div className="flex min-w-max items-center">
          {tabs.map(({ id, label, Icon, count }) => {
            const selected = activeTab === id;
            return (
              <button
                key={id}
                id={`project-workbench-tab-${id}`}
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls={`project-panel-${id}`}
                data-testid={`project-workbench-tab-${id}`}
                onClick={() => setActiveTab(id)}
                className={cn(
                  "relative flex h-10 min-w-[3.35rem] flex-1 items-center justify-center gap-1 px-1.5 text-[11px] font-medium transition-colors focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                  selected
                    ? "text-primary"
                    : "text-muted-foreground hover:bg-muted/45 hover:text-foreground",
                )}
              >
                <Icon className="size-3.5 shrink-0" />
                <span>{label}</span>
                {typeof count === "number" && count > 0 ? (
                  <span className="sr-only">{count} 项</span>
                ) : null}
                {selected ? (
                  <span className="absolute inset-x-1 bottom-0 h-0.5 rounded-full bg-primary" />
                ) : null}
              </button>
            );
          })}
        </div>
      </div>

      <div
        id={`project-panel-${activeTab}`}
        role="tabpanel"
        aria-labelledby={`project-workbench-tab-${activeTab}`}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
      >
        {activeTab === "overview" ? (
          <OverviewTab
            state={state}
            milestones={milestones}
            overallProgress={overallProgress}
            doneTasks={doneTasks}
            totalTasks={totalTasks}
            completedMilestones={completedMilestones}
            riskCount={riskCount}
            assetCount={assets.length}
            isEmptyProject={isEmptyProject}
            pendingAction={pendingAction}
            onAction={(spec, key) => executeAction(spec, key)}
            onNavigate={setActiveTab}
          />
        ) : activeTab === "milestones" ? (
          <MilestonesTab milestones={milestones} />
        ) : activeTab === "tasks" ? (
          <TasksTab
            tasks={visibleTasks}
            allTasks={tasks}
            milestones={milestones}
            filter={taskFilter}
            counts={taskCounts}
            pendingAction={pendingAction}
            onFilter={setTaskFilter}
            onAction={(spec, key) => executeAction(spec, key, true)}
          />
        ) : activeTab === "assets" ? (
          <AssetsTab assets={assets} onOpenArtifact={onOpenArtifact} />
        ) : (
          <MembersTab
            members={members}
            tasks={tasks}
            onInvitePeople={onInvitePeople}
          />
        )}
      </div>
    </div>
  );
}

function OverviewTab({
  state,
  milestones,
  overallProgress,
  doneTasks,
  totalTasks,
  completedMilestones,
  riskCount,
  assetCount,
  isEmptyProject,
  pendingAction,
  onAction,
  onNavigate,
}: {
  state: ProjectFullState;
  milestones: MilestoneView[];
  overallProgress: number;
  doneTasks: number;
  totalTasks: number;
  completedMilestones: number;
  riskCount: number;
  assetCount: number;
  isEmptyProject: boolean;
  pendingAction: string | null;
  onAction: (spec: ProjectActionSpec, key: string) => void;
  onNavigate: (tab: ProjectWorkbenchTabId) => void;
}) {
  const { project, pm, retro, action_specs: actionSpecs = [] } = state;
  const nextActions = pm?.next_actions ?? [];
  const risks = uniqueProjectRisks(pm);
  const blockers = pm?.blockers ?? [];
  const decisions = projectDecisionViews(state.decisions);
  const hasRemainingEstimate = Number(pm?.remaining_estimate) > 0;

  if (isEmptyProject) {
    return (
      <div className="p-3">
        <section
          data-testid="project-empty-launch-card"
          className="overflow-hidden rounded-2xl border border-primary/15 bg-gradient-to-br from-primary/[0.08] via-card to-card p-4 shadow-[var(--shadow-xs)]"
        >
          <div className="flex size-10 items-center justify-center rounded-xl border border-primary/15 bg-primary/10 text-primary">
            <CircleDotDashedIcon className="size-5" />
          </div>
          <h4 className="mt-3 text-sm font-semibold text-foreground">
            从第一个里程碑开始
          </h4>
          <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
            在群聊中明确项目计划，或打开管理页创建里程碑和事项；后续进展会自动汇总到这里。
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {actionSpecs.length > 0 ? (
              actionSpecs.map((spec, index) => {
                const key = `project:${spec.action}:${index}`;
                const loading = pendingAction === key;
                return (
                  <Button
                    key={key}
                    type="button"
                    size="sm"
                    variant={index === 0 ? "default" : "outline"}
                    className="h-8 rounded-lg px-2.5 text-[11px]"
                    disabled={!!pendingAction}
                    onClick={() => onAction(spec, key)}
                  >
                    {loading ? (
                      <Loader2Icon className="size-3 animate-spin" />
                    ) : spec.action.startsWith("recover") ? (
                      <RotateCcwIcon className="size-3" />
                    ) : (
                      <PlayIcon className="size-3" />
                    )}
                    {displayActionLabel(spec)}
                  </Button>
                );
              })
            ) : (
              <Button asChild size="sm" className="h-8 rounded-lg text-[11px]">
                <Link to="/workspace/projects">
                  打开项目管理
                  <ArrowRightIcon className="size-3" />
                </Link>
              </Button>
            )}
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="space-y-3 p-3">
      <section className="rounded-xl border border-border-default bg-card/70 p-3 shadow-[var(--shadow-xs)]">
        <div className="flex items-end justify-between gap-3">
          <div>
            <div className="text-[11px] font-medium text-muted-foreground">
              整体进度
            </div>
            <div className="mt-0.5 text-2xl font-semibold tracking-tight">
              {overallProgress}
              <span className="ml-0.5 text-sm font-medium text-muted-foreground">
                %
              </span>
            </div>
          </div>
          <div className="text-right text-[10px] leading-relaxed text-muted-foreground">
            <div>
              {doneTasks}/{totalTasks} 个事项完成
            </div>
            <div>
              {completedMilestones}/{milestones.length} 个里程碑达成
            </div>
          </div>
        </div>
        <Progress
          value={overallProgress}
          aria-label={`项目整体进度 ${overallProgress}%`}
          className="mt-2.5 h-1.5 rounded-full"
        />
      </section>

      <div className="grid grid-cols-2 gap-2">
        <MetricCard
          label="待推进事项"
          value={String(
            Object.values(state.tasks ?? {})
              .flat()
              .filter((task) => ACTIVE_TASK_STATUSES.has(task.status)).length,
          )}
          icon={<ListTodoIcon className="size-3.5" />}
          onClick={() => onNavigate("tasks")}
        />
        <MetricCard
          label="风险与阻塞"
          value={String(riskCount)}
          icon={<AlertTriangleIcon className="size-3.5" />}
          tone={riskCount > 0 ? "warning" : "default"}
        />
        <MetricCard
          label={hasRemainingEstimate ? "剩余估时" : "项目资料"}
          value={
            hasRemainingEstimate
              ? formatEstimate(pm?.remaining_estimate)
              : String(assetCount)
          }
          icon={
            hasRemainingEstimate ? (
              <TimerIcon className="size-3.5" />
            ) : (
              <FileBoxIcon className="size-3.5" />
            )
          }
          onClick={
            hasRemainingEstimate ? undefined : () => onNavigate("assets")
          }
        />
        <MetricCard
          label="下一步"
          value={String(nextActions.length)}
          icon={<ListChecksIcon className="size-3.5" />}
        />
      </div>

      {decisions.length > 0 ? (
        <section
          className="space-y-2"
          aria-labelledby="project-decisions-title"
        >
          <SectionTitle
            icon={<CheckCircle2Icon className="size-3.5" />}
            title="项目决策"
            meta={`${decisions.length} 条沉淀`}
            titleId="project-decisions-title"
          />
          <div className="space-y-1.5">
            {decisions.slice(0, 3).map((decision) => {
              const meta = [
                decision.actor ? `记录者 ${decision.actor}` : undefined,
                fmtDecisionTime(decision.createdAt),
                decision.milestoneId
                  ? `里程碑 ${decision.milestoneId}`
                  : undefined,
              ].filter((item): item is string => !!item);
              return (
                <article
                  key={decision.id}
                  className="rounded-lg border border-sky-500/15 bg-sky-500/[0.045] px-2.5 py-2"
                >
                  <h5 className="text-[11px] font-medium leading-relaxed text-foreground">
                    {decision.title}
                  </h5>
                  {decision.body ? (
                    <p className="mt-1 line-clamp-3 text-[10px] leading-relaxed text-muted-foreground">
                      {decision.body}
                    </p>
                  ) : null}
                  {meta.length > 0 ? (
                    <div className="mt-1.5 flex flex-wrap gap-x-2 gap-y-0.5 text-[9px] text-muted-foreground/85">
                      {meta.map((item) => (
                        <span key={item}>{item}</span>
                      ))}
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        </section>
      ) : null}

      {actionSpecs.length > 0 ? (
        <section className="space-y-2">
          <SectionTitle
            icon={<PlayIcon className="size-3.5" />}
            title="项目操作"
          />
          <div className="flex flex-wrap gap-1.5">
            {actionSpecs.map((spec, index) => {
              const key = `project:${spec.action}:${index}`;
              const loading = pendingAction === key;
              return (
                <Button
                  key={key}
                  type="button"
                  size="sm"
                  variant={index === 0 ? "default" : "outline"}
                  className="h-7 rounded-md px-2 text-[11px]"
                  disabled={!!pendingAction}
                  onClick={() => onAction(spec, key)}
                >
                  {loading ? (
                    <Loader2Icon className="size-3 animate-spin" />
                  ) : spec.action.startsWith("recover") ? (
                    <RotateCcwIcon className="size-3" />
                  ) : (
                    <PlayIcon className="size-3" />
                  )}
                  {displayActionLabel(spec)}
                </Button>
              );
            })}
          </div>
        </section>
      ) : null}

      {nextActions.length > 0 ? (
        <section className="space-y-2">
          <SectionTitle
            icon={<ListChecksIcon className="size-3.5" />}
            title="接下来做什么"
            meta={`${nextActions.length} 项可推进`}
          />
          <div className="space-y-1.5">
            {nextActions.slice(0, 4).map((action) => (
              <button
                type="button"
                key={action.task_id}
                onClick={() => onNavigate("tasks")}
                className="group flex w-full items-start gap-2 rounded-lg border border-border-subtle bg-card/50 px-2.5 py-2 text-left transition-colors hover:border-border-default hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Badge
                  variant="outline"
                  className={cn(
                    "mt-px h-4 px-1 text-[9px]",
                    PRIORITY_TONE[action.priority],
                  )}
                >
                  {action.priority}
                </Badge>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[11px] font-medium">
                    {action.task}
                  </span>
                  <span className="mt-0.5 block truncate text-[10px] text-muted-foreground">
                    {action.milestone || "未关联里程碑"}
                    {action.due_at ? ` · ${fmtDate(action.due_at)} 截止` : ""}
                  </span>
                </span>
                <ArrowRightIcon className="mt-1 size-3 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
              </button>
            ))}
          </div>
        </section>
      ) : !pm && project.status === "planning" ? (
        <div className="rounded-xl border border-dashed border-border-default bg-muted/25 px-3 py-4 text-center">
          <CircleDotDashedIcon className="mx-auto size-5 text-muted-foreground" />
          <div className="mt-2 text-xs font-medium">项目正在等待拆解</div>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            开始推进后，这里会出现里程碑、下一步事项和风险提醒。
          </p>
        </div>
      ) : null}

      {risks.length > 0 || blockers.length > 0 ? (
        <section className="space-y-2">
          <SectionTitle
            icon={<AlertTriangleIcon className="size-3.5" />}
            title="需要关注"
            meta={`${risks.length + blockers.length} 条`}
          />
          <div className="space-y-1.5">
            {blockers.map((blocker, index) => (
              <div
                key={`blocker:${index}`}
                className="flex items-start gap-2 rounded-lg border border-rose-500/15 bg-rose-500/[0.06] px-2.5 py-2 text-[11px] text-rose-700 dark:text-rose-300"
              >
                <XCircleIcon className="mt-px size-3.5 shrink-0" />
                <span className="min-w-0 flex-1">{blocker}</span>
              </div>
            ))}
            {risks.slice(0, 4).map((risk, index) => (
              <div
                key={`risk:${risk.task_id ?? risk.milestone ?? index}`}
                className="flex items-start gap-2 rounded-lg border border-amber-500/15 bg-amber-500/[0.06] px-2.5 py-2 text-[11px] text-amber-800 dark:text-amber-300"
              >
                <AlertTriangleIcon className="mt-px size-3.5 shrink-0" />
                <span className="min-w-0 flex-1">{safeRiskDetail(risk)}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {retro ? (
        <section className="space-y-2 rounded-xl border border-sky-500/20 bg-sky-500/[0.05] p-3">
          <SectionTitle
            icon={<ClipboardListIcon className="size-3.5 text-sky-600" />}
            title="项目复盘"
            meta={
              retro.duration_days == null
                ? undefined
                : `${retro.duration_days} 天`
            }
          />
          <div className="grid grid-cols-3 gap-1.5 text-center">
            <RetroMetric
              label="交付"
              value={`${retro.done_tasks}/${retro.task_count}`}
            />
            <RetroMetric label="失败" value={String(retro.failed_tasks)} />
            <RetroMetric label="尝试" value={String(retro.attempts_total)} />
          </div>
          {retro.recommendations.length > 0 ? (
            <ul className="space-y-1.5 pt-0.5">
              {retro.recommendations.map((recommendation, index) => (
                <li
                  key={`${recommendation}:${index}`}
                  className="flex items-start gap-1.5 text-[11px] leading-relaxed text-muted-foreground"
                >
                  <ArrowRightIcon className="mt-0.5 size-3 shrink-0 text-sky-600" />
                  <span>{recommendation}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function MetricCard({
  label,
  value,
  icon,
  tone = "default",
  onClick,
}: {
  label: string;
  value: string;
  icon: ReactNode;
  tone?: "default" | "warning";
  onClick?: () => void;
}) {
  const Comp = onClick ? "button" : "div";
  return (
    <Comp
      {...(onClick ? { type: "button" as const, onClick } : {})}
      className={cn(
        "flex min-w-0 items-center gap-2 rounded-xl border bg-card/60 p-2.5 text-left shadow-[var(--shadow-xs)]",
        tone === "warning" && "border-amber-500/20 bg-amber-500/[0.05]",
        onClick &&
          "transition-colors hover:bg-muted/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      <span
        className={cn(
          "flex size-7 shrink-0 items-center justify-center rounded-lg bg-muted/70 text-muted-foreground",
          tone === "warning" && "bg-amber-500/10 text-amber-700",
        )}
      >
        {icon}
      </span>
      <span className="min-w-0">
        <span className="block truncate text-[10px] text-muted-foreground">
          {label}
        </span>
        <span className="mt-0.5 block truncate text-sm font-semibold">
          {value}
        </span>
      </span>
    </Comp>
  );
}

function RetroMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-background/70 px-1.5 py-2">
      <div className="text-xs font-semibold">{value}</div>
      <div className="mt-0.5 text-[9px] text-muted-foreground">{label}</div>
    </div>
  );
}

function MilestonesTab({ milestones }: { milestones: MilestoneView[] }) {
  if (milestones.length === 0) {
    return (
      <EmptyState
        icon={<FlagIcon className="size-5" />}
        title="还没有里程碑"
        description="项目开始推进后，拆解出的阶段目标会显示在这里。"
      />
    );
  }

  return (
    <div className="space-y-2.5 p-3">
      <SectionTitle
        icon={<FlagIcon className="size-3.5" />}
        title="里程碑计划"
        meta={`${milestones.length} 个阶段`}
      />
      {milestones.map((milestone, index) => (
        <article
          key={milestone.id}
          className="rounded-xl border border-border-default bg-card/60 p-3 shadow-[var(--shadow-xs)]"
        >
          <div className="flex min-w-0 items-start gap-2.5">
            <div className="relative mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border border-border-default bg-background text-[10px] font-semibold text-muted-foreground">
              {milestone.health === "completed" ? (
                <CheckCircle2Icon className="size-3.5 text-sky-600" />
              ) : (
                index + 1
              )}
              <span
                className={cn(
                  "absolute -right-0.5 -top-0.5 size-2 rounded-full border-2 border-card",
                  HEALTH_DOT[milestone.health],
                )}
              />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 items-center gap-1.5">
                <h5 className="min-w-0 flex-1 truncate text-xs font-semibold">
                  {milestone.name}
                </h5>
                <Badge
                  variant="outline"
                  className={cn(
                    "h-4 px-1.5 text-[9px]",
                    HEALTH_TONE[milestone.health],
                  )}
                >
                  {HEALTH_LABEL[milestone.health]}
                </Badge>
              </div>
              {milestone.goal ? (
                <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-muted-foreground">
                  {milestone.goal}
                </p>
              ) : null}
            </div>
          </div>
          <div className="mt-3 flex items-center gap-2">
            <Progress
              value={milestone.progressPercent}
              aria-label={`${milestone.name}进度 ${milestone.progressPercent}%`}
              className="h-1.5 rounded-full"
            />
            <span className="w-8 shrink-0 text-right text-[10px] font-medium text-muted-foreground">
              {milestone.progressPercent}%
            </span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
            <span>
              {milestone.total > 0
                ? `${milestone.done}/${milestone.total} 个事项`
                : "事项待拆解"}
            </span>
            {milestone.failed > 0 ? (
              <span className="text-rose-600">{milestone.failed} 个异常</span>
            ) : null}
            {milestone.due_at ? (
              <span className="inline-flex items-center gap-1">
                <CalendarDaysIcon className="size-3" />
                {fmtDate(milestone.due_at)} 截止
              </span>
            ) : null}
            {(milestone.remaining_estimate ?? 0) > 0 ? (
              <span>剩余 {formatEstimate(milestone.remaining_estimate)}</span>
            ) : null}
          </div>
          {(milestone.success_criteria?.length ?? 0) > 0 ? (
            <div className="mt-2.5 border-t border-border-subtle pt-2">
              <div className="text-[9px] font-medium uppercase tracking-wide text-muted-foreground">
                达成标准
              </div>
              <ul className="mt-1 space-y-1">
                {milestone.success_criteria
                  ?.slice(0, 3)
                  .map((criterion, criterionIndex) => (
                    <li
                      key={`${criterion}:${criterionIndex}`}
                      className="flex items-start gap-1.5 text-[10px] leading-relaxed text-muted-foreground"
                    >
                      <CheckCircle2Icon className="mt-0.5 size-3 shrink-0 text-emerald-600/70" />
                      <span>{criterion}</span>
                    </li>
                  ))}
              </ul>
            </div>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function TasksTab({
  tasks,
  allTasks,
  milestones,
  filter,
  counts,
  pendingAction,
  onFilter,
  onAction,
}: {
  tasks: ProjectTaskReadModel[];
  allTasks: ProjectTaskReadModel[];
  milestones: MilestoneView[];
  filter: "all" | "active" | "done" | "attention";
  counts: Record<"all" | "active" | "done" | "attention", number>;
  pendingAction: string | null;
  onFilter: (filter: "all" | "active" | "done" | "attention") => void;
  onAction: (spec: ProjectActionSpec, key: string) => void;
}) {
  const milestoneNames = new Map(
    milestones.map((milestone) => [milestone.id, milestone.name]),
  );
  const filters: Array<{
    id: "all" | "active" | "done" | "attention";
    label: string;
  }> = [
    { id: "all", label: "全部" },
    { id: "active", label: "进行中" },
    { id: "done", label: "已完成" },
    { id: "attention", label: "需关注" },
  ];

  return (
    <div className="flex min-h-full flex-col">
      <div className="sticky top-0 z-10 border-b border-border-subtle bg-background/95 px-3 py-2 backdrop-blur">
        <div className="flex gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {filters.map((item) => (
            <button
              type="button"
              key={item.id}
              aria-pressed={filter === item.id}
              onClick={() => onFilter(item.id)}
              className={cn(
                "inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-[10px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                filter === item.id
                  ? "bg-foreground/10 text-foreground"
                  : "text-muted-foreground hover:bg-muted/55 hover:text-foreground",
              )}
            >
              {item.label}
              <span className="text-[9px] opacity-70">{counts[item.id]}</span>
            </button>
          ))}
        </div>
      </div>

      {allTasks.length === 0 ? (
        <EmptyState
          icon={<ListTodoIcon className="size-5" />}
          title="还没有事项"
          description="里程碑拆解出的人员与 AI 工作项会统一显示在这里。"
        />
      ) : tasks.length === 0 ? (
        <EmptyState
          icon={<ListChecksIcon className="size-5" />}
          title="这个筛选下没有事项"
          description="切换筛选条件，查看项目中的其他工作项。"
        />
      ) : (
        <div className="space-y-2 p-3">
          {tasks.map((task) => {
            const availableActions = (task.action_specs ?? []).filter(
              (spec) => (spec.requires?.length ?? 0) === 0,
            );
            return (
              <article
                key={task.id}
                className="rounded-xl border border-border-default bg-card/60 p-3 shadow-[var(--shadow-xs)]"
              >
                <div className="flex min-w-0 items-start gap-2">
                  <span
                    className={cn(
                      "mt-1 size-2 shrink-0 rounded-full",
                      task.status === "done"
                        ? "bg-sky-500"
                        : task.status === "running"
                          ? "animate-pulse bg-emerald-500"
                          : ATTENTION_TASK_STATUSES.has(task.status)
                            ? "bg-rose-500"
                            : "bg-muted-foreground/45",
                    )}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 items-start gap-1.5">
                      <h5 className="min-w-0 flex-1 text-xs font-medium leading-relaxed">
                        {task.goal || task.id}
                      </h5>
                      <Badge
                        variant="outline"
                        className={cn(
                          "h-4 px-1.5 text-[9px]",
                          STATUS_TONE[task.status],
                        )}
                      >
                        {STATUS_LABEL[task.status] ?? task.status}
                      </Badge>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[9px] text-muted-foreground">
                      <Badge
                        variant="outline"
                        className={cn(
                          "h-4 px-1 text-[8px]",
                          PRIORITY_TONE[task.priority],
                        )}
                      >
                        {task.priority || "P2"}
                      </Badge>
                      <span className="truncate">
                        {milestoneNames.get(task.milestone_id) ||
                          "未关联里程碑"}
                      </span>
                      {task.estimate > 0 ? (
                        <span>{formatEstimate(task.estimate)}</span>
                      ) : null}
                      {task.due_at ? (
                        <span>{fmtDate(task.due_at)} 截止</span>
                      ) : null}
                    </div>
                    <div className="mt-2 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                      {task.assigned_agent ? (
                        <BotIcon className="size-3" />
                      ) : (
                        <UserRoundIcon className="size-3" />
                      )}
                      <span className="truncate">
                        {task.assigned_agent || task.assigned_role || "待指派"}
                      </span>
                      {task.team_mode && task.team_mode !== "single" ? (
                        <span className="rounded bg-muted px-1 py-px text-[8px] uppercase">
                          {task.team_mode}
                        </span>
                      ) : null}
                    </div>
                    {(task.acceptance_criteria?.length ?? 0) > 0 ? (
                      <div className="mt-2 line-clamp-2 text-[10px] leading-relaxed text-muted-foreground">
                        完成标准：{task.acceptance_criteria.join("；")}
                      </div>
                    ) : null}
                    {availableActions.length > 0 ? (
                      <div className="mt-2.5 flex flex-wrap gap-1.5 border-t border-border-subtle pt-2">
                        {availableActions
                          .slice(0, 3)
                          .map((spec, actionIndex) => {
                            const key = `task:${task.id}:${spec.action}:${actionIndex}`;
                            const loading = pendingAction === key;
                            return (
                              <Button
                                key={key}
                                type="button"
                                size="sm"
                                variant="outline"
                                className="h-6 rounded-md px-2 text-[9px]"
                                disabled={!!pendingAction}
                                onClick={() => onAction(spec, key)}
                              >
                                {loading ? (
                                  <Loader2Icon className="size-3 animate-spin" />
                                ) : (
                                  <ArrowRightIcon className="size-3" />
                                )}
                                {displayActionLabel(spec, true)}
                              </Button>
                            );
                          })}
                      </div>
                    ) : null}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}

function AssetsTab({
  assets,
  onOpenArtifact,
}: {
  assets: ProjectAssetView[];
  onOpenArtifact?: (path: string) => void;
}) {
  if (assets.length === 0) {
    return (
      <EmptyState
        icon={<PackageOpenIcon className="size-5" />}
        title="还没有项目资料"
        description="事项完成后产生的文档、文件和链接会自动汇总到这里。"
      />
    );
  }

  return (
    <div className="space-y-2.5 p-3">
      <SectionTitle
        icon={<FileBoxIcon className="size-3.5" />}
        title="项目资料"
        meta={`${assets.length} 项`}
      />
      {assets.map((asset) => {
        const path = asset.path;
        const canOpenInWorkbench = !!path && !!onOpenArtifact;
        const card = (
          <>
            <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/[0.08] text-primary">
              <FileTextIcon className="size-4" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 items-center gap-1.5">
                <h5 className="min-w-0 flex-1 truncate text-xs font-medium">
                  {asset.name}
                </h5>
                {asset.kind ? (
                  <Badge
                    variant="outline"
                    className="h-4 px-1 text-[8px] text-muted-foreground"
                  >
                    {asset.kind}
                  </Badge>
                ) : null}
              </div>
              {asset.summary ? (
                <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-muted-foreground">
                  {asset.summary}
                </p>
              ) : null}
              <div className="mt-1.5 flex min-w-0 items-center gap-1 text-[9px] text-muted-foreground">
                {asset.sourceTask ? (
                  <span className="min-w-0 truncate">
                    来自：{asset.sourceTask}
                  </span>
                ) : (
                  <span>项目资料</span>
                )}
                {asset.path || asset.url ? (
                  <ExternalLinkIcon className="ml-auto size-3 shrink-0" />
                ) : null}
              </div>
            </div>
          </>
        );

        if (isExternalUrl(asset.url)) {
          return (
            <RoutedWebLink
              key={asset.id}
              href={asset.url}
              openTargetSource="project-asset"
              className="flex w-full items-start gap-2.5 rounded-xl border border-border-default bg-card/60 p-3 text-left shadow-[var(--shadow-xs)] transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {card}
            </RoutedWebLink>
          );
        }
        return (
          <button
            key={asset.id}
            type="button"
            disabled={!canOpenInWorkbench}
            onClick={() => {
              if (path) onOpenArtifact?.(path);
            }}
            className={cn(
              "flex w-full items-start gap-2.5 rounded-xl border border-border-default bg-card/60 p-3 text-left shadow-[var(--shadow-xs)]",
              canOpenInWorkbench &&
                "transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              !canOpenInWorkbench && "cursor-default",
            )}
          >
            {card}
          </button>
        );
      })}
    </div>
  );
}

function MembersTab({
  members,
  tasks,
  onInvitePeople,
}: {
  members: ProjectMemberView[];
  tasks: ProjectTaskReadModel[];
  onInvitePeople?: () => void | Promise<void>;
}) {
  return (
    <div className="space-y-2.5 p-3">
      <div className="flex min-w-0 items-center gap-2">
        <div className="min-w-0 flex-1">
          <SectionTitle
            icon={<UsersIcon className="size-3.5" />}
            title="项目成员"
            meta={`${members.length} 位`}
          />
        </div>
        {onInvitePeople ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 shrink-0 gap-1.5 rounded-lg px-2 text-[10px]"
            onClick={() => void onInvitePeople()}
          >
            <UserPlusIcon className="size-3.5" />
            邀请真人
          </Button>
        ) : null}
      </div>
      {members.length === 0 ? (
        <EmptyState
          icon={<UsersIcon className="size-5" />}
          title="还没有项目成员"
          description="成员加入工作群或被加入项目后，会统一显示在这里。"
        />
      ) : null}
      {members.map((member) => {
        const ownedTasks = tasks.filter(
          (task) =>
            task.assigned_agent === member.name ||
            (!task.assigned_agent && task.assigned_role === member.name),
        );
        const activeCount = ownedTasks.filter((task) =>
          ACTIVE_TASK_STATUSES.has(task.status),
        ).length;
        const doneCount = ownedTasks.filter(
          (task) => task.status === "done",
        ).length;
        return (
          <article
            key={member.id}
            className="flex items-center gap-2.5 rounded-xl border border-border-default bg-card/60 p-2.5 shadow-[var(--shadow-xs)]"
          >
            <Avatar className="size-9 rounded-xl border border-border-subtle">
              {member.avatarUrl ? (
                <AvatarImage src={member.avatarUrl} alt={member.name} />
              ) : null}
              <AvatarFallback
                className={cn(
                  "rounded-xl text-xs font-semibold",
                  member.kind === "agent"
                    ? "bg-violet-500/10 text-violet-600"
                    : member.kind === "role"
                      ? "bg-amber-500/10 text-amber-700"
                      : "bg-sky-500/10 text-sky-600",
                )}
              >
                {member.kind === "agent" ? (
                  <BotIcon className="size-4" />
                ) : member.kind === "role" ? (
                  <UsersIcon className="size-4" />
                ) : (
                  member.name.charAt(0).toLocaleUpperCase()
                )}
              </AvatarFallback>
            </Avatar>
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 items-center gap-1.5">
                <h5 className="min-w-0 truncate text-xs font-medium">
                  {member.name}
                </h5>
                {member.isOwner ? (
                  <Badge className="h-4 bg-primary/10 px-1 text-[8px] text-primary">
                    负责人
                  </Badge>
                ) : null}
              </div>
              <div className="mt-0.5 truncate text-[10px] text-muted-foreground">
                {member.role}
                {member.status ? ` · ${member.status}` : ""}
              </div>
            </div>
            <div className="shrink-0 text-right text-[9px] leading-relaxed text-muted-foreground">
              {ownedTasks.length > 0 ? (
                <>
                  <div>{activeCount} 项进行中</div>
                  <div>{doneCount} 项已完成</div>
                </>
              ) : (
                <div>暂无事项</div>
              )}
            </div>
          </article>
        );
      })}
    </div>
  );
}
