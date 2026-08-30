/* Skill Packs (技能包) sub-tab for the Agent Market.

   Originally lived at ``app/workspace/meta-skills/page.tsx`` as a
   standalone workspace route. Moved into the market page so that the
   skill-pack catalog and agent catalog share the same sidebar
   entry-point and the user can flip between them without losing
   context. Renders the on-disk ``meta_skills/`` YAML catalog by
   calling ``GET /api/meta-skills`` (list) and
   ``GET /api/meta-skills/{name}/mermaid`` (per-pack Mermaid
   ``flowchart`` source) on the FastAPI backend.

   The Mermaid ``flowchart`` source is rendered to SVG by the shared
   ``MermaidBlock`` component, which lazy-imports the real ``mermaid``
   library (aliased to ``mermaid-real`` so it stays in its own chunk
   and out of the workspace entry bundle). On a render/parse failure
   MermaidBlock falls back to showing the raw source.
*/

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  BoxesIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronUpIcon,
  RefreshCwIcon,
  SearchIcon,
  SparklesIcon,
  XCircleIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { MermaidBlock } from "@/components/workspace/messages/mermaid-block";
import { useI18n } from "@/core/i18n/hooks";
import { getBackendBaseURL } from "@/core/config";
import { swallow } from "@/core/utils/log";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ */
/*  Types — kept in sync with runtime/sensing/siphon/meta_skill_router.py */
/* ------------------------------------------------------------------ */

type MetaSkillSummary = {
  name: string;
  description: string;
  affinity: string[];
  budget_tokens: number;
  budget_usd: number;
  budget_latency_ms: number;
  kind: string;
  display_name: string;
  step_count: number;
  steps?: string[];
};

type MetaSkillListResponse = {
  count: number;
  packs: MetaSkillSummary[];
};

type MetaSkillMermaidResponse = {
  name: string;
  direction: string;
  mermaid: string;
};

type MetaSkillMatchResponse = {
  query: string;
  matched: string;
  description: string;
  affinity: string[];
};

const DIRECTIONS = ["LR", "TD", "RL", "BT"] as const;
type Direction = (typeof DIRECTIONS)[number];
type SkillPacksVariant = "standalone" | "embedded";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

async function copyText(value: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    /* fall through to fallback */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = value;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/*  Pack card                                                          */
/* ------------------------------------------------------------------ */

function PackCard({
  compact = false,
  pack,
  onExpandedChange,
}: {
  compact?: boolean;
  pack: MetaSkillSummary;
  onExpandedChange?: (name: string, expanded: boolean) => void;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const [mermaid, setMermaid] = useState<string | null>(null);
  const [direction, setDirection] = useState<Direction>("LR");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const handleToggle = useCallback(() => {
    const next = !expanded;
    setExpanded(next);
    onExpandedChange?.(pack.name, next);
  }, [expanded, onExpandedChange, pack.name]);

  // Lazy-fetch the Mermaid source the first time the user expands a
  // card. Re-fetch if the user changes direction.
  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    setLoading(true);
    setErr(null);
    (async () => {
      try {
        const url = `${getBackendBaseURL()}/api/meta-skills/${encodeURIComponent(pack.name)}/mermaid?direction=${direction}&include_budget=true`;
        const r = await fetch(url);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = (await r.json()) as MetaSkillMermaidResponse;
        if (!cancelled) {
          setMermaid(data.mermaid);
        }
      } catch (e) {
        swallow(e);
        if (!cancelled) {
          setErr(e instanceof Error ? e.message : "unknown error");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [expanded, direction, pack.name]);

  const handleCopy = useCallback(async () => {
    if (!mermaid) return;
    const ok = await copyText(mermaid);
    if (ok) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  }, [mermaid]);

  return (
    <div
      className={cn(
        "transition-colors",
        compact
          ? "group rounded-lg px-3 py-3 hover:bg-muted/35"
          : "rounded-lg border border-border-default bg-card/30 p-4 shadow-[var(--shadow-xs)] hover:border-border",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <div
              className={cn(
                "flex items-center justify-center bg-success/10 text-success",
                compact ? "size-8 rounded-lg" : "size-6 rounded-md",
              )}
            >
              <BoxesIcon className={compact ? "size-4" : "size-3.5"} />
            </div>
            <div
              className={cn("text-sm font-semibold", !compact && "font-mono")}
            >
              {pack.name}
            </div>
            <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground">
              {pack.display_name}
            </span>
            <span className="rounded bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary">
              {t.metaSkills.steps(pack.step_count ?? pack.steps?.length ?? 0)}
            </span>
          </div>
          {pack.description && (
            <p
              className={cn(
                "text-muted-foreground",
                compact
                  ? "line-clamp-1 text-sm leading-5"
                  : "text-xs leading-relaxed",
              )}
            >
              {pack.description}
            </p>
          )}
          {!compact && pack.affinity.length > 0 ? (
            <div className="flex flex-wrap gap-1.5 pt-0.5">
              {pack.affinity.map((tag) => (
                <span
                  key={tag}
                  className="rounded-full bg-info/10 px-2 py-0.5 text-xs font-medium text-info dark:text-info"
                >
                  {tag}
                </span>
              ))}
            </div>
          ) : !compact ? (
            <div className="text-xs text-muted-foreground">
              {t.metaSkills.noAffinity}
            </div>
          ) : null}
          {!compact && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 pt-1 text-xs text-muted-foreground">
              <span>
                <span className="font-medium">{t.metaSkills.budget}:</span>{" "}
                {t.metaSkills.budgetTokens(pack.budget_tokens ?? 0)} ·{" "}
                {t.metaSkills.budgetUsd(pack.budget_usd ?? 0)} ·{" "}
                {t.metaSkills.budgetLatency(pack.budget_latency_ms ?? 0)}
              </span>
            </div>
          )}
        </div>
        <Button
          size="sm"
          variant={compact ? "outline" : "ghost"}
          onClick={handleToggle}
          className={cn(
            "shrink-0 text-xs",
            compact
              ? "h-9 rounded-lg bg-muted/55 px-3 shadow-none hover:bg-muted"
              : "h-7",
          )}
        >
          {expanded ? (
            <>
              <ChevronUpIcon className="mr-1 size-3.5" />
              {compact
                ? t.metaSkills.collapseDiagram
                : t.metaSkills.hideDiagram}
            </>
          ) : (
            <>
              <ChevronDownIcon className="mr-1 size-3.5" />
              {compact ? t.metaSkills.diagramButton : t.metaSkills.viewDiagram}
            </>
          )}
        </Button>
      </div>
      {expanded && (
        <div className="mt-3 space-y-2 border-t border-border-subtle pt-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">
              {t.metaSkills.directionLabel}:
            </span>
            {DIRECTIONS.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDirection(d)}
                className={cn(
                  "rounded border px-2 py-0.5 text-xs font-medium transition-colors",
                  direction === d
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border-default text-muted-foreground hover:border-border hover:text-foreground",
                )}
              >
                {d}
              </button>
            ))}
            <div className="flex-1" />
            <Button
              size="sm"
              variant="ghost"
              className="h-6 text-xs"
              onClick={() => void handleCopy()}
              disabled={!mermaid}
            >
              {copied ? (
                <>
                  <CheckCircle2Icon className="mr-1 size-3" />
                  Copied
                </>
              ) : (
                "Copy"
              )}
            </Button>
          </div>
          <div className="min-h-[80px] rounded-md border border-dashed border-border-default bg-muted/20 p-2">
            {loading && (
              <div className="py-4 text-center text-xs text-muted-foreground">
                {t.metaSkills.diagramLoading}
              </div>
            )}
            {err && !loading && (
              <div className="flex items-center gap-2 py-3 text-xs text-destructive">
                <XCircleIcon className="size-3.5" />
                {t.metaSkills.diagramFailed(err)}
              </div>
            )}
            {!loading && !err && mermaid && <MermaidBlock code={mermaid} />}
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Match box                                                          */
/* ------------------------------------------------------------------ */

function MatchBox({ compact = false }: { compact?: boolean }) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<MetaSkillMatchResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setBusy(true);
    setErr(null);
    setResult(null);
    try {
      const r = await fetch(`${getBackendBaseURL()}/api/meta-skills/match`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q }),
      });
      if (r.status === 404) {
        setErr(t.metaSkills.matchNoResult(q));
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as MetaSkillMatchResponse;
      setResult(data);
    } catch (e) {
      swallow(e);
      setErr(e instanceof Error ? e.message : "unknown error");
    } finally {
      setBusy(false);
    }
  }, [query, t]);

  return (
    <div
      className={cn(
        "space-y-2",
        compact
          ? "rounded-lg border border-border-default bg-background/60 p-3"
          : "workspace-panel rounded-lg p-4",
      )}
    >
      <div
        className={cn(
          "gap-2",
          compact
            ? "flex flex-col md:flex-row md:items-center"
            : "flex flex-col",
        )}
      >
        <div
          className={cn(
            "text-xs font-medium",
            compact &&
              "flex shrink-0 items-center gap-1.5 whitespace-nowrap text-muted-foreground",
          )}
        >
          {compact && <SparklesIcon className="size-3.5 text-success" />}
          {t.metaSkills.matchLabel}
        </div>
        <div className="relative flex-1">
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void submit();
            }}
            placeholder={t.metaSkills.matchPlaceholder}
            className={cn(
              "w-full border border-border-default bg-background/60 py-2 pl-9 pr-3",
              "text-sm placeholder:text-muted-foreground/60 outline-none",
              "focus:border-primary/50 focus:ring-2 focus:ring-primary/10",
              compact ? "h-9 rounded-full" : "rounded-lg",
            )}
          />
        </div>
        <Button
          size="sm"
          variant={compact ? "secondary" : "default"}
          className={cn(
            "h-9 shrink-0",
            compact
              ? "rounded-full px-3"
              : "bg-gradient-to-r from-success to-blue-500 text-white",
          )}
          onClick={() => void submit()}
          disabled={busy || !query.trim()}
        >
          <SparklesIcon className="mr-1 size-3.5" />
          {t.metaSkills.matchButton}
        </Button>
      </div>
      {result && (
        <div className="rounded-lg border border-success/30 bg-success/5 px-3 py-2 text-xs">
          <div className="font-medium text-success">
            {t.metaSkills.matchResult(result.query, result.matched)}
          </div>
          {result.description && (
            <div className="mt-1 text-xs text-muted-foreground">
              {result.description}
            </div>
          )}
          {result.affinity.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {result.affinity.map((a) => (
                <span
                  key={a}
                  className="rounded bg-info/10 px-1.5 py-0.5 text-xs text-info dark:text-info"
                >
                  {a}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
      {err && !result && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {err}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab                                                                */
/* ------------------------------------------------------------------ */

export function SkillPacksTab({
  initialQuery = "",
  variant = "standalone",
}: {
  initialQuery?: string;
  variant?: SkillPacksVariant;
}) {
  const { t } = useI18n();
  const [packs, setPacks] = useState<MetaSkillSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [query, setQuery] = useState(initialQuery);

  useEffect(() => {
    setQuery(initialQuery);
  }, [initialQuery]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${getBackendBaseURL()}/api/meta-skills`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = (await r.json()) as MetaSkillListResponse;
        if (!cancelled) {
          setPacks(data.packs);
          setErr(null);
        }
      } catch (e) {
        swallow(e);
        if (!cancelled) {
          setErr(e instanceof Error ? e.message : "unknown error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const filtered = useMemo(() => {
    if (!packs) return [];
    const q = query.trim().toLowerCase();
    if (!q) return packs;
    return packs.filter((p) => {
      const hay = [
        p.name,
        p.description,
        p.kind,
        p.display_name,
        p.affinity.join(" "),
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [packs, query]);

  const handleRefresh = useCallback(() => {
    setTick((n) => n + 1);
  }, []);

  const embedded = variant === "embedded";

  return (
    <div className="flex flex-col gap-3">
      {/* Header */}
      {embedded ? (
        <div className="flex flex-col gap-3 text-xs text-muted-foreground lg:flex-row lg:items-center lg:justify-between">
          <span className="shrink-0">
            {t.metaSkills.title}
            {" · "}
            {packs
              ? t.metaSkills.count(query ? filtered.length : packs.length)
              : t.metaSkills.loading}
          </span>
          <div className="flex w-full items-center gap-2 lg:w-auto">
            <div className="relative min-w-0 flex-1 lg:w-[360px] lg:flex-none">
              <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t.metaSkills.matchPlaceholder}
                className={cn(
                  "h-10 w-full rounded-lg border border-border-default bg-background py-1.5 pl-9 pr-3 shadow-[var(--shadow-xs)]",
                  "text-xs placeholder:text-muted-foreground/60 outline-none",
                  "focus:border-primary/50 focus:ring-2 focus:ring-primary/10",
                )}
              />
            </div>
            <Button
              size="sm"
              variant="ghost"
              onClick={handleRefresh}
              className="h-10 shrink-0 rounded-full px-3 text-xs"
            >
              <RefreshCwIcon className="mr-1 size-3.5" />
              {t.metaSkills.refresh}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2">
              <div className="flex size-6 items-center justify-center rounded bg-success/10">
                <BoxesIcon className="size-4 text-success" />
              </div>
              <h2 className="text-base font-bold">{t.metaSkills.title}</h2>
              {packs && (
                <span className="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                  {t.metaSkills.count(packs.length)}
                </span>
              )}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {t.metaSkills.subtitle}
            </p>
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={handleRefresh}
            className="h-7 text-xs"
          >
            <RefreshCwIcon className="mr-1 size-3.5" />
            {t.metaSkills.refresh}
          </Button>
        </div>
      )}

      {/* Match box */}
      {!embedded && <MatchBox />}

      {/* Filter bar */}
      {!embedded && (
        <div className="flex items-center justify-between gap-3">
          <div className="relative max-w-md flex-1">
            <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t.metaSkills.matchPlaceholder}
              className={cn(
                "w-full rounded-lg border border-border-default bg-background/60 py-2 pl-9 pr-3",
                "text-sm placeholder:text-muted-foreground/60 outline-none",
                "focus:border-primary/50 focus:ring-2 focus:ring-primary/10",
              )}
            />
          </div>
        </div>
      )}

      {/* Pack list */}
      {err && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {t.metaSkills.loadFailed(err)}
        </div>
      )}

      {!packs && !err && (
        <div className="rounded-lg border border-dashed border-border-default px-6 py-8 text-center text-sm text-muted-foreground">
          {t.metaSkills.loading}
        </div>
      )}

      {packs && packs.length === 0 && !err && (
        <div className="rounded-lg border border-dashed border-border-default px-6 py-10 text-center text-sm text-muted-foreground">
          {t.metaSkills.empty}
        </div>
      )}

      {filtered.length > 0 && (
        <div
          className={cn(
            "grid grid-cols-1",
            embedded
              ? "grid-cols-[repeat(auto-fit,minmax(320px,1fr))] gap-x-12 gap-y-5"
              : "gap-3 xl:grid-cols-2",
          )}
        >
          {filtered.map((p) => (
            <PackCard key={p.name} compact={embedded} pack={p} />
          ))}
        </div>
      )}

      {packs && filtered.length === 0 && query && (
        <div className="rounded-lg border border-dashed border-border-default px-6 py-8 text-center text-sm text-muted-foreground">
          {t.metaSkills.matchNoResult(query)}
        </div>
      )}
    </div>
  );
}
