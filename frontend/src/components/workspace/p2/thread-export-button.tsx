/**
 * Thread Export Button Component
 *
 * Button to export thread/conversation as Markdown file.
 */

import { DownloadIcon, Loader2Icon } from "lucide-react";
import { useThreadExport } from "@/core/api/p2-hooks";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface ThreadExportButtonProps {
  threadId: string;
  threadTitle?: string;
  variant?: "default" | "outline" | "ghost" | "secondary";
  size?: "default" | "sm" | "lg" | "icon";
  className?: string;
  showLabel?: boolean;
}

export function ThreadExportButton({
  threadId,
  threadTitle,
  variant = "ghost",
  size = "sm",
  className,
  showLabel = false,
}: ThreadExportButtonProps) {
  const { exporting, error, exportMarkdown } = useThreadExport();

  const handleExport = async () => {
    if (exporting) return;

    const filename = threadTitle
      ? `${sanitizeFilename(threadTitle)}.md`
      : undefined;

    await exportMarkdown(threadId, filename);
  };

  const buttonContent = (
    <Button
      variant={variant}
      size={size}
      onClick={handleExport}
      disabled={exporting}
      className={cn(className)}
    >
      {exporting ? (
        <Loader2Icon className="size-4 animate-spin" />
      ) : (
        <DownloadIcon className="size-4" />
      )}
      {showLabel && (
        <span className="ml-2">{exporting ? "Exporting..." : "Export"}</span>
      )}
    </Button>
  );

  if (error) {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>{buttonContent}</TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-xs">
            <p className="text-xs text-red-500">Export failed: {error.message}</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>{buttonContent}</TooltipTrigger>
        <TooltipContent side="bottom">
          <p className="text-xs">Export conversation as Markdown</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/**
 * Sanitize filename by removing invalid characters
 */
function sanitizeFilename(name: string): string {
  return name
    .replace(/[<>:"/\\|?*]/g, "-") // Replace invalid chars with dash
    .replace(/\s+/g, "-") // Replace whitespace with dash
    .replace(/-+/g, "-") // Collapse multiple dashes
    .replace(/^-+|-+$/g, "") // Trim dashes from start/end
    .slice(0, 200); // Limit length
}
