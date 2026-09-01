/* Implementation note. */
import { useQuery } from "@tanstack/react-query";
import { BrainCircuitIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  GlobalControlPlaneAccessError,
  getEvolutionStatus,
  type EvolutionStatus,
} from "@/core/observability/api";
import { useI18n } from "@/core/i18n/hooks";
import { canAccessGlobalControlPlane } from "@/core/auth/control-plane-access";
import { cn } from "@/lib/utils";
import { useOptionalAuth } from "@/providers/AuthProvider";

import { EvolutionPanel } from "./evolution-panel";

interface EvolutionIndicatorProps {
  className?: string;
  /* Implementation note. */
  showWhenEmpty?: boolean;
  compact?: boolean;
  quiet?: boolean;
}

export function EvolutionIndicator({
  className,
  showWhenEmpty = false,
  compact = false,
  quiet = false,
}: EvolutionIndicatorProps) {
  const { t } = useI18n();
  const auth = useOptionalAuth();
  const { data, error } = useQuery<EvolutionStatus, Error>({
    queryKey: ["evolution", "status"],
    queryFn: ({ signal }) => getEvolutionStatus(signal),
    enabled: canAccessGlobalControlPlane(
      auth?.authStatus ?? null,
      auth?.user ?? null,
    ),
    refetchInterval: (query) =>
      query.state.error instanceof GlobalControlPlaneAccessError
        ? false
        : 60_000, // 1 min
    retry: (failureCount, failure) =>
      !(failure instanceof GlobalControlPlaneAccessError) && failureCount < 1,
    refetchOnWindowFocus: (query) =>
      !(query.state.error instanceof GlobalControlPlaneAccessError),
    refetchOnReconnect: (query) =>
      !(query.state.error instanceof GlobalControlPlaneAccessError),
    staleTime: 30_000,
  });

  // Implementation note.
  const prevRef = useRef<{ rules: number; memories: number } | null>(null);
  const [delta, setDelta] = useState<{
    rules: number;
    memories: number;
    key: number;
  } | null>(null);

  useEffect(() => {
    if (!data) return;
    const rules = data.rules_count ?? 0;
    const memories = data.memories_count ?? 0;
    const prev = prevRef.current;
    // Implementation note.
    if (prev !== null) {
      const dRules = rules - prev.rules;
      const dMem = memories - prev.memories;
      if (dRules > 0 || dMem > 0) {
        setDelta({ rules: dRules, memories: dMem, key: Date.now() });
      }
    }
    prevRef.current = { rules, memories };
  }, [data]);

  if (error instanceof GlobalControlPlaneAccessError) {
    // Evolution is optional. A tenant-scoped user lacking global
    // observability access must not see that background 403 as a composer or
    // task error; the dedicated observability page keeps its explicit gate.
    return null;
  }
  if (!data || data.enabled === false) return null;
  const rules = data.rules_count ?? 0;
  const memories = data.memories_count ?? 0;
  if (!showWhenEmpty && rules === 0 && memories === 0) return null;
  if (quiet && !delta) return null;
  const summary = t.evolutionIndicator.rulesAndMemories(rules, memories);

  const animating = delta !== null;

  const trigger = (
    <button
      type="button"
      className={cn(
        "relative flex items-center gap-1 rounded-lg px-2 py-1 text-xs",
        compact && "size-7 justify-center gap-0 px-0 py-0 text-xs",
        "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
        "transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
        animating && "text-foreground",
        className,
      )}
      title={
        compact
          ? `${t.evolutionIndicator.clickToView} · ${summary}`
          : t.evolutionIndicator.clickToView
      }
      aria-label={
        compact
          ? `${t.evolutionIndicator.clickToView} · ${summary}`
          : t.evolutionIndicator.clickToView
      }
      data-testid="evolution-indicator"
    >
      <BrainCircuitIcon
        key={animating ? delta?.key : "idle"}
        className={cn(
          compact ? "size-4" : "size-3.5",
          animating && "animate-learn-pulse text-[color:var(--primary)]",
        )}
        onAnimationEnd={() => setDelta(null)}
      />
      {compact ? (
        <span className="sr-only">{summary}</span>
      ) : (
        <span>{summary}</span>
      )}
      {compact && !quiet && (
        <span
          key={`${rules}-${memories}`}
          className={cn(
            "pointer-events-none absolute -top-1 -right-1 rounded-full px-1.5 py-[1px]",
            "text-xs font-semibold leading-none tabular-nums",
            "bg-[color:var(--primary)] text-[color:var(--primary-foreground)]",
            "shadow-[var(--shadow-xs)]",
          )}
        >
          {rules + memories}
        </span>
      )}
      {!compact && delta && (
        <span
          key={delta.key}
          className={cn(
            "pointer-events-none absolute -top-2 -right-1 rounded-full px-1.5 py-[1px]",
            "text-xs font-semibold leading-none tabular-nums",
            "bg-[color:var(--primary)] text-[color:var(--primary-foreground)]",
            "shadow-[var(--shadow-xs)] animate-learn-badge",
          )}
        >
          {formatDelta(delta.rules, delta.memories, t)}
        </span>
      )}
    </button>
  );

  return <EvolutionPanel status={data} trigger={trigger} />;
}

function formatDelta(
  dRules: number,
  dMem: number,
  t: {
    evolutionIndicator: {
      deltaRules: (n: number) => string;
      deltaMemories: (n: number) => string;
    };
  },
): string {
  const parts: string[] = [];
  if (dRules > 0) parts.push(t.evolutionIndicator.deltaRules(dRules));
  if (dMem > 0) parts.push(t.evolutionIndicator.deltaMemories(dMem));
  return parts.join(" ");
}
