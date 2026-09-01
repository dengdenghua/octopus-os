import { DownloadIcon, EyeIcon, LoaderIcon, PackageIcon } from "lucide-react";
import { Suspense, useCallback, lazy, useMemo, useRef } from "react";

import { Button } from "@/components/ui/button";
import { artifactDisplayPath, urlOfArtifact } from "@/core/artifacts/utils";
import { useArtifactContent } from "@/core/artifacts/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { useStreamdownPlugins } from "@/core/streamdown";
import {
  checkCodeFile,
  getFileExtensionDisplayName,
  getFileIcon,
  getFileName,
} from "@/core/utils/files";
import { cn } from "@/lib/utils";

import { ArtifactLink } from "../citations/artifact-link";
import { useThread } from "../messages/context";
import { OfficePreview } from "./artifact-file-detail";
import { useArtifacts } from "./context";
import { officeArtifactKind } from "./office-edit";
import { useInstallSkill } from "./use-install-skill";

export function ArtifactFileList({
  className,
  files,
  threadId,
}: {
  className?: string;
  files: string[];
  threadId: string;
}) {
  const { t } = useI18n();
  const { select: selectArtifact, setOpen } = useArtifacts();
  const { installingFile, install } = useInstallSkill(threadId);

  const handleClick = useCallback(
    (filepath: string) => {
      selectArtifact(filepath);
      setOpen(true);
    },
    [selectArtifact, setOpen],
  );

  const handleInstallSkill = useCallback(
    (e: React.MouseEvent, filepath: string) => {
      e.stopPropagation();
      e.preventDefault();
      void install(filepath);
    },
    [install],
  );

  return (
    <div
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-lg border border-border-default bg-background/80",
        className,
      )}
    >
      <div className="min-h-0 flex-1 overflow-y-auto">
        {files.map((file, index) => (
          <button
            key={file}
            type="button"
            className={cn(
              "flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-muted/50",
              index > 0 && "border-t border-border-subtle",
            )}
            onClick={() => handleClick(file)}
          >
            <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-muted/60">
              {getFileIcon(artifactDisplayPath(file), "size-4")}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-foreground">
                {getFileName(artifactDisplayPath(file))}
              </div>
              <div className="truncate text-xs text-muted-foreground">
                {getFileExtensionDisplayName(artifactDisplayPath(file))}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {file.endsWith(".skill") && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 gap-1 px-2 text-xs"
                  disabled={installingFile === file}
                  onClick={(e) => handleInstallSkill(e, file)}
                >
                  {installingFile === file ? (
                    <LoaderIcon className="size-3.5 animate-spin" />
                  ) : (
                    <PackageIcon className="size-3.5" />
                  )}
                  {t.common.install}
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon-sm"
                className="size-7"
                asChild
                aria-label={t.common.download}
              >
                <a
                  href={urlOfArtifact({
                    filepath: file,
                    threadId: threadId,
                    download: true,
                  })}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                >
                  <DownloadIcon className="size-3.5" />
                </a>
              </Button>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Inline preview grid for the "preview" tab of the drawer panel.   */
/*  Renders HTML / MD / Office artifacts directly as cards.         */
/* ------------------------------------------------------------------ */

const LazyStreamdown = lazy(
  () => import("@/components/ai-elements/streamdown-host"),
);

function InlineHtmlPreview({
  content,
  filepath,
  url,
}: {
  content: string;
  filepath: string;
  url?: string;
}) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const srcDoc = useMemo(() => content, [content]);

  return (
    <iframe
      className="size-full rounded-md border border-border-subtle"
      ref={iframeRef}
      sandbox="allow-scripts allow-forms"
      title={`${getFileName(filepath)} preview`}
      {...(url ? { src: url } : { srcDoc })}
    />
  );
}

function InlineMarkdownPreview({ content }: { content: string }) {
  const streamdownPlugins = useStreamdownPlugins();
  return (
    <div className="size-full overflow-auto px-3 py-2">
      <Suspense
        fallback={
          <div className="size-full whitespace-pre-wrap break-words py-4 text-sm text-muted-foreground">
            {content ?? ""}
          </div>
        }
      >
        <LazyStreamdown
          className="size-full"
          {...streamdownPlugins}
          components={{ a: ArtifactLink }}
        >
          {content ?? ""}
        </LazyStreamdown>
      </Suspense>
    </div>
  );
}

function ArtifactInlinePreviewCard({
  filepath,
  threadId,
}: {
  filepath: string;
  threadId: string;
}) {
  const { t } = useI18n();
  const { isMock } = useThread();
  const displayPath = artifactDisplayPath(filepath);
  const { language } = checkCodeFile(displayPath);
  const officeKind = officeArtifactKind(displayPath);
  const isWriteFile = filepath.startsWith("write-file:");
  const { content, url, isLoading } = useArtifactContent({
    filepath,
    threadId,
    // OfficePreview performs its own authenticated, format-aware fetch.
    // Reading xlsx/pptx/pdf through the text-content hook is both wasteful and
    // can corrupt the preview by treating a binary file as UTF-8.
    enabled: !isWriteFile && !officeKind,
  });
  const effectiveContent = isWriteFile ? "" : (content ?? "");

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border-subtle bg-background">
      <div className="flex shrink-0 items-center gap-2 border-b border-border-subtle px-3 py-1.5">
        <div className="flex size-6 shrink-0 items-center justify-center rounded bg-muted/60">
          {getFileIcon(displayPath, "size-3")}
        </div>
        <span className="min-w-0 flex-1 truncate text-xs font-medium">
          {getFileName(displayPath)}
        </span>
        <span className="shrink-0 text-[10px] text-muted-foreground uppercase">
          {officeKind ?? language}
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          className="size-6"
          asChild
          aria-label={t.common.download}
        >
          <a
            href={urlOfArtifact({ filepath, threadId, download: true })}
            target="_blank"
            rel="noopener noreferrer"
          >
            <DownloadIcon className="size-3" />
          </a>
        </Button>
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden">
        {isLoading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60 text-xs text-muted-foreground">
            {t.common.loading}…
          </div>
        )}
        {officeKind ? (
          <OfficePreview
            displayPath={displayPath}
            filepath={filepath}
            isMock={Boolean(isMock)}
            kind={officeKind}
            threadId={threadId}
          />
        ) : language === "markdown" ? (
          <InlineMarkdownPreview content={effectiveContent} />
        ) : language === "html" ? (
          <InlineHtmlPreview
            content={effectiveContent}
            filepath={displayPath}
            url={url}
          />
        ) : null}
      </div>
    </div>
  );
}

/**
 * Flat grid of inline preview cards (HTML / Markdown / Office / PDF). Used by the
 * drawer's "预览" tab — renders every previewable file as a card stacked
 * vertically. For the workbench's embedded artifacts panel use the
 * master-detail ``ArtifactInlinePreviewEmbedded`` instead.
 */
export function ArtifactInlinePreview({
  className,
  files,
  threadId,
}: {
  className?: string;
  files: string[];
  threadId: string;
}) {
  const { t } = useI18n();

  const previewable = useMemo(
    () =>
      (files ?? []).filter((f) => {
        const lang = checkCodeFile(artifactDisplayPath(f)).language;
        return (
          lang === "html" ||
          lang === "markdown" ||
          Boolean(officeArtifactKind(artifactDisplayPath(f)))
        );
      }),
    [files],
  );

  if (previewable.length === 0) {
    return (
      <div
        className={cn(
          "flex min-h-0 flex-col items-center justify-center gap-2 p-6 text-muted-foreground",
          className,
        )}
      >
        <EyeIcon className="size-8 opacity-40" />
        <p className="text-xs">{t.conversation.noPreviewArtifacts}</p>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-2",
        className,
      )}
    >
      {previewable.map((filepath) => (
        <ArtifactInlinePreviewCard
          key={filepath}
          filepath={filepath}
          threadId={threadId}
        />
      ))}
    </div>
  );
}
