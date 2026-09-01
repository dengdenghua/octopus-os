/**
 * Echo OS NAS 文件管理器(前端 API 客户端)。
 * 对接 /api/appliance/files/*,删除走回收站语义。
 */

import { authHeader } from "@/appliance/auth";
import { approvalHeader } from "@/appliance/approval";

export type FileEntry = {
  name: string;
  path: string; // root 相对路径
  kind: "dir" | "file";
  size: number;
  mtime: number;
};

export type TrashEntry = {
  id: string;
  name: string;
  original: string;
  kind: "dir" | "file";
  trashed_at: number;
};

export type StorageUsage = {
  schema: "echo.storage.usage.v1";
  readOnly: true;
  generatedAt: number;
  disk: {
    totalBytes: number;
    usedBytes: number;
    freeBytes: number;
    reserveBytes: number;
    availableForUploadsBytes: number;
    usedPercent: number;
  };
  library: {
    logicalBytes: number;
    files: number;
    directories: number;
    scannedEntries: number;
    maxEntries: number;
    truncated: boolean;
    skippedLinks: number;
  };
  categories: Array<{
    id: "photos" | "videos" | "audio" | "documents" | "archives" | "other";
    bytes: number;
    files: number;
  }>;
  topFolders: Array<{ name: string; bytes: number; files: number }>;
  trash: { bytes: number; files: number };
  uploads: { reservedBytes: number; active: number };
  quotas: Array<{
    path: string;
    limitBytes: number;
    usedBytes: number;
    reservedBytes: number;
    availableBytes: number;
    estimated: boolean;
  }>;
};

export type TransferProgress = {
  loaded: number;
  total: number;
  percent: number;
};

export type UploadReceipt = {
  ok: boolean;
  entry: FileEntry;
  sha256: string;
  hashVerified: boolean;
};

type UploadSession = {
  ok: boolean;
  sessionId: string;
  target: string;
  expectedBytes: number;
  uploadedBytes: number;
  chunkBytes: number;
  sha256Expected: boolean;
  fingerprint: string | null;
  updatedAt: number;
  quotaBlocked?: boolean;
};

export type UploadOptions = {
  signal?: AbortSignal;
  control?: ResumableUploadController;
  retries?: number;
  retryDelayMs?: number;
};

export type DownloadOptions = {
  signal?: AbortSignal;
  retries?: number;
  retryDelayMs?: number;
};

type WritableDownload = {
  write(data: Uint8Array): Promise<void>;
  seek(position: number): Promise<void>;
  truncate(size: number): Promise<void>;
  close(): Promise<void>;
  abort(reason?: unknown): Promise<void>;
};

type DownloadFileHandle = {
  createWritable(): Promise<WritableDownload>;
};

type SaveFilePicker = (options: {
  suggestedName: string;
}) => Promise<DownloadFileHandle>;

export class ResumableUploadController {
  private readonly abortController = new AbortController();
  private paused = false;
  private resumeWaiters: Array<() => void> = [];

  get signal(): AbortSignal {
    return this.abortController.signal;
  }

  get isPaused(): boolean {
    return this.paused;
  }

  pause(): void {
    if (!this.signal.aborted) this.paused = true;
  }

  resume(): void {
    this.paused = false;
    for (const resolve of this.resumeWaiters.splice(0)) resolve();
  }

  cancel(): void {
    this.abortController.abort();
    this.resume();
  }

  async waitUntilRunnable(): Promise<void> {
    if (this.signal.aborted) throw new DOMException("上传已取消", "AbortError");
    if (!this.paused) return;
    await new Promise<void>((resolve) => this.resumeWaiters.push(resolve));
    if (this.signal.aborted) throw new DOMException("上传已取消", "AbortError");
  }
}

export const RESUMABLE_UPLOAD_THRESHOLD_BYTES = 16 * 1024 * 1024;

class NonRetryableUploadError extends Error {}
class NonRetryableDownloadError extends Error {}

export const FILE_SERVICE_UNAVAILABLE_MESSAGE =
  "NAS 文件服务尚未启用。请在系统设置中完成存储配置，或稍后重试。";

export class FileServiceUnavailableError extends Error {
  constructor(message = FILE_SERVICE_UNAVAILABLE_MESSAGE) {
    super(message);
    this.name = "FileServiceUnavailableError";
  }
}

function fileApiDetail(text: string): unknown {
  try {
    return (JSON.parse(text) as { detail?: unknown }).detail;
  } catch {
    return null;
  }
}

function fileApiError(
  response: Response,
  text: string,
  fallback: string,
): Error {
  const detail = fileApiDetail(text);
  const routeMissing = response.status === 404 && detail === "Not Found";
  if (
    routeMissing ||
    response.status === 502 ||
    response.status === 503 ||
    response.status === 504
  ) {
    return new FileServiceUnavailableError();
  }
  if (typeof detail === "string" && detail.trim()) {
    return new Error(detail.trim());
  }
  return new Error(text.trim() || `${fallback} → ${response.status}`);
}

async function jsonGet<T>(url: string): Promise<T> {
  let r: Response;
  try {
    r = await fetch(url, { headers: authHeader() });
  } catch {
    throw new FileServiceUnavailableError();
  }
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw fileApiError(r, text, "无法读取 NAS 文件");
  }
  return (await r.json()) as T;
}

async function jsonPost<T>(
  url: string,
  body: unknown,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: {
      ...authHeader(),
      ...extraHeaders,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r
      .json()
      .then((b) => b?.detail)
      .catch(() => null);
    throw new Error(detail || `POST ${url} → ${r.status}`);
  }
  return (await r.json()) as T;
}

function xhrError(xhr: XMLHttpRequest, fallback: string): Error {
  try {
    const body = JSON.parse(xhr.responseText) as { detail?: string };
    if (body.detail) return new Error(body.detail);
  } catch {
    // 非 JSON 错误页使用统一提示。
  }
  return new Error(`${fallback} → ${xhr.status || "网络错误"}`);
}

function progressOf(loaded: number, total: number): TransferProgress {
  return {
    loaded,
    total,
    percent: total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0,
  };
}

async function responseDetail(
  response: Response,
  fallback: string,
): Promise<string> {
  const detail = await response
    .clone()
    .json()
    .then((body) => body?.detail)
    .catch(() => null);
  if (typeof detail === "string" && detail) return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  const text = await response.text().catch(() => "");
  return text || `${fallback} → ${response.status}`;
}

async function sha256Hex(data: Blob | ArrayBuffer): Promise<string> {
  const bytes = data instanceof Blob ? await data.arrayBuffer() : data;
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

async function uploadFingerprint(file: File): Promise<string> {
  const sampleBytes = 64 * 1024;
  const first = await file.slice(0, sampleBytes).arrayBuffer();
  const lastStart = Math.max(first.byteLength, file.size - sampleBytes);
  const last = await file.slice(lastStart).arrayBuffer();
  const identity = new TextEncoder().encode(
    `${file.size}:${file.lastModified}:${file.type}\n`,
  );
  const combined = new Uint8Array(
    identity.byteLength + first.byteLength + last.byteLength,
  );
  combined.set(identity);
  combined.set(new Uint8Array(first), identity.byteLength);
  combined.set(new Uint8Array(last), identity.byteLength + first.byteLength);
  return sha256Hex(combined.buffer);
}

function uploadTarget(path: string, filename: string): string {
  return [path.replace(/^\/+|\/+$/g, ""), filename].filter(Boolean).join("/");
}

async function uploadStorageKey(
  path: string,
  file: File,
  fingerprint: string,
): Promise<string> {
  const identity = new TextEncoder().encode(
    `${uploadTarget(path, file.name)}\n${file.size}\n${fingerprint}`,
  );
  return `echo.upload.session.${await sha256Hex(identity.buffer)}`;
}

function storedSessionId(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storeSessionId(key: string, sessionId: string | null): void {
  try {
    if (sessionId) localStorage.setItem(key, sessionId);
    else localStorage.removeItem(key);
  } catch {
    // 隐私模式或受限 WebView 中仍可完成当前会话，只是不跨刷新恢复。
  }
}

async function getUploadSession(
  sessionId: string,
): Promise<UploadSession | null> {
  const response = await fetch(
    `/api/appliance/files/upload/sessions/${encodeURIComponent(sessionId)}`,
    { headers: authHeader() },
  );
  if (response.status === 404) return null;
  if (!response.ok)
    throw new Error(await responseDetail(response, "读取上传会话失败"));
  return (await response.json()) as UploadSession;
}

async function createUploadSession(
  path: string,
  file: File,
  fingerprint: string,
): Promise<UploadSession> {
  return jsonPost<UploadSession>("/api/appliance/files/upload/sessions", {
    path,
    filename: file.name,
    size: file.size,
    overwrite: false,
    fingerprint,
  });
}

async function cancelUploadSession(sessionId: string): Promise<void> {
  await fetch(
    `/api/appliance/files/upload/sessions/${encodeURIComponent(sessionId)}`,
    {
      method: "DELETE",
      headers: authHeader(),
    },
  ).catch(() => undefined);
}

function retryWait(delayMs: number, signal?: AbortSignal): Promise<void> {
  if (delayMs <= 0) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("上传已取消", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, delayMs);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function uploadFileResumable(
  path: string,
  file: File,
  onProgress: ((progress: TransferProgress) => void) | undefined,
  options: UploadOptions,
): Promise<UploadReceipt> {
  const fingerprint = await uploadFingerprint(file);
  const storageKey = await uploadStorageKey(path, file, fingerprint);
  const expectedTarget = uploadTarget(path, file.name);
  let session: UploadSession | null = null;
  const previousSessionId = storedSessionId(storageKey);
  if (previousSessionId) {
    session = await getUploadSession(previousSessionId);
    if (
      !session ||
      session.target !== expectedTarget ||
      session.expectedBytes !== file.size ||
      session.fingerprint !== fingerprint
    ) {
      storeSessionId(storageKey, null);
      session = null;
    }
  }
  if (!session) {
    session = await createUploadSession(path, file, fingerprint);
    storeSessionId(storageKey, session.sessionId);
  }
  let activeSession: UploadSession = session;

  const signal = options.control?.signal ?? options.signal;
  const retries = Math.max(0, options.retries ?? 3);
  const retryDelayMs = Math.max(0, options.retryDelayMs ?? 250);
  try {
    let offset = activeSession.uploadedBytes;
    if (offset < 0 || offset > file.size) throw new Error("服务器上传偏移无效");
    onProgress?.(progressOf(offset, file.size));
    while (offset < file.size) {
      await options.control?.waitUntilRunnable();
      if (signal?.aborted) throw new DOMException("上传已取消", "AbortError");
      const end = Math.min(file.size, offset + activeSession.chunkBytes);
      const chunk = file.slice(offset, end);
      const chunkDigest = await sha256Hex(chunk);
      let advancedTo: number | null = null;
      let lastError: unknown = null;
      for (let attempt = 0; attempt <= retries; attempt += 1) {
        try {
          const response = await fetch(
            `/api/appliance/files/upload/sessions/${encodeURIComponent(activeSession.sessionId)}/chunk`,
            {
              method: "PUT",
              headers: {
                ...authHeader(),
                "Content-Type": "application/octet-stream",
                "Upload-Offset": String(offset),
                "Upload-Chunk-SHA256": chunkDigest,
              },
              body: chunk,
              signal,
            },
          );
          if (response.status === 409) {
            const body = await response.json().catch(() => null);
            const serverOffset = body?.detail?.uploadedBytes;
            if (Number.isInteger(serverOffset) && serverOffset >= offset) {
              advancedTo = serverOffset;
              break;
            }
          }
          if (!response.ok) {
            const message = await responseDetail(response, "上传分块失败");
            if (response.status < 500 || response.status === 507)
              throw new NonRetryableUploadError(message);
            lastError = new Error(message);
          } else {
            const updated = (await response.json()) as UploadSession;
            advancedTo = updated.uploadedBytes;
            activeSession = updated;
            break;
          }
        } catch (error) {
          if (signal?.aborted) throw error;
          if (error instanceof NonRetryableUploadError) throw error;
          lastError = error;
          const recovered: UploadSession | null = await getUploadSession(
            activeSession.sessionId,
          ).catch(() => null);
          if (recovered && recovered.uploadedBytes > offset) {
            activeSession = recovered;
            advancedTo = recovered.uploadedBytes;
            break;
          }
        }
        if (attempt < retries) {
          await retryWait(retryDelayMs * 2 ** attempt, signal);
        }
      }
      if (advancedTo === null || advancedTo <= offset || advancedTo > end) {
        throw lastError instanceof Error
          ? lastError
          : new Error("上传分块未被服务器接受");
      }
      offset = advancedTo;
      onProgress?.(progressOf(offset, file.size));
    }
    await options.control?.waitUntilRunnable();
    if (signal?.aborted) throw new DOMException("上传已取消", "AbortError");
    const receipt = await jsonPost<UploadReceipt>(
      `/api/appliance/files/upload/sessions/${encodeURIComponent(activeSession.sessionId)}/complete`,
      {},
    );
    storeSessionId(storageKey, null);
    return receipt;
  } catch (error) {
    if (signal?.aborted) {
      await cancelUploadSession(activeSession.sessionId);
      storeSessionId(storageKey, null);
      throw new Error("上传已取消");
    }
    throw error;
  }
}

function totalDownloadBytes(response: Response, loaded: number): number {
  const contentRange = response.headers.get("Content-Range");
  const match = contentRange?.match(/^bytes \d+-\d+\/(\d+)$/i);
  if (match) return Number(match[1]);
  const contentLength = Number(response.headers.get("Content-Length") || 0);
  return Number.isFinite(contentLength) && contentLength > 0
    ? loaded + contentLength
    : 0;
}

async function streamDownloadToHandle(
  path: string,
  handle: DownloadFileHandle,
  onProgress: ((progress: TransferProgress) => void) | undefined,
  options: DownloadOptions,
): Promise<void> {
  const writable = await handle.createWritable();
  const retries = Math.max(0, options.retries ?? 3);
  const retryDelayMs = Math.max(0, options.retryDelayMs ?? 250);
  let loaded = 0;
  let total = 0;
  try {
    for (let attempt = 0; ; attempt += 1) {
      if (options.signal?.aborted) {
        throw new DOMException("下载已取消", "AbortError");
      }
      try {
        const response = await fetch(
          `/api/appliance/files/download?path=${encodeURIComponent(path)}`,
          {
            headers: {
              ...authHeader(),
              ...(loaded > 0 ? { Range: `bytes=${loaded}-` } : {}),
            },
            signal: options.signal,
          },
        );
        if (!response.ok) {
          const message = await responseDetail(response, "下载失败");
          if (response.status < 500)
            throw new NonRetryableDownloadError(message);
          throw new Error(message);
        }
        if (loaded > 0 && response.status !== 206) {
          await writable.truncate(0);
          await writable.seek(0);
          loaded = 0;
        }
        total = totalDownloadBytes(response, loaded);
        const reader = response.body?.getReader();
        if (!reader) throw new Error("浏览器没有提供下载数据流");
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          await writable.write(value);
          loaded += value.byteLength;
          onProgress?.(progressOf(loaded, total));
        }
        if (total > 0 && loaded < total) throw new Error("下载提前中断");
        await writable.close();
        return;
      } catch (error) {
        if (options.signal?.aborted) throw error;
        if (error instanceof NonRetryableDownloadError) throw error;
        if (attempt >= retries) throw error;
        await writable.seek(loaded);
        await retryWait(retryDelayMs * 2 ** attempt, options.signal);
      }
    }
  } catch (error) {
    await writable.abort(error).catch(() => undefined);
    if (options.signal?.aborted) throw new Error("下载已取消");
    throw error;
  }
}

export function listDir(
  path: string,
): Promise<{ path: string; entries: FileEntry[] }> {
  return jsonGet(`/api/appliance/files/list?path=${encodeURIComponent(path)}`);
}

export function fetchStorageUsage(fresh = false): Promise<StorageUsage> {
  return jsonGet(`/api/appliance/files/usage${fresh ? "?fresh=true" : ""}`);
}

export async function uploadFile(
  path: string,
  file: File,
  onProgress?: (progress: TransferProgress) => void,
  options: UploadOptions = {},
): Promise<UploadReceipt> {
  if (file.size >= RESUMABLE_UPLOAD_THRESHOLD_BYTES) {
    return uploadFileResumable(path, file, onProgress, options);
  }
  await jsonPost<{
    ok: boolean;
    availableBytes: number;
    reserveBytes: number;
    maxUploadBytes: number;
  }>("/api/appliance/files/upload/preflight", {
    path,
    filename: file.name,
    size: file.size,
    overwrite: false,
  });
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/appliance/files/upload");
    for (const [name, value] of Object.entries(authHeader())) {
      xhr.setRequestHeader(name, value);
    }
    xhr.upload.onprogress = (event) => {
      onProgress?.(
        progressOf(
          event.loaded,
          event.lengthComputable ? event.total : file.size,
        ),
      );
    };
    xhr.onerror = () => reject(new Error("上传网络中断"));
    xhr.onabort = () => reject(new Error("上传已取消"));
    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(xhrError(xhr, "上传失败"));
        return;
      }
      try {
        resolve(JSON.parse(xhr.responseText) as UploadReceipt);
      } catch {
        reject(new Error("上传响应无效"));
      }
    };
    const form = new FormData();
    form.append("path", path);
    form.append("size", String(file.size));
    form.append("file", file, file.name);
    const signal = options.control?.signal ?? options.signal;
    if (signal) {
      if (signal.aborted) {
        reject(new Error("上传已取消"));
        return;
      }
      signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }
    xhr.send(form);
  });
}

export async function downloadFile(
  path: string,
  filename: string,
  onProgress?: (progress: TransferProgress) => void,
  options: DownloadOptions = {},
): Promise<void> {
  const picker = (window as Window & { showSaveFilePicker?: SaveFilePicker })
    .showSaveFilePicker;
  if (picker) {
    let handle: DownloadFileHandle;
    try {
      handle = await picker({ suggestedName: filename });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new Error("下载已取消");
      }
      if (error instanceof DOMException && error.name === "NotAllowedError") {
        throw new Error("没有写入所选下载位置的权限");
      }
      return downloadFileWithoutPicker(path, filename, onProgress, options);
    }
    return streamDownloadToHandle(path, handle, onProgress, options);
  }
  return downloadFileWithoutPicker(path, filename, onProgress, options);
}

function downloadFileWithoutPicker(
  path: string,
  filename: string,
  onProgress: ((progress: TransferProgress) => void) | undefined,
  options: DownloadOptions,
): Promise<void> {
  const headers = authHeader();
  if (!("Authorization" in headers)) {
    return downloadFileWithBrowser(path, filename, options);
  }
  return downloadFileWithBlob(path, filename, onProgress, options, headers);
}

function downloadFileWithBrowser(
  path: string,
  filename: string,
  options: DownloadOptions,
): Promise<void> {
  if (options.signal?.aborted) return Promise.reject(new Error("下载已取消"));
  const anchor = document.createElement("a");
  anchor.href = `/api/appliance/files/download?path=${encodeURIComponent(path)}`;
  anchor.download = filename;
  anchor.hidden = true;
  document.body.appendChild(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
  }
  return Promise.resolve();
}

function downloadFileWithBlob(
  path: string,
  filename: string,
  onProgress: ((progress: TransferProgress) => void) | undefined,
  options: DownloadOptions,
  headers: Record<string, string>,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(
      "GET",
      `/api/appliance/files/download?path=${encodeURIComponent(path)}`,
    );
    xhr.responseType = "blob";
    for (const [name, value] of Object.entries(headers)) {
      xhr.setRequestHeader(name, value);
    }
    xhr.onprogress = (event) => {
      onProgress?.(
        progressOf(event.loaded, event.lengthComputable ? event.total : 0),
      );
    };
    xhr.onerror = () => reject(new Error("下载网络中断"));
    xhr.onabort = () => reject(new Error("下载已取消"));
    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(xhrError(xhr, "下载失败"));
        return;
      }
      const url = URL.createObjectURL(xhr.response as Blob);
      try {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        resolve();
      } finally {
        URL.revokeObjectURL(url);
      }
    };
    if (options.signal) {
      if (options.signal.aborted) {
        reject(new Error("下载已取消"));
        return;
      }
      options.signal.addEventListener("abort", () => xhr.abort(), {
        once: true,
      });
    }
    xhr.send();
  });
}

export function copyEntry(src: string, dst: string) {
  return jsonPost<{ ok: boolean; entry: FileEntry }>(
    "/api/appliance/files/copy",
    { src, dst },
  );
}

export function trashEntry(path: string) {
  return jsonPost<{ ok: boolean }>("/api/appliance/files/trash", { path });
}

export function listTrash(): Promise<{ entries: TrashEntry[] }> {
  return jsonGet("/api/appliance/files/trash");
}

export function restoreTrash(id: string) {
  return jsonPost<{ ok: boolean }>("/api/appliance/files/trash/restore", {
    id,
  });
}

export function emptyTrash(approvalToken: string) {
  return jsonPost<{ ok: boolean; emptied: number }>(
    "/api/appliance/files/trash/empty",
    {},
    approvalHeader(approvalToken),
  );
}

export function mkdir(path: string) {
  return jsonPost<{ ok: boolean }>("/api/appliance/files/mkdir", { path });
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[i]}`;
}
