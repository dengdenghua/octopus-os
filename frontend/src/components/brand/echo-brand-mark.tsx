import { cn } from "@/lib/utils";

type EchoBrandMarkProps = {
  className?: string;
  size?: "md" | "lg";
};

const sizeConfig = {
  md: {
    box: "size-9 rounded-xl",
    svg: "size-5",
  },
  lg: {
    box: "size-11 rounded-xl",
    svg: "size-6",
  },
} as const;

function EchoGlyph({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      fill="none"
      className={className}
      aria-hidden="true"
    >
      {/* Head */}
      <circle cx="16" cy="11" r="6.5" fill="currentColor" />
      {/* Eyes */}
      <circle cx="13.8" cy="10.2" r="1.3" fill="white" fillOpacity="0.95" />
      <circle cx="18.2" cy="10.2" r="1.3" fill="white" fillOpacity="0.95" />
      <circle cx="14" cy="10.5" r="0.6" fill="#1a1a1e" />
      <circle cx="18.4" cy="10.5" r="0.6" fill="#1a1a1e" />
      <circle cx="14.2" cy="10" r="0.25" fill="white" />
      <circle cx="18.6" cy="10" r="0.25" fill="white" />
      {/* Tentacles: 5 elegant curves */}
      <g
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        fill="none"
        strokeOpacity="0.9"
      >
        <path d="M10.5 15.5 C8 19, 7 23, 8 26.5" />
        <path d="M13 16.5 C11.5 20, 11 24, 12 27" />
        <path d="M16 17 C15.5 21, 15.5 25, 16 27.5" />
        <path d="M19 16.5 C20.5 20, 21 24, 20 27" />
        <path d="M21.5 15.5 C24 19, 25 23, 24 26.5" />
      </g>
      {/* Tentacle tips */}
      <g fill="currentColor" fillOpacity="0.9">
        <circle cx="8" cy="26.5" r="1.4" />
        <circle cx="12" cy="27" r="1.3" />
        <circle cx="16" cy="27.5" r="1.2" />
        <circle cx="20" cy="27" r="1.3" />
        <circle cx="24" cy="26.5" r="1.4" />
      </g>
    </svg>
  );
}

export function EchoBrandMark({
  className,
  size = "md",
}: EchoBrandMarkProps) {
  const cfg = sizeConfig[size];

  return (
    <span
      aria-hidden="true"
      className={cn(
        "relative inline-grid shrink-0 place-items-center overflow-hidden border border-foreground/10 bg-foreground/[0.04] text-primary shadow-[var(--shadow-xs)]",
        cfg.box,
        className,
      )}
    >
      <EchoGlyph className={cfg.svg} />
    </span>
  );
}
