/**
 * API functions for file uploads
 */

import type { components } from "@/core/api/openapi-types";

import { getBackendBaseURL } from "../config";
import { authHeaders } from "@/core/auth/api";

// Generated from the backend's ``UploadFileMetadata`` pydantic model
// (see runtime/sensing/siphon/uploads_router.py). Drift is blocked
// by the ``openapi-contract`` CI job · ADR-004.
//
// Note · this drop of the ``markdown_*`` quadruplet (markdown_file /
// markdown_path / markdown_virtual_path / markdown_artifact_url)
// that used to live on the hand-written interface · a grep of the
// frontend at the time of this replacement showed them declared but
// NEVER read. If a future feature needs markdown-companion fields,
// add them to the backend pydantic model · they flow through here
// automatically.
export type UploadedFileInfo = components["schemas"]["UploadFileMetadata"];
export type UploadResponse = components["schemas"]["UploadPostResponse"];
export type ListFilesResponse = components["schemas"]["UploadsListResponse"];

async function readErrorDetail(
  response: Response,
  fallback: string,
): Promise<string> {
  const error = await response.json().catch(() => ({ detail: fallback }));
  return error.detail ?? fallback;
}

/**
 * Upload files to a thread
 */
export async function uploadFiles(
  threadId: string,
  files: File[],
): Promise<UploadResponse> {
  const formData = new FormData();

  files.forEach((file) => {
    formData.append("files", file);
  });

  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/uploads`,
    {
      method: "POST",
      headers: authHeaders(),
      body: formData,
    },
  );

  if (!response.ok) {
    throw new Error(await readErrorDetail(response, "Upload failed"));
  }

  return response.json();
}

export type UploadProgressHandler = (percent: number) => void;

/**
 * Upload files to a thread while reporting byte-level progress.
 *
 * ``fetch`` cannot do this: it exposes no upload-progress event, so a
 * composer built on it can only ever show an indeterminate spinner. XHR is
 * still the only browser API that reports bytes sent, hence this parallel
 * transport. ``uploadFiles`` above stays as-is for callers that don't need
 * progress.
 */
export async function uploadFilesWithProgress(
  threadId: string,
  files: File[],
  options: { onProgress?: UploadProgressHandler; signal?: AbortSignal } = {},
): Promise<UploadResponse> {
  const { onProgress, signal } = options;
  const formData = new FormData();
  files.forEach((file) => {
    formData.append("files", file);
  });

  const url = `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/uploads`;

  return new Promise<UploadResponse>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Upload aborted", "AbortError"));
      return;
    }
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    // Do NOT set Content-Type — the browser must add the multipart boundary.
    for (const [key, value] of Object.entries(authHeaders())) {
      if (typeof value === "string") xhr.setRequestHeader(key, value);
    }

    const onAbort = () => xhr.abort();
    signal?.addEventListener("abort", onAbort, { once: true });
    const cleanup = () => signal?.removeEventListener("abort", onAbort);

    if (onProgress) {
      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable || event.total <= 0) return;
        // Cap at 99: the bytes are on the wire but the server has not yet
        // confirmed. 100 belongs to a parsed, successful response.
        const percent = Math.min(
          99,
          Math.round((event.loaded / event.total) * 100),
        );
        onProgress(percent);
      };
    }

    xhr.onload = () => {
      cleanup();
      if (xhr.status < 200 || xhr.status >= 300) {
        let detail = "Upload failed";
        try {
          const parsed = JSON.parse(xhr.responseText) as { detail?: string };
          if (typeof parsed.detail === "string" && parsed.detail) {
            detail = parsed.detail;
          }
        } catch {
          // Non-JSON error body — keep the generic message.
        }
        reject(new Error(detail));
        return;
      }
      try {
        const parsed = JSON.parse(xhr.responseText) as UploadResponse;
        onProgress?.(100);
        resolve(parsed);
      } catch {
        reject(new Error("Upload succeeded but the response was unreadable"));
      }
    };
    xhr.onerror = () => {
      cleanup();
      reject(new Error("Upload failed: network error"));
    };
    xhr.onabort = () => {
      cleanup();
      reject(new DOMException("Upload aborted", "AbortError"));
    };
    xhr.ontimeout = () => {
      cleanup();
      reject(new Error("Upload timed out"));
    };

    xhr.send(formData);
  });
}

/**
 * List all uploaded files for a thread
 */
export async function listUploadedFiles(
  threadId: string,
): Promise<ListFilesResponse> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/uploads/list`,
    { headers: authHeaders() },
  );

  if (!response.ok) {
    throw new Error(
      await readErrorDetail(response, "Failed to list uploaded files"),
    );
  }

  return response.json();
}

/**
 * Delete an uploaded file
 */
export async function deleteUploadedFile(
  threadId: string,
  filename: string,
): Promise<{ success: boolean; message: string }> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/uploads/${encodeURIComponent(filename)}`,
    {
      method: "DELETE",
      headers: authHeaders(),
    },
  );

  if (!response.ok) {
    throw new Error(await readErrorDetail(response, "Failed to delete file"));
  }

  return response.json();
}
