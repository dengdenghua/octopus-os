import { cn } from "@/lib/utils";

/**
 * Segmented control. A pill-shaped group with a single selected
 * option that fades in a subtle surface + shadow. Used wherever
 * the UI needs an inline toggle (e.g. Free / Pro tier picker).
 *
 * Values can be string or number; the generic T carries through so callers
 * get strict onChange typing.
 */

export type SegmentedOption<T extends string | number> = {
  value: T;
  label: React.ReactNode;
  /** Optional secondary label (shown smaller, beneath the primary label). */
  preview?: React.ReactNode;
  /** Optional icon rendered to the left of the label. */
  icon?: React.ReactNode;
  disabled?: boolean;
};

export interface SegmentedControlProps<T extends string | number> {
  value: T;
  onChange: (value: T) => void;
  options: readonly SegmentedOption<T>[];
  /** Visual size. "sm" for inline toolbars, "md" for forms. */
  size?: "sm" | "md";
  /** If true, each option stretches to fill the container. */
  fullWidth?: boolean;
  className?: string;
  "aria-label"?: string;
}

export function SegmentedControl<T extends string | number>({
  value,
  onChange,
  options,
  size = "md",
  fullWidth = false,
  className,
  ...props
}: SegmentedControlProps<T>) {
  return (
    <div
      data-slot="segmented-control"
      role="radiogroup"
      aria-label={props["aria-label"]}
      className={cn(
        "inline-flex gap-1 rounded-xl border bg-muted/30 p-1",
        fullWidth && "flex w-full",
        className,
      )}
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            data-slot="segmented-control-item"
            data-state={active ? "active" : "inactive"}
            key={String(opt.value)}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={opt.disabled}
            onClick={() => onChange(opt.value)}
            className={cn(
              "relative inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-all",
              size === "sm"
                ? "min-w-[64px] px-2.5 py-1 text-xs"
                : "min-w-[92px] px-3 py-1.5 text-xs",
              fullWidth && "flex-1",
              active
                ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                : "text-muted-foreground hover:text-foreground",
              opt.disabled && "pointer-events-none opacity-40",
              opt.preview ? "flex-col gap-0.5" : "",
            )}
          >
            {opt.icon}
            <span>{opt.label}</span>
            {opt.preview && (
              <span className="text-micro font-normal opacity-60">
                {opt.preview}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
