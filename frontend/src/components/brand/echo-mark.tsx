import { useId, type ComponentProps } from "react";

import { cn } from "@/lib/utils";

export type EchoMarkTone = "brand" | "light" | "current";

export interface EchoMarkProps extends Omit<ComponentProps<"svg">, "children"> {
  tone?: EchoMarkTone;
  title?: string;
}

/** Canonical Echo mark: an open echo ring and its returning signal. */
export function EchoMark({
  className,
  tone = "brand",
  title,
  ...props
}: EchoMarkProps) {
  const gradientId = `echo-mark-${useId().replace(/:/g, "")}`;
  const labelled = Boolean(title || props["aria-label"]);
  const paint = tone === "current" ? "currentColor" : `url(#${gradientId})`;

  return (
    <svg
      {...props}
      className={cn("shrink-0", className)}
      viewBox="0 0 64 64"
      fill="none"
      role={labelled ? "img" : undefined}
      aria-hidden={labelled ? undefined : true}
    >
      {title ? <title>{title}</title> : null}
      {tone !== "current" ? (
        <defs>
          <linearGradient
            id={gradientId}
            x1="16"
            y1="10"
            x2="49"
            y2="54"
            gradientUnits="userSpaceOnUse"
          >
            {tone === "light" ? (
              <>
                <stop stopColor="#FFFFFF" />
                <stop offset="0.52" stopColor="#D8EAFF" />
                <stop offset="1" stopColor="#87B8FF" />
              </>
            ) : (
              <>
                <stop stopColor="#2454D8" />
                <stop offset="0.52" stopColor="#4D8CF7" />
                <stop offset="1" stopColor="#A9DDFC" />
              </>
            )}
          </linearGradient>
        </defs>
      ) : null}
      <path
        d="M45.25 15.9A21.5 21.5 0 1 0 45.25 48.1"
        stroke={paint}
        strokeWidth="7.5"
        strokeLinecap="round"
      />
      <circle cx="51.5" cy="32" r="4.6" fill={paint} />
    </svg>
  );
}
