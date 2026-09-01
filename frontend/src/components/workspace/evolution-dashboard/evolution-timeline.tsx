import React from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  GitBranchIcon,
  ArrowUpIcon,
  ArrowDownIcon,
  ShieldIcon,
  AlertTriangleIcon,
  RotateCcwIcon,
} from "lucide-react";
import { toast } from "sonner";

import {
  useLedger,
  useCanary,
  useRollbackCanary,
} from "@/core/evolution/hooks";
import { cn } from "@/lib/utils";
import type { LedgerRecord } from "@/core/evolution/api";

const STATUS_DOT_COLOR: Record<string, string> = {
  applied: "bg-success",
  pending: "bg-warning",
  rolled_back: "bg-destructive",
  rejected: "bg-muted-foreground",
};

const CANARY_PHASE_STYLE: Record<string, string> = {
  shadow: "bg-info/15 text-info dark:text-info border-info/30",
  canary_5:
    "bg-warning/15 text-warning border-warning/30",
  canary_25:
    "bg-warning/15 text-warning border-warning/30",
  canary_50:
    "bg-warning/15 text-warning border-warning/30",
  full: "bg-success/15 text-success border-success/30",
  rolled_back: "bg-destructive/15 text-destructive border-destructive/30",
};

function canaryPhaseStyle(phase: string): string {
  return (
    CANARY_PHASE_STYLE[phase] ??
    "bg-muted text-muted-foreground border-border"
  );
}

function numberOrZero(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function fixed(value: unknown, digits: number): string {
  return numberOrZero(value).toFixed(digits);
}

function CanaryStatusBadge({ phase }: { phase: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs font-medium",
        canaryPhaseStyle(phase),
      )}
    >
      <ShieldIcon className="size-3" />
      {phase}
    </span>
  );
}

function FitnessDiffCard({ before, after }: { before: number; after: number }) {
  const safeBefore = numberOrZero(before);
  const safeAfter = numberOrZero(after);
  const improved = safeAfter >= safeBefore;
  const maxVal = Math.max(safeBefore, safeAfter, 0.01);

  return (
    <div className="flex items-center gap-3 text-xs">
      <div className="flex-1 space-y-1">
        <div className="flex items-center gap-1.5">
          <span className="text-muted-foreground w-9 shrink-0">Before</span>
          <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full rounded-full bg-muted-foreground"
              style={{ width: `${Math.max((safeBefore / maxVal) * 100, 2)}%` }}
            />
          </div>
          <span className="tabular-nums w-10 text-right shrink-0">
            {fixed(safeBefore, 2)}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-muted-foreground w-9 shrink-0">After</span>
          <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
            <div
              className={cn(
                "h-full rounded-full",
                improved ? "bg-success" : "bg-destructive",
              )}
              style={{ width: `${Math.max((safeAfter / maxVal) * 100, 2)}%` }}
            />
          </div>
          <span className="tabular-nums w-10 text-right shrink-0">
            {fixed(safeAfter, 2)}
          </span>
        </div>
      </div>
    </div>
  );
}

function TimelineNode({
  record,
  canaryPhase,
  onRollback,
  isRollingBack,
}: {
  record: LedgerRecord;
  canaryPhase?: string;
  onRollback?: () => void;
  isRollingBack?: boolean;
}) {
  const dotColor =
    STATUS_DOT_COLOR[record.status] ?? "bg-muted-foreground";
  const isCanaryKind =
    record.kind.toLowerCase().includes("canary") ||
    record.kind.toLowerCase().includes("skill");
  const hasFitness =
    record.fitness_before != null && record.fitness_after != null;
  const delta = hasFitness
    ? record.fitness_after! - record.fitness_before!
    : null;
  const improved = delta != null ? delta >= 0 : null;

  return (
    <div className="relative flex gap-3 pb-6 last:pb-0">
      <div className="flex flex-col items-center">
        <div
          className={cn(
            "size-3 rounded-full shrink-0 ring-2 ring-background",
            dotColor,
          )}
        />
        <div className="flex-1 w-px bg-border/60 mt-1" />
      </div>
      <div className="flex-1 min-w-0">
        <div
          className={cn(
            "rounded-md border border-border-subtle bg-muted/30 px-3 py-2",
            "hover:border-border-strong transition-colors",
          )}
        >
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className="inline-flex items-center gap-1 rounded-md bg-foreground/5 px-1.5 py-0.5 text-xs font-medium">
              <GitBranchIcon className="size-3" />
              {record.kind}
            </span>
            {canaryPhase && <CanaryStatusBadge phase={canaryPhase} />}
            {record.status === "rolled_back" && (
              <AlertTriangleIcon className="size-3 text-destructive" />
            )}
            <span className="ml-auto text-xs text-muted-foreground tabular-nums">
              {new Date(record.ts).toLocaleString()}
            </span>
          </div>
          <p className="text-xs leading-relaxed break-words">
            {record.description}
          </p>
          {hasFitness && (
            <div className="mt-2">
              <FitnessDiffCard
                before={record.fitness_before!}
                after={record.fitness_after!}
              />
            </div>
          )}
          {record.status === "applied" && isCanaryKind && onRollback && (
            <div className="mt-2 flex justify-end">
              <button
                type="button"
                onClick={onRollback}
                disabled={isRollingBack}
                className={cn(
                  "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs",
                  "text-muted-foreground hover:bg-destructive/10 hover:text-destructive",
                  "disabled:opacity-50 disabled:cursor-not-allowed transition-colors",
                )}
              >
                <RotateCcwIcon
                  className={cn("size-3", isRollingBack && "animate-spin")}
                />
                Rollback
              </button>
            </div>
          )}
        </div>
      </div>
      {delta != null && (
        <div
          className={cn(
            "shrink-0 flex items-center gap-0.5 text-xs font-medium tabular-nums pt-2",
            improved
              ? "text-success"
              : "text-destructive",
          )}
        >
          {improved ? (
            <ArrowUpIcon className="size-3" />
          ) : (
            <ArrowDownIcon className="size-3" />
          )}
          {improved ? "+" : ""}
          {fixed(Math.abs(delta), 3)}
        </div>
      )}
    </div>
  );
}

export function EvolutionTimeline() {
  const qc = useQueryClient();
  const ledgerQuery = useLedger();
  const canaryQuery = useCanary();
  const rollbackMutation = useRollbackCanary();

  const records = ledgerQuery.data?.records ?? [];
  const canaries = canaryQuery.data?.canaries ?? [];
  const sorted = [...records].sort(
    (a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime(),
  );

  const canaryPhaseMap = new Map<string, string>();
  for (const c of canaries) {
    canaryPhaseMap.set(c.skill_name, c.phase);
  }

  const findCanarySkill = (record: LedgerRecord): string | undefined => {
    for (const skillName of canaryPhaseMap.keys()) {
      if (
        record.description.toLowerCase().includes(skillName.toLowerCase()) ||
        record.kind.toLowerCase().includes(skillName.toLowerCase())
      ) {
        return skillName;
      }
    }
    return undefined;
  };

  const handleRollback = (skillName: string) => {
    rollbackMutation.mutate(skillName, {
      onSuccess: () => {
        toast.success("Canary rolled back successfully");
        void qc.invalidateQueries({ queryKey: ["evolution"] });
      },
      onError: (e: Error) => {
        toast.error(`Rollback failed: ${e.message}`);
      },
    });
  };

  if (ledgerQuery.isLoading) {
    return (
      <div className="flex items-center justify-center py-8 text-muted-foreground text-xs">
        <RotateCcwIcon className="size-4 mr-2 animate-spin" />
        Loading timeline...
      </div>
    );
  }

  if (sorted.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <GitBranchIcon className="size-8 mb-2 opacity-40" />
        <p className="text-xs">No evolution events recorded yet</p>
      </div>
    );
  }

  return (
    <div>
      {sorted.map((record) => {
        const canarySkill = findCanarySkill(record);
        const canaryPhase = canarySkill
          ? canaryPhaseMap.get(canarySkill)
          : undefined;
        return (
          <TimelineNode
            key={record.id}
            record={record}
            canaryPhase={canaryPhase}
            onRollback={
              record.status === "applied" && canarySkill
                ? () => handleRollback(canarySkill)
                : undefined
            }
            isRollingBack={rollbackMutation.isPending}
          />
        );
      })}
    </div>
  );
}
