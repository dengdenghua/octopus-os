import { CheckCircle2Icon, ShieldAlertIcon, XCircleIcon } from "lucide-react";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { swallow } from "@/core/utils/log";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface ToolApprovalRequest {
  type: "tool_approval_request";
  tool_name: string;
  tool_call_id: string;
  detail: string;
  args_preview: string;
}

function parseApprovalRequest(content: string): ToolApprovalRequest | null {
  try {
    const match = content.match(/\[APPROVAL REQUIRED\]/);
    if (!match) return null;
    const jsonMatch = content.match(
      /\{[\s\S]*"type"\s*:\s*"tool_approval_request"[\s\S]*\}/,
    );
    if (!jsonMatch) return null;
    return JSON.parse(jsonMatch[0]) as ToolApprovalRequest;
  } catch (e) {
    swallow(e);
    return null;
  }
}

export function isApprovalRequest(
  content: string,
  additional_kwargs?: Record<string, unknown>,
): boolean {
  if (content?.includes("[APPROVAL REQUIRED]")) return true;
  if (additional_kwargs?.approval_request) return true;
  return false;
}

export function getApprovalData(
  content: string,
  additional_kwargs?: Record<string, unknown>,
): ToolApprovalRequest | null {
  if (additional_kwargs?.approval_request) {
    try {
      return JSON.parse(additional_kwargs.approval_request as string);
    } catch (e) {
      swallow(e);
      return null;
    }
  }
  return parseApprovalRequest(content);
}

// Colors stay here (styling); the human-readable label comes from i18n
// (t.toolApproval.tools) so a beginner sees "运行命令"/"改文件", not "bash".
const TOOL_COLORS: Record<string, string> = {
  bash: "bg-chart-7/10 text-chart-7 dark:text-chart-7",
  write_file: "bg-info/10 text-info dark:text-info",
  str_replace: "bg-chart-1/10 text-chart-1 dark:text-chart-1",
  git_commit: "bg-success/10 text-success",
  schedule_cron: "bg-warning/10 text-warning",
  remote_trigger: "bg-destructive/10 text-destructive",
};

export function ToolApprovalCard({
  content,
  additional_kwargs,
  onApprove,
  onReject,
}: {
  content: string;
  additional_kwargs?: Record<string, unknown>;
  onApprove?: (approvalData: ToolApprovalRequest) => void;
  onReject?: (approvalData: ToolApprovalRequest) => void;
}) {
  const { t } = useI18n();
  const [status, setStatus] = useState<"pending" | "approved" | "rejected">(
    "pending",
  );
  const approvalData = getApprovalData(content, additional_kwargs);

  const handleApprove = useCallback(() => {
    setStatus("approved");
    if (approvalData) {
      onApprove?.(approvalData);
    }
    toast.success(t.toolApproval.approved);
  }, [approvalData, onApprove, t]);

  const handleReject = useCallback(() => {
    setStatus("rejected");
    if (approvalData) {
      onReject?.(approvalData);
    }
    toast.info(t.toolApproval.rejected);
  }, [approvalData, onReject, t]);

  if (!approvalData) return null;

  const toolLabel =
    (t.toolApproval.tools as Record<string, string>)[approvalData.tool_name] ??
    approvalData.tool_name;
  const toolColor =
    TOOL_COLORS[approvalData.tool_name] ??
    "bg-muted-foreground/10 text-muted-foreground";

  return (
    <div
      className={cn(
        "border rounded-lg p-3 space-y-3 transition-colors",
        status === "approved" && "border-success/30 bg-success/5",
        status === "rejected" && "border-destructive/30 bg-destructive/5",
        status === "pending" && "border-warning/30 bg-warning/5",
      )}
    >
      <div className="flex items-center gap-2">
        <ShieldAlertIcon className="size-4 text-warning" />
        <span
          className={cn(
            "text-xs font-medium px-2 py-0.5 rounded-lg",
            toolColor,
          )}
        >
          {toolLabel}
        </span>
        <span className="text-muted-foreground text-xs">
          {t.toolApproval.requiresApproval}
        </span>
      </div>

      <div className="text-sm">
        <div className="font-medium">{approvalData.detail}</div>
        {approvalData.args_preview && (
          <pre className="mt-2 max-h-32 overflow-auto rounded bg-muted/50 p-2 text-xs font-mono">
            {approvalData.args_preview}
          </pre>
        )}
      </div>

      {status === "pending" ? (
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="default"
            className="h-7 gap-1.5 text-xs"
            onClick={handleApprove}
          >
            <CheckCircle2Icon className="size-3.5" />
            {t.toolApproval.approve}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 gap-1.5 text-xs"
            onClick={handleReject}
          >
            <XCircleIcon className="size-3.5" />
            {t.toolApproval.reject}
          </Button>
        </div>
      ) : (
        <div
          className={cn(
            "flex items-center gap-1.5 text-xs font-medium",
            status === "approved" && "text-success",
            status === "rejected" && "text-destructive",
          )}
        >
          {status === "approved" ? (
            <>
              <CheckCircle2Icon className="size-3.5" />
              {t.toolApproval.approved}
            </>
          ) : (
            <>
              <XCircleIcon className="size-3.5" />
              {t.toolApproval.rejected}
            </>
          )}
        </div>
      )}
    </div>
  );
}
