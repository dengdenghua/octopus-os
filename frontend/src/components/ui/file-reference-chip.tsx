import { FileCode2Icon, FileTextIcon, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Inline file reference. In chat transcripts, cited files appear
 * as a compact chip: icon + filename (mono) + optional line range
 * in parentheses. Clicks (if href provided) jump to the file in
 * the editor side panel. Used inline within markdown-rendered text.
 *
 *   <FileReferenceChip path="router.tsx" lines="16-23" />
 *     → 📄 router.tsx (line 16-23)
 */

export interface FileReferenceChipProps {
  /** Display path; just the filename or repo-relative path. */
  path: string;
  /** Optional line range, e.g. "16-23" or "42". Renders in parens. */
  lines?: string;
  /** Click handler — typically to open the file in the editor pane. */
  onClick?: () => void;
  /** If provided, the chip becomes an anchor (e.g. to a file viewer). */
  href?: string;
  /** Optional icon override; defaults to filetype-inferred. */
  icon?: LucideIcon | ReactNode;
  className?: string;
}

function inferIcon(path: string): LucideIcon {
  if (
    /\.(ts|tsx|js|jsx|py|go|rs|java|cpp|c|h|rb|php|lua|swift|kt)$/i.test(path)
  ) {
    return FileCode2Icon;
  }
  return FileTextIcon;
}

export function FileReferenceChip({
  path,
  lines,
  onClick,
  href,
  icon,
  className,
}: FileReferenceChipProps) {
  const InferredIcon = inferIcon(path);
  const iconNode =
    icon === undefined ? (
      <InferredIcon className="size-3 shrink-0 opacity-60" />
    ) : typeof icon === "function" ? (
      // Treat LucideIcon (function component) uniformly.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (() => {
        const Ic = icon as LucideIcon;
        return <Ic className="size-3 shrink-0 opacity-60" />;
      })()
    ) : (
      icon
    );

  // Strip directory prefix for display; keep full path on title for hover.
  const displayName = path.split(/[\\/]/).pop() ?? path;

  const content = (
    <>
      {iconNode}
      <span className="font-mono text-mini leading-none">
        {displayName}
      </span>
      {lines && (
        <span className="text-muted-foreground/80 text-micro leading-none">
          ({lines.includes("-") ? `L${lines}` : `line ${lines}`})
        </span>
      )}
    </>
  );

  const cls = cn(
    "inline-flex items-center gap-1 rounded-md border border-border-default bg-muted/40 px-1.5 py-0.5",
    "align-[2px]", // vertical align to sit nicely in text runs
    "transition-colors hover:border-border hover:bg-muted/70",
    (href || onClick) && "cursor-pointer",
    className,
  );

  if (href) {
    return (
      <a href={href} title={path} className={cls}>
        {content}
      </a>
    );
  }
  if (onClick) {
    return (
      <button type="button" onClick={onClick} title={path} className={cls}>
        {content}
      </button>
    );
  }
  return (
    <span title={path} className={cls}>
      {content}
    </span>
  );
}
