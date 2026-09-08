import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyPhotoIndex,
  cancelPhotoIndexJob,
  createPhotoIndexPlan,
  PhotoIndexConflictError,
  fetchPhotoLibrary,
  pausePhotoIndexJob,
  photoOriginalUrl,
  photoThumbnailUrl,
  resumePhotoIndexJob,
  searchPhotos,
} from "@/appliance/photos";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("photos appliance client", () => {
  it("distinguishes a stale cleanup plan from a retryable device failure", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ detail: "照片索引计划已变化，请重新确认" }),
          { status: 409 },
        ),
      )
      .mockResolvedValueOnce(new Response("offline", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      applyPhotoIndex("a".repeat(64), false, "once"),
    ).rejects.toBeInstanceOf(PhotoIndexConflictError);
    await expect(
      applyPhotoIndex("a".repeat(64), false, "twice"),
    ).rejects.not.toBeInstanceOf(PhotoIndexConflictError);
  });
  it("cancels only the encoded job id and accepts a completion race", async () => {
    const job = {
      state: "succeeded",
      jobId: "job / one",
      result: { indexed: 12 },
    };
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ schema: "echo.photos.index-job.v1", job }),
        ),
    );
    vi.stubGlobal("fetch", fetchMock);
    expect((await cancelPhotoIndexJob(job.jobId)).job).toEqual(job);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/appliance/photos/index-jobs/job%20%2F%20one/cancel",
      { method: "POST", headers: expect.any(Object) },
    );
  });

  it("pauses and resumes only the encoded job id", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            schema: "echo.photos.index-job.v1",
            job: { state: "paused", jobId: "job / one" },
          }),
        ),
    );
    vi.stubGlobal("fetch", fetchMock);
    await pausePhotoIndexJob("job / one");
    await resumePhotoIndexJob("job / one");
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/appliance/photos/index-jobs/job%20%2F%20one/pause",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/appliance/photos/index-jobs/job%20%2F%20one/resume",
    );
  });

  it.each([
    ["photo_index_job_changed", "索引任务已变化，请查看最新状态后重试。"],
    ["photo_index_not_owner", "当前连接无法停止该任务，请刷新状态后重试。"],
    [
      "photo_index_cancel_unavailable",
      "当前版本暂不支持停止索引，请等待本次任务完成。",
    ],
    [
      "photo_index_job_state_unavailable",
      "停止请求未能保存，请检查设备存储空间与写入权限后重试。",
    ],
  ])(
    "explains cancellation conflict %s without exposing internal details",
    async (code, message) => {
      vi.stubGlobal(
        "fetch",
        vi.fn(
          async () =>
            new Response(
              JSON.stringify({
                detail: {
                  error: code,
                  message: "private worker implementation detail",
                },
              }),
              { status: 409 },
            ),
        ),
      );
      await expect(cancelPhotoIndexJob("job")).rejects.toThrow(message);
    },
  );

  it("preserves useful server feedback for an unknown cancellation conflict", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              detail: {
                error: "additional_condition",
                message: "请先恢复设备连接。",
              },
            }),
            { status: 409 },
          ),
      ),
    );
    await expect(cancelPhotoIndexJob("job")).rejects.toThrow(
      "请先恢复设备连接。",
    );
  });

  it("does not report a cancellation when the service returns an unreadable error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("offline", { status: 503 })),
    );
    await expect(cancelPhotoIndexJob("job")).rejects.toThrow(
      "未能停止索引，请刷新任务状态后重试",
    );
  });

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
