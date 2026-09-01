import { Badge } from "@/components/ui/badge";
import { getBackendBaseURL } from "@/core/config";
import { cn } from "@/lib/utils";
import { useEffect, useState } from "react";

interface AgentMessageHeaderProps {
  agentDisplayName: string;
  avatarUrl?: string;
  icon?: string | null;
  role?: "tl" | "member";
}

export function AgentAvatar({
  agentDisplayName,
  avatarUrl,
  icon,
  className = "h-6 w-6 rounded-lg",
}: {
  agentDisplayName: string;
  avatarUrl?: string;
  icon?: string | null;
  className?: string;
}) {
  const backendBase = getBackendBaseURL();
  const fullAvatarUrl = avatarUrl
    ? avatarUrl.startsWith("http")
      ? avatarUrl
      : `${backendBase}${avatarUrl}`
    : null;
  const [failedAvatarUrl, setFailedAvatarUrl] = useState<string | null>(null);
  useEffect(() => {
    setFailedAvatarUrl(null);
  }, [fullAvatarUrl]);
  const showImage = Boolean(fullAvatarUrl && failedAvatarUrl !== fullAvatarUrl);
  const emoji = icon?.trim() || "";
  const initial = (agentDisplayName || "?").trim().charAt(0).toUpperCase();

  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-center overflow-hidden border border-border-default bg-muted text-sm leading-none",
        !showImage &&
          !emoji &&
          "text-xs font-semibold text-muted-foreground",
        className,
      )}
    >
      {showImage && fullAvatarUrl ? (
        <img
          src={fullAvatarUrl}
          alt={agentDisplayName}
          className="h-full w-full object-cover"
          onError={() => setFailedAvatarUrl(fullAvatarUrl)}
        />
      ) : emoji ? (
        emoji
      ) : (
        initial
      )}
    </div>
  );
}

export function AgentMessageHeader({
  agentDisplayName,
  avatarUrl,
  icon,
  role,
}: AgentMessageHeaderProps) {
  return (
    <div className="mt-3 mb-1 flex items-center gap-2 first:mt-0">
      <AgentAvatar
        agentDisplayName={agentDisplayName}
        avatarUrl={avatarUrl}
        icon={icon}
      />
      <span className="text-sm font-semibold">{agentDisplayName}</span>
      {role === "tl" && (
        <Badge
          variant="outline"
          className="border-success/50 bg-success/10 text-success px-1.5 py-0 text-xs leading-4 dark:text-success"
        >
          TL
        </Badge>
      )}
    </div>
  );
}
