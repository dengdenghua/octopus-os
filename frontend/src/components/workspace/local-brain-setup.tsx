import { useCallback, useEffect, useState } from "react";
import {
  AlertCircleIcon,
  BrainIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  Loader2Icon,
  RefreshCwIcon,
  XIcon,
} from "lucide-react";

import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

// One plain-language readiness item from GET /api/local-brain/status. All copy
// (label / what / detail / action) is authored on the backend in plain Chinese,
// so this panel just renders it — no i18n keys to drift.
interface BrainItem {
  id: string;
  label: string;
  what: string;
  ok: boolean;
  detail: string;
  action: string;
}

interface BrainStatus {
  ready: boolean;
  core_ready: boolean;
  summary: string;
  ollama_url: string;
  items: BrainItem[];
}

const DISMISS_KEY = "echo.localBrain.dismissed";

export default function LocalBrainSetup() {
  const { t } = useI18n();
  const [status, setStatus] = useState<BrainStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [dismissed, setDismissed] = useState(() => {
    try {
      return (
        typeof window !== "undefined" &&
        window.localStorage.getItem(DISMISS_KEY) === "1"
      );
    } catch {
      return false;
    }
  });

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${getBackendBaseURL()}/api/local-brain/status`, {
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setStatus((await res.json()) as BrainStatus);
    } catch (e) {
      setError(e instanceof Error ? e.message : t.localBrain.requestFailed);
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (dismissed) return null;

  const pending = status ? status.items.filter((i) => !i.ok).length : 0;
  const ready = status?.ready ?? false;

  return (
    <div className="mb-2 overflow-hidden rounded-lg border border-border-default bg-card">
      {/* Collapsed by default: a single quiet row, never shoves the page. */}
      <div className="flex items-center gap-2 px-3 py-1.5">
        <BrainIcon className="size-4 shrink-0 text-muted-foreground" />
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <span className="shrink-0 text-xs font-medium">
            {t.localBrain.title}
          </span>
          {status && (
            <span
              className={cn(
                "shrink-0 rounded px-1.5 py-0.5 text-xs",
                ready
                  ? "bg-success/5 text-success"
                  : "bg-warning/5 text-warning",
              )}
            >
              {ready ? t.localBrain.ready : t.localBrain.pending(pending)}
            </span>
          )}
          <span className="truncate text-xs text-muted-foreground">
            {status?.summary ?? (loading ? t.localBrain.checking : "")}
          </span>
          {expanded ? (
            <ChevronDownIcon className="ml-auto size-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRightIcon className="ml-auto size-3.5 shrink-0 text-muted-foreground" />
          )}
        </button>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          title={t.localBrain.refresh}
          aria-label={t.localBrain.refresh}
          className="shrink-0 rounded p-1 text-muted-foreground hover:bg-muted/60"
        >
          {loading ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <RefreshCwIcon className="size-3.5" />
          )}
        </button>
        <button
          type="button"
          onClick={() => {
            setDismissed(true);
            try {
              window.localStorage.setItem(DISMISS_KEY, "1");
            } catch {
              /* ignore */
            }
          }}
          title={t.localBrain.dismiss}
          aria-label={t.localBrain.dismiss}
          className="shrink-0 rounded p-1 text-muted-foreground hover:bg-muted/60"
        >
          <XIcon className="size-3.5" />
        </button>
      </div>

      {expanded && (
        <div className="space-y-1.5 border-t border-border-subtle px-3 py-2">
          {error && (
            <p className="text-xs text-destructive">
              {t.localBrain.checkFailed(error)}
            </p>
          )}
          {status?.items.map((it, i) => (
            <div key={it.id} className="flex items-start gap-2.5">
              <span className="mt-0.5 shrink-0">
                {it.ok ? (
                  <CheckCircle2Icon className="size-3.5 text-success" />
                ) : (
                  <AlertCircleIcon className="size-3.5 text-warning" />
                )}
              </span>
              <div className="min-w-0 flex-1">
                <span className="text-xs font-medium">
                  {i + 1}. {it.label}
                </span>
                <span className="ml-1.5 text-xs text-muted-foreground">
                  · {it.what}
                </span>
                <div className="text-xs text-foreground/60">
                  {t.localBrain.currentState(it.detail)}
                </div>
                {!it.ok && it.action && (
                  <div className="text-xs leading-snug text-info">
                    {t.localBrain.nextStep(it.action)}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
