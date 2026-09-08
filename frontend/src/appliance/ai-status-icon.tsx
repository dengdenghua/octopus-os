/** A fixed AI core silhouette; state stays legible without color or animation. */
export function AiStatusIcon({
  state,
}: {
  state: "connected" | "checking" | "unavailable" | "unknown";
}) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 24 24"
      className="size-[15px] shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M7 4a9 9 0 0 0 0 16M17 4a9 9 0 0 1 0 16" />
      {state === "unavailable" ? (
        <path d="m9 15 6-6" />
      ) : (
        <path
          d="m12 8 4 4-4 4-4-4Z"
          fill={state === "connected" ? "currentColor" : "none"}
          strokeWidth="1.6"
          opacity={state === "unknown" ? 0.45 : 1}
        />
      )}
    </svg>
  );
}
