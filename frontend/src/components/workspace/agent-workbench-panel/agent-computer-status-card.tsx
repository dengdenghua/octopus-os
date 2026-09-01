import { BotIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export function AgentComputerStatusCard({
  avatar,
  avatarUrl,
  fallbackInitial,
  label,
  status,
  statusClassName,
  title,
}: {
  avatar?: string | null;
  avatarUrl?: string | null;
  fallbackInitial?: string;
  label: string;
  status: string;
  statusClassName: string;
  title: string;
}) {
  return (
    <div className="border-t border-border-subtle px-3 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <div className="flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-sm border border-border-default bg-muted/20 text-base font-semibold text-foreground">
          {avatarUrl ? (
            <img
              src={avatarUrl}
              alt={label}
              className="size-full object-cover"
            />
          ) : avatar?.trim() ? (
            <span aria-hidden="true">{avatar}</span>
          ) : fallbackInitial ? (
            fallbackInitial
          ) : (
            <BotIcon className="size-4 text-muted-foreground" />
          )}
        </div>
        <div className="min-w-0">
          <div className="truncate font-mono text-xs font-semibold text-foreground">
            {title}
          </div>
          <div className={cn("mt-0.5 truncate text-xs", statusClassName)}>
            {status}
          </div>
        </div>
      </div>
    </div>
  );
}
