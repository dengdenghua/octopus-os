/**
 * Feature Flags Panel · read-only catalog viewer for operators.
 *
 * Mounted on a settings/admin page. Lists every backend-registered
 * flag with its current value, source (env / file / default), and
 * description. Provides a one-click "Reload from disk" that calls
 * ``POST /api/feature-flags/reload``.
 *
 * Display-only: editing flag values is intentionally NOT supported
 * here — those go through env vars or ``data/feature_flags.json``
 * (so the audit trail lives on the filesystem). Live edits would
 * require a write endpoint we deliberately did not ship.
 */

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useI18n } from "@/core/i18n/hooks";
import { useFeatureFlags } from "@/hooks/use-feature-flags";

function formatValue(value: unknown): string {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value === null || value === undefined) return "—";
  return String(value);
}

function sourceVariant(
  source: string,
): "default" | "secondary" | "destructive" | "outline" {
  switch (source) {
    case "env":
      return "default";
    case "file":
      return "secondary";
    case "default":
      return "outline";
    default:
      return source.startsWith("legacy_env:") ? "secondary" : "outline";
  }
}

export interface FeatureFlagsPanelProps {
  /** Hide flags whose ``experimental`` is false. */
  experimentalOnly?: boolean;
  /** Override the API base URL (mainly for embedded contexts). */
  baseUrl?: string;
}

export function FeatureFlagsPanel({
  experimentalOnly = false,
  baseUrl,
}: FeatureFlagsPanelProps) {
  const { t } = useI18n();
  const { flags, loading, error, reload } = useFeatureFlags({ baseUrl });
  const [reloading, setReloading] = useState(false);

  const visible = useMemo(() => {
    if (!experimentalOnly) return flags;
    return flags.filter((f) => f.experimental);
  }, [flags, experimentalOnly]);

  const onReload = async () => {
    setReloading(true);
    try {
      await reload();
    } finally {
      setReloading(false);
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle className="text-base">{t.featureFlagsPanel.title}</CardTitle>
        <Button
          variant="outline"
          size="sm"
          onClick={onReload}
          disabled={reloading || loading}
          aria-label={t.featureFlagsPanel.reloadAria}
        >
          {reloading
            ? t.featureFlagsPanel.reloading
            : t.featureFlagsPanel.reload}
        </Button>
      </CardHeader>
      <CardContent>
        {loading && (
          <div className="text-muted-foreground text-sm">
            {t.featureFlagsPanel.loading}
          </div>
        )}
        {error && (
          <div role="alert" className="text-destructive text-sm">
            {t.featureFlagsPanel.loadFailed(error)}
          </div>
        )}
        {!loading && !error && visible.length === 0 && (
          <div className="text-muted-foreground text-sm">
            {t.featureFlagsPanel.empty}
          </div>
        )}
        {!loading && !error && visible.length > 0 && (
          <ul className="divide-y divide-border">
            {visible.map((flag) => (
              <li
                key={flag.name}
                className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0"
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <code className="text-sm font-semibold">{flag.name}</code>
                  <Badge variant="outline" className="font-mono">
                    {formatValue(flag.value)}
                  </Badge>
                  <Badge variant={sourceVariant(flag.source)}>
                    {flag.source}
                  </Badge>
                  {flag.experimental && (
                    <Badge variant="destructive">
                      {t.featureFlagsPanel.experimental}
                    </Badge>
                  )}
                </div>
                {flag.description && (
                  <p className="text-muted-foreground text-xs">
                    {flag.description}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
