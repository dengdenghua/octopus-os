import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer test-token" }),
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8000",
}));

import {
  type ArtifactLoadError,
  downloadArtifactFile,
  loadArtifactContent,
} from "./loader";
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

  it.each([401, 403, 404, 503])(
    "does not fall back to a same-name upload after source HTTP %s",
    async (status) => {
      const fetchMock = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(new Response("private path detail", { status }));
      await expect(
        loadArtifactContent({
          filepath: "/authorized/invoice.txt",
          threadId: "t1",
        }),
      ).rejects.toMatchObject({ status });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const url = new URL(String(fetchMock.mock.calls[0]?.[0]));
      expect(url.pathname).toBe("/api/fs/content");
      expect(url.searchParams.get("path")).toBe("/authorized/invoice.txt");
      expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
        cache: "no-store",
        headers: { Authorization: "Bearer test-token" },
      });
    },
  );

  it("downloads source bytes with authentication and the original filename", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("synthetic source bytes"),
    );
    const create = vi.fn((_blob: Blob) => "blob:source");
    const revoke = vi.fn();
    vi.stubGlobal(
      "URL",
      class extends URL {
        static createObjectURL = create;
        static revokeObjectURL = revoke;
      },
    );
    let clicked: { href: string; download: string } | null = null;
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked = { href: this.href, download: this.download };
    });
    await downloadArtifactFile({
      filepath: "C:\\authorized\\invoice.pdf",
      threadId: "t1",
    });
    expect(clicked).toEqual({ href: "blob:source", download: "invoice.pdf" });
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/fs/content?"),
      expect.objectContaining({
        headers: { Authorization: "Bearer test-token" },
      }),
    );
    expect(await (create.mock.calls[0]?.[0] as Blob).text()).toBe(
      "synthetic source bytes",
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(revoke).toHaveBeenCalledWith("blob:source");
    vi.unstubAllGlobals();
  });
});
