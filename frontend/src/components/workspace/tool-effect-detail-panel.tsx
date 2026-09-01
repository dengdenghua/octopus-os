import {
  ArrowLeftIcon,
  CheckCircle2Icon,
  RefreshCwIcon,
  RotateCcwIcon,
  ShieldAlertIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { authorizeToolEffectRetry } from "@/core/observability/api";
import { useToolEffects } from "@/core/observability/tool-effects-context";

const INTERNAL_EFFECT_ACTOR_RE =
  /^(?:read_file|read_file_range|exec_shell|shell_command|run_command|todo_write|apply_patch|write_file|edit_file|str_replace|web_search|fetch_url)$/i;

function publicEffectActor(value: unknown): string {
  const actor = typeof value === "string" ? value.trim() : "";
  return actor && !INTERNAL_EFFECT_ACTOR_RE.test(actor) ? actor : "外部动作";
}

export function ToolEffectDetailPanel({
  effectKey,
  onBack,
}: {
  effectKey: string;
  onBack: () => void;
}) {
  const { snapshot, receiptsByEffectKey, loading, error, refresh } =
    useToolEffects();
  const receipt = receiptsByEffectKey.get(effectKey);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => setReason(""), [effectKey]);

  const authorize = async () => {
    if (!receipt || receipt.state !== "indeterminate") return;
    const reviewReason = reason.trim();
    if (reviewReason.length < 8) return;
    setSubmitting(true);
    try {
      const response = await authorizeToolEffectRetry(receipt, reviewReason);
      if (response.audit_warning) {
        toast.warning(`已放行，但审计记录异常：${response.audit_warning}`);
      } else {
        toast.success("已允许一次受 fencing token 保护的重试");
      }
      refresh();
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "放行失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-background/75">
      <header className="flex shrink-0 items-center gap-2 border-b border-border-default px-4 py-3">
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="size-8"
          onClick={onBack}
          aria-label="返回执行详情"
        >
          <ArrowLeftIcon className="size-4" />
        </Button>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-medium">外部动作核对</h2>
          <p className="text-xs text-muted-foreground">
            确认真实世界结果后再决定是否重试
          </p>
        </div>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="size-8"
          onClick={refresh}
          aria-label="刷新外部动作回执"
        >
          <RefreshCwIcon
            className={loading ? "size-3.5 animate-spin" : "size-3.5"}
          />
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
        {error && !receipt ? (
          <p className="text-sm text-destructive">{error.message}</p>
        ) : !receipt ? (
          <p className="text-sm text-muted-foreground">
            {loading ? "正在读取最新回执…" : "该回执已不在当前状态窗口中。"}
          </p>
        ) : (
          <div className="mx-auto max-w-xl space-y-5">
            <div className="flex items-start gap-3 border-l-2 border-warning/70 pl-3">
              {receipt.state === "indeterminate" ? (
                <ShieldAlertIcon className="mt-0.5 size-4 shrink-0 text-warning" />
              ) : (
                <CheckCircle2Icon className="mt-0.5 size-4 shrink-0 text-success" />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">
                    {publicEffectActor(receipt.sucker_id)}
                  </span>
                  <EffectStateBadge state={receipt.state} />
                </div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {receipt.reason || stateExplanation(receipt.state)}
                </p>
              </div>
            </div>

            <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-4 gap-y-2 border-y border-border-subtle py-4 text-xs">
              <dt className="text-muted-foreground">协调范围</dt>
              <dd>
                {snapshot.shared_across_hosts ? "跨节点共享" : "本机协调"}
              </dd>
              <dt className="text-muted-foreground">任务</dt>
              <dd className="truncate font-mono" title={receipt.task_id}>
                {receipt.task_id || "—"}
              </dd>
              <dt className="text-muted-foreground">步骤</dt>
              <dd>{receipt.step_id}</dd>
              <dt className="text-muted-foreground">fencing token</dt>
              <dd className="font-mono">{receipt.fencing_token}</dd>
              <dt className="text-muted-foreground">最近持有者</dt>
              <dd className="truncate font-mono" title={receipt.holder_id}>
                {receipt.holder_id || "—"}
              </dd>
            </dl>

            {receipt.state === "indeterminate" &&
            snapshot.can_authorize_retry ? (
              <section className="space-y-3">
                <div>
                  <h3 className="text-sm font-medium">确认动作没有发生</h3>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">
                    先在外部系统、文件或远程服务中核对。只有确认没有成功，才允许执行器消费一次重试授权。
                  </p>
                </div>
                <Textarea
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="填写核对依据，例如：支付平台确认没有生成订单。"
                  className="min-h-24"
                />
                <Button
                  type="button"
                  variant="destructive"
                  className="w-full"
                  disabled={submitting || reason.trim().length < 8}
                  onClick={() => void authorize()}
                >
                  <RotateCcwIcon className="size-4" />
                  {submitting
                    ? "正在核对最新状态…"
                    : "确认未发生并允许一次重试"}
                </Button>
              </section>
            ) : receipt.state === "indeterminate" ? (
              <p className="border-l-2 border-border-default pl-3 text-xs leading-5 text-muted-foreground">
                当前账号可以查看回执，但只有管理员能放行外部动作重试。
              </p>
            ) : (
              <p className="text-xs leading-5 text-muted-foreground">
                {stateExplanation(receipt.state)}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function EffectStateBadge({
  state,
}: {
  state:
    | "claimed"
    | "started"
    | "committed"
    | "indeterminate"
    | "retry_authorized";
}) {
  const labels = {
    claimed: "已占用",
    started: "执行中",
    committed: "已提交",
    indeterminate: "需人工核对",
    retry_authorized: "已放行一次",
  } as const;
  return (
    <Badge variant={state === "indeterminate" ? "destructive" : "secondary"}>
      {labels[state]}
    </Badge>
  );
}

function stateExplanation(state: string): string {
  if (state === "retry_authorized") {
    return "授权已写入持久化回执；下一位持有者只能消费这一次重试机会。";
  }
  if (state === "committed") return "外部动作结果已持久化，可以安全复用。";
  if (state === "started" || state === "claimed") {
    return "当前仍有执行器持有该动作，请等待它提交最终结果。";
  }
  return "执行器无法判断外部动作是否已经发生，已默认停止重复执行。";
}
