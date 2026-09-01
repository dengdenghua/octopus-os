import { swallow } from "@/core/utils/log";
import {
  CheckCircle2Icon,
  ClockIcon,
  FileTextIcon,
  HistoryIcon,
  Loader2Icon,
  SearchIcon,
  TelescopeIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { listDeepResearchJobs, type ResearchJob } from "@/core/research/api";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface DeepResearchHistoryPanelProps {
  activeJobId?: string | null;
  onSelect: (job: ResearchJob) => void;
  onClose?: () => void;
}

export function DeepResearchHistoryPanel({
  activeJobId,
  onSelect,
  onClose,
}: DeepResearchHistoryPanelProps) {
  const { t } = useI18n();
  const [jobs, setJobs] = useState<ResearchJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadJobs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await listDeepResearchJobs();
      setJobs(next);
    } catch (err) {
      swallow(err);
      setError(
        err instanceof Error ? err.message : "Failed to load research history",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  const sortedJobs = useMemo(
    () =>
      [...jobs].sort(
        (a, b) =>
          Date.parse(b.completed_at ?? b.created_at) -
          Date.parse(a.completed_at ?? a.created_at),
      ),
    [jobs],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border-default px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <HistoryIcon className="size-4 text-primary" />
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">Agent History</div>
            <div className="truncate text-xs text-muted-foreground">
              {sortedJobs.length} saved runs
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => void loadJobs()}
            disabled={loading}
            className="flex size-7 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground disabled:opacity-45"
            title="Refresh"
          >
            {loading ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <SearchIcon className="size-3.5" />
            )}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label={t.common.close}
            className="rounded-lg p-1 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            <XIcon className="size-3.5" />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {error && (
          <div className="mb-3 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </div>
        )}
        {!loading && sortedJobs.length === 0 ? (
          <div className="rounded-lg border border-border-default bg-muted/20 p-4 text-sm text-muted-foreground">
            No agent runs yet.
          </div>
        ) : (
          <div className="space-y-2">
            {sortedJobs.map((job) => (
              <button
                key={job.job_id}
                type="button"
                onClick={() => onSelect(job)}
                className={cn(
                  "w-full rounded-lg border p-3 text-left transition-colors",
                  activeJobId === job.job_id
                    ? "border-primary/30 bg-primary/10"
                    : "border-border-default bg-background/60 hover:bg-muted/40",
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="line-clamp-2 text-xs font-medium">
                      {job.topic}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                      <span>{job.status}</span>
                      {job.lead_agent_name && (
                        <span>{job.lead_agent_name}</span>
                      )}
                      <span>
                        {formatDate(job.completed_at ?? job.created_at)}
                      </span>
                    </div>
                  </div>
                  {job.final_report ? (
                    <CheckCircle2Icon className="size-4 shrink-0 text-success" />
                  ) : (
                    <TelescopeIcon className="size-4 shrink-0 text-muted-foreground" />
                  )}
                </div>
                <div className="mt-2 grid grid-cols-3 gap-1.5 text-xs text-muted-foreground">
                  <HistoryMetric
                    icon={<FileTextIcon className="size-3" />}
                    value={job.materials.length}
                    label="materials"
                  />
                  <HistoryMetric
                    icon={<SearchIcon className="size-3" />}
                    value={job.evidence.length}
                    label="evidence"
                  />
                  <HistoryMetric
                    icon={<ClockIcon className="size-3" />}
                    value={job.roles.length}
                    label="roles"
                  />
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function HistoryMetric({
  icon,
  value,
  label,
}: {
  icon: React.ReactNode;
  value: number;
  label: string;
}) {
  return (
    <div className="flex items-center gap-1 rounded-md bg-muted/40 px-1.5 py-1">
      {icon}
      <span>{value}</span>
      <span className="truncate">{label}</span>
    </div>
  );
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
