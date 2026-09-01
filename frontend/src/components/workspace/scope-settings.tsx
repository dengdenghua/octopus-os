/**
 * Extra-workspaces authorization panel for project-write threads.
 *
 * Background
 * ----------
 *
 * Every turn runs inside a *write scope* resolved from the thread's
 * mode tier — see `runtime/platform/scope.py`:
 *
 *     chat  → own agent workspace only
 *     team  → + team workspace
 *     code  → + user-authorized `extra_workspaces`
 *
 * Code mode is available to every agent by default · the per-agent
 * ``code_mode_unlock`` gate was removed. Tool/permission scoping lives in
 * the skills & permissions system. Write reach is still bounded to the
 * agent's own workspace plus the ``extra_workspaces`` authorized here.
 *
 * The panel is purely UI for the third tier: it reads and writes
 * `thread.metadata.extra_workspaces` through the existing
 * ``POST /api/threads/:id/state`` endpoint. No new backend surface.
 *
 * UX contract
 * -----------
 *   • User types an absolute path, clicks "Add" → appended to the
 *     list, immediately persisted.
 *   • User clicks the × next to an entry → removed, immediately
 *     persisted.
 *   • Errors (non-absolute path, 4xx from server) shown inline; the
 *     list stays in its previous state so a failed save doesn't
 *     desync client from server.
 */
import { useCallback, useEffect, useState } from "react";
import { FolderIcon, PlusIcon, ShieldIcon, XIcon } from "lucide-react";
import { swallow } from "@/core/utils/log";
import { useI18n } from "@/core/i18n/hooks";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { apiClient } from "@/core/api/api-client";
import { cn } from "@/lib/utils";

interface ScopeSettingsProps {
  threadId: string;
  /** Whether code mode is available. Always true now (the per-agent
   *  unlock gate was removed); kept as a prop so the panel can still
   *  render a read-only notice if a future gate disables it. */
  codeModeEnabled: boolean;
}

export function ScopeSettings({
  threadId,
  codeModeEnabled,
}: ScopeSettingsProps) {
  const [paths, setPaths] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const { t } = useI18n();

  // Load current list from the thread state.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        const data = await apiClient.threads.getState(threadId);
        if (cancelled) return;
        const raw = (data?.metadata as { extra_workspaces?: unknown })
          ?.extra_workspaces;
        setPaths(
          Array.isArray(raw)
            ? raw.filter((x): x is string => typeof x === "string" && !!x)
            : [],
        );
      } catch (e) {
        swallow(e);
        // Empty state is valid — don't spam errors on a fresh thread.
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [threadId]);

  // Persist a new list to the thread metadata. We send the full array
  // (not a diff) because the backend `update_state` merges metadata
  // key-by-key, and overwriting the whole `extra_workspaces` entry is
  // the only way to delete a path cleanly.
  const persist = useCallback(
    async (next: string[]) => {
      try {
        await apiClient.threads.updateState(threadId, {
          metadata: { extra_workspaces: next },
        });
        setPaths(next);
        setError(null);
      } catch (e) {
        swallow(e);
        setError(e instanceof Error ? e.message : "Failed to save");
      }
    },
    [threadId],
  );

  const onAdd = useCallback(() => {
    const v = input.trim();
    if (!v) return;
    // Absolute-path check mirrors `scope.py::_extra_workspaces_from_metadata`
    // which drops non-absolute entries. Reject client-side too so the
    // user gets immediate feedback instead of a silent no-op server-side.
    const isAbs =
      v.startsWith("/") ||
      /^[A-Za-z]:[\\/]/.test(v) || // Windows drive-letter
      v.startsWith("\\\\"); // UNC
    if (!isAbs) {
      setError("Path must be absolute (e.g. /home/me/proj or C:\\proj)");
      return;
    }
    if (paths.includes(v)) {
      setInput("");
      return;
    }
    persist([...paths, v]);
    setInput("");
  }, [input, paths, persist]);

  const onRemove = useCallback(
    (p: string) => {
      persist(paths.filter((x) => x !== p));
    },
    [paths, persist],
  );

  if (!codeModeEnabled) {
    return (
      <div className="rounded-lg border border-dashed border-border-default bg-muted/20 px-4 py-3 text-xs text-muted-foreground">
        {t.scopeSettings.codeModeDisabled}
        {/* team workspace also allowed when team_id set — we don't
            surface that here because it's driven by the team picker
            elsewhere, not by this panel */}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border-default bg-background/40 px-4 py-3">
      <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <FolderIcon className="size-3.5" />
        {t.scopeSettings.authorizeWorkspaces}
      </div>

      {loading ? (
        <div className="text-xs text-muted-foreground/60">Loading...</div>
      ) : paths.length === 0 ? (
        <div className="text-xs text-muted-foreground/60">
          {t.scopeSettings.noAuthorized}
        </div>
      ) : (
        <ul className="flex flex-col gap-1">
          {paths.map((p) => (
            <li
              key={p}
              className="group flex items-center gap-2 rounded-md bg-muted/30 px-2 py-1 text-xs font-mono"
            >
              <span className="min-w-0 flex-1 truncate">{p}</span>
              <button
                type="button"
                aria-label={`Remove ${p}`}
                onClick={() => onRemove(p)}
                className="shrink-0 rounded p-0.5 text-muted-foreground/60 opacity-0 transition group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 hover:bg-muted hover:text-foreground"
              >
                <XIcon className="size-3" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-2">
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="/absolute/path or C:\\path"
          className="h-8 text-xs font-mono"
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              onAdd();
            }
          }}
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 shrink-0 gap-1 text-xs"
          onClick={onAdd}
        >
          <PlusIcon className="size-3" />
          Add
        </Button>
      </div>

      {error && <div className="text-xs text-destructive">{error}</div>}
    </div>
  );
}

/**
 * Drop-in button + dialog for the code page header. Code mode is
 * available to every agent now; this wrapper keeps the former prop
 * boundary in one place in case a future deployment introduces another
 * gate.
 */
export function ScopeSettingsButton({
  threadId,
  className,
}: {
  threadId: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const { t } = useI18n();
  // Code mode is available to every agent by default · tool/permission
  // scoping is configured in the skills & permissions system.
  const codeModeEnabled = true;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <button
          type="button"
          title={
            codeModeEnabled
              ? t.scopeSettings.writeScopeTooltip
              : t.scopeSettings.codeModeDisabled
          }
          className={cn(
            "text-muted-foreground hover:text-foreground flex size-7 items-center justify-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
            className,
          )}
        >
          <ShieldIcon className="size-4" />
        </button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t.scopeSettings.writeScopeTitle}</DialogTitle>
          <DialogDescription>
            {t.scopeSettings.writeScopeDescription}
          </DialogDescription>
        </DialogHeader>
        <ScopeSettings threadId={threadId} codeModeEnabled={codeModeEnabled} />
      </DialogContent>
    </Dialog>
  );
}
