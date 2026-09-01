/**
 * Invariants Panel · browse constitution rules + their enforcers.
 *
 * Pulls from ``/api/invariants`` via the ``useInvariants`` hook.
 * Read-only — rules are declared in code via the ``@enforces``
 * decorator, not editable from the UI.
 *
 * Uses a simple filter input so an operator auditing a specific
 * rule id doesn't have to scroll through the full catalog.
 */

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useI18n } from "@/core/i18n/hooks";
import { useInvariants } from "@/hooks/use-invariants";

export interface InvariantsPanelProps {
  baseUrl?: string;
}

export function InvariantsPanel({ baseUrl }: InvariantsPanelProps) {
  const { t } = useI18n();
  const { rules, totalRules, totalEnforcers, loading, error, rebuild } =
    useInvariants({ baseUrl });
  const [filter, setFilter] = useState("");
  const [rebuilding, setRebuilding] = useState(false);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return rules;
    return rules.filter(
      (r) =>
        r.rule_id.toLowerCase().includes(q) ||
        r.enforcers.some(
          (e) =>
            e.module.toLowerCase().includes(q) ||
            e.qualname.toLowerCase().includes(q),
        ),
    );
  }, [rules, filter]);

  const onRebuild = async () => {
    setRebuilding(true);
    try {
      await rebuild();
    } finally {
      setRebuilding(false);
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle className="text-base">{t.invariantsPanel.title}</CardTitle>
        <div className="flex items-center gap-2">
          <Badge variant="outline" aria-label={t.invariantsPanel.ruleCountAria}>
            {t.invariantsPanel.ruleCount(totalRules)}
          </Badge>
          <Badge
            variant="outline"
            aria-label={t.invariantsPanel.enforcerCountAria}
          >
            {t.invariantsPanel.enforcerCount(totalEnforcers)}
          </Badge>
          <Button
            variant="outline"
            size="sm"
            onClick={onRebuild}
            disabled={rebuilding || loading}
            aria-label={t.invariantsPanel.rebuildAria}
          >
            {rebuilding
              ? t.invariantsPanel.rebuilding
              : t.invariantsPanel.rebuild}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {error && (
          <div role="alert" className="text-destructive text-sm">
            {t.invariantsPanel.loadFailed(error)}
          </div>
        )}

        {!error && (
          <input
            type="text"
            placeholder={t.invariantsPanel.filterPlaceholder}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="border-border bg-background rounded border px-2 py-1 text-sm"
            aria-label={t.invariantsPanel.filterAria}
          />
        )}

        {loading && rules.length === 0 && (
          <div className="text-muted-foreground text-sm">
            {t.invariantsPanel.loading}
          </div>
        )}

        {!loading && !error && visible.length === 0 && (
          <div className="text-muted-foreground text-sm">
            {filter
              ? t.invariantsPanel.emptyFiltered(filter)
              : t.invariantsPanel.empty}
          </div>
        )}

        {visible.length > 0 && (
          <ul className="divide-border divide-y">
            {visible.map((rule) => (
              <li key={rule.rule_id} className="py-3 first:pt-0 last:pb-0">
                <div className="flex items-center gap-2">
                  <code className="text-sm font-semibold">{rule.rule_id}</code>
                  <Badge variant="outline">
                    {t.invariantsPanel.enforcerCountLabel(
                      rule.enforcers.length,
                    )}
                  </Badge>
                </div>
                {rule.enforcers.length > 0 && (
                  <ul className="mt-1 ml-2 flex flex-col gap-0.5">
                    {rule.enforcers.map((e, i) => (
                      <li
                        key={`${rule.rule_id}-${i}`}
                        className="text-muted-foreground text-xs"
                      >
                        <code>
                          {e.module}:{e.qualname}
                        </code>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
