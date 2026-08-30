import { FileIcon, ChevronRightIcon, XIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface FileBreadcrumbProps {
  filePath: string | null;
  onClear?: () => void;
  className?: string;
}

export function FileBreadcrumb({
  filePath,
  onClear,
  className,
}: FileBreadcrumbProps) {
  if (!filePath) return null;

  const parts = filePath.split(/[/\\]/);
  const fileName = parts.pop() || "";
  const dirParts = parts.slice(-3);

  return (
    <div
      className={cn(
        "flex items-center gap-0.5 border-b bg-muted/30 px-3 py-1 font-mono text-xs text-muted-foreground",
        className,
      )}
    >
      <FileIcon className="text-muted-foreground/60 size-3 shrink-0" />
      {dirParts.map((part, i) => (
        <span key={i} className="flex items-center">
          <span className="text-muted-foreground/60">{part}</span>
          <ChevronRightIcon className="text-muted-foreground/40 size-2.5" />
        </span>
      ))}
      <span className="text-primary font-medium">{fileName}</span>
      {onClear && (
        <button
          onClick={onClear}
          className="text-muted-foreground/40 hover:text-muted-foreground ml-auto"
        >
          <XIcon className="size-3" />
        </button>
      )}
    </div>
  );
}
