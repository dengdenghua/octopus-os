import {
  FileIcon,
  FileTextIcon,
  FileCodeIcon,
  FileJsonIcon,
  FileArchiveIcon,
  RotateCcwIcon,
  XIcon,
} from "lucide-react";

import type { Translations } from "@/core/i18n/locales";
import type { AttachmentUpload } from "@/core/uploads";
import type { PendingContextFile } from "./helpers";
import { imageFileKey, uploadFileKey } from "./helpers";

interface FileAttachmentProps {
  pendingFiles: PendingContextFile[];
  pendingImages: File[];
  pendingImagePreviews: Record<string, string>;
  pendingImageSources: Record<string, string>;
  onRemoveFile: (id: string) => void;
  onRemoveImage: (index: number) => void;
  isUploading?: boolean;
  /** Per-attachment upload state, keyed by ``uploadFileKey(file)``. */
  uploads?: Map<string, AttachmentUpload>;
  onRetryUpload?: (key: string) => void;
  t: Translations;
}

/** Progress bar pinned to the bottom edge of a chip. */
function UploadProgressBar({
  upload,
  t,
}: {
  upload: AttachmentUpload | undefined;
  t: Translations;
}) {
  if (!upload || upload.status === "done") return null;
  const failed = upload.status === "error";
  return (
    <div
      className="absolute bottom-0 left-0 right-0 h-1 bg-muted"
      role="progressbar"
      aria-valuenow={failed ? 0 : upload.progress}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={
        failed ? t.uploads.uploadFailed : t.uploads.uploadProgress(upload.progress)
      }
      data-upload-status={upload.status}
    >
      <div
        className={`h-full transition-[width] duration-200 ${
          failed ? "bg-danger" : "bg-primary"
        }`}
        style={{ width: failed ? "100%" : `${upload.progress}%` }}
      />
    </div>
  );
}

/** Retry affordance shown on a failed chip. */
function UploadRetryButton({
  upload,
  onRetry,
  t,
  className,
}: {
  upload: AttachmentUpload | undefined;
  onRetry?: (key: string) => void;
  t: Translations;
  className?: string;
}) {
  if (!upload || upload.status !== "error" || !onRetry) return null;
  return (
    <button
      type="button"
      onClick={() => onRetry(upload.key)}
      data-testid={`upload-retry-${upload.key}`}
      className={`flex size-5 shrink-0 items-center justify-center rounded bg-background/80 text-danger backdrop-blur-sm hover:bg-danger/10 ${className ?? ""}`}
      title={`${t.uploads.retryUpload}${upload.error ? `: ${upload.error}` : ""}`}
      aria-label={t.uploads.retryUpload}
    >
      <RotateCcwIcon className="size-3.5" />
    </button>
  );
}

function fileExt(name: string): string {
  const idx = name.lastIndexOf(".");
  return idx > 0 ? name.slice(idx + 1).toLowerCase() : "";
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function cleanFileName(name: string): string {
  return name
    .replace(
      /^[\s【】\[\]「」『』《》（）()]+/,
      "",
    )
    .replace(/[-_]?\s*(?:副本|copy|复件|\(\d+\))$/i, "")
    .trim();
}

type FileTypeInfo = {
  icon: React.ReactNode;
  bgClass: string;
  textClass: string;
};

function fileTypeInfo(ext: string): FileTypeInfo {
  const pdfLike = new Set(["pdf"]);
  const docLike = new Set(["doc", "docx", "txt", "md", "rtf", "odt"]);
  const sheetLike = new Set(["xls", "xlsx", "csv", "tsv", "ods"]);
  const codeLike = new Set([
    "js",
    "ts",
    "tsx",
    "jsx",
    "py",
    "go",
    "rs",
    "java",
    "c",
    "cpp",
    "h",
    "hpp",
    "cs",
    "rb",
    "php",
    "swift",
    "kt",
    "scala",
    "r",
    "sh",
    "bash",
    "zsh",
    "ps1",
    "sql",
    "dart",
    "lua",
    "pl",
    "erl",
    "ex",
    "elm",
    "fs",
    "fsx",
    "ml",
    "mli",
    "hs",
    "lhs",
    "clj",
    "cljs",
    "coffee",
    "scala",
    "groovy",
    "vhdl",
    "verilog",
    "tcl",
    "awk",
    "sed",
    "make",
    "cmake",
    "dockerfile",
    "jenkinsfile",
    "vue",
    "svelte",
    "astro",
  ]);
  const configLike = new Set([
    "json",
    "xml",
    "yaml",
    "yml",
    "toml",
    "ini",
    "conf",
    "cfg",
    "properties",
    "env",
    "lock",
  ]);
  const archiveLike = new Set([
    "zip",
    "rar",
    "7z",
    "tar",
    "gz",
    "bz2",
    "xz",
    "lz",
    "lzma",
    "z",
    "tgz",
    "tbz",
    "txz",
    "jar",
    "war",
    "ear",
  ]);
  const slideLike = new Set(["ppt", "pptx", "key", "odp"]);

  if (pdfLike.has(ext))
    return {
      icon: <FileTextIcon className="size-4" />,
      bgClass: "bg-destructive/10",
      textClass: "text-destructive",
    };
  if (docLike.has(ext))
    return {
      icon: <FileTextIcon className="size-4" />,
      bgClass: "bg-primary/10",
      textClass: "text-primary",
    };
  if (sheetLike.has(ext))
    return {
      icon: <FileTextIcon className="size-4" />,
      bgClass: "bg-accent/10",
      textClass: "text-accent",
    };
  if (slideLike.has(ext))
    return {
      icon: <FileTextIcon className="size-4" />,
      bgClass: "bg-secondary/10",
      textClass: "text-secondary",
    };
  if (codeLike.has(ext))
    return {
      icon: <FileCodeIcon className="size-4" />,
      bgClass: "bg-ring/10",
      textClass: "text-ring",
    };
  if (configLike.has(ext))
    return {
      icon: <FileJsonIcon className="size-4" />,
      bgClass: "bg-chart-3/10",
      textClass: "text-chart-3",
    };
  if (archiveLike.has(ext))
    return {
      icon: <FileArchiveIcon className="size-4" />,
      bgClass: "bg-muted",
      textClass: "text-muted-foreground",
    };
  return {
    icon: <FileIcon className="size-4" />,
    bgClass: "bg-muted",
    textClass: "text-muted-foreground",
  };
}

function FileTypeBadge({ ext }: { ext: string }) {
  const { icon, bgClass, textClass } = fileTypeInfo(ext);
  return (
    <span
      className={`flex size-8 shrink-0 items-center justify-center rounded-md ${bgClass} ${textClass}`}
      title={ext.toUpperCase()}
    >
      {icon}
    </span>
  );
}

function buildFileMeta(file: PendingContextFile): string {
  const parts: string[] = [];
  const ext = fileExt(file.name);
  if (ext) parts.push(ext.toUpperCase());
  if (file.file && typeof file.file.size === "number") {
    parts.push(formatFileSize(file.file.size));
  }
  if (parts.length === 0 && file.sourceLabel) {
    parts.push(file.sourceLabel);
  }
  if (parts.length === 0 && file.path) {
    parts.push(file.path);
  }
  return parts.join(" · ");
}

export function FileAttachment({
  pendingFiles,
  pendingImages,
  pendingImagePreviews,
  pendingImageSources,
  onRemoveFile,
  onRemoveImage,
  isUploading = false,
  uploads,
  onRetryUpload,
  t,
}: FileAttachmentProps) {
  if (pendingFiles.length === 0 && pendingImages.length === 0 && !isUploading) {
    return null;
  }
  return (
    <div className="flex flex-wrap items-center gap-2 overflow-x-auto px-3 pb-2 pt-1">
      {pendingFiles.map((file) => {
        const ext = fileExt(file.name);
        const displayName = cleanFileName(file.name);
        const meta = buildFileMeta(file);
        // Context files carry their own id, which is already ``uploadFileKey``
        // for browser uploads (see ``addPendingUploadFiles``).
        const upload = uploads?.get(file.id);
        const failed = upload?.status === "error";
        return (
          <div
            key={file.id}
            className={`group relative flex h-[52px] min-w-[180px] max-w-[260px] items-center gap-2.5 overflow-hidden rounded-md border bg-background px-2.5 py-1.5 shadow-sm ${
              failed ? "border-danger" : "border-border-default"
            }`}
            title={file.workDir ? `${file.path}\n${file.workDir}` : file.path}
          >
            <FileTypeBadge ext={ext} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-medium leading-tight text-foreground">
                {displayName}
              </span>
              <span
                className={`block truncate text-mini leading-tight ${
                  failed ? "text-danger" : "text-muted-foreground"
                }`}
              >
                {failed
                  ? t.uploads.uploadFailed
                  : upload?.status === "uploading"
                    ? t.uploads.uploadProgress(upload.progress)
                    : meta}
              </span>
            </span>
            <UploadRetryButton
              upload={upload}
              onRetry={onRetryUpload}
              t={t}
            />
            <button
              type="button"
              onClick={() => onRemoveFile(file.id)}
              className="flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100"
              title={t.chatInputBox.removeImage}
              aria-label={`${t.chatInputBox.removeImage}: ${displayName}`}
            >
              <XIcon className="size-3.5" />
            </button>
            <UploadProgressBar upload={upload} t={t} />
          </div>
        );
      })}
      {pendingImages.map((file, index) => {
        const key = imageFileKey(file);
        const url = pendingImagePreviews[key];
        const sourceLabel = pendingImageSources[key];
        // Images are keyed by ``imageFileKey`` in the composer but tracked by
        // ``uploadFileKey`` in the upload hook. They agree for real File
        // objects; the fallback keeps a pasted blob without a name working.
        const upload = uploads?.get(uploadFileKey(file)) ?? uploads?.get(key);
        const failed = upload?.status === "error";
        return (
          <div
            key={key}
            className={`group relative h-16 w-16 shrink-0 overflow-hidden rounded border ${
              failed ? "border-danger" : "border-border-default"
            }`}
          >
            {url && (
              <img
                src={url}
                alt={file.name}
                className="h-full w-full object-cover"
              />
            )}
            {sourceLabel ? (
              <div className="absolute bottom-0 left-0 right-0 truncate bg-black/45 px-1 py-0.5 text-xs font-medium text-white backdrop-blur-sm">
                {sourceLabel}
              </div>
            ) : null}
            {/* Percent rides the top-left corner so it never covers the
                source label, which says where the image came from. */}
            {upload && upload.status !== "done" ? (
              <div
                className={`absolute left-0.5 top-0.5 rounded px-1 text-mini font-medium text-white backdrop-blur-sm ${
                  failed ? "bg-danger/85" : "bg-black/60"
                }`}
              >
                {failed ? "!" : `${upload.progress}%`}
              </div>
            ) : null}
            <UploadRetryButton
              upload={upload}
              onRetry={onRetryUpload}
              t={t}
              className="absolute bottom-0.5 left-0.5"
            />
            <button
              type="button"
              onClick={() => onRemoveImage(index)}
              className="absolute right-0.5 top-0.5 flex size-5 items-center justify-center rounded-full bg-background/80 text-muted-foreground opacity-0 backdrop-blur-sm transition-opacity hover:text-foreground group-hover:opacity-100"
              title={t.chatInputBox.removeImage}
            >
              ×
            </button>
            <UploadProgressBar upload={upload} t={t} />
          </div>
        );
      })}
    </div>
  );
}
