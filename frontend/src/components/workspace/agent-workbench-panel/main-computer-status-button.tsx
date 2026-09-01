import { MonitorIcon } from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { AgentRunState } from "../agent-run-status";
import {
  agentRunRobotButtonClass,
  agentRunIconClass,
  agentRunStatusLightPulseClass,
  agentRunStatusLightClass,
} from "../agent-run-status";

export function MainComputerStatusButton({
  active,
  label,
  onClick,
  runState,
  title,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  runState: AgentRunState;
  title: string;
}) {
  const { t } = useI18n();
  const buttonClassName = cn(
    "relative flex size-9 shrink-0 items-center justify-center rounded-md border font-mono shadow-[var(--shadow-xs)] transition-colors",
    active && "ring-1 ring-primary/30",
    agentRunRobotButtonClass(runState),
  );
  const iconClassName = cn(
    "size-4 transition-colors",
    agentRunIconClass(runState),
  );
  const pulseClassName = agentRunStatusLightPulseClass(runState);

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={onClick}
          className={buttonClassName}
          aria-label={`${t.agentWorkbenchPanel.mainComputer} · ${label}`}
          title={`${t.agentWorkbenchPanel.mainComputer} · ${label}`}
        >
          <MonitorIcon className={iconClassName} />
          {pulseClassName && (
            <span
              className={cn(
                "absolute -top-0.5 -right-0.5 size-2 rounded-full",
                agentRunStatusLightClass(runState),
                pulseClassName,
              )}
            />
          )}
        </button>
      </TooltipTrigger>
      <TooltipContent align="start" side="bottom" className="max-w-52">
        <div className="font-medium">{t.agentWorkbenchPanel.mainComputer}</div>
        <div className="mt-0.5 text-xs opacity-80">
          {t.agentWorkbenchPanel.currentConversation}
          {" · "}
          {label}
        </div>
        <div className="mt-1 text-xs opacity-75">{title}</div>
      </TooltipContent>
    </Tooltip>
  );
}
