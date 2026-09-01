import { useState } from "react";
import { getBackendBaseURL } from "@/core/config";

interface AgentVisualGalleryProps {
  visualUrls?: Record<string, string> | null;
  agentName: string;
  className?: string;
}

/**
 * Display agent character illustrations (front/side/back views).
 * Used for ECHO Universe characters and other agents with full-body art.
 */
export function AgentVisualGallery({
  visualUrls,
  agentName,
  className = "",
}: AgentVisualGalleryProps) {
  const [activeView, setActiveView] = useState<"front" | "side" | "back">("front");

  if (!visualUrls || Object.keys(visualUrls).length === 0) {
    return null;
  }

  const views = ["front", "side", "back"] as const;
  const availableViews = views.filter((view) => visualUrls[view]);

  if (availableViews.length === 0) {
    return null;
  }

  const currentUrl = visualUrls[activeView];
  const fullUrl = currentUrl
    ? `${getBackendBaseURL()}${currentUrl}`
    : undefined;

  return (
    <div className={`flex flex-col gap-3 ${className}`}>
      {/* View selector tabs */}
      <div className="flex gap-2">
        {availableViews.map((view) => (
          <button
            key={view}
            onClick={() => setActiveView(view)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              activeView === view
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:bg-muted/80"
            }`}
          >
            {view === "front" && "正面"}
            {view === "side" && "侧面"}
            {view === "back" && "背面"}
          </button>
        ))}
      </div>

      {/* Illustration display */}
      {fullUrl && (
        <div className="relative aspect-[3/4] w-full overflow-hidden rounded-lg border border-border bg-card">
          <img
            src={fullUrl}
            alt={`${agentName} - ${activeView} view`}
            className="h-full w-full object-contain"
          />
        </div>
      )}
    </div>
  );
}
