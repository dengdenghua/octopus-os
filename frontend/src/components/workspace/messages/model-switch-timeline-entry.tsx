import { cn } from "@/lib/utils";

export function ModelSwitchTimelineEntry({
  modelName,
  className,
}: {
  modelName: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "my-2 flex w-full items-center gap-3 px-1",
        "animate-[message-entrance_180ms_cubic-bezier(0.33,1,0.68,1)]",
        className,
      )}
      role="status"
      aria-label={`模型已切换为 ${modelName}`}
      data-testid="model-switch-timeline-entry"
    >
      <span aria-hidden="true" className="h-px min-w-6 flex-1 bg-border/45" />
      <span className="max-w-[70%] truncate text-[11px] font-normal tracking-[0.01em] text-muted-foreground/55">
        模型已切换为 {modelName}
      </span>
      <span aria-hidden="true" className="h-px min-w-6 flex-1 bg-border/45" />
    </div>
  );
}
