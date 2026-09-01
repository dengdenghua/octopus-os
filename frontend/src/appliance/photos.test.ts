import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyPhotoIndex,
  createPhotoIndexPlan,
  fetchPhotoLibrary,
  photoOriginalUrl,
  photoThumbnailUrl,
  searchPhotos,
} from "@/appliance/photos";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("photos appliance client", () => {
  it("encodes thumbnail paths without exposing a filesystem location", () => {
    const url = photoThumbnailUrl("家庭 相册/八月.jpg", 512);
    const parsed = new URL(url, "http://echo.local");

    expect(parsed.pathname).toBe("/api/appliance/photos/thumbnail");
    expect(parsed.searchParams.get("path")).toBe("家庭 相册/八月.jpg");
    expect(parsed.searchParams.get("size")).toBe("512");
    expect(url).not.toContain("/data/nas");
    expect(photoOriginalUrl("家庭 相册/八月.jpg")).toContain(
      "/api/appliance/photos/original?path=",
    );
  });

  it("uses the bounded library and semantic-search contracts", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/library?")) {
          return new Response(
            JSON.stringify({
              schema: "echo.photos.library.v1",
              total: 1,
              offset: 0,
              limit: 500,
              scanTruncated: false,
              unsafeLinksSkipped: 0,
              items: [],
            }),
            { status: 200 },
          );
        }
        if (url.endsWith("/search") && init?.method === "POST") {
          return new Response(
            JSON.stringify({
              schema: "echo.photos.search.v1",
              query: "海边",
              mode: "semantic",
              total: 0,
              items: [],
            }),
            { status: 200 },
          );
        }
        return new Response(null, { status: 404 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchPhotoLibrary("", 120, 80);
    await searchPhotos("海边", 12);

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("offset=120");
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("limit=80");
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      query: "海边",
      limit: 12,
    });
  });

  it("binds index apply to plan, face choice and one-shot approval", async () => {
    const planId = "a".repeat(64);
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/plans/index") && init?.method === "POST") {
          return new Response(
            JSON.stringify({
              schema: "echo.photos.index-plan.v1",
              planId,
              operation: "build",
              ready: true,
              blockers: [],
            }),
            { status: 200 },
          );
        }
        if (url.endsWith("/plans/index/apply")) {
          return new Response(
            JSON.stringify({
              schema: "echo.photos.index-job.v1",
              job: { state: "running", planId },
            }),
            { status: 200 },
          );
        }
        return new Response(null, { status: 404 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    await createPhotoIndexPlan(true);
    await applyPhotoIndex(planId, true, "approval-once");

    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      includeFaces: true,
    });
    expect(fetchMock.mock.calls[1]?.[1]?.headers).toEqual(
      expect.objectContaining({ "X-Echo-Approval": "approval-once" }),
    );
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      planId,
      includeFaces: true,
    });
  });
});
