"use client";

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  InfoIcon,
  Loader2Icon,
  RefreshCwIcon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";
import type { PreviewDiagnostic } from "./live-preview-panel";

interface SessionInfo {
  thread_id: string;
  workspace_path: string;
  workspace_exists: boolean;
  workspace_resolved: string | null;
  project: { kind: string; checks: string[] };
  rules_file: string | null;
  git_initialized: boolean;
  thread_metadata: {
    mode: string | null;
    sandbox_mode: string | null;
    workspace_path: string | null;
    extra_workspaces: string[] | null;
    agent_name: string | null;
  } | null;
  write_scope: {
    mode: string;
    roots: string[];
    requested_mode: string;
    error?: string;
  } | null;
  server_cwd: string;
  python_executable: string;
}

interface DiagnosticsPanelProps {
  threadId: string;
  workDir: string;
  previewDiagnostics?: PreviewDiagnostic[];
  className?: string;
}

export function DiagnosticsPanel({
  threadId,
  workDir,
  previewDiagnostics = [],
  className,
}: DiagnosticsPanelProps) {
  const { t } = useI18n();
  const [info, setInfo] = useState<SessionInfo | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const base = getBackendBaseURL();
      const params = new URLSearchParams({
        thread_id: threadId,
        workspace_path: workDir,
      });
      const res = await fetch(`${base}/api/debug/session-info?${params}`);
      if (res.ok) setInfo(await res.json());
    } catch (e) {
      swallow(e);
    }
    setLoading(false);
  }, [threadId, workDir]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className={cn("flex flex-col h-full", className)}>
      <div className="flex items-center justify-between px-3 py-2 border-b border-border-default">
        <div className="flex items-center gap-2">
          <InfoIcon className="size-4 text-primary" />
          <span className="text-sm font-medium">
            {t.diagnosticsPanel.title}
          </span>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
        >
          {loading ? (
            <Loader2Icon className="size-3 animate-spin" />
          ) : (
            <RefreshCwIcon className="size-3" />
          )}
        </button>
      </div>

      <div className="flex-1 overflow-auto px-3 py-2 space-y-3">
        {!info ? (
          <div className="flex items-center justify-center h-full text-muted-foreground/50">
            <Loader2Icon className="size-6 animate-spin" />
          </div>
        ) : (
          <>
            <Section title={t.diagnosticsPanel.sections.preview}>
              {previewDiagnostics.length === 0 ? (
                <div className="flex items-center gap-1.5 text-xs text-success">
                  <CheckCircle2Icon className="size-3 shrink-0" />
                  {t.diagnosticsPanel.noPreviewIssues}
                </div>
              ) : (
                <div className="space-y-1">
                  {previewDiagnostics.map((item) => (
                    <PreviewDiagnosticRow key={item.id} item={item} />
                  ))}
                </div>
              )}
            </Section>

            <Section title={t.diagnosticsPanel.sections.workspace}>
              <Row
                label={t.diagnosticsPanel.labels.path}
                value={info.workspace_path}
              />
              <Row
                label={t.diagnosticsPanel.labels.resolved}
                value={info.workspace_resolved ?? "—"}
              />
              <StatusRow
                label={t.diagnosticsPanel.labels.exists}
                ok={info.workspace_exists}
              />
              <StatusRow
                label={t.diagnosticsPanel.labels.git}
                ok={info.git_initialized}
              />
              <Row
                label={t.diagnosticsPanel.labels.rules}
                value={info.rules_file ?? t.diagnosticsPanel.labels.none}
              />
            </Section>

            <Section title={t.diagnosticsPanel.sections.project}>
              <Row
                label={t.diagnosticsPanel.labels.type}
                value={info.project.kind}
              />
              <Row
                label={t.diagnosticsPanel.labels.checks}
                value={
                  info.project.checks.join(", ") ||
                  t.diagnosticsPanel.labels.none
                }
              />
            </Section>

            {info.thread_metadata && (
              <Section title={t.diagnosticsPanel.sections.thread}>
                <Row
                  label={t.diagnosticsPanel.labels.mode}
                  value={info.thread_metadata.mode ?? "—"}
                />
                <Row
                  label={t.diagnosticsPanel.labels.sandbox}
                  value={info.thread_metadata.sandbox_mode ?? "—"}
                />
                <Row
                  label={t.diagnosticsPanel.labels.agent}
                  value={info.thread_metadata.agent_name ?? "—"}
                />
                {info.thread_metadata.workspace_path && (
                  <Row
                    label={t.diagnosticsPanel.labels.persistedWD}
                    value={info.thread_metadata.workspace_path}
                  />
                )}
              </Section>
            )}

            {info.write_scope && (
              <Section title={t.diagnosticsPanel.sections.writeScope}>
                {info.write_scope.error ? (
                  <div className="text-xs text-destructive">
                    {info.write_scope.error}
                  </div>
                ) : (
                  <>
                    <Row
                      label={t.diagnosticsPanel.labels.mode}
                      value={info.write_scope.mode}
                    />
                    <Row
                      label={t.diagnosticsPanel.labels.requested}
                      value={info.write_scope.requested_mode}
                    />
                    {info.write_scope.roots.map((r, i) => (
                      <Row
                        key={i}
                        label={
                          i === 0
                            ? t.diagnosticsPanel.labels.primaryRoot
                            : t.diagnosticsPanel.labels.rootN(i + 1)
                        }
                        value={r}
                      />
                    ))}
                  </>
                )}
              </Section>
            )}

            <Section title={t.diagnosticsPanel.sections.server}>
              <Row
                label={t.diagnosticsPanel.labels.cwd}
                value={info.server_cwd}
              />
              <Row
                label={t.diagnosticsPanel.labels.python}
                value={info.python_executable}
              />
              {info.server_cwd !== info.workspace_path && (
                <div className="flex items-center gap-1.5 mt-1 text-xs text-warning">
                  <AlertTriangleIcon className="size-3 shrink-0" />
                  {t.diagnosticsPanel.serverCwdDiffers}
                </div>
              )}
            </Section>
          </>
        )}
      </div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">
        {title}
      </div>
      <div className="space-y-0.5 ml-1">{children}</div>
    </div>
  );
}

function PreviewDiagnosticRow({ item }: { item: PreviewDiagnostic }) {
  const isError = item.level === "error";
  return (
    <div
      className={cn(
        "rounded border px-2 py-1.5 text-xs",
        isError
          ? "border-destructive/25 bg-destructive/8"
          : "border-warning/25 bg-warning/8",
      )}
    >
      <div className="flex items-center gap-1.5">
        <AlertTriangleIcon
          className={cn(
            "size-3 shrink-0",
            isError ? "text-destructive" : "text-warning",
          )}
        />
        <span
          className={cn(
            "font-medium uppercase",
            isError
              ? "text-destructive"
              : "text-warning",
          )}
        >
          {item.source}
        </span>
        <span className="text-muted-foreground">
          {new Date(item.timestamp).toLocaleTimeString()}
        </span>
      </div>
      <div className="mt-1 break-words font-mono text-foreground/80">
        {item.message}
      </div>
      {item.stack && (
        <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-all text-muted-foreground">
          {item.stack}
        </pre>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start gap-2 text-xs">
      <span className="text-muted-foreground shrink-0 w-20">{label}</span>
      <span className="font-mono text-foreground/80 break-all">{value}</span>
    </div>
  );
}

function StatusRow({ label, ok }: { label: string; ok: boolean }) {
  const { t } = useI18n();
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-muted-foreground shrink-0 w-20">{label}</span>
      {ok ? (
        <CheckCircle2Icon className="size-3 text-success" />
      ) : (
        <AlertTriangleIcon className="size-3 text-warning" />
      )}
      <span className={ok ? "text-success" : "text-warning"}>
        {ok ? t.diagnosticsPanel.status.yes : t.diagnosticsPanel.status.no}
      </span>
    </div>
  );
}
