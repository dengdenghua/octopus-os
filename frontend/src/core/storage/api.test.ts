import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getNASBaseURL,
  NASRequestError,
  NASRequestTimeoutError,
  isNASConnectivityError,
  isNASAuthenticationError,
  loadNASAssetURL,
  loadNASAssetText,
  listNASDirectory,
  nasResourceId,
  nasResourceSelectionKey,
  parseApplianceFileResourceId,
  parseStorageFileResourceId,
  startNASService,
  storageFileResourceId,
} from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("builds deterministic storage file identities without host paths", () => {
  const id = storageFileResourceId("source/报告", "/docs/报告.md");
  expect(id).toMatch(/^storage-file:v1:/);
  expect(id).not.toContain("报告");
  expect(storageFileResourceId("source", "../secret")).toBeUndefined();
  expect(parseStorageFileResourceId(id!)).toEqual({
    sourceId: "source/报告",
    path: "/docs/报告.md",
  });
  expect(parseStorageFileResourceId("storage-file:v1:bad")).toBeUndefined();
});

it("uses server identities first and derives one for legacy Storage rows", () => {
  expect(
    nasResourceId({
      path: "/docs/report.md",
      source_id: "source",
      resource_id: "storage-file:v1:server:identity",
    }),
  ).toBe("storage-file:v1:server:identity");
  expect(nasResourceId({ path: "/docs/report.md", source_id: "source" })).toBe(
    storageFileResourceId("source", "/docs/report.md"),
  );
  expect(nasResourceId({ path: "/docs/report.md" })).toBeUndefined();
  expect(
    nasResourceId({
      path: "/docs/report.md",
      resource_id: 42 as unknown as string,
      source_id: { bad: true } as unknown as string,
    }),
  ).toBeUndefined();
});

it("converts native appliance identities to the shared Storage identity", () => {
  const sourceId = "a".repeat(64);
  const applianceId = `appliance-file:v1:${sourceId}:ZG9jcy9yZXBvcnQubWQ`;
  expect(parseApplianceFileResourceId(applianceId)).toEqual({
    sourceId: sourceId.toLowerCase(),
    path: "docs/report.md",
  });
  expect(
    nasResourceId({ path: "docs/report.md", resource_id: applianceId }),
  ).toBe(storageFileResourceId(sourceId, "docs/report.md"));
});

it("keeps search selection keys distinct across storage sources", () => {
  expect(
    nasResourceSelectionKey({ source_id: "a", path: "docs/report.md" }),
  ).not.toBe(
    nasResourceSelectionKey({ source_id: "b", path: "docs/report.md" }),
  );
});

describe("Storage API errors", () => {
  it("keeps the storage HTTP status so the UI can recover an expired token", () => {
    const error = new NASRequestError(
      "/v1/manifest",
      401,
      "missing bearer token",
    );
    expect(isNASAuthenticationError(error)).toBe(true);
    expect(error.message).toContain("401");
  });

  it("does not misclassify an unavailable service as an authentication error", () => {
    const error = new NASRequestError("/v1/manifest", 503, "unavailable");
    expect(isNASAuthenticationError(error)).toBe(false);
    expect(isNASAuthenticationError(new Error("network"))).toBe(false);
  });

  it("classifies gateway outages and timeouts as recoverable connectivity errors", () => {
    expect(
      isNASConnectivityError(new NASRequestError("/v1/manifest", 502)),
    ).toBe(true);
    expect(
      isNASConnectivityError(new NASRequestError("/v1/manifest", 503)),
    ).toBe(true);
    expect(
      isNASConnectivityError(new NASRequestError("/v1/manifest", 504)),
    ).toBe(true);
    expect(
      isNASConnectivityError(new NASRequestTimeoutError("/v1/manifest")),
    ).toBe(true);
    expect(
      isNASConnectivityError(new NASRequestError("/v1/manifest", 400)),
    ).toBe(false);
  });

  it("uses the agent same-origin storage gateway", () => {
    expect(getNASBaseURL()).toBe("/api/storage");
  });

  it("reads a bounded text preview through the authenticated storage gateway", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response("0123456789", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(loadNASAssetText("asset-1", 4)).resolves.toBe("0123");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/storage/v1/files/asset-1/content",
      expect.objectContaining({ headers: expect.any(Object) }),
    );
  });

  it("reads appliance fallback resources through their path-free identity", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response("fallback text", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      loadNASAssetText("appliance-file:v1:root:ref", 8),
    ).resolves.toBe("fallback");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/appliance/files/resource/appliance-file%3Av1%3Aroot%3Aref",
      expect.objectContaining({ headers: expect.any(Object) }),
    );
  });

  it("keeps already-authenticated appliance preview paths on the same origin", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(new Blob(["image"]), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const createObjectURL = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:preview");

    await expect(
      loadNASAssetURL(
        "/api/appliance/files/resource/appliance-file%3Av1%3Aroot%3Aref",
      ),
    ).resolves.toBe("blob:preview");
    expect(createObjectURL).toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/appliance/files/resource/appliance-file%3Av1%3Aroot%3Aref",
      expect.objectContaining({ headers: expect.any(Object) }),
    );
  });

  it("starts storage without persisting its private token in the browser", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          status: "already_running",
          base_url: "/api/storage",
          auth_token: null,
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await startNASService();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/local-brain/storage/start",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: {},
      }),
    );
    expect(sessionStorage.getItem("echo.storage.auth-token")).toBeNull();
  });

  it("falls back to the authenticated appliance directory when Storage is offline", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response("gateway unavailable", { status: 502 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            path: "",
            entries: [
              {
                name: "报告.md",
                path: "报告.md",
                kind: "file",
                size: 12,
                mtime: 1,
                resource_id: "appliance-file:v1:root:ref",
              },
              {
                name: "资料",
                path: "资料",
                kind: "dir",
                size: 0,
                mtime: 1,
                resource_id: "appliance-file:v1:root:dir",
              },
            ],
          }),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listNASDirectory("/")).resolves.toEqual([
      {
        name: "报告.md",
        path: "报告.md",
        type: "file",
        size: 12,
        resource_id: "appliance-file:v1:root:ref",
      },
      {
        name: "资料",
        path: "资料",
        type: "dir",
        size: null,
        resource_id: "appliance-file:v1:root:dir",
      },
    ]);
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/appliance/files/list?path=%2F",
      expect.anything(),
    );
  });

  it("falls back when the optional Storage browse route is absent", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("route missing", { status: 404 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ path: "", entries: [] }), {
          status: 200,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listNASDirectory("/")).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/appliance/files/list?path=%2F",
      expect.anything(),
    );
  });

  it("normalizes native directory identities at the API boundary", async () => {
    const sourceId = "b".repeat(64);
    const applianceId = `appliance-file:v1:${sourceId}:ZG9jcy9ub3Rlcy50eHQ`;
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("route missing", { status: 404 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            path: "docs",
            entries: [
              {
                name: "notes.txt",
                path: "docs/notes.txt",
                kind: "file",
                size: 12,
                mtime: 1,
                resource_id: applianceId,
              },
            ],
          }),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const entries = await listNASDirectory("docs");
    expect(entries[0]?.resource_id).toBe(
      storageFileResourceId(sourceId, "docs/notes.txt"),
    );
  });
});
