import {
  ChevronDownIcon,
  ShieldCheckIcon,
  ShieldIcon,
  TriangleAlertIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/* Implementation note. */

export type AccessTone = "safe" | "warn" | "danger";

export interface AccessPillProps {
  tone?: AccessTone;
  label: ReactNode;
  icon?: ReactNode;
  onClick?: () => void;
  chevron?: boolean;
  className?: string;
}

const TONE: Record<AccessTone, { icon: ReactNode; styles: string }> = {
  safe: {
    icon: <ShieldCheckIcon className="size-3.5" />,
    styles:
      "border-emerald-500/25 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-500/10",
  },
  warn: {
    icon: <ShieldIcon className="size-3.5" />,
    styles:
      "border-amber-500/30 text-amber-700 dark:text-amber-400 hover:bg-amber-500/10",
  },
  danger: {
    icon: <TriangleAlertIcon className="size-3.5" />,
    styles:
      "border-red-500/35 text-red-700 dark:text-red-400 hover:bg-red-500/10",
  },
};

export function AccessPill({
  tone = "warn",
  label,
  icon,
  onClick,
  chevron = true,
  className,
}: AccessPillProps) {
  const t = TONE[tone];
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex h-6 items-center gap-1 rounded-full border bg-transparent px-2 text-[11px] font-medium leading-none",
        "transition-[background-color,border-color] duration-150",
        t.styles,
        className,
      )}
    >
      {icon ?? t.icon}
      <span>{label}</span>
      {chevron && <ChevronDownIcon className="size-3 opacity-60" />}
    </button>
  );
}
