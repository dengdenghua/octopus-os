import { authHeader } from "./auth";
import { approvalHeader } from "./approval";

export type OrganizationEntry = {
  entryId: string;
  source: string;
  target: string | null;
  status:
    | "ready"
    | "needs_review"
    | "already_organized"
    | "conflict"
    | "unsupported";
  reason: string | null;
  date: string | null;
  title: string;
  amount: string | null;
  currency: string | null;
  evidence: Record<string, unknown>;
};

export type OrganizationOutcome = {
  entryId: string;
  source: string;
  target: string | null;
  status: "moved" | "conflict" | "failed" | "pending" | "skipped" | "uncertain";
  committed: boolean | null;
  reason: string | null;
  recoveryPaths: string[];
  actualPath: string | null;
  sha256?: string;
};

export type OrganizationResult = {
  schema: "echo.files.organize.result.v1";
  planId: string;
  operationId: string;
  taskId: string | null;
  direction: "apply" | "undo";
  state:
    | "completed"
    | "partial"
    | "cancelled"
    | "failed"
    | "uncertain"
    | "running";
  executionComplete: boolean;
  finalizationPending?: boolean;
  auditRecorded?: boolean;
  taskRecorded?: boolean;
  receiptRecorded?: boolean;
  counts: {
    moved: number;
    conflicts: number;
    failed: number;
    pending: number;
    uncertain: number;
    skipped: number;
  };
  results: OrganizationOutcome[];
  reviewCount: number;
};

export type OrganizationPlan = {
  schema: "echo.files.organize.plan.v1";
  planId: string;
  path: string;
  createdAt: string | number;
  expiresAt: string | number;
  direction: "apply" | "undo";
  sourcePlanId?: string;
  scanComplete: boolean;
  ready: boolean;
  requiresApproval: true;
  approval: {
    action: "files.organize.apply" | "files.organize.undo";
    target: string;
  };
  entries: OrganizationEntry[];
  summary: {
    scanned: number;
    ready: number;
    needsReview: number;
    alreadyOrganized: number;
    conflicts: number;
    unsupported: number;
  };
  blockers: string[];
  result?: OrganizationResult;
};

export type OrganizationPlanSummary = Pick<
  OrganizationPlan,
  | "planId"
  | "path"
  | "direction"
  | "sourcePlanId"
  | "createdAt"
  | "expiresAt"
  | "ready"
  | "summary"
> & { state: OrganizationResult["state"] | null };
export type OrganizationPlanList = {
  schema: "echo.files.organize.plans.v1";
  path: string;
  complete: boolean;
  plans: OrganizationPlanSummary[];
};

export class OrganizationApiError extends Error {
  constructor(
    message: string,
    public status = 0,
    public code = "",
  ) {
    super(message);
    this.name = "OrganizationApiError";
  }
}

const BASE = "/api/appliance/files/organize/plans";
export const isOrganizationPlanId = (id: unknown): id is string =>
  typeof id === "string" && /^[a-f0-9]{64}$/.test(id);

function planUrl(id: string) {
  if (!isOrganizationPlanId(id))
    throw new OrganizationApiError("整理计划标识无效，请重新预览");
  return `${BASE}/${id}`;
}

async function request(url: string, init: RequestInit = {}): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: {
        ...authHeader(),
        "Content-Type": "application/json",
        ...init.headers,
      },
    });
  } catch {
    throw new OrganizationApiError(
      "连接中断，请刷新结果以确认是否已有文件完成移动。",
    );
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    const code = typeof detail?.error === "string" ? detail.error : "";
    const message =
      response.status === 401
        ? "登录已失效，请重新登录后查看结果"
        : response.status === 403
          ? "当前账户无权执行此操作，请重新检查目录权限"
          : typeof detail?.message === "string"
            ? detail.message
            : typeof detail === "string" && detail !== "Not Found"
              ? detail
              : response.status === 404
                ? "整理计划不存在或整理服务尚未启用"
                : response.status === 409
                  ? "文件或计划状态已变化，请刷新预览或结果后重试"
                  : "整理服务暂时不可用，请稍后刷新结果";
    throw new OrganizationApiError(message, response.status, code);
  }
  return payload;
}

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const counts = (value: unknown, keys: string[]) =>
  object(value) &&
  keys.every(
    (key) => Number.isInteger(value[key]) && (value[key] as number) >= 0,
  );
const strings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === "string");
const nullableString = (value: unknown) =>
  value === null || typeof value === "string";
const timestamp = (value: unknown) =>
  (typeof value === "string" && value.length > 0) ||
  (typeof value === "number" && Number.isFinite(value));

function parseResult(value: unknown, expectedId?: string): OrganizationResult {
  if (
    !object(value) ||
    value.schema !== "echo.files.organize.result.v1" ||
    !isOrganizationPlanId(value.planId) ||
    (expectedId && value.planId !== expectedId) ||
    value.operationId !== value.planId ||
    !["apply", "undo"].includes(value.direction as string) ||
    ![
      "completed",
      "partial",
      "cancelled",
      "failed",
      "uncertain",
      "running",
    ].includes(value.state as string) ||
    typeof value.executionComplete !== "boolean" ||
    ![
      "finalizationPending",
      "auditRecorded",
      "taskRecorded",
      "receiptRecorded",
    ].every(
      (key) => value[key] === undefined || typeof value[key] === "boolean",
    ) ||
    !nullableString(value.taskId) ||
    !counts(value.counts, [
      "moved",
      "conflicts",
      "failed",
      "pending",
      "uncertain",
      "skipped",
    ]) ||
    !Number.isInteger(value.reviewCount) ||
    (value.reviewCount as number) < 0 ||
    !Array.isArray(value.results) ||
    !value.results.every(
      (row) =>
        object(row) &&
        typeof row.entryId === "string" &&
        typeof row.source === "string" &&
        nullableString(row.target) &&
        nullableString(row.actualPath) &&
        nullableString(row.reason) &&
        strings(row.recoveryPaths) &&
        [true, false, null].includes(row.committed as boolean | null) &&
        [
          "moved",
          "conflict",
          "failed",
          "pending",
          "skipped",
          "uncertain",
        ].includes(row.status as string),
    )
  ) {
    throw new OrganizationApiError("整理服务返回的结果无法核实，请刷新结果");
  }
  return value as OrganizationResult;
}

function parsePlan(value: unknown, expectedId?: string): OrganizationPlan {
  if (
    !object(value) ||
    value.schema !== "echo.files.organize.plan.v1" ||
    !isOrganizationPlanId(value.planId) ||
    (expectedId && value.planId !== expectedId) ||
    typeof value.path !== "string" ||
    !timestamp(value.createdAt) ||
    !timestamp(value.expiresAt) ||
    !["apply", "undo"].includes(value.direction as string) ||
    typeof value.ready !== "boolean" ||
    typeof value.scanComplete !== "boolean" ||
    value.requiresApproval !== true ||
    !object(value.approval) ||
    value.approval.target !== value.planId ||
    value.approval.action !== `files.organize.${value.direction}` ||
    !counts(value.summary, [
      "scanned",
      "ready",
      "needsReview",
      "alreadyOrganized",
      "conflicts",
      "unsupported",
    ]) ||
    !strings(value.blockers) ||
    !Array.isArray(value.entries) ||
    !value.entries.every(
      (row) =>
        object(row) &&
        typeof row.entryId === "string" &&
        typeof row.source === "string" &&
        nullableString(row.target) &&
        nullableString(row.reason) &&
        nullableString(row.date) &&
        typeof row.title === "string" &&
        nullableString(row.amount) &&
        nullableString(row.currency) &&
        object(row.evidence) &&
        [
          "ready",
          "needs_review",
          "already_organized",
          "conflict",
          "unsupported",
        ].includes(row.status as string),
    )
  ) {
    throw new OrganizationApiError("整理计划无法核实，请重新预览后再确认");
  }
  if (
    value.result !== undefined &&
    parseResult(value.result, value.planId).direction !== value.direction
  ) {
    throw new OrganizationApiError("整理计划与执行记录不一致，请重新读取结果");
  }
  return value as OrganizationPlan;
}

export async function createOrganizationPlan(
  path: string,
): Promise<OrganizationPlan> {
  const plan = parsePlan(
    await request(BASE, { method: "POST", body: JSON.stringify({ path }) }),
  );
  if (plan.path !== path || plan.direction !== "apply")
    throw new OrganizationApiError(
      "返回的整理目录与当前选择不一致，请重新预览",
    );
  return plan;
}
export async function fetchOrganizationPlan(id: string) {
  return parsePlan(await request(planUrl(id)), id);
}
export async function fetchOrganizationPlans(
  path: string,
): Promise<OrganizationPlanList> {
  const value = await request(`${BASE}?${new URLSearchParams({ path })}`);
  if (
    !object(value) ||
    value.schema !== "echo.files.organize.plans.v1" ||
    value.path !== path ||
    typeof value.complete !== "boolean" ||
    !Array.isArray(value.plans) ||
    value.plans.length > 20 ||
    !value.plans.every(
      (plan) =>
        object(plan) &&
        isOrganizationPlanId(plan.planId) &&
        plan.path === path &&
        ["apply", "undo"].includes(plan.direction as string) &&
        timestamp(plan.createdAt) &&
        timestamp(plan.expiresAt) &&
        typeof plan.ready === "boolean" &&
        counts(plan.summary, [
          "scanned",
          "ready",
          "needsReview",
          "alreadyOrganized",
          "conflicts",
          "unsupported",
        ]) &&
        (plan.state === null ||
          [
            "completed",
            "partial",
            "cancelled",
            "failed",
            "uncertain",
            "running",
          ].includes(plan.state as string)),
    )
  ) {
    throw new OrganizationApiError(
      "最近整理计划无法核实，请刷新或使用计划编号查找",
    );
  }
  return value as OrganizationPlanList;
}
export async function fetchOrganizationResult(id: string) {
  return parseResult(await request(`${planUrl(id)}/result`), id);
}
export async function applyOrganizationPlan(id: string, approvalToken: string) {
  return parseResult(
    await request(`${planUrl(id)}/apply`, {
      method: "POST",
      body: "{}",
      headers: approvalHeader(approvalToken),
    }),
    id,
  );
}
export async function cancelOrganizationPlan(id: string) {
  return parseResult(
    await request(`${planUrl(id)}/cancel`, { method: "POST", body: "{}" }),
    id,
  );
}
export async function createOrganizationUndoPlan(id: string) {
  const plan = parsePlan(
    await request(`${planUrl(id)}/undo-plan`, { method: "POST", body: "{}" }),
  );
  if (plan.direction !== "undo" || plan.sourcePlanId !== id)
    throw new OrganizationApiError("撤销计划与当前操作不一致，请重新预览");
  return plan;
}

export function canApplyOrganizationPlan(
  plan: OrganizationPlan | null,
): boolean {
  return (
    !!plan &&
    plan.ready &&
    plan.scanComplete &&
    !plan.blockers.length &&
    plan.summary.ready > 0 &&
    plan.entries.some(
      (row) =>
        row.status === "ready" &&
        typeof row.target === "string" &&
        row.target.length > 0,
    )
  );
}
