import { useState, useEffect, useCallback } from "react";
import {
  BarChart3Icon,
  TrendingDownIcon,
  Loader2Icon,
  RefreshCwIcon,
} from "lucide-react";
import { swallow } from "@/core/utils/log";
import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface SkillStats {
  skill_name: string;
  total_uses: number;
  success_count: number;
  failure_count: number;
  success_rate: number;
  avg_duration_ms: number;
  last_used: string;
}

export function SkillPerformance({ className }: { className?: string }) {
  const { t } = useI18n();
  const [stats, setStats] = useState<SkillStats[]>([]);
  const [declining, setDeclining] = useState<SkillStats[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [statsRes, decliningRes] = await Promise.all([
        fetch(`${getBackendBaseURL()}/api/skills/performance`, {
          headers: authHeaders(),
        }),
        fetch(`${getBackendBaseURL()}/api/skills/declining`, {
          headers: authHeaders(),
        }),
      ]);
      if (statsRes.ok) setStats(await statsRes.json());
      if (decliningRes.ok) setDeclining(await decliningRes.json());
    } catch (e) {
      swallow(e);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className={cn("space-y-4", className)}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <BarChart3Icon className="text-primary size-4" />
          <span className="text-sm font-semibold">
            {t.evolutionDashboard.skillPerformance}
          </span>
        </div>
        <button
          onClick={() => void refresh()}
          className="text-muted-foreground hover:text-foreground"
        >
          {loading ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <RefreshCwIcon className="size-3.5" />
          )}
        </button>
      </div>

      {/* Declining Skills Warning */}
      {declining.length > 0 && (
        <div className="rounded-lg border border-warning/30 bg-warning/5 p-3">
          <div className="flex items-center gap-1.5 text-warning">
            <TrendingDownIcon className="size-3.5" />
            <span className="text-xs font-medium">
              {t.evolutionDashboard.declining} ({declining.length})
            </span>
          </div>
          <div className="mt-1.5 space-y-1">
            {declining.map((s) => (
              <div
                key={s.skill_name}
                className="flex items-center justify-between text-xs"
              >
                <span className="font-mono text-warning">
                  {s.skill_name}
                </span>
                <span className="text-warning">
                  {(s.success_rate * 100).toFixed(0)}% success
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stats Table */}
      {stats.length === 0 ? (
        <div className="text-muted-foreground py-8 text-center text-xs">
          {t.evolutionDashboard.noSkillData}
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b bg-muted/30">
                <th className="px-3 py-2 text-left font-medium">
                  {t.evolutionDashboard.skillName}
                </th>
                <th className="px-3 py-2 text-right font-medium">
                  {t.evolutionDashboard.usageCount}
                </th>
                <th className="px-3 py-2 text-right font-medium">
                  {t.evolutionDashboard.successRate}
                </th>
                <th className="px-3 py-2 text-right font-medium">
                  {t.evolutionDashboard.avgDuration}
                </th>
              </tr>
            </thead>
            <tbody>
              {stats.map((s) => (
                <tr key={s.skill_name} className="border-b last:border-0">
                  <td className="px-3 py-2 font-mono">{s.skill_name}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {s.total_uses}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <span
                      className={cn(
                        "rounded px-1.5 py-0.5 tabular-nums",
                        s.success_rate >= 0.8
                          ? "bg-success/10 text-success dark:bg-success/30 dark:text-success"
                          : s.success_rate >= 0.5
                            ? "bg-warning/10 text-warning dark:bg-warning/30 dark:text-warning"
                            : "bg-destructive/10 text-destructive dark:bg-destructive/30 dark:text-destructive",
                      )}
                    >
                      {(s.success_rate * 100).toFixed(0)}%
                    </span>
                  </td>
                  <td className="text-muted-foreground px-3 py-2 text-right tabular-nums">
                    {s.avg_duration_ms > 0
                      ? `${(s.avg_duration_ms / 1000).toFixed(1)}s`
                      : "\u2014"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
