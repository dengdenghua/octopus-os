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
  sourceKind?: "photo-library";
  sourceId?: string;
  assetId?: string;
  assetRevision?: string;
  score?: number | null;
};

export type PhotoSource = {
  kind: "photo-library";
  id: string;
  revision?: string;
};

export type PhotoLibrary = {
  schema: "echo.photos.library.v1";
  source?: PhotoSource;
  total: number;
  offset: number;
  limit: number;
  scanTruncated: boolean;
  unsafeLinksSkipped: number;
  items: PhotoItem[];
};

export type PhotoIndexJob = {
  state:
    | "idle"
    | "running"
    | "pausing"
    | "paused"
    | "cancelling"
    | "cancelled"
    | "succeeded"
    | "failed";
  jobId: string | null;
  planId: string | null;
  includeFaces: boolean;
  cleanupOnly?: boolean;
  startedAt: number | null;
  completedAt: number | null;
  result: {
    indexed?: number;
    reused?: number;
    embedded?: number;
    removed?: number;
    faces?: number;
    semantic?: boolean;
    partial?: boolean;
    paused?: boolean;
    resource_limited?: boolean;
  } | null;
  error: string | null;
};

export type PhotoModelError = {
  model: "text" | "vision" | "faces";
  code:
    | "runtime_import_failed"
    | "unsupported_quantization"
    | "invalid_model_configuration"
    | "model_load_failed";
};

export type PhotoFeatureReadiness = {
  available: boolean;
  state:
    | "disabled"
    | "unavailable"
    | "dependencies-ready"
    | "loading"
    | "load-failed"
    | "loaded";
  missingDependencies: string[];
  modelsLoaded: boolean;
  modelErrors?: PhotoModelError[];
};

export type PhotoReadiness = {
  schema: "echo.photos.readiness.v1";
  browseAvailable: boolean;
  previewAvailable: boolean;
  semantic: PhotoFeatureReadiness;
  faces: PhotoFeatureReadiness;
  modelDownloadMayBeRequired: boolean;
  inference?: {
    maxConcurrent: number;
    active: number;
    waiting: number;
    available: number;
    processShared: boolean;
  };
};

export type PhotoStatus = {
  schema: "echo.photos.status.v1";
  source?: PhotoSource;
  library: {
    imageCount: number;
    scanTruncated: boolean;
    unsafeLinksSkipped: number;
    scanErrors?: number;
  };
  index: {
    backendAvailable: boolean;
    canManage?: boolean;
    cleanupAvailable?: boolean;
    readiness?: PhotoReadiness;
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
  source?: PhotoSource;
  query: string;
  mode: "semantic" | "filename";
  total: number;
  items: PhotoItem[];
};

export type PhotoIndexBlocker = {
  code:
    | "NO_IMAGES"
    | "PHOTO_SCAN_INCOMPLETE"
    | "INDEX_UNREADABLE"
    | "EMPTY_INDEX_CLEANUP_UNAVAILABLE"
    | "AGENT_INDEX_UNAVAILABLE"
    | "INDEX_RUNNING"
    | "SEMANTIC_DISABLED"
    | "SEMANTIC_DEPENDENCIES_MISSING"
    | "FACE_DEPENDENCIES_MISSING";
  message: string;
};

export type PhotoIndexWarning = {
  code:
    | "UNSAFE_LINKS_PRESENT"
    | "MODEL_DOWNLOAD_MAY_BE_REQUIRED"
    | "EMPTY_LIBRARY_CLEANUP";
  message: string;
};

export type PhotoIndexPlan = {
  schema: "echo.photos.index-plan.v1";
  source?: PhotoSource;
  planId: string;
  operation: "build";
  cleanupOnly?: boolean;
  libraryFingerprint: string;
  imageCount: number;
  unsafeLinks: number;
  scanTruncated: boolean;
  maxFiles: number;
  scanErrors?: number;
  includeFaces: boolean;
  readiness?: PhotoReadiness;
  ready: boolean;
  blockers: PhotoIndexBlocker[];
  warnings: PhotoIndexWarning[];
  requiresApproval: true;
  approvalAction: "photos.index.build";
  approvalTarget: string;
  changes: Array<{ field: string; before: unknown; after: unknown }>;
};

export class PhotoIndexConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PhotoIndexConflictError";
  }
}

async function photoError(
  response: Response,
  fallback: string,
  messages: Record<string, string> = {},
) {
  const detail = await response
    .json()
    .then((body) => body?.detail)
    .catch(() => null);
  if (typeof detail === "string" && detail.trim()) return new Error(detail);
  if (detail && typeof detail === "object") {
    const code = detail.error ?? detail.code;
    if (typeof code === "string" && Object.hasOwn(messages, code))
      return new Error(messages[code]);
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
  if (!response.ok) {
    const error = await photoError(response, "智能索引未启动，照片没有被修改");
    if (response.status === 409)
      throw new PhotoIndexConflictError(error.message);
    throw error;
  }
  return (await response.json()) as {
    schema: "echo.photos.index-job.v1";
    job: PhotoIndexJob;
  };
}

export async function cancelPhotoIndexJob(
  jobId: string,
): Promise<{ schema: "echo.photos.index-job.v1"; job: PhotoIndexJob }> {
  const response = await fetch(
    `/api/appliance/photos/index-jobs/${encodeURIComponent(jobId)}/cancel`,
    { method: "POST", headers: authHeader() },
  );
  if (!response.ok)
    throw await photoError(response, "未能停止索引，请刷新任务状态后重试", {
      photo_index_job_changed: "索引任务已变化，请查看最新状态后重试。",
      photo_index_not_owner: "当前连接无法停止该任务，请刷新状态后重试。",
      photo_index_cancel_unavailable:
        "当前版本暂不支持停止索引，请等待本次任务完成。",
      photo_index_job_state_unavailable:
        "停止请求未能保存，请检查设备存储空间与写入权限后重试。",
    });
  return (await response.json()) as {
    schema: "echo.photos.index-job.v1";
    job: PhotoIndexJob;
  };
}

export async function pausePhotoIndexJob(
  jobId: string,
): Promise<{ schema: "echo.photos.index-job.v1"; job: PhotoIndexJob }> {
  const response = await fetch(
    `/api/appliance/photos/index-jobs/${encodeURIComponent(jobId)}/pause`,
    { method: "POST", headers: authHeader() },
  );
  if (!response.ok)
    throw await photoError(response, "未能暂停索引，请刷新任务状态后重试", {
      photo_index_job_changed: "索引任务已变化，请查看最新状态后重试。",
      photo_index_not_owner: "当前连接无法暂停该任务，请刷新状态后重试。",
      photo_index_pause_unavailable:
        "当前版本暂不支持安全暂停，请等待本次任务完成。",
      photo_index_job_state_unavailable:
        "暂停请求未能保存，请检查设备存储空间与写入权限后重试。",
    });
  return (await response.json()) as {
    schema: "echo.photos.index-job.v1";
    job: PhotoIndexJob;
  };
}

export async function resumePhotoIndexJob(
  jobId: string,
): Promise<{ schema: "echo.photos.index-job.v1"; job: PhotoIndexJob }> {
  const response = await fetch(
    `/api/appliance/photos/index-jobs/${encodeURIComponent(jobId)}/resume`,
    { method: "POST", headers: authHeader() },
  );
  if (!response.ok)
    throw await photoError(response, "未能恢复索引，请刷新任务状态后重试", {
      photo_index_job_changed: "索引任务已变化，请查看最新状态后重试。",
      photo_index_not_owner: "当前连接无法恢复该任务，请刷新状态后重试。",
      photo_index_resume_unavailable:
        "图库或索引组件已变化，请重新预览索引计划后重试。",
      photo_index_job_state_unavailable:
        "恢复请求未能保存，请检查设备存储空间与写入权限后重试。",
    });
  return (await response.json()) as {
    schema: "echo.photos.index-job.v1";
    job: PhotoIndexJob;
  };
}
