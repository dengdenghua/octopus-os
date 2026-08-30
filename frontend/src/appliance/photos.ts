import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type PhotoItem = {
  path: string;
  name: string;
  size: number;
  mtime: number;
  fileType: string;
  width: number | null;
  height: number | null;
  capturedAt: string | null;
  location: string | null;
  indexed: boolean;
  score?: number | null;
};

export type PhotoLibrary = {
  schema: "echo.photos.library.v1";
  total: number;
  offset: number;
  limit: number;
  scanTruncated: boolean;
  unsafeLinksSkipped: number;
  items: PhotoItem[];
};

export type PhotoIndexJob = {
  state: "idle" | "running" | "succeeded" | "failed";
  jobId: string | null;
  planId: string | null;
  includeFaces: boolean;
  startedAt: number | null;
  completedAt: number | null;
  result: { indexed?: number; faces?: number; semantic?: boolean } | null;
  error: string | null;
};

export type PhotoStatus = {
  schema: "echo.photos.status.v1";
  library: {
    imageCount: number;
    scanTruncated: boolean;
    unsafeLinksSkipped: number;
  };
  index: {
    backendAvailable: boolean;
    databaseExists: boolean;
    maxFiles: number;
    indexed: number;
    faces: number;
    duplicateGroups: number;
    blurry: number;
    error?: string;
  };
  job: PhotoIndexJob;
};

export type PhotoSearchResult = {
  schema: "echo.photos.search.v1";
  query: string;
  mode: "semantic" | "filename";
  total: number;
  items: PhotoItem[];
};

export type PhotoIndexBlocker = {
  code: "NO_IMAGES" | "AGENT_INDEX_UNAVAILABLE" | "INDEX_RUNNING";
  message: string;
};

export type PhotoIndexWarning = {
  code: "UNSAFE_LINKS_PRESENT";
  message: string;
};

export type PhotoIndexPlan = {
  schema: "echo.photos.index-plan.v1";
  planId: string;
  operation: "build";
  libraryFingerprint: string;
  imageCount: number;
  unsafeLinks: number;
  scanTruncated: boolean;
  maxFiles: number;
  includeFaces: boolean;
  ready: boolean;
  blockers: PhotoIndexBlocker[];
  warnings: PhotoIndexWarning[];
  requiresApproval: true;
  approvalAction: "photos.index.build";
  approvalTarget: string;
  changes: Array<{ field: string; before: unknown; after: unknown }>;
};

async function photoError(response: Response, fallback: string) {
  const detail = await response
    .json()
    .then((body) => body?.detail)
    .catch(() => null);
  if (typeof detail === "string" && detail.trim()) return new Error(detail);
  if (detail && typeof detail === "object") {
    const message = detail.message;
    if (typeof message === "string" && message.trim())
      return new Error(message);
  }
  if (response.status === 401) return new Error("管理员会话已过期，请重新登录");
  return new Error(fallback);
}

export function photoThumbnailUrl(path: string, size = 320) {
  const params = new URLSearchParams({ path, size: String(size) });
  return `/api/appliance/photos/thumbnail?${params.toString()}`;
}

export function photoOriginalUrl(path: string) {
  const params = new URLSearchParams({ path });
  return `/api/appliance/photos/original?${params.toString()}`;
}

export async function fetchPhotoLibrary(
  search = "",
  offset = 0,
  limit = 120,
): Promise<PhotoLibrary> {
  const params = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  if (search.trim()) params.set("search", search.trim());
  const response = await fetch(`/api/appliance/photos/library?${params}`, {
    headers: authHeader(),
  });
  if (!response.ok) throw await photoError(response, "照片库暂时无法读取");
  return (await response.json()) as PhotoLibrary;
}

export async function fetchPhotoStatus(): Promise<PhotoStatus> {
  const response = await fetch("/api/appliance/photos/status", {
    headers: authHeader(),
  });
  if (!response.ok) throw await photoError(response, "照片索引状态暂时不可用");
  return (await response.json()) as PhotoStatus;
}

export async function searchPhotos(
  query: string,
  limit = 50,
): Promise<PhotoSearchResult> {
  const response = await fetch("/api/appliance/photos/search", {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ query, limit }),
  });
  if (!response.ok) throw await photoError(response, "照片搜索失败");
  return (await response.json()) as PhotoSearchResult;
}

export async function createPhotoIndexPlan(
  includeFaces: boolean,
): Promise<PhotoIndexPlan> {
  const response = await fetch("/api/appliance/photos/plans/index", {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ includeFaces }),
  });
  if (!response.ok) throw await photoError(response, "无法生成照片索引计划");
  return (await response.json()) as PhotoIndexPlan;
}

export async function applyPhotoIndex(
  planId: string,
  includeFaces: boolean,
  approvalToken: string,
): Promise<{ schema: "echo.photos.index-job.v1"; job: PhotoIndexJob }> {
  const response = await fetch("/api/appliance/photos/plans/index/apply", {
    method: "POST",
    headers: {
      ...authHeader(),
      ...approvalHeader(approvalToken),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ planId, includeFaces }),
  });
  if (!response.ok)
    throw await photoError(response, "智能索引未启动，照片没有被修改");
  return (await response.json()) as {
    schema: "echo.photos.index-job.v1";
    job: PhotoIndexJob;
  };
}
