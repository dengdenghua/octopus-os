import { ActivityIcon, AlertTriangleIcon } from "lucide-react";
import { useState, useEffect, useCallback } from "react";
import { swallow } from "@/core/utils/log";
import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { cn } from "@/lib/utils";

interface RateLimitInfo {
  requests_used: number;
  requests_limit: number;
  tokens_used: number;
  tokens_limit: number;
  reset_at: string;
}

export function RateLimitBar({ className }: { className?: string }) {
  const [limits, setLimits] = useState<RateLimitInfo | null>(null);

  const fetchLimits = useCallback(async () => {
    try {
      const res = await fetch(`${getBackendBaseURL()}/api/telemetry/stats`, {
        headers: authHeaders(),
      });
      if (res.ok) {
        const data = await res.json();
        // Extract rate limit info if available
        setLimits(data.rate_limits || null);
      }
    } catch (e) {
      swallow(e);
    }
  }, []);

  useEffect(() => {
    fetchLimits();
    const interval = setInterval(fetchLimits, 30000); // Every 30s
    return () => clearInterval(interval);
  }, [fetchLimits]);

  // Always show a minimal status bar even without rate limit data
  return (
    <div
      className={cn(
        "text-muted-foreground flex items-center justify-between border-t px-3 py-1 text-xs",
        className,
      )}
    >
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-1">
          <ActivityIcon className="size-2.5" />
          Echo
        </span>
        {limits && (
          <>
            <span>
              Requests: {limits.requests_used}/{limits.requests_limit}
            </span>
            <span>
              Tokens: {(limits.tokens_used / 1000).toFixed(1)}K/
              {(limits.tokens_limit / 1000).toFixed(0)}K
            </span>
          </>
        )}
      </div>
      <div className="flex items-center gap-2">
        {limits && limits.requests_used / limits.requests_limit > 0.8 && (
          <span className="flex items-center gap-0.5 text-warning">
            <AlertTriangleIcon className="size-2.5" />
            Rate limit warning
          </span>
        )}
        {limits?.reset_at && (
          <span>Resets: {new Date(limits.reset_at).toLocaleTimeString()}</span>
        )}
      </div>
    </div>
  );
}
