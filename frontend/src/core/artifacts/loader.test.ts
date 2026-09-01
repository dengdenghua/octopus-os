import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer test-token" }),
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8000",
}));

import { type ArtifactLoadError, loadArtifactContent } from "./loader";
import { workspaceOutputRef } from "./utils";

describe("loadArtifactContent", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("does not cache a 404 response body as artifact content", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response('{"detail":"output not found"}', { status: 404 }),
      );

    await expect(
      loadArtifactContent({
        filepath: workspaceOutputRef({
          area: "final",
          relativePath: "报告.md",
        }),
        threadId: "thread-1",
      }),
    ).rejects.toEqual(
      expect.objectContaining<Partial<ArtifactLoadError>>({ status: 404 }),
    );
    expect(fetchMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer test-token",
        }),
      }),
    );
  });

  it("returns persisted artifact content", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("# 报告", { status: 200 }),
    );
    const result = await loadArtifactContent({
      filepath: workspaceOutputRef({ area: "final", relativePath: "报告.md" }),
      threadId: "thread-1",
    });
    expect(result.content).toBe("# 报告");
  });
});
