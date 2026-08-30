import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));
vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer test" }),
}));

import {
  type ArtifactSaveError,
  canSaveWorkspaceOutput,
  restoreWorkspaceOutputRevision,
  saveWorkspaceOutputContent,
} from "./save";

describe("workspace output saving", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("only enables visual saving for scoped HTML outputs", () => {
    expect(
      canSaveWorkspaceOutput("workspace-output:final:site/index.html"),
    ).toBe(true);
    expect(canSaveWorkspaceOutput("workspace-output:final:report.md")).toBe(
      false,
    );
    expect(canSaveWorkspaceOutput("/tmp/index.html")).toBe(false);
  });

  it("writes through the scoped output endpoint with authentication", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          success: true,
          path: "/site.html",
          bytes: 12,
          sha256: "a",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await saveWorkspaceOutputContent({
      filepath: "workspace-output:final:site.html",
      threadId: "t1",
      content: "<h1>New</h1>",
      expectedContent: "<h1>Old</h1>",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8001/api/threads/t1/outputs/site.html?area=final",
      expect.objectContaining({
        method: "PUT",
        headers: expect.objectContaining({ Authorization: "Bearer test" }),
      }),
    );
    expect(
      JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)),
    ).toMatchObject({
      content: "<h1>New</h1>",
    });
  });

  it("surfaces optimistic-lock conflicts", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ detail: { message: "文件已被 Agent 更新" } }),
            { status: 409, headers: { "Content-Type": "application/json" } },
          ),
        ),
    );

    await expect(
      saveWorkspaceOutputContent({
        filepath: "workspace-output:final:site.html",
        threadId: "t1",
        content: "new",
        expectedContent: "old",
      }),
    ).rejects.toEqual(
      expect.objectContaining<ArtifactSaveError>({ status: 409 }),
    );
  });

  it("restores a saved revision through the scoped endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          success: true,
          path: "/site.html",
          bytes: 12,
          sha256: "b",
          revision_id: "2-bbbbbbbbbbbb.bak",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await restoreWorkspaceOutputRevision({
      filepath: "workspace-output:final:site.html",
      threadId: "t1",
      revisionId: "1-aaaaaaaaaaaa.bak",
      expectedContent: "<h1>New</h1>",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8001/api/threads/t1/output-revisions/site.html?area=final",
      expect.objectContaining({ method: "POST" }),
    );
    expect(
      JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)),
    ).toMatchObject({ revision_id: "1-aaaaaaaaaaaa.bak" });
  });
});
