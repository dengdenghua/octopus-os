import { afterEach, describe, expect, it, vi } from "vitest";
import {
  applyOrganizationPlan,
  cancelOrganizationPlan,
  canApplyOrganizationPlan,
  createOrganizationPlan,
  createOrganizationUndoPlan,
  fetchOrganizationPlan,
  fetchOrganizationPlans,
  fetchOrganizationResult,
  OrganizationApiError,
} from "./file-organization";
import {
  baseUrl,
  jsonResponse,
  organizationPlan,
  organizationPlanList,
  organizationResult,
  planId,
  undoId,
} from "./file-organization.test-support";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("file organization HTTP contract", () => {
  it.each([
    "finalizationPending",
    "auditRecorded",
    "taskRecorded",
    "receiptRecorded",
  ])("validates optional %s evidence as boolean", async (key) => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(organizationResult({ [key]: false })))
      .mockResolvedValueOnce(
        jsonResponse({ ...organizationResult(), [key]: "true" }),
      );
    vi.stubGlobal("fetch", fetch);
    await expect(fetchOrganizationResult(planId)).resolves.toHaveProperty(
      key,
      false,
    );
    await expect(fetchOrganizationResult(planId)).rejects.toBeInstanceOf(
      OrganizationApiError,
    );
  });
  it("discovers bounded summaries for the exact directory through a read-only request", async () => {
    const path = "receipts/中文 2026";
    const payload = organizationPlanList([organizationPlan({ path })], {
      path,
      complete: false,
    });
    const fetch = vi.fn().mockResolvedValue(jsonResponse(payload));
    vi.stubGlobal("fetch", fetch);
    expect(await fetchOrganizationPlans(path)).toEqual(payload);
    expect(String(fetch.mock.calls[0]![0])).toBe(
      `${baseUrl}?${new URLSearchParams({ path })}`,
    );
    expect(fetch.mock.calls[0]![1].method).toBeUndefined();
  });

  it.each([
    ["another directory", { path: "other" }],
    ["missing completeness", { complete: undefined }],
    [
      "too many summaries",
      {
        plans: Array.from(
          { length: 21 },
          () => organizationPlanList([organizationPlan()]).plans[0],
        ),
      },
    ],
    [
      "invalid summary",
      {
        plans: [
          {
            ...organizationPlanList([organizationPlan()]).plans[0],
            planId: "bad",
          },
        ],
      },
    ],
    [
      "another directory in a summary",
      {
        plans: [
          {
            ...organizationPlanList([organizationPlan()]).plans[0],
            path: "other",
          },
        ],
      },
    ],
    [
      "unknown state",
      {
        plans: [
          {
            ...organizationPlanList([organizationPlan()]).plans[0],
            state: "success",
          },
        ],
      },
    ],
  ])("rejects a recent list with %s", async (_name, changes) => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ ...organizationPlanList(), ...changes }),
        ),
    );
    await expect(fetchOrganizationPlans("receipts")).rejects.toBeInstanceOf(
      OrganizationApiError,
    );
  });
  it("previews only the selected NAS path and applies an immutable ID with one approval token", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(organizationPlan()))
      .mockResolvedValueOnce(jsonResponse(organizationResult()));
    vi.stubGlobal("fetch", fetch);
    await createOrganizationPlan("receipts");
    await applyOrganizationPlan(planId, "single-use-token");
    expect(fetch.mock.calls[0]?.[0]).toBe(baseUrl);
    expect(JSON.parse(fetch.mock.calls[0]?.[1].body)).toEqual({
      path: "receipts",
    });
    expect(fetch.mock.calls[1]?.[0]).toBe(`${baseUrl}/${planId}/apply`);
    expect(fetch.mock.calls[1]?.[1]).toMatchObject({
      method: "POST",
      body: "{}",
      headers: { "X-Echo-Approval": "single-use-token" },
    });
  });

  it("uses separate undo plans and cancellation without sending modified entries or roots", async () => {
    const undo = organizationPlan({
      planId: undoId,
      direction: "undo",
      sourcePlanId: planId,
      approval: { action: "files.organize.undo", target: undoId },
    });
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(undo))
      .mockResolvedValueOnce(
        jsonResponse(organizationResult({ state: "cancelled" })),
      );
    vi.stubGlobal("fetch", fetch);
    expect(await createOrganizationUndoPlan(planId)).toEqual(undo);
    expect((await cancelOrganizationPlan(planId)).state).toBe("cancelled");
    expect(
      fetch.mock.calls.map(([url, init]) => [url, init.method, init.body]),
    ).toEqual([
      [`${baseUrl}/${planId}/undo-plan`, "POST", "{}"],
      [`${baseUrl}/${planId}/cancel`, "POST", "{}"],
    ]);
  });

  it.each([
    ["wrong ID", { planId: undoId }],
    [
      "wrong approval target",
      { approval: { action: "files.organize.apply", target: undoId } },
    ],
    [
      "wrong approval action",
      { approval: { action: "files.organize.undo", target: planId } },
    ],
    [
      "malformed entries",
      { entries: [{ source: "receipts/a.txt", status: "ready" }] },
    ],
    [
      "negative count",
      { summary: { ...organizationPlan().summary, ready: -1 } },
    ],
  ])("rejects a restored plan with %s", async (_name, changes) => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(jsonResponse({ ...organizationPlan(), ...changes })),
    );
    await expect(fetchOrganizationPlan(planId)).rejects.toBeInstanceOf(
      OrganizationApiError,
    );
  });

  it("rejects a preview for another directory and an undo for another receipt", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          jsonResponse(organizationPlan({ path: "another" })),
        )
        .mockResolvedValueOnce(
          jsonResponse(
            organizationPlan({
              planId: undoId,
              direction: "undo",
              sourcePlanId: "d".repeat(64),
              approval: { action: "files.organize.undo", target: undoId },
            }),
          ),
        ),
    );
    await expect(createOrganizationPlan("receipts")).rejects.toThrow("目录");
    await expect(createOrganizationUndoPlan(planId)).rejects.toThrow(
      "撤销计划",
    );
  });

  it.each(["../outside", "a".repeat(63), "A".repeat(64)])(
    "does not request an invalid plan ID %s",
    async (id) => {
      const fetch = vi.fn();
      vi.stubGlobal("fetch", fetch);
      await expect(fetchOrganizationResult(id)).rejects.toThrow("标识无效");
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it.each([401, 403, 404, 409, 500])(
    "preserves HTTP %i failure instead of synthesizing a successful result",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          jsonResponse(
            {
              detail: {
                error: "operation_rejected",
                message: "服务已拒绝本次执行",
              },
            },
            status,
          ),
        ),
      );
      await expect(
        applyOrganizationPlan(planId, "token"),
      ).rejects.toMatchObject({ status, code: "operation_rejected" });
    },
  );

  it("distinguishes unknown network outcome and incompatible result payloads", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockRejectedValueOnce(new TypeError("network private details"))
        .mockResolvedValueOnce(
          jsonResponse({ ...organizationResult(), operationId: undoId }),
        ),
    );
    await expect(applyOrganizationPlan(planId, "token")).rejects.toThrow(
      "刷新结果",
    );
    await expect(fetchOrganizationResult(planId)).rejects.toThrow("无法核实");
  });

  it.each([
    { scanComplete: false },
    { ready: false },
    { blockers: ["scan_incomplete"] },
    { entries: [] },
    { summary: { ...organizationPlan().summary, ready: 0 } },
  ])(
    "does not enable execution without a complete executable plan: %j",
    (overrides) => {
      expect(canApplyOrganizationPlan(organizationPlan(overrides))).toBe(false);
      expect(canApplyOrganizationPlan(organizationPlan())).toBe(true);
    },
  );
});
