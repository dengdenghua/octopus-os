/**
 * Gene-lock status badge · compact "who's allowed to do what"
 * indicator for governance surfaces in the workspace.
 *
 * Shows:
 *   - Current maturity level (Lv 0..4) with color coding
 *   - Panic active badge when engaged
 *   - Mode: dev / production
 *
 * Click opens a compact dropdown with:
 *   - Level up/down buttons
 *   - Panic trigger (destructive)
 *   - "Clear panic" (only when panic active)
 *
 * Full reference: docs/gene-locks.md
 */

import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { DnaIcon, SirenIcon, ShieldCheckIcon, ShieldIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { cn } from "@/lib/utils";

type LockStatus = {
  schema_version: number;
  maturity_level: number;
  maturity_level_name: string;
  panic: { active: boolean; since: number | null; reason: string };
  mode: string;
  last_mutation_at?: Record<string, number>;
  required_levels?: Record<
    string,
    { autonomous: number; human_signed: number }
  >;
  cooldowns_seconds?: Record<string, number>;
};

export function GeneLockBadge() {
  const { t } = useI18n();
  const g = t.geneLockBadge;
  const [status, setStatus] = useState<LockStatus | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const { confirm, confirmDialog } = useConfirmDialog();

  const reload = useCallback(async () => {
    try {
      const r: LockStatus = await fetch(
        `${getBackendBaseURL()}/api/gene-locks/status`,
      ).then((r) => r.json());
      setStatus(r);
    } catch (e) {
      swallow(e);
      // Silent · the badge is optional. A missing endpoint means
      // older backend · we render nothing.
    }
  }, []);

  useEffect(() => {
    void reload();
    const tid = window.setInterval(() => void reload(), 15000);
    return () => window.clearInterval(tid);
  }, [reload]);

  const changeLevel = useCallback(
    async (delta: number) => {
      if (!status) return;
      const target = Math.max(0, Math.min(4, status.maturity_level + delta));
      if (target === status.maturity_level) return;
      setBusy(true);
      try {
        // In dev mode any change goes through; in prod, up-moves need
        // a human approver. For the UI badge we always pass a
        // synthetic "ui-operator" signature · real deploys should
        // replace this with the logged-in user's ID.
        const r = await fetch(
          `${getBackendBaseURL()}/api/gene-locks/maturity`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Human-Approver": "ui-operator",
            },
            body: JSON.stringify({ level: target }),
          },
        ).then((r) => r.json());
        setMsg(
          r.ok
            ? `${status.maturity_level} → ${target}`
            : `✕ ${r.message ?? r.error}`,
        );
        void reload();
      } finally {
        setBusy(false);
        window.setTimeout(() => setMsg(null), 3500);
      }
    },
    [status, reload],
  );

  const triggerPanic = useCallback(async () => {
    const ok = await confirm({
      title: g.panicButton,
      description: g.panicConfirm,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await fetch(`${getBackendBaseURL()}/api/gene-locks/panic`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "ui-operator" }),
      });
      void reload();
    } finally {
      setBusy(false);
    }
  }, [confirm, g.panicButton, g.panicConfirm, reload]);

  const clearPanic = useCallback(async () => {
    setBusy(true);
    try {
      await fetch(`${getBackendBaseURL()}/api/gene-locks/panic/clear`, {
        method: "POST",
        headers: { "X-Human-Approver": "ui-operator" },
      });
      void reload();
    } finally {
      setBusy(false);
    }
  }, [reload]);

  if (!status) return null;
  const lvl = status.maturity_level;
  const panic = status.panic.active;
  const levelColor = panic
    ? "bg-destructive/20 text-destructive border-destructive/40"
    : lvl === 0
      ? "bg-muted-foreground/20 text-muted-foreground border-muted-foreground/40"
      : lvl <= 2
        ? "bg-warning/20 text-warning border-warning/40"
        : "bg-success/20 text-success border-success/40";

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={cn(
          "inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded-md border px-2 text-xs font-medium transition-colors",
          levelColor,
        )}
        title={g.badgeTitle}
        aria-expanded={expanded}
        aria-controls="gene-lock-details"
      >
        {panic ? (
          <SirenIcon className="size-3 animate-pulse" />
        ) : lvl >= 3 ? (
          <ShieldCheckIcon className="size-3" />
        ) : (
          <DnaIcon className="size-3" />
        )}
        <span>{g.badgeLabel}</span>
        <span className="tabular-nums">Lv {lvl}</span>
        <span className="text-xs opacity-75">
          {panic ? g.panicBadge : (g.levelNames[lvl] ?? "?")}
        </span>
        {status.mode === "production" && (
          <Badge className="ml-1 h-4 bg-foreground/40 px-1 text-xs uppercase tracking-wider text-muted-foreground">
            {g.productionBadge}
          </Badge>
        )}
      </button>

      {expanded && (
        <div
          id="gene-lock-details"
          className="absolute right-0 top-full z-30 mt-1 w-72 rounded-lg border border-border-default bg-background/95 p-3 text-xs shadow-xl backdrop-blur"
        >
          <div className="mb-2 flex items-center gap-2 font-medium">
            <ShieldIcon className="size-3.5" />
            {g.dropdownTitle}
          </div>
          <div className="space-y-1 text-muted-foreground">
            <div>
              {g.modeLabel}:{" "}
              <span className="text-foreground">
                {status.mode === "production" ? g.modeStrict : g.modeRelaxed}
              </span>
            </div>
            <div>
              {g.maturityLabel}:{" "}
              <span className="text-foreground">
                Lv {lvl} · {g.levelNames[lvl]}
              </span>
            </div>
            {panic && (
              <div className="rounded bg-destructive/10 px-2 py-1 text-destructive">
                <div className="font-medium">{g.panicActive}</div>
                <div className="text-xs">
                  {g.panicStartedAt}{" "}
                  {status.panic.since
                    ? new Date(status.panic.since * 1000).toLocaleString()
                    : "?"}
                </div>
                <div className="text-xs">
                  {g.panicReason}: {status.panic.reason}
                </div>
              </div>
            )}
          </div>

          <div className="mt-3 flex items-center gap-1">
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => changeLevel(-1)}
              disabled={busy || lvl === 0 || panic}
            >
              ← Lv {Math.max(0, lvl - 1)}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => changeLevel(+1)}
              disabled={busy || lvl === 4 || panic}
            >
              Lv {Math.min(4, lvl + 1)} →
            </Button>
            <div className="flex-1" />
            {panic ? (
              <Button
                size="sm"
                className="h-7 bg-success text-xs hover:bg-success"
                onClick={clearPanic}
                disabled={busy}
              >
                <ShieldCheckIcon className="mr-1 size-3" />
                {g.unlockButton}
              </Button>
            ) : (
              <Button
                size="sm"
                variant="destructive"
                className="h-7 text-xs"
                onClick={() => void triggerPanic()}
                disabled={busy}
              >
                <SirenIcon className="mr-1 size-3" />
                {g.panicButton}
              </Button>
            )}
          </div>
          {msg && (
            <div className="mt-2 text-xs text-muted-foreground">{msg}</div>
          )}
          <div className="mt-2 border-t border-border-subtle pt-2 text-xs text-muted-foreground">
            {g.levelSummary(
              0,
              g.levelNames[0] ?? "",
              g.levelDescriptions[0] ?? "",
            )}
            <br />
            {g.levelSummary(
              1,
              g.levelNames[1] ?? "",
              g.levelDescriptions[1] ?? "",
            )}
            <br />
            {g.levelSummary(
              2,
              g.levelNames[2] ?? "",
              g.levelDescriptions[2] ?? "",
            )}
            <br />
            {g.levelSummary(
              3,
              g.levelNames[3] ?? "",
              g.levelDescriptions[3] ?? "",
            )}
            <br />
            {g.levelSummary(
              4,
              g.levelNames[4] ?? "",
              g.levelDescriptions[4] ?? "",
            )}
          </div>
        </div>
      )}
      {confirmDialog}
    </div>
  );
}

export function GeneLockControlCard({
  compact = false,
  className,
}: {
  compact?: boolean;
  className?: string;
} = {}) {
  const { t } = useI18n();
  const g = t.geneLockBadge;
  const [status, setStatus] = useState<LockStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const { confirm, confirmDialog } = useConfirmDialog();

  const reload = useCallback(async () => {
    try {
      const r: LockStatus = await fetch(
        `${getBackendBaseURL()}/api/gene-locks/status`,
      ).then((r) => r.json());
      setStatus(r);
    } catch (e) {
      swallow(e);
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    void reload();
    const tid = window.setInterval(() => void reload(), 15000);
    return () => window.clearInterval(tid);
  }, [reload]);

  const run = useCallback(
    async (key: string, action: () => Promise<Response>) => {
      setBusy(key);
      try {
        const r = await action();
        const body = await r.json().catch(() => ({}));
        if (body?.ok === false) {
          setMsg(body.message ?? body.error ?? g.operationFailed);
        } else {
          setMsg(g.updateSuccess);
        }
        await reload();
      } catch (error) {
        swallow(error);
        setMsg(error instanceof Error ? error.message : g.operationFailed);
      } finally {
        setBusy(null);
        window.setTimeout(() => setMsg(null), 3500);
      }
    },
    [reload, g.operationFailed, g.updateSuccess],
  );

  const setMode = useCallback(
    (mode: "dev" | "production") =>
      run(`mode:${mode}`, () =>
        fetch(`${getBackendBaseURL()}/api/gene-locks/mode`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Human-Approver": "ui-operator",
          },
          body: JSON.stringify({ mode }),
        }),
      ),
    [run],
  );

  const setLevel = useCallback(
    (level: number) =>
      run(`level:${level}`, () =>
        fetch(`${getBackendBaseURL()}/api/gene-locks/maturity`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Human-Approver": "ui-operator",
          },
          body: JSON.stringify({ level }),
        }),
      ),
    [run],
  );

  const setPanic = useCallback(
    async (enabled: boolean) => {
      if (enabled) {
        const ok = await confirm({
          title: g.panicButton,
          description: g.panicConfirm,
        });
        if (!ok) return;
      }
      void run(enabled ? "panic:on" : "panic:off", () =>
        fetch(
          `${getBackendBaseURL()}/api/gene-locks/panic${enabled ? "" : "/clear"}`,
          {
            method: "POST",
            headers: enabled
              ? { "Content-Type": "application/json" }
              : { "X-Human-Approver": "ui-operator" },
            body: enabled
              ? JSON.stringify({ reason: "ui-operator" })
              : undefined,
          },
        ),
      );
    },
    [confirm, g.panicButton, g.panicConfirm, run],
  );

  if (!status) return null;

  const lvl = Math.max(0, Math.min(4, status.maturity_level));
  const panic = status.panic.active;
  const strict = status.mode === "production";

  if (compact) {
    return (
      <>
        <div
          aria-busy={busy !== null}
          className={cn(
            "rounded-lg border border-border-default bg-background/75 px-3 py-2 shadow-[var(--shadow-xs)]",
            className,
          )}
        >
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex min-w-0 items-center gap-1.5">
            <DnaIcon className="size-3.5 shrink-0 text-primary" />
            <span className="text-xs font-semibold">{g.compactTitle}</span>
            <Badge
              variant={panic ? "destructive" : "outline"}
              className="h-5 rounded-md px-1.5 text-xs"
            >
              {panic ? g.panicBadge : `Lv ${lvl} · ${g.levelNames[lvl]}`}
            </Badge>
          </div>

          <span className="hidden h-4 w-px bg-border/70 sm:block" />

          <div
            className="flex items-center rounded-md bg-muted/45 p-0.5"
            role="group"
            aria-label={g.openModeLabel}
          >
            {[
              ["dev", g.modeRelaxed],
              ["production", g.modeStrict],
            ].map(([mode, label]) => {
              const selected =
                (mode === "production" && strict) ||
                (mode === "dev" && !strict);
              return (
                <button
                  key={mode}
                  type="button"
                  disabled={busy !== null}
                  onClick={() => setMode(mode as "dev" | "production")}
                  aria-pressed={selected}
                  className={cn(
                    "h-6 rounded-md px-2 text-xs transition-colors",
                    selected
                      ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                      : "text-muted-foreground hover:text-foreground",
                    "disabled:cursor-not-allowed disabled:opacity-45",
                  )}
                >
                  {label}
                </button>
              );
            })}
          </div>

          <div
            className="flex items-center rounded-md bg-muted/45 p-0.5"
            role="group"
            aria-label={g.levelLabel}
          >
            {g.levelNames.map((name, index) => (
              <button
                key={name}
                type="button"
                disabled={busy !== null || panic}
                onClick={() => setLevel(index)}
                aria-pressed={index === lvl}
                title={`${name} · ${g.levelDescriptions[index]}`}
                className={cn(
                  "h-6 rounded-md px-2 font-mono text-xs transition-colors",
                  index === lvl
                    ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                    : "text-muted-foreground hover:text-foreground",
                  "disabled:cursor-not-allowed disabled:opacity-45",
                )}
              >
                Lv{index}
              </button>
            ))}
          </div>

          <Button
            type="button"
            size="sm"
            variant={panic ? "secondary" : "outline"}
            className="h-7 px-2 text-xs"
            disabled={busy !== null}
            onClick={() => void setPanic(!panic)}
            aria-pressed={panic}
          >
            {panic ? (
              <>
                <ShieldCheckIcon className="mr-1 size-3" />
                {g.unlockButton}
              </>
            ) : (
              <>
                <SirenIcon className="mr-1 size-3" />
                {g.panicButton}
              </>
            )}
          </Button>

          {msg ? (
            <span
              className="text-xs text-muted-foreground"
              role="status"
              aria-live="polite"
            >
              {msg}
            </span>
          ) : null}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs leading-5 text-muted-foreground">
          <span>{strict ? g.strictHint : g.relaxedHint}</span>
          <span>{g.levelDescriptions[lvl]}</span>
          {panic ? (
            <span className="text-destructive">{g.evolutionPaused}</span>
          ) : null}
        </div>
        </div>
        {confirmDialog}
      </>
    );
  }

  return (
    <>
      <section
        className="workspace-panel border border-border-default px-4 py-4"
        aria-busy={busy !== null}
      >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <DnaIcon className="size-4" />
            </div>
            <div>
              <h2 className="text-sm font-semibold">{g.settingsTitle}</h2>
              <p className="text-xs text-muted-foreground">
                {g.settingsDescription}
              </p>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={panic ? "destructive" : "outline"} className="h-7">
            {panic ? g.panicActive : `Lv ${lvl} · ${g.levelNames[lvl]}`}
          </Badge>
          {msg ? (
            <span
              className="text-xs text-muted-foreground"
              role="status"
              aria-live="polite"
            >
              {msg}
            </span>
          ) : null}
        </div>
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_1.4fr_1fr]">
        <div className="rounded-lg border border-border-default bg-muted/20 p-3">
          <div className="text-xs font-medium">{g.openModeLabel}</div>
          <div
            className="mt-2 grid grid-cols-2 gap-1"
            role="group"
            aria-label={g.openModeLabel}
          >
            <Button
              type="button"
              size="sm"
              variant={!strict ? "secondary" : "outline"}
              className="h-8 text-xs"
              disabled={busy !== null}
              onClick={() => setMode("dev")}
              aria-pressed={!strict}
            >
              {g.modeRelaxed}
            </Button>
            <Button
              type="button"
              size="sm"
              variant={strict ? "secondary" : "outline"}
              className="h-8 text-xs"
              disabled={busy !== null}
              onClick={() => setMode("production")}
              aria-pressed={strict}
            >
              {g.modeStrict}
            </Button>
          </div>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">
            {g.modeDescription}
          </p>
        </div>

        <div className="rounded-lg border border-border-default bg-muted/20 p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-xs font-medium">{g.levelLabel}</div>
            <span className="text-xs text-muted-foreground">
              {g.levelDescriptions[lvl]}
            </span>
          </div>
          <div
            className="mt-2 grid grid-cols-5 gap-1"
            role="group"
            aria-label={g.levelLabel}
          >
            {g.levelNames.map((name, index) => (
              <Button
                key={name}
                type="button"
                size="sm"
                variant={index === lvl ? "secondary" : "outline"}
                className="h-auto min-h-10 flex-col gap-0.5 px-1 py-1 text-xs"
                disabled={busy !== null || panic}
                onClick={() => setLevel(index)}
                aria-pressed={index === lvl}
                title={g.levelDescriptions[index]}
              >
                <span className="font-mono">Lv {index}</span>
                <span className="truncate">{name}</span>
              </Button>
            ))}
          </div>
        </div>

        <div className="rounded-lg border border-border-default bg-muted/20 p-3">
          <div className="text-xs font-medium">{g.masterSwitchLabel}</div>
          <Button
            type="button"
            size="sm"
            variant={panic ? "secondary" : "destructive"}
            className="mt-2 h-8 w-full text-xs"
            disabled={busy !== null}
            onClick={() => void setPanic(!panic)}
            aria-pressed={panic}
          >
            {panic ? (
              <>
                <ShieldCheckIcon className="mr-1 size-3.5" />
                {g.unlockButton}
              </>
            ) : (
              <>
                <SirenIcon className="mr-1 size-3.5" />
                {g.disableEvolutionButton}
              </>
            )}
          </Button>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">
            {g.disabledHint}
          </p>
        </div>
      </div>
      </section>
      {confirmDialog}
    </>
  );
}
