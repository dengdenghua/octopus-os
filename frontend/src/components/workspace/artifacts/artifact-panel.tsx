import { EyeIcon, FilesIcon, GitPullRequestIcon, XIcon } from "lucide-react";
import { useMemo } from "react";

import { ConversationEmptyState } from "@/components/ai-elements/conversation";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { artifactDisplayPath } from "@/core/artifacts/utils";
import { useI18n } from "@/core/i18n/hooks";
import { checkCodeFile } from "@/core/utils/files";
import { cn } from "@/lib/utils";

import { ArtifactFileDetail } from "./artifact-file-detail";
import { ArtifactFileList, ArtifactInlinePreview } from "./artifact-file-list";
import { useArtifacts } from "./context";
import { officeArtifactKind } from "./office-edit";

// 产物面板将产物按「全部 / 变更 / 预览」分类展示：变更指 Agent 通过
// write-file 写入的改动，预览包含可渲染的 HTML / Markdown
// 以及文档、表格、演示文稿和 PDF。不要把 Office 产物埋在“全部”里：
// 用户应该能从“预览”直接进入人机共改闭环。
export function ArtifactPanel({
  className,
  showHeader = true,
  threadId,
}: {
  className?: string;
  showHeader?: boolean;
  threadId: string;
}) {
  const { t } = useI18n();
  const {
    artifacts,
    open: artifactsOpen,
    setOpen: setArtifactsOpen,
    selectedArtifact,
  } = useArtifacts();
  const artifactCount = artifacts?.length ?? 0;

  const { changeArtifacts, previewArtifacts } = useMemo(() => {
    const changes: string[] = [];
    const previews: string[] = [];
    for (const file of artifacts ?? []) {
      if (file.startsWith("write-file:")) changes.push(file);
      const displayPath = artifactDisplayPath(file);
      const language = checkCodeFile(displayPath).language;
      if (
        language === "html" ||
        language === "markdown" ||
        officeArtifactKind(displayPath)
      ) {
        previews.push(file);
      }
    }
    return { changeArtifacts: changes, previewArtifacts: previews };
  }, [artifacts]);

  if (selectedArtifact) {
    return (
      <ArtifactFileDetail
        className={cn("size-full", className)}
        filepath={selectedArtifact}
        threadId={threadId}
      />
    );
  }

  return (
    <div
      aria-hidden={!artifactsOpen}
      className={cn("flex min-h-0 flex-1 flex-col overflow-hidden", className)}
    >
      {showHeader && (
        <header className="flex shrink-0 items-center gap-2 border-b border-border-subtle px-3 py-2.5">
          <FilesIcon className="size-4 text-muted-foreground" />
          <h2 className="flex-1 truncate text-sm font-medium">
            {t.conversation.artifactsTitle}
          </h2>
          {artifactCount > 0 && (
            <span className="rounded-full bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">
              {artifactCount}
            </span>
          )}
          <Button
            size="icon-sm"
            variant="ghost"
            onClick={() => setArtifactsOpen(false)}
            aria-label={t.common.close}
            className="size-7"
          >
            <XIcon className="size-3.5" />
          </Button>
        </header>
      )}
      <main className="min-h-0 grow overflow-y-auto p-2">
        <Tabs defaultValue="all" className="gap-0">
          <TabsList
            variant="line"
            className="h-8 w-full justify-start gap-0 rounded-none px-1"
          >
            <TabsTrigger value="all" className="h-7 rounded-md px-2 text-xs">
              {t.conversation.artifactsTitle}
              {artifactCount > 0 && (
                <span className="ml-1 font-mono text-mini text-muted-foreground tabular-nums">
                  {artifactCount}
                </span>
              )}
            </TabsTrigger>
            <TabsTrigger
              value="changes"
              className="h-7 gap-1 rounded-md px-2 text-xs"
            >
              <GitPullRequestIcon className="size-3.5" />
              {t.conversation.artifactsTabChanges}
              {changeArtifacts.length > 0 && (
                <span className="ml-0.5 font-mono text-mini text-muted-foreground tabular-nums">
                  {changeArtifacts.length}
                </span>
              )}
            </TabsTrigger>
            <TabsTrigger
              value="preview"
              className="h-7 gap-1 rounded-md px-2 text-xs"
            >
              <EyeIcon className="size-3.5" />
              {t.conversation.artifactsTabPreview}
              {previewArtifacts.length > 0 && (
                <span className="ml-0.5 font-mono text-mini text-muted-foreground tabular-nums">
                  {previewArtifacts.length}
                </span>
              )}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="all" className="mt-2">
            {artifactCount === 0 ? (
              <ConversationEmptyState
                icon={<FilesIcon />}
                title={t.conversation.noArtifactSelected}
                description={t.conversation.selectArtifactToView}
              />
            ) : (
              <ArtifactFileList files={artifacts ?? []} threadId={threadId} />
            )}
          </TabsContent>

          <TabsContent value="changes" className="mt-2">
            {changeArtifacts.length === 0 ? (
              <ConversationEmptyState
                icon={<GitPullRequestIcon />}
                title={t.conversation.noChangesArtifacts}
              />
            ) : (
              <ArtifactFileList files={changeArtifacts} threadId={threadId} />
            )}
          </TabsContent>

          <TabsContent value="preview" className="mt-2">
            {previewArtifacts.length === 0 ? (
              <ConversationEmptyState
                icon={<EyeIcon />}
                title={t.conversation.noPreviewArtifacts}
              />
            ) : (
              <ArtifactInlinePreview
                files={previewArtifacts}
                threadId={threadId}
              />
            )}
          </TabsContent>
        </Tabs>
      </main>
    </div>
  );
}
