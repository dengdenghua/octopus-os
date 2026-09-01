import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function IconButton({
  label,
  icon,
  disabled,
  onClick,
}: {
  label: string;
  icon: ReactNode;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="size-8"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
    >
      {icon}
    </Button>
  );
}

export function PanelTitle({
  icon,
  title,
  meta,
}: {
  icon: ReactNode;
  title: string;
  meta: string;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <span className="text-primary">{icon}</span>
        {title}
      </div>
      <span className="text-xs text-muted-foreground">{meta}</span>
    </div>
  );
}

export function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "amber" | "emerald" | "rose" | "blue";
}) {
  const tones = {
    amber: "border-warning/25 bg-warning/10",
    emerald: "border-success/25 bg-success/10",
    rose: "border-destructive/25 bg-destructive/10",
    blue: "border-info/25 bg-info/10",
  };
  return (
    <div className={cn("rounded-lg border px-3 py-2", tones[tone])}>
      <div className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 font-mono text-xl font-semibold">{value}</div>
    </div>
  );
}

export function EmptyPanel({ title }: { title: string }) {
  return (
    <div className="rounded-lg border border-dashed border-border-default px-3 py-8 text-center text-sm text-muted-foreground">
      {title}
    </div>
  );
}
