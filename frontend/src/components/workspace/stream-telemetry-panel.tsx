import { ActivityIcon, Trash2Icon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import {
  clearStreamTelemetry,
  readStreamTelemetry,
  STREAM_TELEMETRY_UPDATED_EVENT,
  summarizeStreamTelemetry,
  type StreamTurnOutcome,
} from "@/core/realtime";

function formatDuration(value: number | null): string {
  if (value == null) return "-";
  if (value < 1_000) return `${Math.round(value)} ms`;
  return `${(value / 1_000).toFixed(value < 10_000 ? 1 : 0)} s`;
}

export function StreamTelemetryPanel() {
  const { t, locale } = useI18n();
  const [records, setRecords] = useState(readStreamTelemetry);
  const summary = useMemo(() => summarizeStreamTelemetry(records), [records]);

  useEffect(() => {
    const refresh = () => setRecords(readStreamTelemetry());
    window.addEventListener(STREAM_TELEMETRY_UPDATED_EVENT, refresh);
    window.addEventListener("storage", refresh);
    return () => {
      window.removeEventListener(STREAM_TELEMETRY_UPDATED_EVENT, refresh);
      window.removeEventListener("storage", refresh);
    };
  }, []);

  const outcomeLabel = (outcome: StreamTurnOutcome) =>
    t.diagnosticsPage.streaming.outcomes[outcome];

  return (
    <section className="workspace-panel rounded-lg px-5 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <ActivityIcon className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
          <div className="min-w-0">
            <h2 className="text-sm font-semibold">
              {t.diagnosticsPage.streaming.title}
            </h2>
            <p className="text-muted-foreground mt-1 text-xs">
              {t.diagnosticsPage.streaming.description}
            </p>
          </div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={!records.length}
          onClick={() => clearStreamTelemetry()}
          title={t.diagnosticsPage.streaming.clear}
          aria-label={t.diagnosticsPage.streaming.clear}
        >
          <Trash2Icon className="size-4" />
        </Button>
      </div>

      {records.length ? (
        <>
          <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-md border bg-border sm:grid-cols-3 lg:grid-cols-6">
            {[
              [t.diagnosticsPage.streaming.samples, String(summary.count)],
              [
                t.diagnosticsPage.streaming.ttftP50,
                formatDuration(summary.ttftP50Ms),
              ],
              [
                t.diagnosticsPage.streaming.ttftP95,
                formatDuration(summary.ttftP95Ms),
              ],
              [
                t.diagnosticsPage.streaming.maxGapP95,
                formatDuration(summary.maxGapP95Ms),
              ],
              [
                t.diagnosticsPage.streaming.stalledRate,
                `${Math.round(summary.stalledRate * 100)}%`,
              ],
              [
                t.diagnosticsPage.streaming.unsuccessfulRate,
                `${Math.round(summary.unsuccessfulRate * 100)}%`,
              ],
            ].map(([label, value]) => (
              <div key={label} className="bg-background px-3 py-3">
                <div className="text-muted-foreground text-xs">{label}</div>
                <div className="mt-1 text-sm font-semibold tabular-nums">
                  {value}
                </div>
              </div>
            ))}
          </div>

          <div className="mt-5 overflow-x-auto max-w-full">
            <table className="w-full min-w-[480px] text-left text-xs md:min-w-0">
              <thead className="text-muted-foreground border-b">
                <tr>
                  <th className="px-2 py-2 font-medium">
                    {t.diagnosticsPage.streaming.time}
                  </th>
                  <th className="px-2 py-2 font-medium">
                    {t.diagnosticsPage.streaming.outcome}
                  </th>
                  <th className="px-2 py-2 font-medium">TTFT</th>
                  <th className="px-2 py-2 font-medium">
                    {t.diagnosticsPage.streaming.maxGap}
                  </th>
                  <th className="px-2 py-2 font-medium">
                    {t.diagnosticsPage.streaming.duration}
                  </th>
                  <th className="px-2 py-2 font-medium">
                    {t.diagnosticsPage.streaming.endState}
                  </th>
                </tr>
              </thead>
              <tbody>
                {records.slice(0, 20).map((record) => (
                  <tr key={record.id} className="border-b last:border-0">
                    <td className="text-muted-foreground px-2 py-2.5 tabular-nums">
                      {new Intl.DateTimeFormat(locale, {
                        month: "2-digit",
                        day: "2-digit",
                        hour: "2-digit",
                        minute: "2-digit",
                      }).format(record.completedAt)}
                    </td>
                    <td className="px-2 py-2.5">
                      {outcomeLabel(record.outcome)}
                    </td>
                    <td className="px-2 py-2.5 tabular-nums">
                      {formatDuration(record.ttftMs)}
                    </td>
                    <td className="px-2 py-2.5 tabular-nums">
                      {formatDuration(record.maxDeltaGapMs)}
                    </td>
                    <td className="px-2 py-2.5 tabular-nums">
                      {formatDuration(record.durationMs)}
                    </td>
                    <td className="px-2 py-2.5">
                      {record.stalledAtEnd
                        ? t.diagnosticsPage.streaming.stalled
                        : t.diagnosticsPage.streaming.normal}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="text-muted-foreground mt-4 rounded-md border border-dashed px-4 py-8 text-center text-xs">
          {t.diagnosticsPage.streaming.empty}
        </div>
      )}
    </section>
  );
}
