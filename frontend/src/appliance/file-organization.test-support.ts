import type {
  OrganizationPlan,
  OrganizationPlanList,
  OrganizationResult,
} from "./file-organization";

export const planId = "a".repeat(64);
export const undoId = "b".repeat(64);
export const baseUrl = "/api/appliance/files/organize/plans";

export function organizationPlanList(
  plans: OrganizationPlan[] = [],
  overrides: Partial<OrganizationPlanList> = {},
): OrganizationPlanList {
  return {
    schema: "echo.files.organize.plans.v1",
    path: "receipts",
    complete: true,
    plans: plans.map(
      ({
        planId,
        path,
        direction,
        sourcePlanId,
        createdAt,
        expiresAt,
        ready,
        summary,
        result,
      }) => ({
        planId,
        path,
        direction,
        sourcePlanId,
        createdAt,
        expiresAt,
        ready,
        summary,
        state: result?.state ?? null,
      }),
    ),
    ...overrides,
  };
}

export function organizationPlan(
  overrides: Partial<OrganizationPlan> = {},
): OrganizationPlan {
  return {
    schema: "echo.files.organize.plan.v1",
    planId,
    path: "receipts",
    createdAt: "2026-09-05T10:00:00Z",
    expiresAt: "2026-09-05T10:10:00Z",
    direction: "apply",
    scanComplete: true,
    ready: true,
    requiresApproval: true,
    approval: { action: "files.organize.apply", target: planId },
    entries: [
      {
        entryId: "first",
        source: "receipts/invoice.txt",
        target: "receipts/2026/09/invoice.txt",
        status: "ready",
        reason: null,
        date: "2026-09-01",
        title: "invoice",
        amount: "128.50",
        currency: "CNY",
        evidence: {
          dateCandidates: [
            {
              label: "开票日期",
              value: "2026-09-01",
              snippet: "开票日期：2026年9月1日",
              line: 1,
              reason: null,
            },
          ],
        },
      },
      {
        entryId: "review",
        source: "receipts/unknown.pdf",
        target: null,
        status: "needs_review",
        reason: "no_text",
        date: null,
        title: "unknown",
        amount: null,
        currency: null,
        evidence: {},
      },
    ],
    summary: {
      scanned: 2,
      ready: 1,
      needsReview: 1,
      alreadyOrganized: 0,
      conflicts: 0,
      unsupported: 0,
    },
    blockers: [],
    ...overrides,
  };
}

export function organizationResult(
  overrides: Partial<OrganizationResult> = {},
): OrganizationResult {
  return {
    schema: "echo.files.organize.result.v1",
    planId,
    operationId: planId,
    taskId: "task-123",
    direction: "apply",
    state: "completed",
    executionComplete: true,
    counts: {
      moved: 1,
      conflicts: 0,
      failed: 0,
      pending: 0,
      uncertain: 0,
      skipped: 0,
    },
    results: [
      {
        entryId: "first",
        source: "receipts/invoice.txt",
        target: "receipts/2026/09/invoice.txt",
        status: "moved",
        committed: true,
        reason: null,
        recoveryPaths: [],
        actualPath: "receipts/2026/09/invoice.txt",
        sha256: "c".repeat(64),
      },
    ],
    reviewCount: 1,
    ...overrides,
  };
}

export function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}
