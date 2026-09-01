import { Skeleton } from "@/components/ui/skeleton";

function SkeletonBar({
  className,
  originRight,
}: {
  className?: string;
  originRight?: boolean;
}) {
  return (
    <div
      className={`animate-skeleton-entrance fill-mode-[forwards] overflow-hidden rounded-lg opacity-0 ${originRight ? "origin-[right]" : "origin-[left]"} ${className ?? ""}`}
    >
      <Skeleton className="h-full w-full rounded-lg" />
    </div>
  );
}

export function MessageListSkeleton() {
  return (
    <div
      aria-label="Loading conversation"
      className="mx-auto flex w-full max-w-(--container-width-md) flex-col gap-8 px-5 pb-8 pt-10 sm:gap-12 sm:p-8 sm:pt-16"
    >
      <div
        role="human-message"
        className="flex w-[58%] max-w-sm flex-col items-end gap-2 self-end sm:w-[50%]"
      >
        <SkeletonBar
          className="h-4 w-full [animation-delay:0ms] sm:h-5"
          originRight
        />
        <SkeletonBar
          className="h-4 w-[72%] [animation-delay:60ms] sm:h-5 sm:w-[80%]"
          originRight
        />
      </div>
      <div role="assistant-message" className="flex items-start gap-3">
        <SkeletonBar className="mt-0.5 size-7 shrink-0 rounded-full [animation-delay:100ms] sm:size-8" />
        <div className="flex min-w-0 flex-1 flex-col gap-2 pt-0.5">
          <SkeletonBar className="h-4 w-[92%] [animation-delay:140ms] sm:h-5" />
          <SkeletonBar className="h-4 w-full [animation-delay:200ms] sm:h-5" />
          <SkeletonBar className="h-4 w-[64%] [animation-delay:260ms] sm:h-5" />
        </div>
      </div>
      <div
        role="human-message"
        className="flex w-[44%] max-w-xs flex-col items-end gap-2 self-end"
      >
        <SkeletonBar
          className="h-4 w-full [animation-delay:320ms] sm:h-5"
          originRight
        />
      </div>
      <div role="assistant-message" className="flex items-start gap-3">
        <SkeletonBar className="mt-0.5 size-7 shrink-0 rounded-full [animation-delay:380ms] sm:size-8" />
        <div className="flex min-w-0 flex-1 flex-col gap-2 pt-0.5">
          <SkeletonBar className="h-4 w-full [animation-delay:420ms] sm:h-5" />
          <SkeletonBar className="h-4 w-[78%] [animation-delay:480ms] sm:h-5" />
        </div>
      </div>
    </div>
  );
}
