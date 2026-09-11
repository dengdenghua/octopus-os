import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyFileShare,
  listFileShares,
  planFileShare,
  planFileShareRevocation,
  revokeFileShare,
} from "./file-shares";

vi.mock("./auth", () => ({
  authHeader: () => ({ Authorization: "Bearer member-token" }),
}));

beforeEach(() => vi.restoreAllMocks());

describe("file share API", () => {
  it("uses plan and one-shot approval endpoints without putting paths in URLs", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ shares: [] }),
    } as Response);

    await listFileShares();
    await planFileShare("family/report.pdf", 300, 2);
    await applyFileShare("family/report.pdf", "a".repeat(64), "approval-once", 300, 2);
    await planFileShareRevocation("b".repeat(24));
    await revokeFileShare("b".repeat(24), "c".repeat(64), "approval-revoke");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/appliance/file-shares",
      "/api/appliance/file-shares/plans",
      "/api/appliance/file-shares/apply",
      `/api/appliance/file-shares/${"b".repeat(24)}/revoke-plan`,
      `/api/appliance/file-shares/${"b".repeat(24)}/revoke`,
    ]);
    expect(fetchMock.mock.calls[2]?.[1]?.headers).toMatchObject({
      Authorization: "Bearer member-token",
      "X-Echo-Approval": "approval-once",
    });
    expect(fetchMock.mock.calls[2]?.[1]?.body).toBe(
      JSON.stringify({
        path: "family/report.pdf",
        ttlSeconds: 300,
        maxDownloads: 2,
        planId: "a".repeat(64),
      }),
    );
  });
});
