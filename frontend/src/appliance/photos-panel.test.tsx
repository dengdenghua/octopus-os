import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PhotosPanel } from "@/appliance/photos-panel";

it("hands a previewed photo to the existing Agent entry", async () => {
  vi.stubGlobal("fetch", initialFetch());
  const onAskAgent = vi.fn();
  const onClose = vi.fn();
  const user = userEvent.setup();
  render(<PhotosPanel open onClose={onClose} onAskAgent={onAskAgent} />);
  await user.click(
    await screen.findByRole("button", { name: "查看 海边.jpg" }),
  );
  await user.click(screen.getByRole("button", { name: "交给 Agent" }));
  expect(onAskAgent).toHaveBeenCalledExactlyOnceWith({
    app: "photos",
    kind: "photo",
    path: "旅行/海边.jpg",
    query: "",
  });
  expect(onClose).toHaveBeenCalledOnce();
});
import type {
  PhotoIndexJob,
  PhotoIndexPlan,
  PhotoLibrary,
  PhotoModelError,
  PhotoReadiness,
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
    canManage: true,
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

const readiness: PhotoReadiness = {
  schema: "echo.photos.readiness.v1",
  browseAvailable: true,
  previewAvailable: true,
  semantic: {
    available: true,
    state: "dependencies-ready",
    missingDependencies: [],
    modelsLoaded: false,
  },
  faces: {
    available: false,
    state: "unavailable",
    missingDependencies: ["insightface"],
    modelsLoaded: false,
  },
  modelDownloadMayBeRequired: true,
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function initialFetch(
  extra?: (
    url: string,
    init?: RequestInit,
  ) => Response | Promise<Response> | null,
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function json(value: unknown) {
  return new Response(JSON.stringify(value), { status: 200 });
}

function searchResult(item: PhotoLibrary["items"][number]) {
  return json({
    schema: "echo.photos.search.v1",
    query: item.name,
    mode: "semantic",
    total: 1,
    items: [item],
  });
}

function submitQuery(value: string) {
  const input = screen.getByRole("textbox", { name: "搜索照片" });
  fireEvent.change(input, { target: { value } });
  fireEvent.submit(input.closest("form")!);
}

function indexPolling() {
  let tick: (() => void) | undefined;
  const setInterval = window.setInterval.bind(window);
  vi.spyOn(window, "setInterval").mockImplementation(
    (handler, delay, ...args) => {
      if (delay === 2_000) {
        tick = handler as () => void;
        return 1_000_000;
      }
      return setInterval(handler, delay, ...args);
    },
  );
  return () => tick?.();
}

function indexJob(
  state: PhotoIndexJob["state"],
  jobId = "current",
): PhotoIndexJob {
  return { ...status.job, state, jobId };
}

const pendingIndexPlan: PhotoIndexPlan = {
  schema: "echo.photos.index-plan.v1",
  planId: "b".repeat(64),
  operation: "build",
  libraryFingerprint: "c".repeat(64),
  imageCount: 2,
  unsafeLinks: 0,
  scanTruncated: false,
  maxFiles: 4000,
  includeFaces: false,
  ready: true,
  blockers: [],
  warnings: [],
  requiresApproval: true,
  approvalAction: "photos.index.build",
  approvalTarget: "b".repeat(64),
  changes: [],
};

const cleanupPlan: PhotoIndexPlan = {
  ...pendingIndexPlan,
  cleanupOnly: true,
  imageCount: 0,
  scanErrors: 0,
  includeFaces: false,
  changes: [
    { field: "indexedPhotos", before: 2, after: 0 },
    { field: "faceRecords", before: 4, after: 0 },
    { field: "derivedRecords", before: 9, after: 0 },
  ],
};

function emptyIndexedStatus(job: PhotoIndexJob = status.job): PhotoStatus {
  return {
    ...status,
    library: { ...status.library, imageCount: 0, scanErrors: 0 },
    index: {
      ...status.index,
      indexed: 2,
      faces: 4,
      cleanupAvailable: true,
      backendAvailable: false,
      readiness: {
        ...readiness,
        semantic: {
          ...readiness.semantic,
          available: false,
          state: "unavailable",
          missingDependencies: ["onnxruntime"],
        },
        modelDownloadMayBeRequired: false,
      },
    },
    job,
  };
}

function emptyIndexFetch(extra?: Parameters<typeof initialFetch>[0]) {
  return initialFetch((url, init) => {
    const reply = extra?.(url, init);
    if (reply) return reply;
    if (url.endsWith("/status")) return json(emptyIndexedStatus());
    if (url.includes("/library?"))
      return json({ ...library, total: 0, items: [] });
    if (url.endsWith("/plans/index")) return json(cleanupPlan);
    return null;
  });
}

describe("PhotosPanel", () => {
  it("restores the search and previews only the exact requested photo", async () => {
    vi.stubGlobal(
      "fetch",
      initialFetch((url) =>
        url.endsWith("/search") ? searchResult(library.items[0]!) : null,
      ),
    );
    render(
      <PhotosPanel
        open
        searchRequest={{ query: "海边", selectedPath: "旅行/海边.jpg" }}
        onClose={vi.fn()}
      />,
    );
    await screen.findByRole("button", { name: "关闭照片预览" });
    expect(
      screen
        .getAllByRole("img", { name: "海边.jpg", exact: true })
        .some(
          (image) =>
            image.getAttribute("src") ===
            `/api/appliance/photos/original?path=${encodeURIComponent("旅行/海边.jpg")}`,
        ),
    ).toBe(true);
  });
  it("reports a missing requested photo instead of previewing another result", async () => {
    vi.stubGlobal("fetch", initialFetch());
    render(
      <PhotosPanel
        open
        searchRequest={{ query: "", selectedPath: "已移动.jpg" }}
        onClose={vi.fn()}
      />,
    );
    await screen.findByText(
      "已恢复相册视图，但当前结果中没有这张照片，可能已移动或不在当前页。",
    );
    expect(
      screen.queryByRole("button", { name: "关闭照片预览" }),
    ).not.toBeInTheDocument();
  });
  it("shows an Agent search in the photo window without a second submission", async () => {
    const fetch = initialFetch((url) =>
      url.endsWith("/search") ? searchResult(library.items[0]!) : null,
    );
    vi.stubGlobal("fetch", fetch);
    const request = { query: "海边" };
    const view = render(
      <PhotosPanel open searchRequest={request} onClose={vi.fn()} />,
    );
    await screen.findByText("海边.jpg");
    expect(screen.getByRole("textbox", { name: "搜索照片" })).toHaveValue(
      "海边",
    );
    expect(screen.queryByText("晚餐.png")).not.toBeInTheDocument();
    view.rerender(
      <PhotosPanel open searchRequest={request} onClose={vi.fn()} />,
    );
    expect(
      fetch.mock.calls.filter(([url]) => String(url).endsWith("/search")),
    ).toHaveLength(1);
  });
  it("previews residual annotations even when no photo or face rows remain", async () => {
    const current = emptyIndexedStatus();
    current.index.indexed = 0;
    current.index.faces = 0;
    const plan = {
      ...cleanupPlan,
      changes: [
        { field: "indexedPhotos", before: 0, after: 0 },
        { field: "faceRecords", before: 0, after: 0 },
        { field: "derivedRecords", before: 3, after: 0 },
      ],
    };
    const fetchMock = emptyIndexFetch((url) => {
      if (url.endsWith("/status")) return json(current);
      if (url.endsWith("/plans/index")) return json(plan);
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PhotosPanel open onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "清理旧索引" }));
    await screen.findByRole("alertdialog", { name: "清理旧照片索引？" });
    expect(
      screen.getByText(/0 条旧照片索引和 0 条人脸记录，共 3 条关联索引记录/),
    ).toHaveTextContent("不删除、修改、移动或上传原图");
    expect(screen.getByRole("button", { name: "确认清理索引" })).toBeDisabled();
    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).endsWith("/plans/index/apply"),
      ),
    ).toHaveLength(0);
  });

  it("previews empty-library cleanup counts and requires bound approval without model readiness", async () => {
    const poll = indexPolling();
    let current = emptyIndexedStatus();
    const fetchMock = emptyIndexFetch((url) => {
      if (url.endsWith("/status")) return json(current);
      if (url.endsWith("/approvals"))
        return json({ approvalToken: "cleanup-once", expiresIn: 60 });
      if (url.endsWith("/plans/index/apply")) {
        current = emptyIndexedStatus({
          ...indexJob("running", "cleanup-job"),
          cleanupOnly: true,
          planId: cleanupPlan.planId,
        });
        return json({ schema: "echo.photos.index-job.v1", job: current.job });
      }
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<PhotosPanel open onClose={vi.fn()} />);
    const cleanup = await screen.findByRole("button", { name: "清理旧索引" });
    expect(cleanup).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: "人物聚类" })).toBeDisabled();
    await user.click(cleanup);
    expect(
      await screen.findByRole("alertdialog", { name: "清理旧照片索引？" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/2 条旧照片索引和 4 条人脸记录，共 9 条关联索引记录/),
    ).toHaveTextContent("不删除、修改、移动或上传原图");
    expect(screen.getByText(/人物名称和类别设置会保留/)).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).endsWith("/plans/index/apply"),
      ),
    ).toHaveLength(0);
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认清理索引" }));
    expect(
      await screen.findByRole("button", { name: "后台清理旧索引中" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "停止清理" })).toBeEnabled();
    const approvalRequest = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/approvals"),
    );
    expect(JSON.parse(String(approvalRequest?.[1]?.body))).toEqual({
      action: "photos.index.build",
      target: cleanupPlan.planId,
      password: "device-password",
    });
    const applyRequest = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/plans/index/apply"),
    );
    expect(JSON.parse(String(applyRequest?.[1]?.body))).toEqual({
      planId: cleanupPlan.planId,
      includeFaces: false,
    });
    expect(applyRequest?.[1]?.headers).toMatchObject({
      "X-Echo-Approval": "cleanup-once",
    });
    current = {
      ...current,
      index: {
        ...current.index,
        cleanupAvailable: false,
        indexed: 0,
        faces: 0,
      },
      job: {
        ...current.job,
        state: "succeeded",
        result: { indexed: 0, faces: 0, removed: 2, embedded: 0, reused: 0 },
      },
    };
    await act(async () => poll());
    expect(
      screen.getByText(/旧照片索引及关联记录已清理，原图未被删除/),
    ).toHaveTextContent("当前剩余 0 条照片索引");
    expect(
      screen.queryByRole("button", { name: "清理旧索引" }),
    ).not.toBeInTheDocument();
  });

  it("restores a cleanup job after reopening, stops it, and allows a fresh plan after cancellation", async () => {
    const poll = indexPolling();
    let currentJob = {
      ...indexJob("running", "cleanup-job"),
      cleanupOnly: true,
    };
    const fetchMock = emptyIndexFetch((url) => {
      if (url.endsWith("/status")) return json(emptyIndexedStatus(currentJob));
      if (url.endsWith("/cancel")) {
        currentJob = { ...currentJob, state: "cancelling" };
        return json({ schema: "echo.photos.index-job.v1", job: currentJob });
      }
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "停止清理" });
    view.rerender(<PhotosPanel open={false} onClose={vi.fn()} />);
    view.rerender(<PhotosPanel open onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "停止清理" }));
    await screen.findByRole("button", { name: "正在停止索引清理" });
    currentJob = { ...currentJob, state: "cancelled" };
    await act(async () => poll());
    expect(
      screen.getByText(/本次索引清理已取消，可以重新预览后清理/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清理旧索引" }));
    await screen.findByRole("alertdialog", { name: "清理旧照片索引？" });
    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).endsWith("/plans/index/apply"),
      ),
    ).toHaveLength(0);
  });

  it("discards a stale cleanup confirmation and requires a fresh plan after an apply conflict", async () => {
    let planned = 0;
    const fetchMock = emptyIndexFetch((url) => {
      if (url.endsWith("/plans/index")) {
        planned += 1;
        return json({
          ...cleanupPlan,
          planId: planned === 1 ? cleanupPlan.planId : "d".repeat(64),
        });
      }
      if (url.endsWith("/approvals"))
        return json({ approvalToken: "cleanup-once", expiresIn: 60 });
      if (url.endsWith("/plans/index/apply"))
        return new Response(
          JSON.stringify({ detail: "照片索引计划已变化，请重新确认" }),
          { status: 409 },
        );
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<PhotosPanel open onClose={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "清理旧索引" }));
    await screen.findByRole("alertdialog", { name: "清理旧照片索引？" });
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认清理索引" }));
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: "清理旧索引" }));
    await screen.findByRole("alertdialog", { name: "清理旧照片索引？" });
    expect(planned).toBe(2);
    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).endsWith("/approvals"),
      ),
    ).toHaveLength(1);
  });

  it("reports interrupted cleanup honestly and re-previews instead of claiming automatic recovery", async () => {
    vi.stubGlobal(
      "fetch",
      emptyIndexFetch((url) =>
        url.endsWith("/status")
          ? json(
              emptyIndexedStatus({
                ...indexJob("failed"),
                cleanupOnly: true,
                error: "service_restart_interrupted",
              }),
            )
          : null,
      ),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "清理因服务重启而中断，请重新预览清理计划",
    );
    fireEvent.click(screen.getByRole("button", { name: "清理旧索引" }));
    await screen.findByRole("alertdialog", { name: "清理旧照片索引？" });
  });

  it.each(["unavailable", "member", "scan-errors"])(
    "does not invent cleanup permission when %s",
    async (reason) => {
      const current = emptyIndexedStatus();
      if (reason === "member") current.index.canManage = false;
      else current.index.cleanupAvailable = false;
      if (reason === "scan-errors") current.library.scanErrors = 1;
      const fetchMock = emptyIndexFetch((url) =>
        url.endsWith("/status") ? json(current) : null,
      );
      vi.stubGlobal("fetch", fetchMock);
      render(<PhotosPanel open onClose={vi.fn()} />);
      await screen.findByRole("button", { name: "更新智能索引" });
      expect(
        screen.queryByRole("button", { name: "清理旧索引" }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "更新智能索引" }),
      ).toBeDisabled();
      if (reason === "scan-errors")
        expect(screen.getByRole("alert")).toHaveTextContent(
          "部分照片目录未能读取",
        );
      expect(
        fetchMock.mock.calls.filter(([url]) =>
          String(url).endsWith("/plans/index"),
        ),
      ).toHaveLength(0);
    },
  );

  it.each(["missing-counts", "blocked"])(
    "does not request approval for an unsafe cleanup plan: %s",
    async (reason) => {
      const plan =
        reason === "blocked"
          ? {
              ...cleanupPlan,
              ready: false,
              blockers: [{ code: "NO_IMAGES", message: "请重新扫描" }],
            }
          : { ...cleanupPlan, changes: [] };
      const fetchMock = emptyIndexFetch((url) =>
        url.endsWith("/plans/index") ? json(plan) : null,
      );
      vi.stubGlobal("fetch", fetchMock);
      render(<PhotosPanel open onClose={vi.fn()} />);
      fireEvent.click(
        await screen.findByRole("button", { name: "清理旧索引" }),
      );
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "清理旧索引" }),
        ).toBeEnabled(),
      );
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      expect(
        fetchMock.mock.calls.filter(([url]) =>
          String(url).endsWith("/approvals"),
        ),
      ).toHaveLength(0);
    },
  );
  it("offers cancellation only after explicit management permission is reported", async () => {
    vi.stubGlobal(
      "fetch",
      initialFetch((url) =>
        url.endsWith("/status")
          ? json({
              ...status,
              index: { ...status.index, canManage: undefined },
              job: indexJob("running"),
            })
          : null,
      ),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 海边.jpg" });
    expect(
      screen.queryByRole("button", { name: "停止索引" }),
    ).not.toBeInTheDocument();
  });

  it("locks duplicate cancellation requests and polls until cancelled without replacing search results", async () => {
    const poll = indexPolling();
    const cancellation = deferred<Response>();
    let currentJob = indexJob("running");
    const fetchMock = initialFetch((url) => {
      if (url.endsWith("/status")) return json({ ...status, job: currentJob });
      if (url.endsWith("/cancel")) return cancellation.promise;
      if (url.endsWith("/search")) return searchResult(library.items[1]);
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PhotosPanel open onClose={vi.fn()} />);
    const stop = await screen.findByRole("button", { name: "停止索引" });
    act(() => {
      fireEvent.click(stop);
      fireEvent.click(stop);
    });
    expect(screen.getByRole("button", { name: "正在请求停止" })).toBeDisabled();
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/cancel")),
    ).toHaveLength(1);
    currentJob = indexJob("cancelling");
    await act(async () =>
      cancellation.resolve(
        json({ schema: "echo.photos.index-job.v1", job: currentJob }),
      ),
    );
    expect(screen.getByRole("button", { name: "正在停止" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "正在停止索引" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "人物聚类" })).toBeDisabled();
    submitQuery("晚餐");
    await screen.findByText("本地语义结果 · 1 张");
    currentJob = indexJob("cancelled");
    await act(async () => poll());
    expect(
      screen.getByText("本次索引已取消，可以重新更新。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "更新智能索引" })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "查看 晚餐.png" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "查看 海边.jpg" }),
    ).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).includes("/library?")),
    ).toHaveLength(1);
  });

  it("discards a status read started before an accepted cancellation", async () => {
    const poll = indexPolling();
    const oldPoll = deferred<Response>();
    let reads = 0;
    vi.stubGlobal(
      "fetch",
      initialFetch((url) => {
        if (url.endsWith("/status")) {
          reads += 1;
          return reads === 2
            ? oldPoll.promise
            : json({
                ...status,
                job: indexJob(reads === 1 ? "running" : "cancelled"),
              });
        }
        if (url.endsWith("/cancel"))
          return json({
            schema: "echo.photos.index-job.v1",
            job: indexJob("cancelling"),
          });
        return null;
      }),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "停止索引" });
    act(() => poll());
    fireEvent.click(screen.getByRole("button", { name: "停止索引" }));
    await screen.findByRole("button", { name: "正在停止索引" });
    // An old read may remain stalled. Cancellation polling must still advance.
    await act(async () => poll());
    expect(await screen.findByText(/本次索引已取消/)).toBeInTheDocument();
    await act(async () =>
      oldPoll.resolve(json({ ...status, job: indexJob("running") })),
    );
    expect(
      screen.queryByRole("button", { name: "停止索引" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/本次索引已取消/)).toBeInTheDocument();
  });

  it("accepts an already committed result returned by cancellation", async () => {
    const finished = {
      ...indexJob("succeeded"),
      result: { indexed: 12, reused: 10, embedded: 2, removed: 3 },
    };
    let cancelled = false;
    vi.stubGlobal(
      "fetch",
      initialFetch((url) => {
        if (url.endsWith("/status"))
          return json({
            ...status,
            job: cancelled ? finished : indexJob("running"),
          });
        if (url.endsWith("/cancel")) {
          cancelled = true;
          return json({ schema: "echo.photos.index-job.v1", job: finished });
        }
        return null;
      }),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "停止索引" }));
    await screen.findByText("智能索引已更新，当前共 12 张照片。");
    expect(
      screen.getByText("复用 10 张 · 更新 2 张 · 移除 3 条旧索引"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/本次索引已取消/)).not.toBeInTheDocument();
  });

  it("does not regress a completed job when its cancellation response arrives last", async () => {
    const poll = indexPolling();
    const cancellation = deferred<Response>();
    let reads = 0;
    vi.stubGlobal(
      "fetch",
      initialFetch((url) => {
        if (url.endsWith("/status"))
          return json({
            ...status,
            job:
              ++reads === 1
                ? indexJob("running")
                : {
                    ...indexJob("succeeded"),
                    result: { indexed: 2, reused: 2, embedded: 0, removed: 0 },
                  },
          });
        if (url.endsWith("/cancel")) return cancellation.promise;
        return null;
      }),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "停止索引" }));
    await act(async () => poll());
    await screen.findByText("智能索引已更新，当前共 2 张照片。");
    await act(async () =>
      cancellation.resolve(
        json({
          schema: "echo.photos.index-job.v1",
          job: indexJob("cancelling"),
        }),
      ),
    );
    expect(
      screen.getByText("复用 2 张 · 更新 0 张 · 移除 0 条旧索引"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/正在停止索引/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "更新智能索引" })).toBeEnabled();
  });

  it("keeps a conflict visible and refreshes status before allowing a retry", async () => {
    let reads = 0;
    const fetchMock = initialFetch((url) => {
      if (url.endsWith("/status")) {
        reads += 1;
        return json({ ...status, job: indexJob("running") });
      }
      if (url.endsWith("/cancel"))
        return new Response(
          JSON.stringify({
            detail: {
              error: "photo_index_not_owner",
              message: "任务由另一服务执行，请在该服务停止索引。",
            },
          }),
          { status: 409 },
        );
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PhotosPanel open onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "停止索引" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "当前连接无法停止该任务，请刷新状态后重试。",
    );
    await waitFor(() => expect(reads).toBe(2));
    expect(screen.getByRole("button", { name: "停止索引" })).toBeEnabled();
    expect(screen.queryByText(/本次索引已取消/)).not.toBeInTheDocument();
  });

  it.each([200, 409])(
    "ignores a cancellation response (%s) from a closed panel session",
    async (httpStatus) => {
      const cancellation = deferred<Response>();
      let reopened = false;
      vi.stubGlobal(
        "fetch",
        initialFetch((url) => {
          if (url.endsWith("/status"))
            return json({
              ...status,
              job: indexJob("running", reopened ? "new-job" : "old-job"),
            });
          if (url.endsWith("/cancel")) return cancellation.promise;
          return null;
        }),
      );
      const { rerender } = render(<PhotosPanel open onClose={vi.fn()} />);
      fireEvent.click(await screen.findByRole("button", { name: "停止索引" }));
      rerender(<PhotosPanel open={false} onClose={vi.fn()} />);
      reopened = true;
      rerender(<PhotosPanel open onClose={vi.fn()} />);
      await screen.findByRole("button", { name: "停止索引" });
      await act(async () =>
        cancellation.resolve(
          new Response(
            JSON.stringify(
              httpStatus === 200
                ? {
                    schema: "echo.photos.index-job.v1",
                    job: indexJob("cancelled", "old-job"),
                  }
                : {
                    detail: {
                      error: "photo_index_job_changed",
                      message: "旧任务已失效",
                    },
                  },
            ),
            { status: httpStatus },
          ),
        ),
      );
      expect(screen.getByRole("button", { name: "停止索引" })).toBeEnabled();
      expect(
        screen.queryByText(/本次索引已取消|旧任务已失效/),
      ).not.toBeInTheDocument();
    },
  );

  it("does not disclose a late cancellation result after management permission is lost", async () => {
    const cancellation = deferred<Response>();
    let member = false;
    vi.stubGlobal(
      "fetch",
      initialFetch((url) => {
        if (url.endsWith("/status"))
          return json({
            ...status,
            index: { ...status.index, canManage: !member },
            job: member ? status.job : indexJob("running"),
          });
        if (url.endsWith("/cancel")) return cancellation.promise;
        return null;
      }),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "停止索引" }));
    member = true;
    fireEvent.click(screen.getByRole("button", { name: "刷新照片库" }));
    await screen.findByText(/当前账户可浏览和搜索照片/);
    await act(async () =>
      cancellation.resolve(
        json({
          schema: "echo.photos.index-job.v1",
          job: {
            ...indexJob("succeeded"),
            result: { indexed: 999, reused: 995, embedded: 4, removed: 5 },
          },
        }),
      ),
    );
    expect(
      screen.queryByText(/999|995|智能索引已更新/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "停止索引" }),
    ).not.toBeInTheDocument();
  });

  it.each(["running", "cancelling", "cancelled", "succeeded"] as const)(
    "does not expose global %s job controls or results to a member",
    async (state) => {
      vi.stubGlobal(
        "fetch",
        initialFetch((url) =>
          url.endsWith("/status")
            ? json({
                ...status,
                index: { ...status.index, canManage: false },
                job: {
                  ...indexJob(state),
                  result: {
                    indexed: 999,
                    reused: 998,
                    embedded: 1,
                    removed: 2,
                  },
                },
              })
            : null,
        ),
      );
      render(<PhotosPanel open onClose={vi.fn()} />);
      await screen.findByRole("button", { name: "查看 海边.jpg" });
      expect(
        screen.queryByText(
          /999|998|智能索引已更新|本次索引已取消|正在停止索引|后台索引中/,
        ),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "停止索引" }),
      ).not.toBeInTheDocument();
    },
  );

  it("does not invent incremental counts for an older completed-job response", async () => {
    vi.stubGlobal(
      "fetch",
      initialFetch((url) =>
        url.endsWith("/status")
          ? json({
              ...status,
              job: { ...indexJob("succeeded"), result: { indexed: 4 } },
            })
          : null,
      ),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByText("智能索引已更新，当前共 4 张照片。");
    expect(screen.queryByText(/复用|条旧索引/)).not.toBeInTheDocument();
  });

  it("lets members browse and search while explaining the administrator-only index controls", async () => {
    const fetchMock = initialFetch((url) => {
      if (url.endsWith("/status"))
        return json({
          ...status,
          index: { ...status.index, canManage: false },
        });
      if (url.endsWith("/search")) return searchResult(library.items[0]);
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 海边.jpg" });
    expect(screen.getByRole("checkbox", { name: "人物聚类" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "更新智能索引" })).toBeDisabled();
    expect(
      screen.getByText("当前账户可浏览和搜索照片，建立索引需设备管理员操作。"),
    ).toBeInTheDocument();
    submitQuery("海边");
    await screen.findByText("本地语义结果 · 1 张");
    expect(screen.getByRole("button", { name: "查看 海边.jpg" })).toBeEnabled();
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/plans/index"),
      ),
    ).toBe(false);
  });

  it("keeps the latest query when earlier search responses arrive last", async () => {
    const earlier = deferred<Response>();
    const later = deferred<Response>();
    vi.stubGlobal(
      "fetch",
      initialFetch((url, init) => {
        if (!url.endsWith("/search")) return null;
        return JSON.parse(String(init?.body)).query === "海边"
          ? earlier.promise
          : later.promise;
      }),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 海边.jpg" });
    submitQuery("海边");
    submitQuery("晚餐");
    await act(async () => later.resolve(searchResult(library.items[1])));
    expect(
      screen.queryByRole("button", { name: "查看 海边.jpg" }),
    ).not.toBeInTheDocument();
    await act(async () => earlier.resolve(searchResult(library.items[0])));
    expect(
      screen.getByRole("button", { name: "查看 晚餐.png" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "查看 海边.jpg" }),
    ).not.toBeInTheDocument();
  });

  it("does not let a late refresh replace a newer search", async () => {
    const refresh = deferred<Response>();
    let libraryCalls = 0;
    vi.stubGlobal(
      "fetch",
      initialFetch((url) => {
        if (url.includes("/library?"))
          return ++libraryCalls === 1 ? null : refresh.promise;
        if (url.endsWith("/search")) return searchResult(library.items[1]);
        return null;
      }),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 海边.jpg" });
    fireEvent.click(screen.getByRole("button", { name: "刷新照片库" }));
    submitQuery("晚餐");
    await screen.findByText("本地语义结果 · 1 张");
    await act(async () => refresh.resolve(json(library)));
    expect(
      screen.getByRole("button", { name: "查看 晚餐.png" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "查看 海边.jpg" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("本地语义结果 · 1 张")).toBeInTheDocument();
  });

  it("keeps the restored library when a cleared search returns late", async () => {
    const search = deferred<Response>();
    vi.stubGlobal(
      "fetch",
      initialFetch((url) => (url.endsWith("/search") ? search.promise : null)),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 海边.jpg" });
    submitQuery("海边");
    fireEvent.click(screen.getByRole("button", { name: "清除照片搜索" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "刷新照片库" })).toBeEnabled(),
    );
    await act(async () => search.resolve(searchResult(library.items[0])));
    expect(
      screen.getByRole("button", { name: "查看 晚餐.png" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("本地语义结果 · 1 张")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "搜索照片" })).toHaveValue("");
  });

  it.each(["search", "refresh"])(
    "discards a pending page after switching to %s",
    async (destination) => {
      const page = deferred<Response>();
      let firstPageCalls = 0;
      vi.stubGlobal(
        "fetch",
        initialFetch((url) => {
          if (url.includes("/library?")) {
            if (
              new URL(url, "http://echo.local").searchParams.get("offset") ===
              "1"
            )
              return page.promise;
            firstPageCalls += 1;
            return json({
              ...library,
              total: 2,
              items: [library.items[firstPageCalls === 1 ? 0 : 1]],
            });
          }
          return url.endsWith("/search")
            ? searchResult(library.items[1])
            : null;
        }),
      );
      render(<PhotosPanel open onClose={vi.fn()} />);
      fireEvent.click(
        await screen.findByRole("button", { name: "加载更多 · 1 / 2" }),
      );
      if (destination === "search") submitQuery("晚餐");
      else fireEvent.click(screen.getByRole("button", { name: "刷新照片库" }));
      await screen.findByRole("button", { name: "查看 晚餐.png" });
      await act(async () =>
        page.resolve(
          json({ ...library, offset: 1, items: [library.items[0]] }),
        ),
      );
      expect(
        screen.queryByRole("button", { name: "查看 海边.jpg" }),
      ).not.toBeInTheDocument();
    },
  );

  it("ignores library and readiness responses from a previously closed panel", async () => {
    const previousLibrary = deferred<Response>();
    const previousStatus = deferred<Response>();
    let libraries = 0;
    let statuses = 0;
    vi.stubGlobal(
      "fetch",
      initialFetch((url) => {
        if (url.includes("/library?"))
          return ++libraries === 1
            ? previousLibrary.promise
            : json({ ...library, total: 1, items: [library.items[1]] });
        if (url.endsWith("/status") && ++statuses === 1)
          return previousStatus.promise;
        return null;
      }),
    );
    const { rerender } = render(<PhotosPanel open onClose={vi.fn()} />);
    rerender(<PhotosPanel open={false} onClose={vi.fn()} />);
    rerender(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 晚餐.png" });
    await act(async () => {
      previousStatus.resolve(
        json({
          ...status,
          index: { ...status.index, backendAvailable: false },
        }),
      );
      previousLibrary.resolve(json(library));
    });
    expect(
      screen.queryByRole("button", { name: "查看 海边.jpg" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "更新智能索引" })).toBeEnabled();
  });

  it("ignores a search completed after closing and reopening the panel", async () => {
    const previousSearch = deferred<Response>();
    vi.stubGlobal(
      "fetch",
      initialFetch((url) =>
        url.endsWith("/search") ? previousSearch.promise : null,
      ),
    );
    const { rerender } = render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 海边.jpg" });
    submitQuery("海边");
    rerender(<PhotosPanel open={false} onClose={vi.fn()} />);
    rerender(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 晚餐.png" });
    await act(async () =>
      previousSearch.resolve(searchResult(library.items[0])),
    );
    expect(
      screen.getByRole("button", { name: "查看 晚餐.png" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "搜索照片" })).toHaveValue("");
  });

  it("does not replace an active query when an index-completion poll returns", async () => {
    const poll = deferred<Response>();
    const search = deferred<Response>();
    let tick: (() => void) | undefined;
    const setInterval = window.setInterval.bind(window);
    vi.spyOn(window, "setInterval").mockImplementation(
      (handler, delay, ...args) => {
        if (delay === 2_000) {
          tick = handler as () => void;
          return 1_000_000;
        }
        return setInterval(handler, delay, ...args);
      },
    );
    let statusCalls = 0;
    const fetchMock = initialFetch((url) => {
      if (url.endsWith("/status"))
        return ++statusCalls === 1
          ? json({
              ...status,
              job: { ...status.job, state: "running", jobId: "current" },
            })
          : poll.promise;
      return url.endsWith("/search") ? search.promise : null;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 海边.jpg" });
    await waitFor(() => expect(tick).toBeDefined());
    act(() => {
      tick?.();
      tick?.();
    });
    expect(statusCalls).toBe(2); // Slow polling requests never overlap.
    submitQuery("晚餐");
    await act(async () =>
      poll.resolve(
        json({ ...status, job: { ...status.job, state: "succeeded" } }),
      ),
    );
    await act(async () => search.resolve(searchResult(library.items[1])));
    expect(
      screen.getByRole("button", { name: "查看 晚餐.png" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "查看 海边.jpg" }),
    ).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).includes("/library?")),
    ).toHaveLength(1);
  });

  it("refreshes the unchanged browsing view after a completed index", async () => {
    const poll = deferred<Response>();
    let tick: (() => void) | undefined;
    const setInterval = window.setInterval.bind(window);
    vi.spyOn(window, "setInterval").mockImplementation(
      (handler, delay, ...args) => {
        if (delay === 2_000) {
          tick = handler as () => void;
          return 1_000_000;
        }
        return setInterval(handler, delay, ...args);
      },
    );
    let statuses = 0;
    let libraries = 0;
    vi.stubGlobal(
      "fetch",
      initialFetch((url) => {
        if (url.endsWith("/status"))
          return ++statuses === 1
            ? json({
                ...status,
                job: { ...status.job, state: "running", jobId: "current" },
              })
            : poll.promise;
        if (url.includes("/library?"))
          return json({
            ...library,
            total: 1,
            items: [library.items[libraries++ === 0 ? 0 : 1]],
          });
        return null;
      }),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 海边.jpg" });
    await waitFor(() => expect(tick).toBeDefined());
    act(() => tick?.());
    await act(async () =>
      poll.resolve(
        json({ ...status, job: { ...status.job, state: "succeeded" } }),
      ),
    );
    await screen.findByRole("button", { name: "查看 晚餐.png" });
    expect(libraries).toBe(2);
    expect(
      screen.queryByRole("button", { name: "查看 海边.jpg" }),
    ).not.toBeInTheDocument();
  });

  it("discards an index plan returned after closing the panel", async () => {
    const plan = deferred<Response>();
    vi.stubGlobal(
      "fetch",
      initialFetch((url) =>
        url.endsWith("/plans/index") ? plan.promise : null,
      ),
    );
    const { rerender } = render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 海边.jpg" });
    fireEvent.click(screen.getByRole("button", { name: "更新智能索引" }));
    rerender(<PhotosPanel open={false} onClose={vi.fn()} />);
    rerender(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 海边.jpg" });
    await act(async () => plan.resolve(json(pendingIndexPlan)));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("does not execute an approved index after its panel session was closed", async () => {
    const approval = deferred<Response>();
    const fetchMock = initialFetch((url) => {
      if (url.endsWith("/plans/index")) return json(pendingIndexPlan);
      if (url.endsWith("/approvals")) return approval.promise;
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 海边.jpg" });
    fireEvent.click(screen.getByRole("button", { name: "更新智能索引" }));
    await screen.findByRole("alertdialog");
    fireEvent.change(screen.getByLabelText("设备管理员密码"), {
      target: { value: "password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始建立" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).endsWith("/approvals"),
        ),
      ).toBe(true),
    );
    rerender(<PhotosPanel open={false} onClose={vi.fn()} />);
    await act(async () =>
      approval.resolve(
        json({ approvalToken: "previous-session", expiresIn: 60 }),
      ),
    );
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).endsWith("/plans/index/apply"),
      ),
    ).toBe(false);
  });

  it("restores browsing pagination after the latest search fails", async () => {
    const search = deferred<Response>();
    vi.stubGlobal(
      "fetch",
      initialFetch((url) => {
        if (url.endsWith("/search")) return search.promise;
        if (url.includes("/library?")) {
          const offset = Number(
            new URL(url, "http://echo.local").searchParams.get("offset"),
          );
          return json({
            ...library,
            total: 2,
            offset,
            items: [library.items[offset === 0 ? 0 : 1]],
          });
        }
        return null;
      }),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "加载更多 · 1 / 2" });
    submitQuery("暂时失败的搜索");
    expect(
      screen.getByRole("button", { name: "加载更多 · 1 / 2" }),
    ).toBeDisabled();
    await act(async () =>
      search.resolve(
        new Response(JSON.stringify({ detail: "try again" }), { status: 503 }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "加载更多 · 1 / 2" }));
    await screen.findByRole("button", { name: "查看 晚餐.png" });
  });

  it("does not replace an accepted index job with an older refresh status", async () => {
    const oldStatus = deferred<Response>();
    let statuses = 0;
    vi.stubGlobal(
      "fetch",
      initialFetch((url) => {
        if (url.endsWith("/status") && ++statuses === 2)
          return oldStatus.promise;
        if (url.endsWith("/plans/index")) return json(pendingIndexPlan);
        if (url.endsWith("/approvals"))
          return json({ approvalToken: "once", expiresIn: 60 });
        if (url.endsWith("/plans/index/apply"))
          return json({
            schema: "echo.photos.index-job.v1",
            job: {
              ...status.job,
              state: "running",
              jobId: "new-job",
              planId: pendingIndexPlan.planId,
            },
          });
        return null;
      }),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 海边.jpg" });
    fireEvent.click(screen.getByRole("button", { name: "刷新照片库" }));
    fireEvent.click(screen.getByRole("button", { name: "更新智能索引" }));
    await screen.findByRole("alertdialog");
    fireEvent.change(screen.getByLabelText("设备管理员密码"), {
      target: { value: "password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始建立" }));
    await screen.findByRole("button", { name: "后台索引中" });
    await act(async () =>
      oldStatus.resolve(
        json({
          ...status,
          index: { ...status.index, backendAvailable: false },
        }),
      ),
    );
    expect(screen.getByRole("button", { name: "后台索引中" })).toBeDisabled();
    expect(screen.queryByText(/智能索引组件尚未就绪/)).not.toBeInTheDocument();
  });

  it("explains host limitations without requesting unsupported previews", async () => {
    vi.stubGlobal(
      "fetch",
      initialFetch((url) =>
        url.endsWith("/status")
          ? new Response(
              JSON.stringify({
                ...status,
                index: {
                  ...status.index,
                  readiness: {
                    ...readiness,
                    previewAvailable: false,
                    semantic: {
                      ...readiness.faces,
                      missingDependencies: ["secure-file-access"],
                    },
                  },
                },
              }),
              { status: 200 },
            )
          : null,
      ),
    );
    const user = userEvent.setup();
    render(<PhotosPanel open onClose={vi.fn()} />);
    const photo = await screen.findByRole("button", { name: "查看 海边.jpg" });
    expect(
      screen.getByText(/当前系统尚不支持相册所需的安全文件读取/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    await user.click(photo);
    expect(
      screen.getByText(/当前设备无法安全生成照片预览/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("preserves a successful semantic result when face indexing failed", async () => {
    vi.stubGlobal(
      "fetch",
      initialFetch((url) =>
        url.endsWith("/status")
          ? new Response(
              JSON.stringify({
                ...status,
                job: {
                  ...status.job,
                  state: "failed",
                  error: "face_model_unavailable",
                  result: { semantic: true, indexed: 1, partial: true },
                },
              }),
              { status: 200 },
            )
          : null,
      ),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "语义索引已写入，人物分组未完成",
    );
  });

  it("keeps photos and filename search available when smart components are missing", async () => {
    const fetchMock = initialFetch((url) => {
      if (url.endsWith("/status"))
        return new Response(
          JSON.stringify({
            ...status,
            index: {
              ...status.index,
              backendAvailable: false,
              readiness: {
                ...readiness,
                semantic: {
                  ...readiness.faces,
                  missingDependencies: ["fastembed"],
                },
              },
            },
          }),
          { status: 200 },
        );
      if (url.endsWith("/search"))
        return new Response(
          JSON.stringify({
            schema: "echo.photos.search.v1",
            query: "海边",
            mode: "filename",
            total: 1,
            items: [library.items[0]],
          }),
          { status: 200 },
        );
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<PhotosPanel open onClose={vi.fn()} />);
    expect(
      await screen.findByRole("button", { name: "查看 海边.jpg" }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: "更新智能索引" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "人物聚类" })).toBeDisabled();
    expect(screen.getByText(/智能索引组件尚未就绪/)).toBeInTheDocument();
    await user.type(
      screen.getByRole("textbox", { name: "搜索照片" }),
      "海边{Enter}",
    );
    expect(
      await screen.findByText(/已按文件名查找 · 1 张/),
    ).toBeInTheDocument();
  });

  it("allows a semantic index without optional face support and explains first use", async () => {
    vi.stubGlobal(
      "fetch",
      initialFetch((url) =>
        url.endsWith("/status")
          ? new Response(
              JSON.stringify({
                ...status,
                index: { ...status.index, readiness },
              }),
              {
                status: 200,
              },
            )
          : null,
      ),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    expect(
      await screen.findByRole("button", { name: "更新智能索引" }),
    ).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: "人物聚类" })).toBeDisabled();
    expect(screen.getByText(/可以先建立语义索引/)).toBeInTheDocument();
    expect(
      screen.getByText(/首次建立索引可能需要联网下载模型/),
    ).toBeInTheDocument();
  });

  it.each<{
    code: PhotoModelError["code"];
    guidance: string;
  }>([
    {
      code: "runtime_import_failed",
      guidance: "推理运行库无法加载",
    },
    {
      code: "unsupported_quantization",
      guidance: "关闭量化或调整模型配置后重试",
    },
    {
      code: "invalid_model_configuration",
      guidance: "检查模型名称、运行设备与参数设置后重试",
    },
    {
      code: "model_load_failed",
      guidance: "检查设备网络、模型缓存及其读取权限后重试",
    },
  ])(
    "explains $code without exposing runtime details or blocking an index retry",
    async ({ code, guidance }) => {
      const fetchMock = initialFetch((url) => {
        if (url.endsWith("/status"))
          return json({
            ...status,
            index: {
              ...status.index,
              readiness: {
                ...readiness,
                semantic: {
                  ...readiness.semantic,
                  state: "load-failed",
                  modelErrors: [
                    {
                      model: "vision",
                      code,
                      message: "private-runtime-exception",
                      path: "/private/model-cache",
                    },
                  ],
                },
              },
            },
          });
        if (url.endsWith("/plans/index")) return json(pendingIndexPlan);
        return null;
      });
      vi.stubGlobal("fetch", fetchMock);
      render(<PhotosPanel open onClose={vi.fn()} />);
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "语义模型加载失败",
      );
      expect(screen.getByRole("alert")).toHaveTextContent(guidance);
      expect(screen.getByRole("alert")).toHaveTextContent("图片模型");
      expect(
        screen.queryByText(/首次建立索引可能需要联网下载模型/),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText(/private-runtime-exception|private\/model-cache/),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "查看 海边.jpg" }),
      ).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: "更新智能索引" }));
      expect(await screen.findByRole("alertdialog")).toHaveTextContent(
        "设备将读取最多 2 张照片",
      );
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).endsWith("/plans/index/apply"),
        ),
      ).toBe(false);
    },
  );

  it("shows local model loading while preserving browsing and the running-job guard", async () => {
    vi.stubGlobal(
      "fetch",
      initialFetch((url) =>
        url.endsWith("/status")
          ? json({
              ...status,
              index: {
                ...status.index,
                readiness: {
                  ...readiness,
                  semantic: {
                    ...readiness.semantic,
                    state: "loading",
                    modelErrors: [],
                  },
                  faces: {
                    ...readiness.semantic,
                    state: "loading",
                    modelErrors: [],
                  },
                },
              },
              job: { ...status.job, state: "running", jobId: "loading-models" },
            })
          : null,
      ),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    expect(await screen.findByText(/语义模型正在本机加载/)).toHaveAttribute(
      "role",
      "status",
    );
    expect(screen.getByText(/人物模型正在本机加载/)).toHaveAttribute(
      "role",
      "status",
    );
    expect(screen.getByRole("button", { name: "后台索引中" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "查看 海边.jpg" })).toBeEnabled();
    expect(
      screen.queryByText(/首次建立索引可能需要联网下载模型/),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("observes a text-model failure after filename fallback and clears it after a successful retry", async () => {
    let stage: "initial" | "failed" | "loaded" = "initial";
    const fetchMock = initialFetch((url) => {
      if (url.endsWith("/status"))
        return json({
          ...status,
          index: {
            ...status.index,
            readiness: {
              ...readiness,
              semantic: {
                ...readiness.semantic,
                state:
                  stage === "initial"
                    ? "dependencies-ready"
                    : stage === "failed"
                      ? "load-failed"
                      : "loaded",
                modelsLoaded: stage === "loaded",
                modelErrors:
                  stage === "failed"
                    ? [{ model: "text", code: "runtime_import_failed" }]
                    : [],
              },
              modelDownloadMayBeRequired: stage === "initial",
            },
          },
        });
      if (url.endsWith("/search")) {
        stage = stage === "initial" ? "failed" : "loaded";
        return json({
          schema: "echo.photos.search.v1",
          query: "海边",
          mode: stage === "failed" ? "filename" : "semantic",
          total: 1,
          items: [library.items[0]],
        });
      }
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "查看 海边.jpg" });
    submitQuery("海边");
    await screen.findByText(/已按文件名查找 · 1 张/);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "文字检索模型：推理运行库无法加载",
    );
    expect(
      screen.queryByText(/首次建立索引可能需要联网下载模型/),
    ).not.toBeInTheDocument();
    submitQuery("海边");
    await screen.findByText("本地语义结果 · 1 张");
    await waitFor(() =>
      expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "查看 海边.jpg" })).toBeEnabled();
  });

  it("keeps a member's search available after a face-model failure without allowing indexing", async () => {
    const fetchMock = initialFetch((url) => {
      if (url.endsWith("/status"))
        return json({
          ...status,
          index: {
            ...status.index,
            canManage: false,
            readiness: {
              ...readiness,
              semantic: {
                ...readiness.semantic,
                state: "loaded",
                modelsLoaded: true,
              },
              faces: {
                ...readiness.semantic,
                state: "load-failed",
                modelErrors: [{ model: "faces", code: "model_load_failed" }],
              },
            },
          },
        });
      if (url.endsWith("/search")) return searchResult(library.items[0]);
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PhotosPanel open onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "人物模型加载失败",
    );
    expect(screen.queryByText(/语义模型加载失败/)).not.toBeInTheDocument();
    expect(
      screen.getByText("当前账户可浏览和搜索照片，建立索引需设备管理员操作。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "人物聚类" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "更新智能索引" })).toBeDisabled();
    submitQuery("海边");
    await screen.findByText("本地语义结果 · 1 张");
    expect(screen.getByRole("button", { name: "查看 海边.jpg" })).toBeEnabled();
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).endsWith("/plans/index"),
      ),
    ).toBe(false);
  });

  it("uses safe fallback guidance when an older or newer server has no recognized model error", async () => {
    vi.stubGlobal(
      "fetch",
      initialFetch((url) =>
        url.endsWith("/status")
          ? json({
              ...status,
              index: {
                ...status.index,
                readiness: {
                  ...readiness,
                  semantic: {
                    ...readiness.semantic,
                    state: "load-failed",
                    modelErrors: [
                      {
                        model: "/private/model",
                        code: "private-exception-code",
                      },
                    ],
                  },
                  faces: { ...readiness.semantic, state: "load-failed" },
                },
              },
            })
          : null,
      ),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    await screen.findByText("语义模型加载失败。");
    expect(screen.getAllByRole("alert")).toHaveLength(2);
    expect(
      screen.getByText(/^本地模型：请设备管理员检查设备网络/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/private-exception-code|private\/model/),
    ).not.toBeInTheDocument();
  });

  it("shows a previously interrupted job after reopening the photo panel", async () => {
    vi.stubGlobal(
      "fetch",
      initialFetch((url) =>
        url.endsWith("/status")
          ? new Response(
              JSON.stringify({
                ...status,
                job: {
                  ...status.job,
                  state: "failed",
                  error: "service_restart_interrupted",
                },
              }),
              { status: 200 },
            )
          : null,
      ),
    );
    render(<PhotosPanel open onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "上次索引因服务重启而中断",
    );
    expect(screen.getByRole("button", { name: "更新智能索引" })).toBeEnabled();
  });

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
    const searchRequest = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/search"),
    );
    expect(JSON.parse(String(searchRequest?.[1]?.body))).toEqual({
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
