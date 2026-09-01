import { BrainCircuitIcon, CheckIcon, ChevronDownIcon } from "lucide-react";

import type { ReasoningEffort } from "@/core/threads";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

const REASONING_EFFORT_OPTIONS: ReasoningEffort[] = [
  "off",
  "low",
  "medium",
  "high",
  "xhigh",
];

function effortLabel(effort: ReasoningEffort, locale: string): string {
  const zh = locale === "zh-CN";
  switch (effort) {
    case "off":
      return zh ? "关闭" : "Off";
    case "minimal":
      return zh ? "极低" : "Minimal";
    case "low":
      return zh ? "低" : "Low";
    case "medium":
      return zh ? "中" : "Medium";
    case "high":
      return zh ? "高" : "High";
    case "xhigh":
      return zh ? "超高" : "Ultra";
    default:
      return effort;
  }
}

export function ReasoningEffortPicker({
  value,
  locale,
  disabled,
  onChange,
}: {
  value?: ReasoningEffort;
  locale: string;
  disabled?: boolean;
  onChange?: (effort: ReasoningEffort) => void;
}) {
  const current = value ?? "medium";
  const title = locale === "zh-CN" ? "推理等级" : "Reasoning effort";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          aria-label={title}
          title={title}
          className={cn(
            "inline-flex h-7 min-w-0 items-center gap-1 rounded-lg border border-transparent",
            "bg-transparent px-2 text-xs text-muted-foreground outline-none transition",
            "hover:border-border-default hover:bg-muted/60 hover:text-foreground",
            "data-[state=open]:bg-muted data-[state=open]:text-foreground",
            "disabled:cursor-not-allowed disabled:opacity-45",
          )}
        >
          <BrainCircuitIcon className="size-3.5 shrink-0" />
          <span className="hidden sm:inline">
            {effortLabel(current, locale)}
          </span>
          <ChevronDownIcon className="size-3 opacity-60" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="top"
        sideOffset={6}
        className="w-36 p-1"
      >
        <DropdownMenuLabel className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
          {locale === "zh-CN" ? "推理" : "Reasoning"}
        </DropdownMenuLabel>
        {REASONING_EFFORT_OPTIONS.map((effort) => {
          const selected = effort === current;
          return (
            <DropdownMenuItem
              key={effort}
              onClick={() => onChange?.(effort)}
              className={cn(
                "flex h-8 items-center justify-between rounded-md text-sm",
                selected && "bg-muted/60 text-foreground",
              )}
            >
              <span>{effortLabel(effort, locale)}</span>
              {selected && <CheckIcon className="size-4" />}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
