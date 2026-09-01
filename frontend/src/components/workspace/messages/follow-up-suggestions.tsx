/**
 * Follow-up Suggestions · contextual question bubbles.
 *
 * Shows 2-3 smart follow-up question chips after the assistant's reply.
 * Click to send instantly, no typing needed. Auto-generated from
 * conversation context via the ambient-suggestions backend.
 */

import { SparklesIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import {
  useAmbientSuggestions,
  type AmbientSuggestion,
} from "@/hooks/use-ambient-suggestions";

// Survives remounts: this component unmounts during loading and remounts on
// every thread switch, so an instance-level ref would re-POST /run each time
// even when the conversation has no new replies.
const generatedConversationVersions = new Map<string, string>();

/** Test-only: clear the cross-mount generation guard. */
export function resetFollowUpGenerationGuard() {
  generatedConversationVersions.clear();
}

export interface FollowUpSuggestionsProps {
  /** Project root for suggestions storage */
  project: string | null;
  /** Thread/agent ID for context-aware generation */
  agentId?: string;
  /**
   * Monotonic conversation version (e.g. turn count). Included in the
   * generate-once guard so re-entering an unchanged thread skips generation
   * while a genuinely new reply still triggers it.
   */
  conversationVersion: string | number;
  /** Whether the conversation is currently loading */
  isLoading: boolean;
  /** Callback when user clicks a suggestion */
  onSelect: (prompt: string) => void;
  /** Optional base URL override */
  baseUrl?: string;
  /** CSS class name */
  className?: string;
}

export function FollowUpSuggestions({
  project,
  agentId,
  conversationVersion,
  isLoading,
  onSelect,
  baseUrl,
  className,
}: FollowUpSuggestionsProps) {
  const { locale, t } = useI18n();
  const { bucket, generate, setStatus } = useAmbientSuggestions(project, {
    baseUrl,
    locale,
    auto: false, // Manual refresh only
  });

  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  // Filter to pending suggestions, exclude dismissed in this session
  const suggestions = useMemo(() => {
    if (!bucket?.suggestions) return [];
    return bucket.suggestions
      .filter(
        (s) =>
          s.locale === locale && s.status === "pending" && !dismissed.has(s.id),
      )
      .slice(0, 3); // Keep the choice set compact.
  }, [bucket?.suggestions, dismissed, locale]);

  // Auto-generate once per project/agent/locale. The parent only renders this component
  // while the conversation is idle (see message-list.tsx), so mounting IS
  // the "conversation just finished" signal. We can't watch a loading→idle
  // transition here because the component is unmounted during loading.
  useEffect(() => {
    const generationKey = `${project ?? ""}:${agentId ?? ""}:${locale}`;
    const version = String(conversationVersion ?? "");
    if (!agentId || !project) return;
    if (generatedConversationVersions.get(generationKey) === version) return;
    generatedConversationVersions.set(generationKey, version);
    generate(agentId, { turnWindow: 5, locale }).catch(() => {
      // Silently fail, suggestions are optional
    });
  }, [agentId, project, locale, conversationVersion, generate]);

  const handleSelect = useCallback(
    async (suggestion: AmbientSuggestion) => {
      // Mark as accepted in backend
      await setStatus(suggestion.id, "accepted").catch(() => {
        // Ignore errors, proceed with sending
      });
      onSelect(suggestion.prompt);
    },
    [onSelect, setStatus],
  );

  const handleDismiss = useCallback((id: string) => {
    setDismissed((prev) => new Set(prev).add(id));
  }, []);

  // Hide while loading or when no suggestions
  if (isLoading || suggestions.length === 0) {
    return null;
  }

  return (
    <div
      className={cn(
        "flex flex-wrap gap-2 pt-3 animate-[fade-in-up_0.3s_cubic-bezier(0.16,1,0.3,1)]",
        className,
      )}
      role="group"
      aria-label={t.followUpSuggestions.title}
    >
      {suggestions.map((suggestion) => (
        <button
          key={suggestion.id}
          type="button"
          onClick={() => handleSelect(suggestion)}
          onContextMenu={(e) => {
            e.preventDefault();
            handleDismiss(suggestion.id);
          }}
          className={cn(
            "group relative inline-flex items-center gap-1.5",
            "rounded-full border border-border bg-background/80",
            "px-4 py-2 text-sm font-medium text-foreground",
            "shadow-sm backdrop-blur-sm transition-all duration-200",
            "hover:border-primary/40 hover:bg-primary/5 hover:shadow-md",
            "active:scale-[0.98]",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
          )}
          title={suggestion.description || suggestion.title}
        >
          <SparklesIcon className="size-3.5 text-primary/70 transition-colors group-hover:text-primary" />
          <span className="max-w-[280px] truncate">{suggestion.title}</span>
        </button>
      ))}
    </div>
  );
}
