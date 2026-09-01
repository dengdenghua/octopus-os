import { getBackendBaseURL } from "@/core/config";
import { authHeaders } from "@/core/auth/api";

export type NASMode = "efficiency" | "privacy";

export interface NASManifest {
  service: string;
  version: string;
  role: string;
  capabilities: string[];
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

export function updateNASPolicy(policy: NASPolicy): Promise<NASPolicy> {
  return request("/v1/policy", {
    method: "PUT",
    body: JSON.stringify(policy),
  });
}

export function listNASSources(): Promise<NASSource[]> {
  return request("/v1/sources");
}

export function listNASDirectory(path: string): Promise<NASDirectoryEntry[]> {
  return request(`/v1/browse?path=${encodeURIComponent(path)}`);
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

export async function loadNASAssetURL(path: string): Promise<string> {
  const response = await fetch(`${getNASBaseURL()}${path}`, {
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
