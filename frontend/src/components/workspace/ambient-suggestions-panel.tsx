/**
 * Ambient Suggestions Panel · "what should I do next?" surface.
 *
 * Renders the per-project suggestion bucket from
 * ``useAmbientSuggestions``. Pending items get Accept / Dismiss
 * buttons; accepted and dismissed go into collapsible sections so
 * the active list stays focused.
 *
 * "Accept" here only flips the status to ``accepted`` — it does
 * NOT auto-spawn a thread with the suggested prompt. That decision
 * belongs to the caller (typically a parent component that owns
 * the chat session) which subscribes to ``setStatus`` / reads the
 * latest accepted suggestion and routes accordingly.
 */

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useI18n } from "@/core/i18n/hooks";
import {
  useAmbientSuggestions,
  type AmbientSuggestion,
} from "@/hooks/use-ambient-suggestions";

export interface AmbientSuggestionsPanelProps {
  /** Project root path. Required — the storage layout is per-project. */
  project: string;
  /**
   * If provided, shows a "Generate" button that calls the LLM
   * generator. Without it the panel is read-only.
   */
  agentId?: string;
  /** Override the API base URL (mainly for embedded contexts). */
  baseUrl?: string;
}

export function AmbientSuggestionsPanel({
  project,
  agentId,
  baseUrl,
}: AmbientSuggestionsPanelProps) {
  const { locale, t } = useI18n();
  const { bucket, loading, error, generate, setStatus } = useAmbientSuggestions(
    project,
    { baseUrl, locale },
  );
  const [generating, setGenerating] = useState(false);

  const suggestions = (bucket?.suggestions ?? []).filter(
    (suggestion) => suggestion.locale === locale,
  );
  const pending = suggestions.filter((s) => s.status === "pending");
  const accepted = suggestions.filter((s) => s.status === "accepted");
  const dismissed = suggestions.filter((s) => s.status === "dismissed");

  const onGenerate = async () => {
    if (!agentId) return;
    setGenerating(true);
    try {
      await generate(agentId);
    } finally {
      setGenerating(false);
    }
  };

  const featureDisabled = bucket?.enabled === false;

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle className="text-base">
          {t.ambientSuggestionsPanel.title}
        </CardTitle>
        {agentId && (
          <Button
            variant="outline"
            size="sm"
            onClick={onGenerate}
            disabled={generating || loading || featureDisabled}
            aria-label={t.ambientSuggestionsPanel.generateAria}
          >
            {generating
              ? t.ambientSuggestionsPanel.generating
              : t.ambientSuggestionsPanel.generate}
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {loading && (
          <div className="text-muted-foreground text-sm">
            {t.ambientSuggestionsPanel.loading}
          </div>
        )}
        {error && (
          <div role="alert" className="text-destructive text-sm">
            {t.ambientSuggestionsPanel.loadFailed(error)}
          </div>
        )}
        {featureDisabled && !loading && !error && (
          <div className="text-muted-foreground text-sm">
            {t.ambientSuggestionsPanel.featureDisabled}
          </div>
        )}
        {!loading && !error && !featureDisabled && (
          <>
            {pending.length === 0 &&
            accepted.length === 0 &&
            dismissed.length === 0 ? (
              <div className="text-muted-foreground text-sm">
                {t.ambientSuggestionsPanel.empty}
                {agentId && <> {t.ambientSuggestionsPanel.emptyGenerateHint}</>}
              </div>
            ) : (
              <div className="flex flex-col gap-4">
                {pending.length > 0 && (
                  <ul className="flex flex-col gap-3">
                    {pending.map((s) => (
                      <SuggestionCard
                        key={s.id}
                        suggestion={s}
                        onAccept={() => setStatus(s.id, "accepted")}
                        onDismiss={() => setStatus(s.id, "dismissed")}
                      />
                    ))}
                  </ul>
                )}

                {accepted.length > 0 && (
                  <details>
                    <summary className="text-muted-foreground cursor-pointer text-sm select-none">
                      {t.ambientSuggestionsPanel.recent(accepted.length)}
                    </summary>
                    <ul className="mt-2 flex flex-col gap-2">
                      {accepted.map((s) => (
                        <li
                          key={s.id}
                          className="border-border-default rounded-md border px-3 py-2"
                        >
                          <div className="text-sm font-medium">{s.title}</div>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}

                {dismissed.length > 0 && (
                  <details>
                    <summary className="text-muted-foreground cursor-pointer text-sm select-none">
                      {t.ambientSuggestionsPanel.dismissed(dismissed.length)}
                    </summary>
                    <ul className="mt-2 flex flex-col gap-2">
                      {dismissed.map((s) => (
                        <li
                          key={s.id}
                          className="border-border-default rounded-md border px-3 py-2"
                        >
                          <div className="text-muted-foreground text-sm">
                            {s.title}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

interface SuggestionCardProps {
  suggestion: AmbientSuggestion;
  onAccept: () => void;
  onDismiss: () => void;
}

function SuggestionCard({
  suggestion,
  onAccept,
  onDismiss,
}: SuggestionCardProps) {
  const { t } = useI18n();

  return (
    <li className="border-border bg-card rounded-md border p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h4 className="text-sm font-semibold">{suggestion.title}</h4>
            {suggestion.experimental && (
              <Badge variant="outline">
                {t.ambientSuggestionsPanel.experimental}
              </Badge>
            )}
          </div>
          {suggestion.description && (
            <p className="text-muted-foreground mt-1 text-xs">
              {suggestion.description}
            </p>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            variant="default"
            size="sm"
            onClick={onAccept}
            aria-label={t.ambientSuggestionsPanel.acceptAria(suggestion.title)}
          >
            {t.ambientSuggestionsPanel.accept}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onDismiss}
            aria-label={t.ambientSuggestionsPanel.dismissAria(suggestion.title)}
          >
            {t.ambientSuggestionsPanel.dismiss}
          </Button>
        </div>
      </div>
    </li>
  );
}
