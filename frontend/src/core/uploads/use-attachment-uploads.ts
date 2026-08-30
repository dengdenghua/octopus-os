/**
 * Composer attachment uploads: start on attach, not on send.
 *
 * The composer used to hand raw ``File`` objects to the send path, which
 * uploaded them *after* the user hit send. That made a real progress bar
 * impossible (nothing was in flight yet) and pushed the completion signal into
 * a detached toast. This hook moves the upload to attach time so the chip
 * itself can show progress and the send button can wait for it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { uploadFilesWithProgress, type UploadedFileInfo } from "./api";

export type AttachmentUploadStatus = "uploading" | "done" | "error";

export interface AttachmentUpload {
  /** Stable key shared with the composer chip (see ``uploadFileKey``). */
  key: string;
  file: File;
  status: AttachmentUploadStatus;
  /** 0–100. Reaches 100 only once the server response is parsed. */
  progress: number;
  uploaded?: UploadedFileInfo;
  error?: string;
}

export interface AttachmentUploadsApi {
  uploads: Map<string, AttachmentUpload>;
  /** True while any attachment is still in flight. */
  isUploading: boolean;
  /** True when at least one attachment failed and is still attached. */
  hasFailed: boolean;
  start: (entries: { key: string; file: File }[]) => void;
  retry: (key: string) => void;
  remove: (key: string) => void;
  reset: () => void;
  /** Server metadata for every attachment that finished uploading. */
  completed: () => UploadedFileInfo[];
}

/**
 * ``threadId`` may be a client-minted UUID for a thread the server has never
 * seen. That is fine: the upload endpoint runs with ``allow_create=True`` and
 * materializes the thread. The trade-off is deliberate — attaching a file to a
 * brand-new conversation creates it server-side even if the user never sends.
 */
export function useAttachmentUploads(
  threadId: string | undefined | null,
): AttachmentUploadsApi {
  const [uploads, setUploads] = useState<Map<string, AttachmentUpload>>(
    () => new Map(),
  );
  // Files are kept out of state-dependency chains so ``retry`` never needs a
  // fresh closure over the map.
  const filesRef = useRef<Map<string, File>>(new Map());
  const threadIdRef = useRef(threadId);
  threadIdRef.current = threadId;
  const disposedRef = useRef(false);

  useEffect(() => {
    disposedRef.current = false;
    return () => {
      disposedRef.current = true;
    };
  }, []);

  const patch = useCallback(
    (key: string, changes: Partial<AttachmentUpload>) => {
      if (disposedRef.current) return;
      setUploads((current) => {
        const existing = current.get(key);
        if (!existing) return current;
        const next = new Map(current);
        next.set(key, { ...existing, ...changes });
        return next;
      });
    },
    [],
  );

  const run = useCallback(
    (key: string, file: File) => {
      const tid = threadIdRef.current;
      if (!tid) {
        patch(key, {
          status: "error",
          error: "No conversation to upload into",
        });
        return;
      }
      void uploadFilesWithProgress(tid, [file], {
        onProgress: (percent) => patch(key, { progress: percent }),
      })
        .then((response) => {
          const uploaded = response.files?.[0];
          if (!uploaded) {
            patch(key, {
              status: "error",
              error: "Upload returned no file metadata",
            });
            return;
          }
          patch(key, { status: "done", progress: 100, uploaded });
        })
        .catch((error: unknown) => {
          patch(key, {
            status: "error",
            error: error instanceof Error ? error.message : "Upload failed",
          });
        });
    },
    [patch],
  );

  const start = useCallback(
    (entries: { key: string; file: File }[]) => {
      if (entries.length === 0) return;
      const fresh = entries.filter(({ key }) => !filesRef.current.has(key));
      if (fresh.length === 0) return;
      for (const { key, file } of fresh) filesRef.current.set(key, file);
      setUploads((current) => {
        const next = new Map(current);
        for (const { key, file } of fresh) {
          next.set(key, { key, file, status: "uploading", progress: 0 });
        }
        return next;
      });
      // Parallel is fine here: the composer caps attachment count and each
      // upload is a single small request.
      for (const { key, file } of fresh) run(key, file);
    },
    [run],
  );

  const retry = useCallback(
    (key: string) => {
      const file = filesRef.current.get(key);
      if (!file) return;
      patch(key, { status: "uploading", progress: 0, error: undefined });
      run(key, file);
    },
    [patch, run],
  );

  const remove = useCallback((key: string) => {
    filesRef.current.delete(key);
    setUploads((current) => {
      if (!current.has(key)) return current;
      const next = new Map(current);
      next.delete(key);
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    filesRef.current.clear();
    setUploads((current) => (current.size === 0 ? current : new Map()));
  }, []);

  const completed = useCallback(() => {
    const out: UploadedFileInfo[] = [];
    for (const entry of uploads.values()) {
      if (entry.status === "done" && entry.uploaded) out.push(entry.uploaded);
    }
    return out;
  }, [uploads]);

  let isUploading = false;
  let hasFailed = false;
  for (const entry of uploads.values()) {
    if (entry.status === "uploading") isUploading = true;
    else if (entry.status === "error") hasFailed = true;
  }

  // Consumers list this object in `useCallback` deps (the composer's submit
  // and attach handlers). Without a memo it would be a new identity on every
  // render and rebuild those callbacks each keystroke.
  return useMemo(
    () => ({
      uploads,
      isUploading,
      hasFailed,
      start,
      retry,
      remove,
      reset,
      completed,
    }),
    [
      uploads,
      isUploading,
      hasFailed,
      start,
      retry,
      remove,
      reset,
      completed,
    ],
  );
}
