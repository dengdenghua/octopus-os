import {
  coderAccountQueryOptions,
  coderModelsQueryOptions,
  coderRateLimitsQueryOptions,
  coderUsageQueryOptions,
} from "@/core/coder/query-options";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCwIcon } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  coderQueryKeys,
  getCoderModelProfile,
  updateCoderModelProfile,
} from "@/core/coder/api";
import { useModels } from "@/core/models/hooks";
import { useLocalSettings } from "@/core/settings/hooks";
import { useAuth } from "@/providers/AuthProvider";
import { AiStatusIcon } from "./ai-status-icon";

function quotaWindowLabel(duration: number | null | undefined): string {
  if (!Number.isFinite(duration) || !duration || duration <= 0) {
    return "周期未知";
  }
  if (duration >= 1440) return `${duration / 1440} 天`;
  if (duration < 60) return `${duration} 分钟`;
  return `${duration / 60} 小时`;
}

export function SystemModelStatus({
  onOpenSettings,
}: {
  onOpenSettings: () => void;
}) {
  const [open, setOpen] = useState(false);
  const modelChoiceId = useId();
  const [portalContainer, setPortalContainer] = useState<HTMLElement | null>(
    null,
  );
  const triggerRef = useRef<HTMLButtonElement>(null);
  const { user, isLoading: authLoading } = useAuth();
  const queryKeys = coderQueryKeys(user?.actor_id || user?.user_id || "local");
  const queryClient = useQueryClient();
  const { models, isLoading, error, refetch: refetchModels } = useModels();
  const [settings, setSettings] = useLocalSettings();
  const value = settings.context.model_name || "auto";
  const options = Array.from(
    new Map(
      models.map((model) => [
        model.selection_id || model.entry_id || model.name,
        model,
      ]),
    ).entries(),
  );
  const selected = options.find(([key]) => key === value)?.[1];
  const accountModel = value.match(/^chatgpt[/:](.+)$/i)?.[1];
  const label =
    value === "auto"
      ? "自动选择"
      : selected?.display_name || selected?.name || accountModel || value;
  const profile = useQuery({
    queryKey: queryKeys.profile,
    queryFn: ({ signal }) => getCoderModelProfile(signal),
    enabled: !authLoading,
    staleTime: 30_000,
    retry: false,
  });
  const profileModelName = profile.data
    ? profile.data.source === "codex_account"
      ? profile.data.effective_model
        ? `chatgpt/${profile.data.effective_model}`
        : "auto"
      : profile.data.selected_model || "auto"
    : null;
  const modelConnection = profile.isLoading
    ? {
        label: "检查中",
        iconState: "checking" as const,
        detail: "正在检查模型执行连接。",
        className: "bg-amber-400",
      }
    : profile.isError
      ? {
          label: "连接异常",
          iconState: "unavailable" as const,
          detail: "模型状态暂时无法确认，请打开模型设置检查连接。",
          className: "bg-red-400",
        }
      : profile.data?.execution_available === false ||
          profile.data?.compatible === false
        ? {
            label: "模型不可用",
            iconState: "unavailable" as const,
            detail:
              profile.data.execution_unavailable_reason ||
              profile.data.compatibility_reason ||
              "当前模型路由暂时不可执行。",
            className: "bg-red-400",
          }
        : profile.data
          ? {
              label: "已连接",
              iconState: "connected" as const,
              detail: "当前模型路由可以接收新的任务。",
              className: "bg-emerald-400",
            }
          : {
              label: "状态未知",
              iconState: "unknown" as const,
              detail: "尚未取得模型执行状态。",
              className: "bg-slate-400",
            };
  const account = useQuery({
    ...coderAccountQueryOptions(queryKeys),
    enabled: open && !authLoading,
  });
  const hasAccount =
    !authLoading && !account.isError && !!account.data?.account;
  const accountModels = useQuery({
    ...coderModelsQueryOptions(queryKeys),
    enabled: open && hasAccount,
  });
  const subscriptionOptions = hasAccount
    ? (accountModels.data?.models ?? [])
    : [];
  const subscriptionKeys = new Set(
    subscriptionOptions.map((model) => `chatgpt/${model.id}`),
  );
  const hasUsage =
    !authLoading &&
    !account.isError &&
    account.data?.account?.type === "chatgpt";
  const limits = useQuery({
    ...coderRateLimitsQueryOptions(queryKeys),
    enabled: open && hasUsage,
  });
  const usage = useQuery({
    ...coderUsageQueryOptions(queryKeys),
    enabled: open && hasUsage,
  });
  const syncCoderModel = useMutation({
    mutationFn: (modelName: string) => {
      const normalized = modelName.trim();
      const accountMatch = normalized.match(/^chatgpt[/:](.+)$/i);
      if (accountMatch?.[1]) {
        return updateCoderModelProfile({
          source: "codex_account",
          model: accountMatch[1],
        });
      }
      return updateCoderModelProfile({
        source: "follow_system",
        ...(normalized && normalized !== "auto" && normalized !== "default"
          ? { model: normalized }
          : {}),
      });
    },
    onSuccess: (profile, modelName) => {
      // CoderEngineControl observes the same principal-scoped query. Updating
      // its cache makes a top-bar change visible in the open workspace
      // without a reload or a second, competing model store.
      queryClient.setQueryData(queryKeys.profile, profile);
      // The server profile is the commit point for Codex-backed models. Only
      // mirror the selection into the shared local setting after that commit
      // succeeds, otherwise a failed request would split the two selectors.
      setSettings("context", { model_name: modelName });
    },
  });

  const selectModel = (modelName: string) => {
    // Echo Mix is intentionally not a Codex-executable model. Keep it in the
    // Echo system setting and let the workspace show its account profile.
    if (modelName === "mix" || modelName === "echo-mix") {
      setSettings("context", { model_name: modelName });
      return;
    }
    syncCoderModel.mutate(modelName);
  };
  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    if (nextOpen) {
      // Radix normally portals to document.body. The desktop shell owns a
      // second visual theme (wallpaper/material/intensity), so host this one
      // menu inside the nearest desktop root while it is open. Workspace
      // menus keep the body portal because they have no desktop material.
      setPortalContainer(
        triggerRef.current?.closest<HTMLElement>(".macos-desktop-root") ?? null,
      );
    } else {
      setPortalContainer(null);
    }
  };
  useEffect(() => {
    if (
      !profileModelName ||
      syncCoderModel.isPending ||
      value === "mix" ||
      value === "echo-mix" ||
      value === profileModelName
    ) {
      return;
    }
    // The server-owned profile is authoritative for Codex-backed models. This
    // also catches changes made by the model settings page or another open
    // workspace, so the desktop badge cannot drift after an external update.
    setSettings("context", { model_name: profileModelName });
  }, [profileModelName, setSettings, syncCoderModel.isPending, value]);
  return (
    <DropdownMenu open={open} onOpenChange={handleOpenChange}>
      <DropdownMenuTrigger asChild>
        <button
          ref={triggerRef}
          type="button"
          className={`mac-status-icon${open ? " is-active" : ""}`}
          aria-label={`模型与用量：${label}（${modelConnection.label}）`}
          title={`系统模型：${label} · ${modelConnection.label}`}
        >
          <AiStatusIcon state={modelConnection.iconState} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        sideOffset={10}
        portalContainer={portalContainer}
        data-liquid-surface="thick-dark"
        className="mac-model-status-popover z-[200] w-[320px] max-w-[calc(100vw-24px)] rounded-xl p-3 text-xs"
      >
        <h2 className="text-sm font-semibold leading-5">模型与用量</h2>
        <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
          系统默认模型 · 后续发送使用，进行中的轮次不变
        </p>
        <div className="mt-2 flex items-start gap-2 rounded-md bg-muted/60 px-2 py-1.5">
          <span
            aria-hidden
            className={`mt-1 size-1.5 shrink-0 rounded-full ${modelConnection.className}`}
          />
          <div className="min-w-0">
            <p className="text-[11px] font-medium leading-4">
              执行连接：{modelConnection.label}
            </p>
            <p className="text-[10px] leading-4 text-muted-foreground">
              {modelConnection.detail}
            </p>
          </div>
        </div>
        <label
          htmlFor={modelChoiceId}
          className="mt-3 block text-[11px] font-medium"
        >
          切换模型
        </label>
        <select
          id={modelChoiceId}
          value={value}
          disabled={
            syncCoderModel.isPending ||
            (isLoading && subscriptionOptions.length === 0)
          }
          onChange={(event) => selectModel(event.target.value)}
          className="mac-model-status-select mt-1 h-8 w-full rounded-md border bg-background px-2 text-xs"
        >
          <option value="auto">自动选择</option>
          {value !== "auto" && !selected && !subscriptionKeys.has(value) && (
            <option value={value}>
              {accountModel
                ? `${accountModel}（ChatGPT 订阅）`
                : `${value}（暂未在目录中找到）`}
            </option>
          )}
          {subscriptionOptions.length > 0 && (
            <optgroup
              label={
                account.data?.account?.type === "chatgpt"
                  ? "ChatGPT 订阅"
                  : "Codex 账户"
              }
            >
              {subscriptionOptions.map((model) => (
                <option key={model.id} value={`chatgpt/${model.id}`}>
                  {model.display_name || model.id}
                </option>
              ))}
            </optgroup>
          )}
          <optgroup label="Echo 模型" disabled={!!error}>
            {options
              .filter(([key]) => key !== "auto" && !subscriptionKeys.has(key))
              .map(([key, model]) => (
                <option key={key} value={key}>
                  {model.display_name || model.name}
                </option>
              ))}
          </optgroup>
        </select>
        {hasAccount &&
          accountModels.isFetching &&
          subscriptionOptions.length === 0 && (
            <p role="status" className="mt-1.5 text-[11px] leading-4">
              正在加载账户模型…
            </p>
          )}
        {hasAccount && accountModels.isError && (
          <div className="mt-1.5 flex items-center justify-between gap-2 text-[11px] leading-4 text-destructive">
            <p role="alert">账户模型目录暂不可用，额度信息不受影响。</p>
            <button
              type="button"
              aria-label="重试账户模型"
              onClick={() => void accountModels.refetch()}
              disabled={accountModels.isFetching}
              className="shrink-0 rounded border border-destructive/30 px-1.5 py-0.5 font-medium"
            >
              重试
            </button>
          </div>
        )}
        {hasAccount &&
          accountModels.isSuccess &&
          !accountModels.isFetching &&
          subscriptionOptions.length === 0 && (
            <p
              role="status"
              className="mt-1.5 text-[11px] leading-4 text-muted-foreground"
            >
              账户已连接，但尚未返回可用模型。请在模型设置中检查账户。
            </p>
          )}
        {isLoading && (
          <p role="status" className="mt-1.5 text-[11px] leading-4">
            正在加载模型…
          </p>
        )}
        {error && (
          <div className="mt-1.5 flex items-center justify-between gap-2 text-[11px] leading-4 text-destructive">
            <p role="alert">模型目录暂不可用，请检查连接后重试。</p>
            <button
              type="button"
              onClick={() => void refetchModels()}
              disabled={isLoading}
              className="shrink-0 rounded border border-destructive/30 px-1.5 py-0.5 font-medium hover:bg-destructive/10 disabled:cursor-wait disabled:opacity-60"
            >
              重试
            </button>
          </div>
        )}
        {syncCoderModel.isError && (
          <p
            role="alert"
            className="mt-1.5 text-[11px] leading-4 text-destructive"
          >
            模型同步失败，请重试。
          </p>
        )}
        <div className="mt-3 border-t pt-2.5">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-medium">Codex 用量</h3>
            <button
              type="button"
              aria-label="刷新用量"
              disabled={
                authLoading ||
                account.isFetching ||
                limits.isFetching ||
                usage.isFetching
              }
              onClick={async () => {
                const result = await account.refetch();
                if (
                  !result.isError &&
                  result.data?.account?.type === "chatgpt"
                ) {
                  void limits.refetch();
                  void usage.refetch();
                }
              }}
              className="rounded p-0.5 hover:bg-muted"
            >
              <RefreshCwIcon className="size-3" />
            </button>
          </div>
          <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
            账户共享额度
          </p>
          {!hasUsage ? (
            <p
              role="status"
              className="mt-2 text-[11px] leading-4 text-muted-foreground"
            >
              {authLoading || account.isLoading
                ? "正在检查账户…"
                : account.isError
                  ? "账户连接暂不可用，请刷新重试。"
                  : account.data?.account?.type === "apiKey"
                    ? "API Key 账户不提供订阅额度，请到服务商查看用量与余额。"
                    : "尚未连接 Codex 账户，可在模型设置中登录。"}
            </p>
          ) : (
            <>
              <p className="mt-2 text-xs">
                累计使用：
                {usage.isError
                  ? "暂不可用"
                  : usage.isLoading
                    ? "加载中…"
                    : typeof usage.data?.summary.lifetime_tokens === "number" &&
                        Number.isFinite(usage.data.summary.lifetime_tokens) &&
                        usage.data.summary.lifetime_tokens >= 0
                      ? `${usage.data.summary.lifetime_tokens.toLocaleString()} tokens`
                      : "服务未提供"}
              </p>
              {limits.isError ? (
                <p
                  role="status"
                  className="mt-1.5 text-[11px] leading-4 text-muted-foreground"
                >
                  额度暂不可用，请检查账户连接后刷新。
                </p>
              ) : limits.isLoading ? (
                <p className="mt-1.5 text-[11px] leading-4">正在加载额度…</p>
              ) : !limits.data?.buckets.length ? (
                <p className="mt-1.5 text-[11px] leading-4 text-muted-foreground">
                  服务未提供额度；其他模型的余额请到服务商查看。
                </p>
              ) : (
                limits.data.buckets.map((bucket, index) => (
                  <div key={index} className="mt-2 space-y-2">
                    <p className="text-[11px] font-medium leading-4">
                      {bucket.limit_name || "账户额度"}
                    </p>
                    {[bucket.primary, bucket.secondary].map((window, index) => {
                      if (!window) return null;
                      if (!Number.isFinite(window.used_percent))
                        return (
                          <p
                            key={index}
                            className="text-[11px] text-muted-foreground"
                          >
                            服务未提供该窗口的有效额度。
                          </p>
                        );
                      const used = Math.min(
                        100,
                        Math.max(0, window.used_percent),
                      );
                      const duration = window.window_duration_mins;
                      const durationLabel = quotaWindowLabel(duration);
                      return (
                        <div
                          key={index}
                          className="space-y-1 text-[11px] leading-4"
                        >
                          <div className="flex justify-between">
                            <span>{durationLabel}额度</span>
                            <span>
                              已用 {used}% · 剩余 {100 - used}%
                            </span>
                          </div>
                          <progress
                            aria-label={`${bucket.limit_name || "账户"} ${durationLabel}已用额度`}
                            max={100}
                            value={used}
                            className="h-1 w-full accent-blue-500"
                          />
                          {Number.isFinite(window.resets_at) &&
                            window.resets_at > 0 &&
                            !Number.isNaN(
                              new Date(window.resets_at * 1000).getTime(),
                            ) && (
                              <p className="text-[10px] leading-4 text-muted-foreground">
                                重置于{" "}
                                {new Date(
                                  window.resets_at * 1000,
                                ).toLocaleString("zh-CN")}
                              </p>
                            )}
                        </div>
                      );
                    })}
                  </div>
                ))
              )}
            </>
          )}
        </div>
        <button
          type="button"
          onClick={() => {
            setOpen(false);
            onOpenSettings();
          }}
          className="mac-model-status-action mt-3 w-full rounded-md bg-muted px-3 py-1.5 text-xs font-medium hover:bg-muted/70"
        >
          打开模型设置…
        </button>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
