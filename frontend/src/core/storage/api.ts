import { getBackendBaseURL } from "@/core/config";
import { authHeaders } from "@/core/auth/api";
import { setSystemAiMode } from "@/core/privacy/api";

export type NASMode = "efficiency" | "privacy";

export interface NASManifest {
  service: string;
  version: string;
  role: string;
  capabilities: string[];
}

export interface NASTextDocument {
  resource_id: string;
  text: string;
  revision: string;
  size: number;
  encoding: "utf-8";
  complete: true;
}

export interface NASTextDiff {
  revision: string;
  patch: string;
  complete: true;
  changed: boolean;
}

export function supportsNASTextEditing(manifest: NASManifest | null): boolean {
  return manifest?.capabilities.includes("text-edit.v1") === true;
}

function textResourceId(resourceId: string): string {
  const appliance = parseApplianceFileResourceId(resourceId);
  const canonical = appliance
    ? storageFileResourceId(appliance.sourceId, appliance.path)
    : resourceId;
  if (!canonical || !parseStorageFileResourceId(canonical)) {
    throw new Error("文件资源标识无效，请重新读取目录");
  }
  return canonical;
}

function verifyTextDocument(document: NASTextDocument, resourceId: string) {
  if (
    document.resource_id !== resourceId ||
    document.complete !== true ||
    document.encoding !== "utf-8" ||
    typeof document.text !== "string" ||
    !/^[0-9a-f]{64}$/.test(document.revision) ||
    document.size !== new TextEncoder().encode(document.text).length ||
    document.size > 1_000_000
  )
    throw new Error("服务未返回完整文本和有效版本，已停止编辑");
  return document;
}

export async function readNASTextDocument(
  resourceId: string,
): Promise<NASTextDocument> {
  const canonical = textResourceId(resourceId);
  return verifyTextDocument(
    await request<NASTextDocument>(
      `/v1/files/${encodeURIComponent(canonical)}/text`,
    ),
    canonical,
  );
}

export async function saveNASTextDocument(
  resourceId: string,
  text: string,
  expectedRevision: string,
): Promise<NASTextDocument> {
  const canonical = textResourceId(resourceId);
  if (new TextEncoder().encode(text).length > 1_000_000) {
    throw new Error("文本超过 1 MB，无法保存");
  }
  return verifyTextDocument(
    await request<NASTextDocument>(
      `/v1/files/${encodeURIComponent(canonical)}/text`,
      {
        method: "PUT",
        body: JSON.stringify({ text, expected_revision: expectedRevision }),
      },
    ),
    canonical,
  );
}

export function diffNASTextDocument(
  resourceId: string,
  text: string,
  expectedRevision: string,
) {
  return request<NASTextDiff>(
    `/v1/files/${encodeURIComponent(textResourceId(resourceId))}/diff`,
    {
      method: "POST",
      body: JSON.stringify({ text, expected_revision: expectedRevision }),
    },
  );
}

export function nasTextEditError(error: unknown): string {
  if (error instanceof NASRequestError) {
    if (error.status === 409)
      return "文件已被其他窗口修改，草稿已保留，请读取最新版本后合并";
    if (error.status === 413)
      return "文本大小或差异行数超过编辑上限，请使用本地编辑器";
    if (error.status === 415) return "仅支持完整 UTF-8 文本，无法编辑此文件";
    if (error.status === 403) return "当前账号或文件没有编辑权限";
    if (error.status === 404) return "文件或编辑服务不存在，请刷新目录";
  }
  if (isNASConnectivityError(error) || error instanceof TypeError) {
    return "连接中断，草稿已保留；请读取最新版本确认是否保存成功";
  }
  return error instanceof Error ? error.message : "编辑失败，草稿已保留";
}

export interface NASPolicy {
  mode: NASMode;
  allow_cloud_answering: boolean;
  allow_snippet_export: boolean;
  max_exported_snippets: number;
  max_snippet_chars: number;
  redact_file_paths_for_cloud: boolean;
}

export interface NASSource {
  source_id: string;
  path: string;
  display_name: string;
  recursive: boolean;
  include_globs: string[];
  exclude_globs: string[];
  status: "authorized" | "indexing" | "ready" | "error";
  file_count: number;
  chunk_count: number;
  last_indexed_at: string | null;
  created_at: string;
}

export interface NASModel {
  model_id: string;
  role: "embedding" | "reranker" | "ocr" | "vision" | "answer";
  display_name: string;
  provider: string;
  status: "not_configured" | "available" | "loading" | "running" | "error";
  endpoint: string | null;
  context_tokens: number | null;
  embedding_dimensions: number | null;
  quantization: string | null;
  notes: string | null;
}

export interface NASIndexJob {
  job_id: string;
  source_ids: string[];
  status: "pending" | "running" | "complete" | "failed";
  full_rescan: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  message: string | null;
}

export interface NASSearchHit {
  chunk_id: string;
  source_id: string;
  path: string;
  title: string;
  snippet: string;
  score: number;
  citation: Record<string, unknown>;
  resource_id?: string | null;
}

export interface NASSearchResponse {
  query: string;
  mode: NASMode;
  hits: NASSearchHit[];
  message: string | null;
}

export interface NASAnswerResponse {
  answer: string;
  mode: NASMode;
  citations: NASSearchHit[];
  cloud_used: boolean;
  message: string;
}

export interface NASServiceStartResponse {
  ok: boolean;
  status: "started" | "already_running" | "not_found" | "error" | string;
  base_url: string;
  auth_token?: string | null;
}

export interface NASDirectoryEntry {
  name: string;
  path: string;
  type: "dir" | "file";
  size: number | null;
  resource_id?: string | null;
  source_id?: string | null;
}

type NASResourceCandidate = Pick<
  NASDirectoryEntry | NASSearchHit,
  "path" | "source_id" | "resource_id"
>;

function base64url(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function decodeBase64url(value: string): string | undefined {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) return undefined;
  try {
    const padded = value + "=".repeat((4 - (value.length % 4)) % 4);
    const binary = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  } catch {
    return undefined;
  }
}

/** Decode the appliance file identity so native and Storage views share one key. */
export function parseApplianceFileResourceId(
  value: string,
): { sourceId: string; path: string } | undefined {
  if (!value.startsWith("appliance-file:v1:")) return undefined;
  const parts = value.split(":");
  const sourceSegment = parts[2];
  const pathSegment = parts[3];
  if (
    parts.length !== 4 ||
    !sourceSegment ||
    !pathSegment ||
    !/^[0-9a-f]{64}$/i.test(sourceSegment)
  )
    return undefined;
  const path = decodeBase64url(pathSegment);
  if (
    !path ||
    path.split("/").some((part) => !part || part === "." || part === "..")
  ) {
    return undefined;
  }
  return { sourceId: sourceSegment.toLowerCase(), path };
}

/** Build the same source/path identity used by the local Storage Agent. */
export function storageFileResourceId(
  sourceId: string,
  path: string,
): string | undefined {
  const source = sourceId.trim();
  const rawLocator = path.replace(/\\/g, "/").trim();
  const rooted = rawLocator.startsWith("/");
  const locator = rawLocator.replace(/^\/+/, "");
  if (
    !source ||
    !locator ||
    locator.split("/").some((part) => !part || part === "." || part === "..")
  ) {
    return undefined;
  }
  return `storage-file:v1:${base64url(source)}:${base64url(rooted ? `/${locator}` : locator)}`;
}

/** Decode a Storage identity for embedded desktop navigation. */
export function parseStorageFileResourceId(
  value: string,
): { sourceId: string; path: string } | undefined {
  if (!value.startsWith("storage-file:v1:")) return undefined;
  const parts = value.split(":");
  if (parts.length !== 4) return undefined;
  const encodedSource = parts[2];
  const encodedLocator = parts[3];
  if (!encodedSource || !encodedLocator) return undefined;
  const sourceId = decodeBase64url(encodedSource);
  const encodedPath = decodeBase64url(encodedLocator);
  if (!sourceId || !encodedPath) return undefined;
  const rooted = encodedPath.startsWith("/");
  const path = rooted ? encodedPath.slice(1) : encodedPath;
  if (
    !path ||
    path.split("/").some((part) => !part || part === "." || part === "..")
  ) {
    return undefined;
  }
  return { sourceId, path: rooted ? `/${path}` : path };
}

/** Resolve the canonical identity shared by browse and search results. */
export function nasResourceId(entry: NASResourceCandidate): string | undefined {
  const provided =
    typeof entry.resource_id === "string" ? entry.resource_id.trim() : "";
  if (provided.startsWith("storage-file:v1:")) return provided;
  if (provided.startsWith("appliance-file:v1:")) {
    const parsed = parseApplianceFileResourceId(provided);
    const canonical =
      parsed && storageFileResourceId(parsed.sourceId, parsed.path);
    if (canonical) return canonical;
  }
  if (provided) return provided;
  const source =
    typeof entry.source_id === "string" ? entry.source_id.trim() : "";
  if (!source) return undefined;
  return storageFileResourceId(source, entry.path);
}

/** Stable UI selection key; source/path is only a legacy fallback. */
export function nasResourceSelectionKey(entry: NASResourceCandidate): string {
  const resource = nasResourceId(entry);
  if (resource) return resource;
  const source =
    typeof entry.source_id === "string" ? entry.source_id.trim() : "";
  return `legacy-storage:${source}:${entry.path}`;
}

export class NASRequestError extends Error {
  constructor(
    public readonly path: string,
    public readonly status: number,
    detail = "",
  ) {
    super(`NAS ${path} failed: ${status}${detail ? ` - ${detail}` : ""}`);
    this.name = "NASRequestError";
  }
}

export class NASRequestTimeoutError extends Error {
  constructor(public readonly path: string) {
    super(`NAS ${path} timed out`);
    this.name = "NASRequestTimeoutError";
  }
}

export function isNASAuthenticationError(error: unknown): boolean {
  return error instanceof NASRequestError && error.status === 401;
}

/**
 * True when the storage gateway is present but cannot serve the request yet,
 * or when its request deadline elapsed.  These failures are actionable as a
 * connection state in the UI; exposing the raw gateway status only adds
 * implementation detail to an otherwise recoverable offline state.
 */
export function isNASConnectivityError(error: unknown): boolean {
  if (error instanceof NASRequestTimeoutError) return true;
  return (
    error instanceof NASRequestError && [502, 503, 504].includes(error.status)
  );
}

function isNASDirectoryGatewayUnavailable(error: unknown): boolean {
  // A native desktop backend may omit the optional Storage router entirely,
  // which appears as a route-level 404. Treat only browse 404s as an absent
  // gateway; a 404 from other Storage resources remains a real resource error.
  return (
    isNASConnectivityError(error) ||
    (error instanceof NASRequestError &&
      error.status === 404 &&
      error.path.startsWith("/v1/browse"))
  );
}

export function getNASBaseURL(): string {
  return `${getBackendBaseURL()}/api/storage`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 8_000);
  const abortFromCaller = () => controller.abort();
  init?.signal?.addEventListener("abort", abortFromCaller, { once: true });
  try {
    const response = await fetch(`${getNASBaseURL()}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
        ...(init?.headers ?? {}),
      },
    });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new NASRequestError(path, response.status, text);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new NASRequestTimeoutError(path);
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
    init?.signal?.removeEventListener("abort", abortFromCaller);
  }
}

/**
 * Send a request to the echo-agent backend (where the video media_router is
 * mounted at `/media`). Unlike `request`, this does NOT target the NAS storage
 * service — the video semantic index lives in the agent's data dir.
 */
async function backendRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getBackendBaseURL()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new NASRequestError(path, response.status, text);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function startNASService(): Promise<NASServiceStartResponse> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/local-brain/storage/start`,
    {
      method: "POST",
      credentials: "include",
      headers: authHeaders(),
    },
  );
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(
      `Storage start failed: ${response.status}${text ? ` - ${text}` : ""}`,
    );
  }
  const body = (await response.json()) as NASServiceStartResponse;
  return body;
}

export function getNASManifest(): Promise<NASManifest> {
  return request("/v1/manifest");
}

export function getNASPolicy(): Promise<NASPolicy> {
  return request("/v1/policy");
}

export function listNASModels(): Promise<NASModel[]> {
  return request("/v1/models");
}

export function downloadNASModel(modelId: string): Promise<NASModel> {
  return request(`/v1/models/${encodeURIComponent(modelId)}/download`, {
    method: "POST",
  });
}

export function enableNASModel(modelId: string): Promise<NASModel> {
  return request(`/v1/models/${encodeURIComponent(modelId)}/enable`, {
    method: "POST",
  });
}

export function disableNASModel(modelId: string): Promise<NASModel> {
  return request(`/v1/models/${encodeURIComponent(modelId)}/disable`, {
    method: "POST",
  });
}

export async function updateNASPolicy(policy: NASPolicy): Promise<NASPolicy> {
  await setSystemAiMode(policy.mode);
  return getNASPolicy();
}

export function listNASSources(): Promise<NASSource[]> {
  return request("/v1/sources");
}

export async function listNASDirectory(
  path: string,
): Promise<NASDirectoryEntry[]> {
  try {
    return await request(`/v1/browse?path=${encodeURIComponent(path)}`);
  } catch (error) {
    if (!isNASDirectoryGatewayUnavailable(error)) throw error;
    // Directory browsing remains useful when the optional Storage sibling is
    // absent. The appliance endpoint applies the same authenticated path
    // scope and returns path-free resource identities for file references.
    try {
      const { listDir } = await import("@/appliance/files");
      const fallback = await listDir(path);
      return fallback.entries.map((entry) => ({
        name: entry.name,
        path: entry.path,
        type: entry.kind === "dir" ? "dir" : "file",
        size: entry.kind === "dir" ? null : entry.size,
        resource_id:
          nasResourceId({
            path: entry.path,
            resource_id: entry.resource_id,
          }) ??
          entry.resource_id ??
          null,
      }));
    } catch {
      throw error;
    }
  }
}

export function createNASSource(path: string): Promise<NASSource> {
  return request("/v1/sources", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export function deleteNASSource(sourceId: string): Promise<void> {
  return request(`/v1/sources/${encodeURIComponent(sourceId)}`, {
    method: "DELETE",
  });
}

export function createNASIndexJob(
  sourceIds: string[] = [],
): Promise<NASIndexJob> {
  return request("/v1/index/jobs", {
    method: "POST",
    body: JSON.stringify({ source_ids: sourceIds, full_rescan: false }),
  });
}

export function getNASIndexJob(jobId: string): Promise<NASIndexJob> {
  return request(`/v1/index/jobs/${encodeURIComponent(jobId)}`);
}

export function searchNAS(query: string): Promise<NASSearchResponse> {
  return request("/v1/search", {
    method: "POST",
    body: JSON.stringify({ query, top_k: 8, source_ids: [] }),
  });
}

export interface NASApp {
  app_id: string;
  name: string;
  path: string;
  category: string;
  bundle_id: string | null;
  icon_available: boolean;
}

export interface NASFileAsset {
  asset_id: string;
  source_id: string;
  name: string;
  path: string;
  resource_id?: string | null;
  extension: string;
  kind: "document" | "image" | "video";
  size: number;
  mtime_ns: number;
  indexed?: boolean;
  ai_labels?: string[];
}

export interface NASAlbum {
  label: string;
  count: number;
  cover_asset_id?: string;
}

export function listNASAlbums(): Promise<NASAlbum[]> {
  return request("/v1/albums");
}

export function listNASApps(): Promise<NASApp[]> {
  return request("/v1/apps");
}

export function openNASApp(
  appId: string,
): Promise<{ ok: boolean; app_id: string }> {
  return request(`/v1/apps/${encodeURIComponent(appId)}/open`, {
    method: "POST",
  });
}

export function revealNASApp(
  appId: string,
): Promise<{ ok: boolean; app_id: string }> {
  return request(`/v1/apps/${encodeURIComponent(appId)}/reveal`, {
    method: "POST",
  });
}

export function listNASFiles(
  kind: "document" | "image" | "video" = "document",
  limit = 500,
): Promise<NASFileAsset[]> {
  return request(`/v1/files?kind=${kind}&limit=${limit}`);
}

export function getNASAppIconURL(appId: string): string {
  return `${getNASBaseURL()}/v1/apps/${encodeURIComponent(appId)}/icon`;
}

export function getNASFileContentURL(assetId: string): string {
  return `${getNASBaseURL()}/v1/files/${encodeURIComponent(assetId)}/content`;
}

function authenticatedAssetURL(path: string): string {
  // Appliance resource URLs already cross the agent boundary. Keeping this
  // small escape hatch here lets the Storage fallback reuse the same preview
  // lifecycle without teaching the UI a second fetch/object-URL pipeline.
  return path.startsWith("/api/")
    ? `${getBackendBaseURL()}${path}`
    : `${getNASBaseURL()}${path}`;
}

export async function loadNASAssetURL(path: string): Promise<string> {
  const response = await fetch(authenticatedAssetURL(path), {
    headers: authHeaders(),
  });
  if (!response.ok) {
    throw new NASRequestError(
      path,
      response.status,
      await response.text().catch(() => ""),
    );
  }
  return URL.createObjectURL(await response.blob());
}

/** Read a bounded UTF-8 preview without making the whole source file durable. */
export async function loadNASAssetText(
  assetId: string,
  maxChars = 200_000,
): Promise<string> {
  const cleanLimit = Math.max(1, Math.min(1_000_000, Math.floor(maxChars)));
  const path = assetId.startsWith("appliance-file:v1:")
    ? `/api/appliance/files/resource/${encodeURIComponent(assetId.trim())}`
    : `/v1/files/${encodeURIComponent(assetId)}/content`;
  const response = await fetch(authenticatedAssetURL(path), {
    headers: authHeaders(),
  });
  if (!response.ok) {
    throw new NASRequestError(
      path,
      response.status,
      await response.text().catch(() => ""),
    );
  }
  if (!response.body) return (await response.text()).slice(0, cleanLimit);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = "";
  try {
    while (text.length < cleanLimit) {
      const next = await reader.read();
      if (next.done) {
        text += decoder.decode();
        break;
      }
      text += decoder.decode(next.value, { stream: true });
    }
  } finally {
    if (text.length >= cleanLimit) await reader.cancel().catch(() => undefined);
  }
  return text.slice(0, cleanLimit);
}

export function answerNAS(query: string): Promise<NASAnswerResponse> {
  return request("/v1/answer", {
    method: "POST",
    body: JSON.stringify({ query, top_k: 8, source_ids: [] }),
  });
}

export interface NASVideoIndexResponse {
  ok: boolean;
  video_count?: number;
  keyframe_count?: number;
  duration_sec?: number;
  incremental?: boolean;
  skipped?: number;
  message?: string;
}

export function triggerVideoIndex(
  incremental = true,
): Promise<NASVideoIndexResponse> {
  return backendRequest("/media/video/index", {
    method: "POST",
    body: JSON.stringify({ directory: ".", incremental }),
  });
}

export interface NASVideoSearchHit {
  video_path: string;
  time_sec: number;
  score: number;
}

export interface NASVideoAppearance {
  video_path: string;
  time_sec: number;
}

export interface NASVideoFaceGroup {
  person: number;
  count_faces: number;
  appearances: NASVideoAppearance[];
}

export interface NASVideoTag {
  label: string;
  score: number;
}

export interface NASVideoClassifyResult {
  video_path: string;
  tags: NASVideoTag[];
}

export interface NASVideoSpeechHit {
  video_path: string;
  start_sec: number;
  end_sec: number;
  text: string;
  score: number;
}

export interface NASVideoOcrHit {
  video_path: string;
  time_sec: number;
  text: string;
  score: number;
}

export interface NASVideoSearchResponse {
  ok: boolean;
  hits: NASVideoSearchHit[];
}

export interface NASVideoFaceGroupsResponse {
  ok: boolean;
  groups: NASVideoFaceGroup[];
}

export interface NASVideoClassifyResponse {
  ok: boolean;
  results: NASVideoClassifyResult[];
}

export interface NASVideoSpeechResponse {
  ok: boolean;
  hits: NASVideoSpeechHit[];
}

export interface NASVideoOcrResponse {
  ok: boolean;
  hits: NASVideoOcrHit[];
}

export function searchVideoByText(
  query: string,
  top_k = 10,
): Promise<NASVideoSearchResponse> {
  return backendRequest("/media/video/search", {
    method: "POST",
    body: JSON.stringify({ query, directory: ".", top_k }),
  });
}

export function searchVideoByFace(
  imagePath: string,
  top_k = 10,
): Promise<NASVideoSearchResponse> {
  return backendRequest("/media/video/search/face", {
    method: "POST",
    body: JSON.stringify({ image_path: imagePath, directory: ".", top_k }),
  });
}

export function searchVideoByImage(
  imagePath: string,
  top_k = 10,
): Promise<NASVideoSearchResponse> {
  return backendRequest("/media/video/search/image", {
    method: "POST",
    body: JSON.stringify({ image_path: imagePath, directory: ".", top_k }),
  });
}

export function searchVideoBySpeech(
  query: string,
  top_k = 10,
): Promise<NASVideoSpeechResponse> {
  return backendRequest("/media/video/search/speech", {
    method: "POST",
    body: JSON.stringify({ query, directory: ".", top_k }),
  });
}

export function listVideoFaceGroups(): Promise<NASVideoFaceGroupsResponse> {
  return backendRequest("/media/video/faces?directory=.&threshold=0.45");
}

export function classifyVideoTags(): Promise<NASVideoClassifyResponse> {
  return backendRequest("/media/video/classify", {
    method: "POST",
    body: JSON.stringify({ directory: ".", top_k: 5 }),
  });
}

export function ocrVideoKeyframes(
  query: string,
  top_k = 20,
): Promise<NASVideoOcrResponse> {
  return backendRequest("/media/video/ocr", {
    method: "POST",
    body: JSON.stringify({ query, directory: ".", top_k }),
  });
}

/**
 * Get the full URL for the video cover image from the agent backend.
 * Covers are generated dynamically on demand from the video file.
 */
export function getVideoCoverURL(videoPath: string, timeSec = 0): string {
  return `${getBackendBaseURL()}/media/video/cover?video_path=${encodeURIComponent(videoPath)}&time_sec=${timeSec}`;
}
