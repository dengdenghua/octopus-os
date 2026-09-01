"use client";

import {
  CheckCircle2Icon,
  ClipboardCopyIcon,
  Loader2Icon,
  MessageCircleIcon,
  PlayIcon,
  ShieldCheckIcon,
  XCircleIcon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { swallow } from "@/core/utils/log";
import { copyTextToClipboard } from "@/core/clipboard";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export interface CheckResult {
  name: string;
  command: string;
  passed: boolean;
  exit_code: number;
  stdout: string;
  stderr: string;
  duration_ms: number;
}

export interface VerifyResult {
  kind: string;
  passed: boolean;
  results: CheckResult[];
}

export interface AutoVerifySummary {
  attempt: number;
  retryCount: number;
  maxRetries: number;
  autoFixQueued: boolean;
  exhausted: boolean;
}

interface VerifyPanelProps {
  workDir: string;
  initialResult?: VerifyResult | null;
  autoSummary?: AutoVerifySummary | null;
  pendingFiles?: string[];
  autoRunning?: boolean;
  browserRegressionEnabled?: boolean;
  browserRegressionPreviewUrl?: string | null;
  onResult?: (result: VerifyResult) => void;
  className?: string;
}

export function VerifyPanel({
  workDir,
  initialResult = null,
  autoSummary = null,
  pendingFiles = [],
  autoRunning = false,
  browserRegressionEnabled = false,
  browserRegressionPreviewUrl = null,
  onResult,
  className,
}: VerifyPanelProps) {
  const { t } = useI18n();
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [expandedCheck, setExpandedCheck] = useState<string | null>(null);

  useEffect(() => {
    if (initialResult) {
      setResult(initialResult);
    }
  }, [initialResult]);

  const runVerification = useCallback(async () => {
    setRunning(true);
    setResult(null);
    try {
      const base = getBackendBaseURL();
      const res = await fetch(`${base}/api/verify/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workspace: workDir,
          timeout: 60,
          browser_regression_enabled: browserRegressionEnabled,
          browser_regression_mode: browserRegressionEnabled
            ? "human_cursor"
            : "off",
          browser_regression_preview_url: browserRegressionEnabled
            ? (browserRegressionPreviewUrl ?? undefined)
            : undefined,
          browser_regression_requires_visible_cursor: browserRegressionEnabled,
        }),
      });
      if (res.ok) {
        const next = normalizeVerifyResult(await res.json());
        setResult(next);
        onResult?.(next);
      }
    } catch (e) {
      swallow(e);
    }
    setRunning(false);
  }, [
    browserRegressionEnabled,
    browserRegressionPreviewUrl,
    workDir,
    onResult,
  ]);

  const passedCount = result?.results.filter((r) => r.passed).length ?? 0;
  const totalCount = result?.results.length ?? 0;

  return (
    <div className={cn("flex flex-col h-full", className)}>
      <div className="flex items-center justify-between px-3 py-2 border-b border-border-default">
        <div className="flex items-center gap-2">
          <ShieldCheckIcon className="size-4 text-primary" />
          <span className="text-sm font-medium">{t.codeMode.verify}</span>
          {result && (
            <span
              className={cn(
                "rounded-full px-1.5 py-0.5 text-xs font-medium",
                result.passed
                  ? "bg-success/10 text-success"
                  : "bg-destructive/10 text-destructive",
              )}
            >
              {passedCount}/{totalCount}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={runVerification}
          disabled={running}
          className={cn(
            "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
            running
              ? "text-muted-foreground"
              : "text-primary hover:bg-primary/10",
          )}
        >
          {running ? (
            <Loader2Icon className="size-3 animate-spin" />
          ) : (
            <PlayIcon className="size-3" />
          )}
          {running ? t.codeMode.running : t.codeMode.runChecks}
        </button>
      </div>

      <div className="flex-1 overflow-auto px-3 py-2">
        {autoSummary && (
          <div
            className={cn(
              "mb-2 rounded-md border px-2.5 py-2 text-xs",
              autoSummary.exhausted
                ? "border-destructive/25 bg-destructive/8 text-destructive"
                : autoSummary.autoFixQueued
                  ? "border-warning/25 bg-warning/8 text-warning"
                  : "border-success/25 bg-success/8 text-success",
            )}
          >
            <div className="font-medium">
              {t.codeMode.autoVerifyAttempt(autoSummary.attempt)}
            </div>
            <div className="mt-0.5 text-xs opacity-80">
              {autoSummary.autoFixQueued
                ? t.codeMode.queuedAutoFix(
                    autoSummary.retryCount,
                    autoSummary.maxRetries,
                  )
                : autoSummary.exhausted
                  ? t.codeMode.autoFixLimitReached(
                      autoSummary.attempt,
                      autoSummary.maxRetries,
                    )
                  : result?.passed
                    ? t.codeMode.latestTurnPassed
                    : t.codeMode.noAutoFixQueued}
            </div>
          </div>
        )}

        {(pendingFiles.length > 0 || autoRunning) && (
          <div className="mb-2 rounded-md border border-info/25 bg-info/8 px-2.5 py-2 text-xs text-info dark:text-info">
            <div className="flex items-center gap-1.5 font-medium">
              {autoRunning && <Loader2Icon className="size-3 animate-spin" />}
              {autoRunning
                ? t.codeMode.autoVerifyRunning
                : t.codeMode.changesAwaitingVerify}
            </div>
            {pendingFiles.length > 0 && (
              <div className="mt-1 max-h-20 space-y-0.5 overflow-auto text-xs opacity-80">
                {pendingFiles.slice(0, 8).map((file) => (
                  <div key={file} className="truncate font-mono" title={file}>
                    {file}
                  </div>
                ))}
                {pendingFiles.length > 8 && (
                  <div>{t.codeMode.moreFiles(pendingFiles.length - 8)}</div>
                )}
              </div>
            )}
          </div>
        )}

        {!result && !running && (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground/50">
            <ShieldCheckIcon className="size-8 mb-2 opacity-30" />
            <span className="text-xs">{t.codeMode.clickRunChecksToVerify}</span>
            <span className="text-xs mt-1 opacity-60">{workDir}</span>
          </div>
        )}

        {running && !result && (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground/50">
            <Loader2Icon className="size-8 mb-2 animate-spin opacity-50" />
            <span className="text-xs">{t.codeMode.runningVerification}</span>
          </div>
        )}

        {result && (
          <div className="space-y-1">
            <div className="text-xs text-muted-foreground mb-2">
              {t.codeMode.projectLabel}: {result.kind}
            </div>
            {result.results.map((check) => (
              <div
                key={check.name}
                className="rounded-md border border-border-subtle"
              >
                <button
                  type="button"
                  onClick={() =>
                    setExpandedCheck(
                      expandedCheck === check.name ? null : check.name,
                    )
                  }
                  className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition-colors hover:bg-muted/50"
                >
                  {check.passed ? (
                    <CheckCircle2Icon className="size-3.5 text-success shrink-0" />
                  ) : (
                    <XCircleIcon className="size-3.5 text-destructive shrink-0" />
                  )}
                  <span className="text-xs font-medium flex-1">
                    {check.name}
                  </span>
                  <span className="text-xs text-muted-foreground font-mono">
                    {check.duration_ms < 1000
                      ? `${check.duration_ms}ms`
                      : `${(check.duration_ms / 1000).toFixed(1)}s`}
                  </span>
                </button>
                {expandedCheck === check.name && (
                  <div className="border-t border-border-subtle px-2.5 py-2">
                    <div className="flex items-center justify-between mb-1">
                      <div className="text-xs text-muted-foreground font-mono">
                        $ {check.command}
                      </div>
                      <div className="flex items-center gap-1">
                        {!check.passed && (check.stderr || check.stdout) && (
                          <button
                            type="button"
                            onClick={() => {
                              const errorText = `Verification check "${check.name}" failed:\n\`\`\`\n$ ${check.command}\n${check.stderr || check.stdout}\n\`\`\`\nPlease fix the errors above.`;
                              window.dispatchEvent(
                                new CustomEvent("echo:edit-message", {
                                  detail: { text: errorText },
                                }),
                              );
                              toast.success("Error sent to input");
                            }}
                            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium text-primary transition-colors hover:bg-primary/10"
                            title={t.codeMode.sendErrorToAI}
                          >
                            <MessageCircleIcon className="size-3" />
                            {t.codeMode.fix}
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => {
                            const text = `$ ${check.command}\n${check.stdout}${check.stderr ? `\n${check.stderr}` : ""}`;
                            void copyTextToClipboard(text)
                              .then(() => toast.success("Copied"))
                              .catch(() =>
                                toast.error("Failed to copy output"),
                              );
                          }}
                          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-muted"
                          title={t.codeMode.copyOutput}
                        >
                          <ClipboardCopyIcon className="size-3" />
                        </button>
                      </div>
                    </div>
                    {check.stdout && (
                      <pre className="text-xs font-mono leading-snug whitespace-pre-wrap break-all max-h-[200px] overflow-auto text-muted-foreground/80">
                        {check.stdout}
                      </pre>
                    )}
                    {check.stderr && (
                      <pre className="text-xs font-mono leading-snug whitespace-pre-wrap break-all max-h-[200px] overflow-auto text-destructive/80 dark:text-destructive/80 mt-1">
                        {check.stderr}
                      </pre>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function normalizeVerifyResult(data: unknown): VerifyResult {
  const payload = (data ?? {}) as Partial<VerifyResult>;
  return {
    kind: typeof payload.kind === "string" ? payload.kind : "unknown",
    passed: Boolean(payload.passed),
    results: Array.isArray(payload.results) ? payload.results : [],
  };
}
