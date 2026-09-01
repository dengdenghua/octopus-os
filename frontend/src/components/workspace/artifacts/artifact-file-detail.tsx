import {
  Code2Icon,
  CopyIcon,
  CrosshairIcon,
  Diff as DiffIcon,
  DownloadIcon,
  EyeIcon,
  LoaderIcon,
  PackageIcon,
  PencilIcon,
  RefreshCwIcon,
  SaveIcon,
  SendIcon,
  SparklesIcon,
  SquareArrowOutUpRightIcon,
  Undo2Icon,
  XIcon,
} from "lucide-react";
import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";
import type { StreamdownProps } from "streamdown";

import {
  Artifact,
  ArtifactAction,
  ArtifactActions,
  ArtifactContent,
  ArtifactHeader,
  ArtifactTitle,
} from "@/components/ai-elements/artifact";
import { Select, SelectItem } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import {
  SelectContent,
  SelectGroup,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { lazy } from "react";

const CodeEditor = lazy(() =>
  import("@/components/workspace/code-editor").then((m) => ({
    default: m.CodeEditor,
  })),
);
const LazyStreamdown = lazy(
  () => import("@/components/ai-elements/streamdown-host"),
);
const DiffViewer = lazy(() =>
  import("@/components/workspace/diff-viewer").then((m) => ({
    default: m.DiffViewer,
  })),
);
import { useArtifactContent, useArtifactDiff } from "@/core/artifacts/hooks";
import {
  ArtifactSaveError,
  canSaveWorkspaceOutput,
  restoreWorkspaceOutputRevision,
  saveWorkspaceOutputContent,
} from "@/core/artifacts/save";
import { artifactDisplayPath, urlOfArtifact } from "@/core/artifacts/utils";
import { authHeaders } from "@/core/auth/api";
import { copyTextToClipboard } from "@/core/clipboard";
import { useI18n } from "@/core/i18n/hooks";
import { dispatchQuickReply } from "@/core/messages/quick-reply";
import { useStreamdownPlugins } from "@/core/streamdown";
import { checkCodeFile, getFileName } from "@/core/utils/files";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import { ArtifactLink } from "../citations/artifact-link";
import { useThread } from "../messages/context";
import { Tooltip } from "../tooltip";

import { useArtifacts } from "./context";
import {
  buildArtifactEditPrompt,
  buildInspectableHtml,
  replaceHtmlBodyContent,
} from "./inspect-html";
import { useInstallSkill } from "./use-install-skill";
import { InspectOverlay } from "./inspect-overlay";
import {
  buildOfficeEditPrompt,
  officeArtifactKind,
  officeArtifactSupportsSelection,
  type OfficeArtifactKind,
  type OfficeArtifactSelection,
} from "./office-edit";

type ViewMode = "code" | "preview" | "diff";

export function ArtifactFileDetail({
  className,
  filepath: filepathFromProps,
  threadId,
}: {
  className?: string;
  filepath: string;
  threadId: string;
}) {
  const { t } = useI18n();
  const streamdownPlugins = useStreamdownPlugins();
  const { artifacts, select, clearSelection } = useArtifacts();
  const isWriteFile = useMemo(() => {
    return filepathFromProps.startsWith("write-file:");
  }, [filepathFromProps]);
  const filepath = useMemo(() => {
    if (isWriteFile) {
      const url = new URL(filepathFromProps);
      return decodeURIComponent(url.pathname);
    }
    return artifactDisplayPath(filepathFromProps);
  }, [filepathFromProps, isWriteFile]);
  const isSkillFile = useMemo(() => {
    return filepath.endsWith(".skill");
  }, [filepath]);
  const officeKind = useMemo(() => officeArtifactKind(filepath), [filepath]);
  const { isCodeFile, language } = useMemo(() => {
    if (isWriteFile) {
      let language = checkCodeFile(filepath).language;
      language ??= "text";
      return { isCodeFile: true, language };
    }
    if (isSkillFile) {
      return { isCodeFile: true, language: "markdown" };
    }
    return checkCodeFile(filepath);
  }, [filepath, isWriteFile, isSkillFile]);
  const isSupportPreview = useMemo(() => {
    return language === "html" || language === "markdown" || !!officeKind;
  }, [language, officeKind]);
  const { content, url, refetch } = useArtifactContent({
    threadId,
    filepath: filepathFromProps,
    enabled: isCodeFile && !isWriteFile,
  });

  const {
    originalContent,
    newContent,
    isDiffAvailable,
    isLoading: _isLoadingDiff,
  } = useArtifactDiff({
    filepath: filepathFromProps,
    threadId,
    enabled: isWriteFile && isCodeFile,
  });

  const displayContent = content ?? "";

  const [viewMode, setViewMode] = useState<ViewMode>("code");
  const [htmlEditProtected, setHtmlEditProtected] = useState(false);
  const { confirm, confirmDialog } = useConfirmDialog();
  const { installingFile, install } = useInstallSkill(threadId);
  const isInstalling = installingFile === filepath;
  const { isMock } = useThread();
  useEffect(() => {
    if (isWriteFile && isDiffAvailable) {
      setViewMode("diff");
    } else if (isSupportPreview) {
      setViewMode("preview");
    } else {
      setViewMode("code");
    }
  }, [isSupportPreview, isWriteFile, isDiffAvailable]);

  const handleInstallSkill = useCallback(() => {
    void install(filepath);
  }, [install, filepath]);

  const effectiveContent = isWriteFile ? newContent : displayContent;

  useEffect(() => setHtmlEditProtected(false), [filepathFromProps]);

  const confirmHtmlEditDiscard = useCallback(async () => {
    if (!htmlEditProtected) return true;
    return confirm({
      title: t.livePreview.humanDiscardTitle,
      description: t.livePreview.humanDiscardDescription,
      confirmLabel: t.livePreview.humanDiscardConfirm,
      destructive: true,
    });
  }, [confirm, htmlEditProtected, t.livePreview]);

  const selectArtifact = useCallback(
    async (nextFilepath: string) => {
      if (
        nextFilepath === filepathFromProps ||
        !(await confirmHtmlEditDiscard())
      )
        return;
      setHtmlEditProtected(false);
      select(nextFilepath);
    },
    [confirmHtmlEditDiscard, filepathFromProps, select],
  );

  const changeViewMode = useCallback(
    async (nextMode: ViewMode) => {
      if (nextMode === viewMode) return;
      if (viewMode === "preview" && !(await confirmHtmlEditDiscard())) return;
      setHtmlEditProtected(false);
      setViewMode(nextMode);
    },
    [confirmHtmlEditDiscard, viewMode],
  );

  const closeArtifact = useCallback(async () => {
    if (!(await confirmHtmlEditDiscard())) return;
    setHtmlEditProtected(false);
    clearSelection();
  }, [clearSelection, confirmHtmlEditDiscard]);

  return (
    <Artifact className={cn(className)}>
      <ArtifactHeader className="px-2">
        <div className="flex items-center gap-2">
          <ArtifactTitle>
            {isWriteFile ? (
              <div className="px-2">{getFileName(filepath)}</div>
            ) : (
              <Select
                value={filepathFromProps}
                onValueChange={(nextFilepath) =>
                  void selectArtifact(nextFilepath)
                }
              >
                <SelectTrigger className="border-none bg-transparent! shadow-none select-none focus:outline-0 active:outline-0">
                  <SelectValue placeholder="Select a file" />
                </SelectTrigger>
                <SelectContent className="select-none">
                  <SelectGroup>
                    {(artifacts ?? []).map((filepath) => (
                      <SelectItem key={filepath} value={filepath}>
                        {getFileName(artifactDisplayPath(filepath))}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            )}
          </ArtifactTitle>
        </div>
        <div className="flex min-w-0 grow items-center justify-center">
          {(isSupportPreview || isDiffAvailable) && (
            <ToggleGroup
              className="mx-auto"
              type="single"
              variant="outline"
              size="sm"
              value={viewMode}
              onValueChange={(value) => {
                if (value) {
                  void changeViewMode(value as ViewMode);
                }
              }}
            >
              {isDiffAvailable && (
                <ToggleGroupItem value="diff">
                  <DiffIcon />
                </ToggleGroupItem>
              )}
              {isCodeFile && (
                <ToggleGroupItem value="code">
                  <Code2Icon />
                </ToggleGroupItem>
              )}
              {isSupportPreview && (
                <ToggleGroupItem value="preview">
                  <EyeIcon />
                </ToggleGroupItem>
              )}
            </ToggleGroup>
          )}
        </div>
        <div className="flex items-center gap-2">
          <ArtifactActions>
            {!isWriteFile && filepath.endsWith(".skill") && (
              <Tooltip content={t.toolCalls.skillInstallTooltip}>
                <ArtifactAction
                  icon={isInstalling ? LoaderIcon : PackageIcon}
                  label={t.common.install}
                  tooltip={t.common.install}
                  disabled={isInstalling || env.STATIC_WEBSITE_ONLY}
                  onClick={handleInstallSkill}
                />
              </Tooltip>
            )}
            {!isWriteFile && (
              <ArtifactAction
                icon={SquareArrowOutUpRightIcon}
                label={t.common.openInNewWindow}
                tooltip={t.common.openInNewWindow}
                onClick={() => {
                  const w = window.open(
                    urlOfArtifact({ filepath: filepathFromProps, threadId }),
                    "_blank",
                    "noopener,noreferrer",
                  );
                  if (w) w.opener = null;
                }}
              />
            )}
            {isCodeFile && (
              <ArtifactAction
                icon={CopyIcon}
                label={t.clipboard.copyToClipboard}
                disabled={!effectiveContent}
                onClick={async () => {
                  try {
                    await copyTextToClipboard(effectiveContent ?? "");
                    toast.success(t.clipboard.copiedToClipboard);
                  } catch {
                    toast.error(t.clipboard.failedToCopyToClipboard);
                  }
                }}
                tooltip={t.clipboard.copyToClipboard}
              />
            )}
            {!isWriteFile && (
              <ArtifactAction
                icon={DownloadIcon}
                label={t.common.download}
                tooltip={t.common.download}
                onClick={() => {
                  const w = window.open(
                    urlOfArtifact({
                      filepath: filepathFromProps,
                      threadId,
                      download: true,
                    }),
                    "_blank",
                    "noopener,noreferrer",
                  );
                  if (w) w.opener = null;
                }}
              />
            )}
            <ArtifactAction
              icon={XIcon}
              label={t.common.close}
              onClick={() => void closeArtifact()}
              tooltip={t.common.close}
            />
          </ArtifactActions>
        </div>
      </ArtifactHeader>
      <ArtifactContent className="p-0">
        {isSupportPreview &&
          viewMode === "preview" &&
          (language === "markdown" || language === "html") && (
            <ArtifactFilePreview
              artifactRef={filepathFromProps}
              content={effectiveContent}
              filepath={filepath}
              language={language ?? "text"}
              streamdownPlugins={streamdownPlugins}
              url={url}
              threadId={threadId}
              onSaved={() => void refetch()}
              onReload={() => void refetch()}
              onEditProtectionChange={setHtmlEditProtected}
            />
          )}
        {isCodeFile && viewMode === "diff" && isDiffAvailable && (
          <Suspense
            fallback={
              <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
                Loading diff...
              </div>
            }
          >
            <DiffViewer
              className="size-full resize-none rounded-none border-none"
              oldValue={originalContent}
              newValue={newContent}
            />
          </Suspense>
        )}
        {isCodeFile && viewMode === "code" && (
          <Suspense
            fallback={
              <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
                Loading editor...
              </div>
            }
          >
            <CodeEditor
              className="size-full resize-none rounded-none border-none"
              value={effectiveContent ?? ""}
              readonly={isWriteFile}
              filePath={isWriteFile ? undefined : filepath}
              threadId={threadId}
            />
          </Suspense>
        )}
        {officeKind && viewMode === "preview" && (
          <OfficePreview
            displayPath={filepath}
            filepath={filepathFromProps}
            isMock={Boolean(isMock)}
            kind={officeKind}
            threadId={threadId}
          />
        )}
        {!isCodeFile && !officeKind && (
          <iframe
            className="size-full"
            title={t.common.preview}
            src={urlOfArtifact({
              filepath: filepathFromProps,
              threadId,
              isMock,
            })}
          />
        )}
      </ArtifactContent>
      {confirmDialog}
    </Artifact>
  );
}

export function OfficePreview({
  displayPath,
  filepath,
  isMock,
  kind,
  threadId,
}: {
  displayPath: string;
  filepath: string;
  isMock: boolean;
  kind: OfficeArtifactKind;
  threadId: string;
}) {
  const { t } = useI18n();
  const {
    thread: { isLoading },
  } = useThread();
  const [editing, setEditing] = useState(false);
  const [iframeReady, setIframeReady] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [selection, setSelection] = useState<OfficeArtifactSelection | null>(
    null,
  );
  const [instruction, setInstruction] = useState("");
  const [revision, setRevision] = useState(0);
  const [previewDocument, setPreviewDocument] = useState<{
    format: "fidelity" | "html" | "pdf";
    src?: string;
    srcDoc?: string;
  } | null>(null);
  const [previewError, setPreviewError] = useState(false);
  const [structuredMode, setStructuredMode] = useState(false);
  const [selectionRequested, setSelectionRequested] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const wasLoadingRef = useRef(isLoading);
  const canSelect = officeArtifactSupportsSelection(displayPath);

  useEffect(() => {
    if (!canSelect) return;
    const onMessage = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) return;
      const data = event.data as {
        type?: unknown;
        active?: unknown;
        payload?: unknown;
      } | null;
      if (!data || typeof data !== "object") return;
      if (data.type === "echo:office:ready") {
        setIframeReady(true);
        if (selectionRequested) {
          iframeRef.current?.contentWindow?.postMessage(
            { type: "echo:office:enable" },
            "*",
          );
          setSelecting(true);
          setSelectionRequested(false);
        }
      } else if (data.type === "echo:office:state") {
        setSelecting(data.active === true);
      } else if (data.type === "echo:office:select") {
        const payload = data.payload as Partial<OfficeArtifactSelection> | null;
        if (
          payload &&
          typeof payload.node === "string" &&
          typeof payload.label === "string" &&
          typeof payload.text === "string"
        ) {
          setSelection({
            node: payload.node,
            label: payload.label,
            text: payload.text,
          });
          setSelecting(false);
          setEditing(true);
        }
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [canSelect, selectionRequested]);

  useEffect(() => {
    const justFinished = wasLoadingRef.current && !isLoading;
    wasLoadingRef.current = isLoading;
    if (justFinished) setRevision((value) => value + 1);
  }, [isLoading]);

  useEffect(() => {
    setEditing(false);
    setIframeReady(false);
    setSelecting(false);
    setSelection(null);
    setInstruction("");
    setRevision(0);
    setStructuredMode(false);
    setSelectionRequested(false);
  }, [filepath]);

  const toggleSelection = useCallback(() => {
    if (!canSelect || !previewDocument || isLoading) return;
    if (
      previewDocument.format === "fidelity" ||
      previewDocument.format === "pdf"
    ) {
      setEditing(false);
      setSelection(null);
      setSelectionRequested(true);
      setStructuredMode(true);
      return;
    }
    if (!iframeReady) return;
    const next = !selecting;
    iframeRef.current?.contentWindow?.postMessage(
      { type: next ? "echo:office:enable" : "echo:office:disable" },
      "*",
    );
    setSelecting(next);
    if (next) {
      setEditing(false);
      setSelection(null);
    }
  }, [canSelect, iframeReady, isLoading, previewDocument, selecting]);

  const previewUrl = useMemo(() => {
    const base = urlOfArtifact({
      filepath,
      threadId,
      isMock,
      officePreview: kind !== "pdf",
      officeFidelityPreview: !structuredMode,
    });
    const separator = base.includes("?") ? "&" : "?";
    return `${base}${separator}preview_revision=${revision}`;
  }, [filepath, isMock, kind, revision, structuredMode, threadId]);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setPreviewDocument(null);
    setPreviewError(false);
    void fetch(previewUrl, {
      cache: "no-store",
      headers: authHeaders(),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Office preview: ${response.status}`);
        const previewMode = response.headers.get("X-Echo-Office-Preview");
        if (previewMode === "fidelity") {
          setPreviewDocument({
            format: "fidelity",
            srcDoc: await response.text(),
          });
          return;
        }
        const contentType = (
          response.headers.get("Content-Type") ?? ""
        ).toLowerCase();
        if (contentType.includes("application/pdf")) {
          objectUrl = URL.createObjectURL(await response.blob());
          setPreviewDocument({ format: "pdf", src: objectUrl });
          return;
        }
        if (
          !contentType.includes("text/html") &&
          !contentType.includes("application/xhtml+xml")
        ) {
          throw new Error(
            `Office preview returned unsupported content type: ${contentType || "unknown"}`,
          );
        }
        setPreviewDocument({ format: "html", srcDoc: await response.text() });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        console.warn("Unable to load authenticated Office preview", error);
        setPreviewError(true);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [kind, previewUrl]);

  useEffect(() => {
    setIframeReady(false);
    setSelecting(false);
    setSelection(null);
  }, [previewUrl]);

  const submit = useCallback(() => {
    const request = instruction.trim();
    if (!request || isLoading) return;
    const accepted = dispatchQuickReply({
      threadId,
      text: buildOfficeEditPrompt({
        filepath,
        displayPath,
        kind,
        instruction: request,
        selection,
      }),
    });
    if (!accepted) {
      toast.error(t.livePreview.aiEditUnavailable);
      return;
    }
    toast.success(t.livePreview.aiEditQueued);
    setEditing(false);
    setSelection(null);
    setInstruction("");
  }, [
    displayPath,
    filepath,
    instruction,
    isLoading,
    kind,
    selection,
    t,
    threadId,
  ]);

  return (
    <div className="relative size-full overflow-hidden bg-muted/30">
      {previewDocument ? (
        <iframe
          className="size-full border-0"
          key={previewUrl}
          ref={iframeRef}
          sandbox={previewDocument.format === "html" ? "allow-scripts" : ""}
          src={previewDocument.src}
          srcDoc={previewDocument.srcDoc}
          title={`${displayPath} ${t.common.preview}`}
        />
      ) : (
        <div className="flex size-full flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
          <span>
            {previewError ? t.livePreview.previewError : `${t.common.loading}…`}
          </span>
          {previewError && (
            <Button
              onClick={() => setRevision((value) => value + 1)}
              size="sm"
              type="button"
              variant="outline"
            >
              {t.livePreview.previewRetry}
            </Button>
          )}
        </div>
      )}
      <div className="pointer-events-none absolute top-2 right-2 z-10 flex items-center gap-1.5">
        {canSelect && structuredMode && previewDocument?.format === "html" && (
          <Button
            className="pointer-events-auto h-8 gap-1.5 shadow-md"
            onClick={() => {
              setStructuredMode(false);
              setSelectionRequested(false);
              setSelecting(false);
              setSelection(null);
              setEditing(false);
            }}
            size="sm"
            type="button"
            variant="secondary"
          >
            <EyeIcon className="size-3.5" />
            {t.livePreview.officeFidelity}
          </Button>
        )}
        {canSelect && (
          <Button
            className="pointer-events-auto h-8 gap-1.5 shadow-md"
            disabled={
              !previewDocument ||
              isLoading ||
              (previewDocument.format === "html" && !iframeReady)
            }
            onClick={toggleSelection}
            size="sm"
            type="button"
            variant={selecting ? "default" : "secondary"}
          >
            <CrosshairIcon className="size-3.5" />
            {selecting
              ? t.livePreview.officeCancelSelect
              : t.livePreview.officeSelect}
          </Button>
        )}
        <Button
          className="pointer-events-auto h-8 gap-1.5 shadow-md"
          disabled={isLoading}
          onClick={() => {
            setSelection(null);
            setEditing((value) => !value);
          }}
          size="sm"
          type="button"
          variant={editing ? "default" : "secondary"}
        >
          <SparklesIcon className="size-3.5" />
          {t.livePreview.officeEdit}
        </Button>
      </div>
      {editing && (
        <div className="pointer-events-none absolute inset-x-3 bottom-3 z-20 flex justify-center">
          <div className="pointer-events-auto w-full max-w-xl rounded-xl border border-primary/25 bg-background/95 p-3 shadow-[var(--shadow-lg)] backdrop-blur">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium">
              <SparklesIcon className="size-4 text-primary" />
              {t.livePreview.officeEditTitle}
            </div>
            {selection && (
              <div className="mb-2 truncate rounded-md border border-primary/20 bg-primary/5 px-2 py-1 text-xs text-primary">
                {t.livePreview.officeSelected}: {selection.label}
                {selection.text ? ` · ${selection.text}` : ""}
              </div>
            )}
            <form
              className="flex gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                submit();
              }}
            >
              <Input
                autoFocus
                disabled={isLoading}
                onChange={(event) => setInstruction(event.target.value)}
                placeholder={t.livePreview.officeEditPlaceholder}
                value={instruction}
              />
              <Button
                aria-label={t.livePreview.aiEditSend}
                disabled={!instruction.trim() || isLoading}
                size="icon"
                type="submit"
              >
                <SendIcon className="size-4" />
              </Button>
            </form>
            <p className="mt-2 text-xs text-muted-foreground">
              {t.livePreview.officeEditHint}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export function ArtifactFilePreview({
  artifactRef,
  content,
  filepath,
  language,
  streamdownPlugins,
  url,
  threadId,
  onSaved,
  onReload,
  onEditProtectionChange,
}: {
  artifactRef?: string;
  content: string;
  filepath: string;
  language: string;
  streamdownPlugins: Pick<StreamdownProps, "remarkPlugins" | "rehypePlugins">;
  url?: string;
  threadId?: string;
  onSaved?: (content: string) => void;
  onReload?: () => void;
  onEditProtectionChange?: (protectedFromUnload: boolean) => void;
}) {
  if (language === "markdown") {
    return (
      <div className="size-full px-4">
        <Suspense
          fallback={
            <div className="size-full whitespace-pre-wrap break-words py-4">
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
  if (language === "html") {
    return (
      <HtmlPreview
        artifactRef={artifactRef}
        content={content}
        filepath={filepath}
        url={url}
        threadId={threadId}
        onSaved={onSaved}
        onReload={onReload}
        onEditProtectionChange={onEditProtectionChange}
      />
    );
  }
  return null;
}

function createHtmlBridgeToken(): string {
  const values = new Uint32Array(4);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(values);
    return Array.from(values, (value) => value.toString(36)).join("-");
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function HtmlPreview({
  artifactRef,
  content,
  filepath,
  url,
  threadId,
  onSaved,
  onReload,
  onEditProtectionChange,
}: {
  artifactRef?: string;
  content: string;
  filepath: string;
  url?: string;
  threadId?: string;
  onSaved?: (content: string) => void;
  onReload?: () => void;
  onEditProtectionChange?: (protectedFromUnload: boolean) => void;
}) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [inspectionMode, setInspectionMode] = useState(!url);
  const [localContent, setLocalContent] = useState<string | null>(null);
  const [bridgeReady, setBridgeReady] = useState(false);
  const [bridgeRevision, setBridgeRevision] = useState(0);
  const [humanEditing, setHumanEditing] = useState(false);
  const [humanDirty, setHumanDirty] = useState(false);
  const [humanSaving, setHumanSaving] = useState(false);
  const [pendingHumanEdit, setPendingHumanEdit] = useState(false);
  const [humanConflict, setHumanConflict] = useState<string | null>(null);
  const [undoRevision, setUndoRevision] = useState<{
    revisionId: string;
    expectedContent: string;
    restoredContent: string;
  } | null>(null);
  const humanSavingRef = useRef(false);
  const humanEditActiveRef = useRef(false);
  humanEditActiveRef.current = humanEditing || pendingHumanEdit;
  const { t } = useI18n();
  const {
    thread: { isLoading },
  } = useThread();

  const effectiveContent = localContent ?? content;
  const bridgeToken = useMemo(() => {
    // Reading the identity inputs is intentional: a fresh token invalidates
    // any message source captured before the preview or rendered HTML changed.
    void effectiveContent;
    void filepath;
    void url;
    return createHtmlBridgeToken();
  }, [effectiveContent, filepath, url]);
  const canInspect = Boolean(effectiveContent);
  const canHumanEdit = Boolean(
    artifactRef && threadId && canSaveWorkspaceOutput(artifactRef),
  );

  useEffect(() => {
    setInspectionMode(!url);
    setBridgeReady(false);
    setHumanEditing(false);
    setHumanDirty(false);
    setHumanSaving(false);
    humanSavingRef.current = false;
    setPendingHumanEdit(false);
    setHumanConflict(null);
    setUndoRevision(null);
  }, [filepath, url]);

  useEffect(() => {
    onEditProtectionChange?.(humanDirty || humanSaving);
  }, [humanDirty, humanSaving, onEditProtectionChange]);

  useEffect(() => {
    if (!humanEditActiveRef.current) setLocalContent(null);
  }, [content]);

  // Prepend the inspect script so it runs before any inline scripts in the
  // artifact. Wrapping in a <script> at document start keeps the user's
  // original markup untouched if it already declares <html>/<head>.
  const srcDoc = useMemo(() => {
    if (!effectiveContent) return undefined;
    return buildInspectableHtml(effectiveContent, url, bridgeToken);
  }, [bridgeToken, effectiveContent, url]);

  const postBridgeMessage = useCallback(
    (type: string) => {
      iframeRef.current?.contentWindow?.postMessage(
        { type, echoBridgeToken: bridgeToken },
        "*",
      );
    },
    [bridgeToken],
  );

  useEffect(() => {
    setBridgeReady(false);
  }, [srcDoc]);

  const persistHumanEdit = useCallback(
    async (bodyHtml: string) => {
      if (!artifactRef || !threadId || humanSavingRef.current) return;
      const updated = replaceHtmlBodyContent(effectiveContent, bodyHtml);
      humanSavingRef.current = true;
      setHumanSaving(true);
      try {
        const result = await saveWorkspaceOutputContent({
          filepath: artifactRef,
          threadId,
          content: updated,
          expectedContent: effectiveContent,
        });
        setUndoRevision(
          result.revision_id
            ? {
                revisionId: result.revision_id,
                expectedContent: updated,
                restoredContent: effectiveContent,
              }
            : null,
        );
        setLocalContent(updated);
        postBridgeMessage("echo:edit:commit");
        setHumanEditing(false);
        setHumanDirty(false);
        setPendingHumanEdit(false);
        setHumanConflict(null);
        toast.success(t.livePreview.humanSaved);
        onSaved?.(updated);
      } catch (error) {
        if (error instanceof ArtifactSaveError && error.status === 409) {
          setHumanConflict(error.message);
        }
        toast.error(
          error instanceof Error ? error.message : t.codeEditor.fileSaveFailed,
        );
      } finally {
        humanSavingRef.current = false;
        setHumanSaving(false);
      }
    },
    [
      artifactRef,
      effectiveContent,
      onSaved,
      postBridgeMessage,
      t.codeEditor.fileSaveFailed,
      t.livePreview.humanSaved,
      threadId,
    ],
  );

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.source !== iframeRef.current?.contentWindow) return;
      const data = event.data as {
        type?: unknown;
        active?: unknown;
        dirty?: unknown;
        bodyHtml?: unknown;
        echoBridgeToken?: unknown;
      } | null;
      if (!data || typeof data !== "object") return;
      if (data.echoBridgeToken !== bridgeToken) return;
      if (data.type === "echo:inspect:ready") {
        setBridgeReady(true);
        if (pendingHumanEdit) {
          postBridgeMessage("echo:edit:enable");
          setPendingHumanEdit(false);
        }
      } else if (data.type === "echo:edit:state") {
        setHumanEditing(data.active === true);
        setHumanDirty(data.dirty === true);
      } else if (
        data.type === "echo:edit:content" &&
        typeof data.bodyHtml === "string"
      ) {
        void persistHumanEdit(data.bodyHtml);
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [bridgeToken, pendingHumanEdit, persistHumanEdit, postBridgeMessage]);

  useEffect(() => {
    if (!humanDirty) return;
    const protectUnsavedEdit = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protectUnsavedEdit);
    return () => window.removeEventListener("beforeunload", protectUnsavedEdit);
  }, [humanDirty]);

  const beginHumanEdit = useCallback(() => {
    if (!canHumanEdit || isLoading) return;
    setLocalContent(effectiveContent);
    if (!inspectionMode || !bridgeReady) {
      setPendingHumanEdit(true);
      setBridgeReady(false);
      if (inspectionMode) setBridgeRevision((value) => value + 1);
      setInspectionMode(true);
      return;
    }
    postBridgeMessage("echo:edit:enable");
  }, [
    bridgeReady,
    canHumanEdit,
    effectiveContent,
    inspectionMode,
    isLoading,
    postBridgeMessage,
  ]);

  const cancelHumanEdit = useCallback(() => {
    postBridgeMessage("echo:edit:cancel");
    setHumanEditing(false);
    setHumanDirty(false);
    setPendingHumanEdit(false);
    setHumanConflict(null);
    setLocalContent(null);
  }, [postBridgeMessage]);

  const reloadAfterConflict = useCallback(() => {
    postBridgeMessage("echo:edit:cancel");
    setHumanEditing(false);
    setHumanDirty(false);
    setPendingHumanEdit(false);
    setHumanConflict(null);
    setLocalContent(null);
    setBridgeReady(false);
    setBridgeRevision((value) => value + 1);
    onReload?.();
  }, [onReload, postBridgeMessage]);

  const undoLastHumanSave = useCallback(async () => {
    if (!artifactRef || !threadId || !undoRevision || humanSavingRef.current)
      return;
    humanSavingRef.current = true;
    setHumanSaving(true);
    try {
      await restoreWorkspaceOutputRevision({
        filepath: artifactRef,
        threadId,
        revisionId: undoRevision.revisionId,
        expectedContent: undoRevision.expectedContent,
      });
      setLocalContent(undoRevision.restoredContent);
      setUndoRevision(null);
      toast.success(t.livePreview.humanRestored);
      onSaved?.(undoRevision.restoredContent);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t.codeEditor.fileSaveFailed,
      );
    } finally {
      humanSavingRef.current = false;
      setHumanSaving(false);
    }
  }, [artifactRef, onSaved, t, threadId, undoRevision]);

  const requestAiEdit = useCallback(
    (
      selection: {
        selector: string;
        tagName: string;
        outerHTML: string;
        textContent: string;
      },
      instruction: string,
    ) => {
      const prompt = buildArtifactEditPrompt(filepath, selection, instruction);
      const accepted = dispatchQuickReply({ text: prompt, threadId });
      if (accepted) {
        toast.success(t.livePreview.aiEditQueued);
      } else {
        toast.error(t.livePreview.aiEditUnavailable);
      }
      return accepted;
    },
    [
      filepath,
      t.livePreview.aiEditQueued,
      t.livePreview.aiEditUnavailable,
      threadId,
    ],
  );

  return (
    <div className="relative size-full">
      <InspectOverlay
        bridgeToken={bridgeToken}
        enabled={canInspect && !humanEditing}
        filepath={filepath}
        iframeRef={iframeRef}
        busy={isLoading || humanSaving}
        onPrepareInspect={
          url && !inspectionMode ? () => setInspectionMode(true) : undefined
        }
        onRequestAiEdit={requestAiEdit}
      >
        <iframe
          key={`${inspectionMode ? "inspect" : "live"}-${bridgeRevision}`}
          className="size-full"
          ref={iframeRef}
          sandbox={
            inspectionMode ? "allow-scripts" : "allow-scripts allow-forms"
          }
          title="Artifact preview"
          {...(inspectionMode && srcDoc
            ? { srcDoc }
            : url
              ? { src: url }
              : srcDoc
                ? { srcDoc }
                : {})}
        />
      </InspectOverlay>
      {canHumanEdit && (
        <div className="pointer-events-none absolute top-2 left-2 z-20 flex items-center gap-1.5">
          {humanEditing ? (
            <>
              <span className="rounded-md bg-background/95 px-2 py-1 text-xs font-medium shadow-md backdrop-blur">
                {humanConflict
                  ? t.livePreview.humanConflict
                  : humanDirty
                    ? t.livePreview.humanUnsaved
                    : t.livePreview.humanEditing}
              </span>
              <Button
                aria-label={t.livePreview.humanSave}
                className="pointer-events-auto h-7 gap-1.5 px-2 text-xs shadow-md"
                disabled={humanSaving || isLoading || Boolean(humanConflict)}
                onClick={() => postBridgeMessage("echo:edit:request-save")}
                size="sm"
                type="button"
              >
                {humanSaving ? (
                  <LoaderIcon className="size-3 animate-spin" />
                ) : (
                  <SaveIcon className="size-3" />
                )}
                {t.livePreview.humanSave}
              </Button>
              {humanConflict && (
                <Button
                  className="pointer-events-auto h-7 gap-1.5 px-2 text-xs shadow-md"
                  disabled={humanSaving || isLoading}
                  onClick={reloadAfterConflict}
                  size="sm"
                  type="button"
                  variant="secondary"
                >
                  <RefreshCwIcon className="size-3" />
                  {t.livePreview.humanReloadLatest}
                </Button>
              )}
              <Button
                aria-label={t.livePreview.humanCancel}
                className="pointer-events-auto size-7 shadow-md"
                disabled={humanSaving}
                onClick={cancelHumanEdit}
                size="icon-sm"
                type="button"
                variant="secondary"
              >
                <XIcon className="size-3.5" />
              </Button>
            </>
          ) : (
            <>
              <Button
                className="pointer-events-auto h-7 gap-1.5 px-2 text-xs shadow-md"
                disabled={isLoading || pendingHumanEdit || humanSaving}
                onClick={beginHumanEdit}
                size="sm"
                type="button"
                variant="secondary"
              >
                {pendingHumanEdit ? (
                  <LoaderIcon className="size-3 animate-spin" />
                ) : (
                  <PencilIcon className="size-3" />
                )}
                {t.livePreview.humanEdit}
              </Button>
              {undoRevision && (
                <Button
                  className="pointer-events-auto h-7 gap-1.5 px-2 text-xs shadow-md"
                  disabled={isLoading || humanSaving}
                  onClick={() => void undoLastHumanSave()}
                  size="sm"
                  type="button"
                  variant="secondary"
                >
                  {humanSaving ? (
                    <LoaderIcon className="size-3 animate-spin" />
                  ) : (
                    <Undo2Icon className="size-3" />
                  )}
                  {t.livePreview.humanUndo}
                </Button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
