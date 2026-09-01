import { cn } from "@/lib/utils";

/**
 * Skeleton. Codex-inspired: instead of the default `animate-pulse` opacity
 * toggle, we sweep a shimmer band across a muted base. Reads less "broken
 * content" and more "loading", especially in dense lists.
 *
 * Falls back to a static muted block when `prefers-reduced-motion: reduce`.
 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("codex-skeleton rounded-lg", className)}
      {...props}
    />
  );
}

export { Skeleton };
