import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RefreshCw, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  approveCoderUpstreamUpdate,
  checkCoderUpstreamUpdate,
  coderUpstreamUpdateQueryKey,
  getCoderUpstreamUpdate,
  type CoderUpstreamUpdate,
} from "@/core/coder/api";
import { useI18n } from "@/core/i18n/hooks";
import { useAuth } from "@/providers/AuthProvider";

function displayTime(value: string | null, locale: string) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function replaceStatus(
  client: ReturnType<typeof useQueryClient>,
  status: CoderUpstreamUpdate,
) {
  client.setQueryData(coderUpstreamUpdateQueryKey, status);
}

export function CodexUpdateRadar() {
  const { locale } = useI18n();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const zh = locale.toLowerCase().startsWith("zh");
  const status = useQuery({
    queryKey: coderUpstreamUpdateQueryKey,
    queryFn: ({ signal }) => getCoderUpstreamUpdate(signal),
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  });
  const check = useMutation({
    mutationFn: checkCoderUpstreamUpdate,
    onSuccess: (next) => replaceStatus(queryClient, next),
  });
  const approve = useMutation({
    mutationFn: approveCoderUpstreamUpdate,
    onSuccess: (next) => replaceStatus(queryClient, next),
  });
  const data = status.data;
  const mutationError = check.error ?? approve.error;
  const error =
    mutationError instanceof Error
      ? mutationError.message
      : status.error instanceof Error
        ? status.error.message
        : data?.error;
  const isApproved = data?.approval_status === "approved_for_next_release";
  const canApprove = Boolean(
    user?.roles?.some((role) => role === "admin" || role === "operator"),
  );

  const openReleaseNotes = () => {
    if (!data?.release_url) return;
    if (window.echo?.app?.openExternal) {
      void window.echo.app.openExternal(data.release_url);
      return;
    }
    window.open(data.release_url, "_blank", "noopener,noreferrer");
  };

  return (
    <section
      className="mt-6 rounded-lg border p-4"
      aria-label="Codex update radar"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <ShieldCheck className="size-4 text-primary" aria-hidden="true" />
            {zh ? "Codex 引擎更新雷达" : "Codex engine update radar"}
          </h3>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">
            {zh
              ? "自动发现 OpenAI 上游版本；批准后仅进入下一次 Echo 发版，不会热替换当前引擎。"
              : "Detects OpenAI upstream releases. Approval only queues a version for the next Echo release; it never hot-swaps the running engine."}
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={check.isPending}
          onClick={() => check.mutate()}
        >
          <RefreshCw
            className={`mr-1.5 size-3.5 ${check.isPending ? "animate-spin" : ""}`}
            aria-hidden="true"
          />
          {check.isPending
            ? zh
              ? "检查中"
              : "Checking"
            : zh
              ? "检查 Codex 引擎更新"
              : "Check now"}
        </Button>
      </div>

      <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-3">
        <div className="rounded-md bg-muted/45 px-3 py-2">
          <dt className="text-xs text-muted-foreground">
            {zh ? "当前内置" : "Bundled"}
          </dt>
          <dd className="mt-1 font-mono text-xs">
            {data?.current_version ?? "—"}
          </dd>
        </div>
        <div className="rounded-md bg-muted/45 px-3 py-2">
          <dt className="text-xs text-muted-foreground">
            {zh ? "上游最新" : "Latest upstream"}
          </dt>
          <dd className="mt-1 font-mono text-xs">
            {data?.latest_version ?? "—"}
          </dd>
        </div>
        <div className="rounded-md bg-muted/45 px-3 py-2">
          <dt className="text-xs text-muted-foreground">
            {zh ? "最近检查" : "Last checked"}
          </dt>
          <dd className="mt-1 text-xs">
            {displayTime(data?.checked_at ?? null, locale)}
          </dd>
        </div>
      </dl>

      {data?.update_available ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-primary/25 bg-primary/5 px-3 py-2.5">
          <p className="text-xs">
            {isApproved
              ? zh
                ? `v${data.approved_version} 已批准，等待随下一版 Echo 发布。`
                : `v${data.approved_version} is approved for the next Echo release.`
              : zh
                ? `发现 v${data.latest_version}，等待管理员批准。`
                : `v${data.latest_version} is available and awaiting approval.`}
          </p>
          {!isApproved && data.latest_version && canApprove ? (
            <Button
              type="button"
              size="sm"
              disabled={approve.isPending}
              onClick={() => approve.mutate(data.latest_version!)}
            >
              {approve.isPending
                ? zh
                  ? "批准中"
                  : "Approving"
                : zh
                  ? "批准纳入下一版本"
                  : "Approve for next release"}
            </Button>
          ) : null}
        </div>
      ) : data?.checked_at && !data.error ? (
        <p className="mt-3 text-xs text-muted-foreground">
          {zh ? "当前已是最新内置版本。" : "The bundled engine is up to date."}
        </p>
      ) : null}

      {error ? (
        <p className="mt-3 text-xs text-destructive" role="alert">
          {zh ? "检查失败：" : "Check failed: "}
          {error}
        </p>
      ) : null}

      {data?.release_url ? (
        <Button
          type="button"
          variant="link"
          size="sm"
          className="mt-2 h-auto px-0 text-xs"
          onClick={openReleaseNotes}
        >
          {zh ? "查看 OpenAI 官方更新记录" : "View OpenAI release notes"}
          <ExternalLink className="ml-1 size-3" aria-hidden="true" />
        </Button>
      ) : null}
    </section>
  );
}
