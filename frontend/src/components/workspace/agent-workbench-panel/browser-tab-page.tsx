import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { BrowserPreviewPanel } from "../browser-preview-panel";
import { LivePreviewPanel } from "../live-preview-panel";
import type { ExtractedCodeBlocks } from "@/lib/extract-code-blocks";

export function BrowserTabPage({
  canShowDeployedPreview,
  canShowInlinePreview,
  browserPreviewSource,
  setBrowserSourceOverride,
  resultPreviewUrl,
  threadId,
  inferredWorkDir,
  browserPreviewBlocks,
}: {
  canShowDeployedPreview: boolean;
  canShowInlinePreview: boolean;
  browserPreviewSource: "deployed" | "inline" | "session";
  setBrowserSourceOverride: (source: "deployed" | "inline" | null) => void;
  resultPreviewUrl?: string | null;
  threadId?: string | null;
  inferredWorkDir?: string;
  browserPreviewBlocks?: ExtractedCodeBlocks | null;
}) {
  const { t } = useI18n();
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {canShowDeployedPreview && canShowInlinePreview && (
        <div className="flex shrink-0 items-center gap-1 border-b border-border-subtle px-3 py-1.5">
          {(
            [
              { id: "inline", label: t.livePreview.title },
              { id: "deployed", label: t.codeStatus.deployed },
            ] as const
          ).map((source) => (
            <button
              key={source.id}
              type="button"
              onClick={() => setBrowserSourceOverride(source.id)}
              className={cn(
                "rounded-md px-2 py-0.5 text-xs font-medium transition-colors",
                browserPreviewSource === source.id
                  ? "bg-muted/70 text-foreground"
                  : "text-muted-foreground hover:bg-muted/40 hover:text-foreground",
              )}
            >
              {source.label}
            </button>
          ))}
        </div>
      )}
      {browserPreviewSource === "deployed" ? (
        <LivePreviewPanel
          previewUrl={resultPreviewUrl}
          threadId={threadId ?? "default"}
          workspacePath={inferredWorkDir}
          className="min-h-0 flex-1"
        />
      ) : browserPreviewSource === "inline" ? (
        <LivePreviewPanel
          htmlContent={browserPreviewBlocks?.html}
          cssContent={browserPreviewBlocks?.css}
          jsContent={browserPreviewBlocks?.js}
          className="min-h-0 flex-1"
        />
      ) : (
        <BrowserPreviewPanel
          threadId={threadId ?? "default"}
          workspacePath={inferredWorkDir}
          className="min-h-0 flex-1"
        />
      )}
    </div>
  );
}
