import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PhotosPanel } from "@/appliance/photos-panel";
import type {
  PhotoIndexPlan,
  PhotoLibrary,
  PhotoStatus,
} from "@/appliance/photos";

const library: PhotoLibrary = {
  schema: "echo.photos.library.v1",
  total: 2,
  offset: 0,
  limit: 500,
  scanTruncated: false,
  unsafeLinksSkipped: 0,
  items: [
    {
      path: "旅行/海边.jpg",
      name: "海边.jpg",
      size: 2048,
      mtime: 1_787_900_000,
      fileType: "jpg",
      width: 2400,
      height: 1600,
      capturedAt: "2026:08:28 12:00:00",
      location: null,
      indexed: true,
    },
    {
      path: "家庭/晚餐.png",
      name: "晚餐.png",
      size: 1024,
      mtime: 1_787_800_000,
      fileType: "png",
      width: null,
      height: null,
      capturedAt: null,
      location: null,
      indexed: false,
    },
  ],
};

const status: PhotoStatus = {
  schema: "echo.photos.status.v1",
  library: {
    imageCount: 2,
    scanTruncated: false,
    unsafeLinksSkipped: 0,
  },
  index: {
    backendAvailable: true,
    databaseExists: true,
    maxFiles: 4000,
    indexed: 1,
    faces: 3,
    duplicateGroups: 0,
    blurry: 1,
  },
  job: {
    state: "idle",
    jobId: null,
    planId: null,
    includeFaces: false,
    startedAt: null,
    completedAt: null,
    result: null,
    error: null,
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function initialFetch(
  extra?: (url: string, init?: RequestInit) => Response | null,
) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const response = extra?.(url, init);
    if (response) return response;
    if (url.includes("/library?"))
      return new Response(JSON.stringify(library), { status: 200 });
    if (url.endsWith("/status"))
      return new Response(JSON.stringify(status), { status: 200 });
    return new Response(null, { status: 404 });
  });
}

describe("PhotosPanel", () => {
  it("shows a single local photo surface with library and index facts", async () => {
    vi.stubGlobal("fetch", initialFetch());
    render(<PhotosPanel open onClose={vi.fn()} />);

    expect(
      await screen.findByRole("dialog", { name: "照片" }),
    ).toBeInTheDocument();
    expect(screen.getByText("你的照片，只在这台设备理解")).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "查看 海边.jpg" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "查看 晚餐.png" }),
    ).toBeInTheDocument();
    expect(screen.getByText("1 · 50%")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("0 / 1")).toBeInTheDocument();
  });

  it("uses semantic search and clearly labels the local result mode", async () => {
    const fetchMock = initialFetch((url, init) => {
      if (url.endsWith("/search") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            schema: "echo.photos.search.v1",
            query: "海边的家人",
            mode: "semantic",
            total: 1,
            items: [{ ...library.items[0], score: 0.91 }],
          }),
          { status: 200 },
        );
      }
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<PhotosPanel open onClose={vi.fn()} />);

    const search = await screen.findByRole("textbox", { name: "搜索照片" });
    await user.type(search, "海边的家人{Enter}");

    expect(await screen.findByText("本地语义结果 · 1 张")).toBeInTheDocument();
    expect(JSON.parse(String(fetchMock.mock.calls.at(-1)?.[1]?.body))).toEqual({
      query: "海边的家人",
      limit: 50,
    });
  });

  it("loads a large library in bounded pages without replacing earlier photos", async () => {
    const fetchMock = initialFetch((url) => {
      if (!url.includes("/library?")) return null;
      const parsed = new URL(url, "http://echo.local");
      const offset = Number(parsed.searchParams.get("offset") || 0);
      return new Response(
        JSON.stringify({
          ...library,
          total: 2,
          offset,
          limit: 120,
          items: offset === 0 ? [library.items[0]] : [library.items[1]],
        }),
        { status: 200 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<PhotosPanel open onClose={vi.fn()} />);

    await user.click(
      await screen.findByRole("button", { name: "加载更多 · 1 / 2" }),
    );

    expect(
      await screen.findByRole("button", { name: "查看 晚餐.png" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "查看 海边.jpg" }),
    ).toBeInTheDocument();
    const pageRequest = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("offset=1"),
    );
    expect(pageRequest).toBeDefined();
  });

  it("reviews the read-only index plan and requires password approval", async () => {
    const planId = "b".repeat(64);
    const plan: PhotoIndexPlan = {
      schema: "echo.photos.index-plan.v1",
      planId,
      operation: "build",
      libraryFingerprint: "c".repeat(64),
      imageCount: 2,
      unsafeLinks: 0,
      scanTruncated: false,
      maxFiles: 4000,
      includeFaces: true,
      ready: true,
      blockers: [],
      warnings: [],
      requiresApproval: true,
      approvalAction: "photos.index.build",
      approvalTarget: planId,
      changes: [],
    };
    const fetchMock = initialFetch((url, init) => {
      if (url.endsWith("/plans/index") && init?.method === "POST")
        return new Response(JSON.stringify(plan), { status: 200 });
      if (url.endsWith("/approvals"))
        return new Response(
          JSON.stringify({
            approvalToken: "photos-once",
            expiresIn: 60,
            action: "photos.index.build",
            target: planId,
          }),
          { status: 200 },
        );
      if (url.endsWith("/plans/index/apply"))
        return new Response(
          JSON.stringify({
            schema: "echo.photos.index-job.v1",
            job: { ...status.job, state: "running", planId, jobId: "job-1" },
          }),
          { status: 200 },
        );
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<PhotosPanel open onClose={vi.fn()} />);

    await user.click(await screen.findByRole("checkbox", { name: "人物聚类" }));
    await user.click(screen.getByRole("button", { name: "更新智能索引" }));
    expect(
      await screen.findByRole("alertdialog", {
        name: "建立本地照片智能索引？",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/原图不会被修改、移动或上传/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "开始建立" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "后台索引中" })).toBeDisabled();
    });
    const approvalRequest = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/approvals"),
    );
    const applyRequest = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/plans/index/apply"),
    );
    expect(JSON.parse(String(approvalRequest?.[1]?.body))).toEqual({
      action: "photos.index.build",
      target: planId,
      password: "device-password",
    });
    expect(applyRequest?.[1]?.headers).toEqual(
      expect.objectContaining({ "X-Echo-Approval": "photos-once" }),
    );
  });
});
