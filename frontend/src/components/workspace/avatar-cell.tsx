/**
 * AvatarCell — extracted from `workspace-sidebar.tsx` (P3 decomposition).
 * Behavior-preserving move.
 */
import { useState } from "react";

import { withAgentAvatarVersion } from "@/core/agents/avatar";
import { getBackendBaseURL } from "@/core/config";
import { cn } from "@/lib/utils";

export function AvatarCell({
  agentId,
  className,
}: {
  agentId: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span
        className={cn(
          "flex items-center justify-center bg-muted text-micro font-semibold uppercase text-muted-foreground",
          className,
        )}
        title={agentId}
      >
        {(agentId[0] || "?").toUpperCase()}
      </span>
    );
  }
  return (
    <img
      src={withAgentAvatarVersion(
        `${getBackendBaseURL()}/api/agents/${encodeURIComponent(agentId)}/avatar`,
      )}
      alt={agentId}
      title={agentId}
      onError={() => setFailed(true)}
      className={cn("object-cover", className)}
    />
  );
}
