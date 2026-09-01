import { ShieldAlertIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { PendingApproval } from "@/core/realtime/items";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

function approvalToolLabel(
  tool: string | undefined,
  method: PendingApproval["method"],
  t: ReturnType<typeof useI18n>["t"],
): string {
  const normalized = tool?.trim().toLowerCase() ?? "";
  if (
    normalized === "bash" ||
    normalized === "exec_shell" ||
    normalized === "shell_command" ||
    normalized === "run_command"
  ) {
    return t.toolApproval.tools.bash;
  }
  if (
    normalized === "write_file" ||
    normalized === "write_text_file" ||
    normalized === "create_file"
  ) {
    return t.toolApproval.tools.write_file;
  }
  if (
    normalized === "apply_patch" ||
    normalized === "edit_file" ||
    normalized === "edit_text_file" ||
    normalized === "str_replace"
  ) {
    return t.toolApproval.tools.str_replace;
  }
  const knownTool =
    (t.toolApproval.tools as Record<string, string>)[normalized];
  if (knownTool) return knownTool;
  if (method.includes("commandExecution")) {
    return t.toolApproval.tools.bash;
  }
  if (method.includes("fileChange")) {
    return t.toolApproval.tools.str_replace;
  }
  return t.liveTools.genericAction;
}

function approvalArgsSummary(
  params: {
    argsPreview?: string;
    detail?: string;
  },
  method: PendingApproval["method"],
): string {
  const preview = params.argsPreview?.trim();
  if (!preview) return params.detail?.trim() ?? "";

  const field = method.includes("commandExecution") ? "command" : "path";
  const quoted = new RegExp(
    `["']${field}["']\\s*:\\s*["']([^"']+)["']`,
    "i",
  ).exec(preview);
  if (quoted?.[1]) return quoted[1].trim();

  try {
    const parsed = JSON.parse(preview) as Record<string, unknown>;
    const value = parsed[field];
    if (typeof value === "string" && value.trim()) return value.trim();
  } catch {
    // Tool adapters may provide a Python-style dict preview. The targeted
    // field extraction above handles that common form; otherwise retain the
    // original preview instead of inventing a command or path.
  }
  return preview;
}

export function RealtimeApprovalPrompt({
  approvals,
  resolveApproval,
  className,
}: {
  approvals: PendingApproval[];
  resolveApproval: (requestId: string | number, accept: boolean) => void;
  className?: string;
}) {
  const { t } = useI18n();
  if (approvals.length === 0) return null;

  return (
    <div
      className={cn(
        "mx-1 max-h-44 space-y-1.5 overflow-y-auto overscroll-contain",
        className,
      )}
    >
      {approvals.map((approval) => {
        const params = approval.params as {
          tool?: string;
          argsPreview?: string;
          detail?: string;
        };
        const toolLabel = approvalToolLabel(params.tool, approval.method, t);
        const summary = approvalArgsSummary(params, approval.method);
        const label = `${toolLabel} · ${t.toolApproval.requiresApproval}`;
        const labelId = `approval-${String(approval.requestId)}-label`;
        return (
          <section
            key={String(approval.requestId)}
            aria-labelledby={labelId}
            className="grid min-h-14 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-xl border border-warning/20 bg-background/95 px-3 py-2 shadow-[0_12px_32px_-24px_rgba(15,23,42,0.55)] backdrop-blur-xl"
          >
            <div className="flex min-w-0 items-center gap-2.5">
              <ShieldAlertIcon
                aria-hidden="true"
                className="size-4 shrink-0 text-warning"
              />
              <div className="min-w-0">
                <p
                  id={labelId}
                  className="truncate text-[13px] font-medium leading-5 text-foreground"
                >
                  {label}
                </p>
                {summary ? (
                  <code
                    className="block truncate font-mono text-mini leading-4 text-muted-foreground"
                    title={summary}
                  >
                    {summary}
                  </code>
                ) : null}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => resolveApproval(approval.requestId, false)}
                className="text-muted-foreground hover:text-foreground"
              >
                {t.toolApproval.reject}
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={() => resolveApproval(approval.requestId, true)}
              >
                {t.toolApproval.approve}
              </Button>
            </div>
          </section>
        );
      })}
    </div>
  );
}
