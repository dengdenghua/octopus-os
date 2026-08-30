import { BrainIcon, PlayIcon, EyeIcon, RepeatIcon } from "lucide-react";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

type TAORPhase = "think" | "act" | "observe" | "repeat";

const PHASE_ICONS: Record<
  TAORPhase,
  { icon: React.ElementType; color: string }
> = {
  think: { icon: BrainIcon, color: "text-foreground" },
  act: { icon: PlayIcon, color: "text-muted-foreground/65" },
  observe: { icon: EyeIcon, color: "text-muted-foreground/65" },
  repeat: { icon: RepeatIcon, color: "text-muted-foreground/65" },
};

export function TAORBadge({
  phase,
  active = false,
  labelOverride,
  className,
}: {
  phase: TAORPhase;
  active?: boolean;
  labelOverride?: string;
  className?: string;
}) {
  const { t } = useI18n();
  const config = PHASE_ICONS[phase];
  const Icon = config.icon;
  const label =
    labelOverride ??
    (phase === "think"
      ? active
        ? t.taor.think
        : t.taor.thinking
      : phase === "act"
        ? active
          ? t.taor.act
          : t.taor.acting
        : phase === "observe"
          ? active
            ? t.taor.observe
            : t.taor.observing
          : t.taor.repeat);
  return (
    <span
      className={cn(
        phase === "think"
          ? "inline-flex items-center gap-1 rounded px-0 py-0 text-sm font-medium normal-case tracking-normal"
          : "inline-flex items-center gap-1 rounded px-0 py-0 text-xs font-medium normal-case tracking-normal",
        config.color,
        className,
      )}
    >
      <Icon className={phase === "think" ? "size-4" : "size-3"} />
      {label}
    </span>
  );
}

export function IterationDivider({
  iteration: _iteration,
  maxIterations: _maxIterations,
}: {
  iteration: number;
  maxIterations?: number;
}) {
  return (
    <div aria-hidden="true" className="flex items-center gap-2 py-1">
      <div className="border-border h-px flex-1 border-t border-dashed" />
      <div className="border-border h-px flex-1 border-t border-dashed" />
    </div>
  );
}
