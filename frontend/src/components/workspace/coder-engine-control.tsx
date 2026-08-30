import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ClipboardIcon,
  ExternalLinkIcon,
  KeyRoundIcon,
  Loader2Icon,
  LogOutIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
  UserRoundIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  cancelCoderLogin,
  coderQueryKeys,
  getCoderAccount,
  getCoderApps,
  getCoderModelProfile,
  getCoderModels,
  getCoderRateLimits,
  getCoderUsage,
  logoutCoderAccount,
  startCoderLogin,
  updateCoderApps,
  updateCoderModelProfile,
  type CoderLoginResult,
  type CoderLoginType,
  type CoderModelProfile,
  type UpdateCoderModelProfile,
} from "@/core/coder/api";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/AuthProvider";
import type { ReasoningEffort } from "@/core/threads";
import {
  ModelContextSetting,
  type PickerModel,
} from "@/components/workspace/model-picker";

const COPY = {
  zh: {
    triggerFallback: "Coder 模型",
    followSystem: "跟随系统模型",
    followSystemShort: "系统",
    followSystemDescription:
      "沿用兼容的 Echo 系统模型配置；凭据始终由本机后端安全管理。",
    accountMode: "使用 ChatGPT / Codex",
    accountModeShort: "Codex 账号",
    subscriptionModeShort: "ChatGPT 订阅",
    accountModeDescription:
      "使用你的 ChatGPT 订阅或单独提交的 OpenAI API Key。",
    loading: "正在读取 Coder 配置…",
    loadFailed: "暂时无法读取 Coder 模型配置。",
    retry: "重试",
    compatible: "可由 Codex 引擎运行",
    incompatible: "当前系统模型与 Codex 不兼容",
    provider: "Provider",
    effectiveModel: "实际模型",
    systemModel: "系统模型",
    title: "Coder 引擎",
    subtitle:
      "Coder 是普通角色；人设、技能和小队规则由 Echo 管理，代码执行由 Codex 引擎完成。",
    sourceTitle: "模型来源",
    systemSource: "Echo 模型",
    subscriptionSource: "ChatGPT 订阅",
    apiKeySource: "OpenAI API Key（按量）",
    systemDefault: "跟随系统默认",
    systemDefaultHint: "使用系统当前配置",
    smartRoutingHint: "按任务智能选择",
    systemOrchestratorHint: "当前是编排模型，请在下方选择实际模型",
    signInToChoose: "登录后选择 Codex 模型",
    reasoningShort: "推理等级",
    reasoningAdaptive: "当前模型自动控制",
    connected: "已连接",
    notConnected: "未连接",
    pending: "等待授权",
    connectChatGPT: "登录 ChatGPT",
    connectDevice: "使用设备码",
    connectApiKey: "使用 API Key",
    browserLoginHint:
      "授权页会直接在系统浏览器打开，授权地址不会写入本地存储。",
    deviceCode: "设备码",
    openAuthorization: "打开授权页面",
    cancelLogin: "取消授权",
    copyCode: "复制设备码",
    copied: "已复制",
    account: "Codex 账号",
    apiKeyLabel: "OpenAI API Key",
    apiKeyPlaceholder: "sk-…（只提交给本机后端）",
    apiKeyHint: "Key 不会进入 URL、localStorage、角色配置或日志。",
    submitApiKey: "连接 API Key",
    logout: "断开连接",
    model: "Codex 模型",
    modelDefault: "使用 Codex 默认模型",
    modelUnavailable: "连接账号后加载可用模型",
    reasoning: "推理强度",
    reasoningDefault: "跟随模型默认",
    allowance: "使用额度",
    remaining: "剩余",
    resetsAt: "重置时间",
    lifetimeTokens: "累计 Token",
    peakDailyTokens: "单日峰值",
    resetCredits: "可用额度重置",
    usageUnavailable: "此登录方式不提供 ChatGPT 账户用量。",
    connectors: "OpenAI Connectors",
    connectorsHint: "仅启用你明确选择的连接；调用仍需经过 Echo 审批。",
    connectorUnavailable: "当前账号没有可访问的 Connector。",
    accountDetails: "Connector 与用量",
    saved: "已更新 Coder 模型配置",
    loginStarted: "请在授权页面完成登录",
    loginComplete: "Codex 账号已连接",
    loginFailed: "Codex 授权未完成",
    loginCancelled: "已取消登录",
    apiKeyConnected: "API Key 已安全连接",
    accountSummary: (email: string, plan: string) =>
      [email, plan].filter(Boolean).join(" · ") || "Codex 账号",
    openSettings: "管理登录与模型",
    activeSummary: (model: string) => `当前通过 Codex 引擎运行 ${model}`,
    technicalDetails: "运行详情",
  },
  en: {
    triggerFallback: "Coder model",
    followSystem: "Follow system model",
    followSystemShort: "System",
    followSystemDescription:
      "Use the compatible Echo system model configuration. Credentials stay managed by the local backend.",
    accountMode: "Use ChatGPT / Codex",
    accountModeShort: "Codex account",
    subscriptionModeShort: "ChatGPT subscription",
    accountModeDescription:
      "Use your ChatGPT subscription or a separately submitted OpenAI API key.",
    loading: "Loading Coder configuration…",
    loadFailed: "The Coder model configuration is unavailable.",
    retry: "Retry",
    compatible: "Compatible with the Codex engine",
    incompatible: "The system model is not compatible with Codex",
    provider: "Provider",
    effectiveModel: "Effective model",
    systemModel: "System model",
    title: "Coder engine",
    subtitle:
      "Coder remains a regular role. Echo owns its persona, skills, and team rules; Codex runs the coding work.",
    sourceTitle: "Model source",
    systemSource: "Echo models",
    subscriptionSource: "ChatGPT subscription",
    apiKeySource: "OpenAI API key (metered)",
    systemDefault: "Follow system default",
    systemDefaultHint: "Use the current system configuration",
    smartRoutingHint: "Choose intelligently per task",
    systemOrchestratorHint:
      "The current model is an orchestrator; choose an executable model below",
    signInToChoose: "Sign in to choose Codex models",
    reasoningShort: "Reasoning effort",
    reasoningAdaptive: "Controlled automatically by this model",
    connected: "Connected",
    notConnected: "Not connected",
    pending: "Authorization pending",
    connectChatGPT: "Sign in with ChatGPT",
    connectDevice: "Use device code",
    connectApiKey: "Use API key",
    browserLoginHint:
      "Authorization opens directly in your system browser. The URL is never saved in local storage.",
    deviceCode: "Device code",
    openAuthorization: "Open authorization page",
    cancelLogin: "Cancel authorization",
    copyCode: "Copy device code",
    copied: "Copied",
    account: "Codex account",
    apiKeyLabel: "OpenAI API key",
    apiKeyPlaceholder: "sk-… (sent only to the local backend)",
    apiKeyHint:
      "The key is never placed in a URL, localStorage, role profile, or log.",
    submitApiKey: "Connect API key",
    logout: "Disconnect",
    model: "Codex model",
    modelDefault: "Use Codex default model",
    modelUnavailable: "Connect an account to load available models",
    reasoning: "Reasoning effort",
    reasoningDefault: "Use model default",
    allowance: "Usage allowance",
    remaining: "remaining",
    resetsAt: "Resets",
    lifetimeTokens: "Lifetime tokens",
    peakDailyTokens: "Peak daily tokens",
    resetCredits: "Rate-limit resets",
    usageUnavailable:
      "This login method does not expose ChatGPT account usage.",
    connectors: "OpenAI connectors",
    connectorsHint:
      "Only explicitly selected connections are exposed. Calls still require Echo approval.",
    connectorUnavailable:
      "No accessible connectors are available for this account.",
    accountDetails: "Connectors and usage",
    saved: "Coder model configuration updated",
    loginStarted: "Complete sign-in in the authorization page",
    loginComplete: "Codex account connected",
    loginFailed: "Codex authorization did not complete",
    loginCancelled: "Sign-in cancelled",
    apiKeyConnected: "API key connected securely",
    accountSummary: (email: string, plan: string) =>
      [email, plan].filter(Boolean).join(" · ") || "Codex account",
    openSettings: "Manage sign-in and models",
    activeSummary: (model: string) =>
      `Currently running ${model} with the Codex engine`,
    technicalDetails: "Runtime details",
  },
};

function copyForLocale(locale: string) {
  return (locale || "en").toLowerCase().startsWith("zh") ? COPY.zh : COPY.en;
}

async function openSensitiveAuthorizationUrl(url: string): Promise<boolean> {
  // Do not use the regular in-app URL router here: it persists its navigation
  // handoff in localStorage, while OAuth URLs can contain one-time state.
  if (!/^https?:\/\//i.test(url)) return false;
  if (window.echo?.app?.openExternal) {
    await window.echo.app.openExternal(url);
    return true;
  }
  const opened = window.open(url, "_blank", "noopener,noreferrer");
  if (opened) opened.opener = null;
  return Boolean(opened);
}

function profileLabel(
  profile: CoderModelProfile | undefined,
  copy: typeof COPY.en,
) {
  if (!profile) return copy.triggerFallback;
  const source =
    profile.source === "follow_system"
      ? copy.followSystemShort
      : copy.accountModeShort;
  return profile.effective_model
    ? `${source} · ${profile.effective_model}`
    : source;
}

function compactProfileLabel(
  profile: CoderModelProfile | undefined,
  copy: typeof COPY.en,
) {
  if (!profile) return copy.triggerFallback;
  return (
    profile.effective_model ||
    (profile.source === "follow_system"
      ? copy.followSystemShort
      : copy.accountModeShort)
  );
}

const DEFAULT_REASONING_EFFORTS: ReasoningEffort[] = [
  "off",
  "low",
  "medium",
  "high",
  "xhigh",
];

function reasoningLabel(effort: ReasoningEffort, locale: string) {
  const zh = locale.toLowerCase().startsWith("zh");
  const labels: Record<ReasoningEffort, [string, string]> = {
    off: ["关闭", "Off"],
    minimal: ["极低", "Minimal"],
    low: ["低", "Low"],
    medium: ["中", "Medium"],
    high: ["高", "High"],
    xhigh: ["极高", "XHigh"],
    max: ["最大", "Max"],
  };
  return labels[effort][zh ? 0 : 1];
}

function pickerModelValue(model: PickerModel) {
  return model.selection_id || model.entry_id || model.name;
}

function systemModelFamilyKey(model: PickerModel) {
  if (model.entry_id && model.model) {
    return `${model.entry_id}\u0000${model.model}`;
  }
  return model.name.replace(/::1m$/, "");
}

function modelMatches(model: PickerModel, value: string | null | undefined) {
  if (!value) return false;
  return [
    model.selection_id,
    model.entry_id,
    model.name,
    model.model,
    model.id,
  ].includes(value);
}

function isCoderSystemModel(model: PickerModel) {
  const identifiers = [
    model.name,
    model.model,
    model.entry_id,
    pickerModelValue(model),
  ]
    .filter((value): value is string => Boolean(value))
    .map((value) => value.trim().toLowerCase());
  return !identifiers.some(
    (value) => value === "mix" || value === "echo-mix",
  );
}

function isSystemOrchestratorModel(value: string | null | undefined) {
  const normalized = value?.trim().toLowerCase();
  return normalized === "mix" || normalized === "echo-mix";
}

function ProfileCompatibility({
  profile,
  compact = false,
}: {
  profile: CoderModelProfile;
  compact?: boolean;
}) {
  const { locale } = useI18n();
  const copy = copyForLocale(locale);
  return (
    <div
      className={cn(
        "flex gap-2 rounded-lg border px-3 py-2 text-xs",
        profile.compatible
          ? "border-success/20 bg-success/[0.06] text-success"
          : "border-warning/25 bg-warning/[0.06] text-warning",
      )}
    >
      {profile.compatible ? (
        <CheckCircle2Icon className="mt-0.5 size-3.5 shrink-0" />
      ) : (
        <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
      )}
      <div className="min-w-0">
        <div className="font-medium">
          {profile.compatible ? copy.compatible : copy.incompatible}
        </div>
        {!compact && profile.compatibility_reason ? (
          <div className="mt-0.5 break-words text-current/80">
            {profile.compatibility_reason}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function UsageStat({
  label,
  value,
  locale,
}: {
  label: string;
  value: number | null | undefined;
  locale: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-background/60 p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 font-mono text-sm font-medium">
        {typeof value === "number" ? value.toLocaleString(locale) : "—"}
      </div>
    </div>
  );
}

export function CoderEngineControl({
  systemModels = [],
  disabled = false,
  executionEngine = "codex",
  value,
  onChange,
  onEffectiveModelChange,
  reasoningEffort,
  onReasoningEffortChange,
}: {
  systemModels?: PickerModel[];
  disabled?: boolean;
  executionEngine?: "echo" | "codex";
  value?: string;
  onChange?: (model: string) => void;
  onEffectiveModelChange?: (model: string) => void;
  reasoningEffort?: ReasoningEffort;
  onReasoningEffortChange?: (effort: ReasoningEffort) => void;
}) {
  const { locale } = useI18n();
  const copy = copyForLocale(locale);
  const queryClient = useQueryClient();
  const { user, isLoading: authLoading } = useAuth();
  const principalKey = user?.actor_id || user?.user_id || "local";
  const queryKeys = useMemo(() => coderQueryKeys(principalKey), [principalKey]);
  const [open, setOpen] = useState(false);
  const [viewSource, setViewSource] = useState<
    "follow_system" | "codex_account"
  >("follow_system");
  const profileQuery = useQuery({
    queryKey: queryKeys.profile,
    queryFn: ({ signal }) => getCoderModelProfile(signal),
    enabled: !authLoading,
    staleTime: 30_000,
  });
  const accountQuery = useQuery({
    queryKey: queryKeys.account,
    queryFn: ({ signal }) => getCoderAccount(signal),
    enabled: !authLoading,
    staleTime: 10_000,
  });
  const modelsQuery = useQuery({
    queryKey: queryKeys.models,
    queryFn: ({ signal }) => getCoderModels(signal),
    enabled: Boolean(accountQuery.data?.account),
    staleTime: 60_000,
  });
  const saveProfile = useMutation({
    mutationFn: updateCoderModelProfile,
    onMutate: async (input: UpdateCoderModelProfile) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.profile });
      const previous = queryClient.getQueryData<CoderModelProfile>(
        queryKeys.profile,
      );
      if (!previous) return { previous };

      const reasoningEffort =
        input.reasoning_effort !== undefined
          ? input.reasoning_effort
          : previous.source === input.source
            ? previous.reasoning_effort
            : null;
      if (input.source === "codex_account") {
        const model =
          input.model ??
          (previous.source === "codex_account"
            ? previous.selected_model
            : null);
        queryClient.setQueryData<CoderModelProfile>(queryKeys.profile, {
          ...previous,
          source: "codex_account",
          selected_model: model,
          effective_model: model,
          reasoning_effort: reasoningEffort,
          model_source: model ? "role" : "codex_default",
          compatible: true,
          compatibility_reason: null,
          provider: "openai",
          proxy_required: false,
        });
      } else {
        const selected = input.model ?? null;
        const selectedEntry = selected
          ? systemModels.find((model) => modelMatches(model, selected))
          : undefined;
        queryClient.setQueryData<CoderModelProfile>(queryKeys.profile, {
          ...previous,
          source: "follow_system",
          selected_model: selected,
          effective_model:
            selectedEntry?.model ||
            selectedEntry?.name ||
            previous.system_model,
          reasoning_effort: reasoningEffort,
          model_source: selected ? "role" : "system",
        });
      }
      return { previous };
    },
    onSuccess: (profile) => {
      queryClient.setQueryData(queryKeys.profile, profile);
    },
    onError: (_error, _input, context) => {
      if (context?.previous) {
        queryClient.setQueryData(queryKeys.profile, context.previous);
      }
    },
  });
  const profile = profileQuery.data;
  const nativeKernel = executionEngine === "echo";
  const pendingNativeModelRef = useRef(value || "auto");
  useEffect(() => {
    pendingNativeModelRef.current = value || "auto";
  }, [value]);
  const nativeAccountModel = nativeKernel
    ? String(value || "").replace(/^chatgpt[/:]/i, "")
    : "";
  const nativeAccountSelected =
    nativeKernel && /^chatgpt[/:]/i.test(String(value || ""));
  const chatGPTSubscriptionConnected =
    accountQuery.data?.account?.type === "chatgpt";
  const visibleAccountSource =
    accountQuery.data?.account?.type === "apiKey"
      ? copy.apiKeySource
      : copy.subscriptionSource;
  const visibleSystemModels = useMemo(() => {
    const seen = new Set<string>();
    return systemModels.filter((model) => {
      if (!isCoderSystemModel(model)) return false;
      if (model.context_profile === "1m") return false;
      const key = pickerModelValue(model);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [systemModels]);
  const activeSystemModel = useMemo(
    () =>
      (nativeKernel
        ? systemModels.find((model) => modelMatches(model, value))
        : profile?.source === "follow_system"
          ? systemModels.find((model) =>
              modelMatches(
                model,
                profile.selected_model || profile.effective_model,
              ),
            )
          : undefined) ??
      systemModels.find((model) => modelMatches(model, profile?.system_model)),
    [
      profile?.effective_model,
      profile?.selected_model,
      profile?.source,
      profile?.system_model,
      systemModels,
      nativeKernel,
      value,
    ],
  );
  const visibleSystemModelName =
    activeSystemModel?.display_name ||
    activeSystemModel?.name ||
    activeSystemModel?.model ||
    value ||
    "auto";
  const visibleSystemSource = activeSystemModel?.source_display_name;
  const fullProfileLabel = nativeKernel
    ? nativeAccountSelected
      ? `${copy.subscriptionModeShort} · ${nativeAccountModel}`
      : `${visibleSystemSource || copy.followSystemShort} · ${visibleSystemModelName}`
    : profile?.source === "codex_account" && profile.effective_model
      ? `${visibleAccountSource} · ${profile.effective_model}`
      : profileLabel(profile, copy);
  const activeCodexModel = useMemo(
    () =>
      nativeKernel
        ? modelsQuery.data?.models.find(
            (model) => model.id === nativeAccountModel,
          )
        : profile?.source === "codex_account"
          ? modelsQuery.data?.models.find(
              (model) => model.id === profile?.effective_model,
            )
          : undefined,
    [
      modelsQuery.data?.models,
      nativeAccountModel,
      nativeKernel,
      profile?.effective_model,
      profile?.source,
    ],
  );
  const offeredEfforts = useMemo(() => {
    const raw =
      viewSource === "codex_account"
        ? activeCodexModel?.reasoning_efforts
        : activeSystemModel?.reasoning_efforts;
    if (Array.isArray(raw)) {
      return raw.filter((effort): effort is ReasoningEffort =>
        ["off", "minimal", "low", "medium", "high", "xhigh", "max"].includes(
          effort,
        ),
      );
    }
    return DEFAULT_REASONING_EFFORTS;
  }, [
    activeCodexModel?.reasoning_efforts,
    activeSystemModel?.reasoning_efforts,
    viewSource,
  ]);

  const changeReasoningEffort = (effort: ReasoningEffort) => {
    if (nativeKernel) {
      onReasoningEffortChange?.(effort);
      return;
    }
    if (!profile) return;
    saveProfile.mutate({
      source: viewSource,
      ...(profile.source === viewSource && profile.selected_model
        ? { model: profile.selected_model }
        : {}),
      reasoning_effort: effort,
    });
  };

  const selectSystemModel = (model?: string) => {
    if (nativeKernel) {
      const nextValue = model || "auto";
      const pendingValue = pendingNativeModelRef.current;
      const pendingSystemModel = model
        ? systemModels.find((candidate) => modelMatches(candidate, model))
        : undefined;
      const alreadySelected = model
        ? !/^chatgpt[/:]/i.test(pendingValue) &&
          Boolean(
            pendingSystemModel &&
            modelMatches(pendingSystemModel, pendingValue),
          )
        : !/^chatgpt[/:]/i.test(pendingValue) &&
          (!pendingValue ||
            pendingValue === "auto" ||
            pendingValue === "default");
      if (alreadySelected) return;
      pendingNativeModelRef.current = nextValue;
      onChange?.(nextValue);
      onEffectiveModelChange?.(
        pendingSystemModel?.display_name ||
          pendingSystemModel?.model ||
          model ||
          profile?.system_model ||
          copy.systemDefault,
      );
      return;
    }
    const alreadySelected = model
      ? profile?.source === "follow_system" &&
        profile.model_source === "role" &&
        Boolean(activeSystemModel && modelMatches(activeSystemModel, model))
      : profile?.source === "follow_system" &&
        profile.model_source === "system";
    if (alreadySelected) return;
    saveProfile.mutate(
      {
        source: "follow_system",
        ...(model ? { model } : {}),
        reasoning_effort: profile?.reasoning_effort,
      },
      {
        onSuccess: (nextProfile) =>
          onEffectiveModelChange?.(
            nextProfile.effective_model || model || copy.systemDefault,
          ),
      },
    );
  };

  const selectAccountModel = (model: string) => {
    if (nativeKernel) {
      const nextValue = `chatgpt/${model}`;
      if (pendingNativeModelRef.current === nextValue) return;
      pendingNativeModelRef.current = nextValue;
      onChange?.(nextValue);
      onEffectiveModelChange?.(model);
      return;
    }
    if (
      profile?.source === "codex_account" &&
      profile.effective_model === model
    ) {
      return;
    }
    saveProfile.mutate(
      { source: "codex_account", model },
      {
        onSuccess: (nextProfile) =>
          onEffectiveModelChange?.(nextProfile.effective_model || model),
      },
    );
  };

  const controlPending = !nativeKernel && saveProfile.isPending;
  const compactLabel = nativeKernel
    ? nativeAccountSelected
      ? nativeAccountModel
      : visibleSystemModelName
    : compactProfileLabel(profile, copy);
  const selectedReasoningEffort = nativeKernel
    ? reasoningEffort
    : profile?.reasoning_effort;
  const activeSystemSelectionValue = nativeKernel
    ? value
    : profile?.source === "follow_system"
      ? profile.selected_model || profile.effective_model
      : profile?.system_model;

  const openModelSettings = () => {
    setOpen(false);
    window.dispatchEvent(
      new CustomEvent("echo:open-settings", { detail: { tab: "models" } }),
    );
  };

  return (
    <DropdownMenu
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (nextOpen) {
          setViewSource(
            nativeKernel
              ? nativeAccountSelected
                ? "codex_account"
                : "follow_system"
              : profile?.source || "follow_system",
          );
        }
      }}
    >
      <Tooltip delayDuration={80}>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              data-testid="coder-engine-trigger"
              aria-label={fullProfileLabel}
              className="inline-flex min-w-0 max-w-32 items-center gap-1 rounded-lg border border-transparent bg-transparent px-2 py-1 text-xs text-muted-foreground outline-none transition hover:border-border-default hover:bg-muted/60 hover:text-foreground data-[state=open]:bg-muted data-[state=open]:text-foreground"
              disabled={disabled}
            >
              {profileQuery.isLoading ? (
                <Loader2Icon className="size-3 animate-spin" />
              ) : null}
              <span className="truncate">{compactLabel}</span>
              <ChevronDownIcon className="size-3 opacity-60" />
            </button>
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent side="top" sideOffset={6}>
          {fullProfileLabel}
        </TooltipContent>
      </Tooltip>
      <DropdownMenuContent
        align="end"
        side="top"
        sideOffset={6}
        className="w-72 p-1.5"
      >
        {profileQuery.isError ? (
          <div className="space-y-2 rounded-lg border border-destructive/20 bg-destructive/[0.04] p-2 text-xs text-destructive">
            <div>{copy.loadFailed}</div>
            <button
              type="button"
              className="font-medium hover:underline"
              onClick={() => void profileQuery.refetch()}
            >
              {copy.retry}
            </button>
          </div>
        ) : profile ? (
          <>
            <div className="grid grid-cols-2 gap-0.5 rounded-lg bg-muted/45 p-0.5">
              <button
                type="button"
                aria-pressed={viewSource === "follow_system"}
                disabled={controlPending}
                onClick={() => setViewSource("follow_system")}
                className={cn(
                  "h-7 rounded-md px-2 text-xs outline-none transition focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring/40",
                  viewSource === "follow_system"
                    ? "bg-background font-medium text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {copy.systemSource}
              </button>
              <button
                type="button"
                aria-pressed={viewSource === "codex_account"}
                disabled={controlPending}
                onClick={() => setViewSource("codex_account")}
                className={cn(
                  "h-7 rounded-md px-2 text-xs outline-none transition focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring/40",
                  viewSource === "codex_account"
                    ? "bg-background font-medium text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {nativeKernel ? copy.subscriptionSource : visibleAccountSource}
              </button>
            </div>

            <div className="mt-1 max-h-52 space-y-0.5 overflow-y-auto">
              {viewSource === "follow_system" ? (
                <>
                  <button
                    type="button"
                    disabled={controlPending}
                    onClick={() => selectSystemModel()}
                    className={cn(
                      "flex h-8 w-full items-center justify-between rounded-md px-2 text-left text-xs hover:bg-muted/60",
                      (nativeKernel
                        ? !nativeAccountSelected &&
                          (!value || value === "auto" || value === "default")
                        : profile.source === "follow_system" &&
                          profile.model_source === "system") &&
                        "bg-muted/70 text-foreground",
                    )}
                  >
                    <span className="truncate font-medium">
                      {locale.toLowerCase().startsWith("zh") ? "自动" : "Auto"}
                    </span>
                    <span className="ml-2 truncate text-muted-foreground">
                      {nativeKernel
                        ? copy.smartRoutingHint
                        : isSystemOrchestratorModel(profile.system_model)
                          ? copy.systemOrchestratorHint
                          : profile.system_model || copy.systemDefaultHint}
                    </span>
                  </button>
                  {visibleSystemModels.map((model, index) => {
                    const selected = nativeKernel
                      ? !nativeAccountSelected && modelMatches(model, value)
                      : profile.source === "follow_system" &&
                        profile.model_source === "role" &&
                        modelMatches(
                          model,
                          profile.selected_model || profile.effective_model,
                        );
                    const contextVariantSelected = Boolean(
                      activeSystemModel &&
                      systemModelFamilyKey(activeSystemModel) ===
                        systemModelFamilyKey(model) &&
                      (nativeKernel
                        ? !nativeAccountSelected &&
                          value !== "auto" &&
                          value !== "default"
                        : profile.source === "follow_system" &&
                          profile.model_source === "role"),
                    );
                    return (
                      <div
                        key={`${pickerModelValue(model)}:${model.id || model.model || index}:${index}`}
                        className={cn(
                          "flex h-8 w-full items-stretch rounded-md text-xs hover:bg-muted/60",
                          (selected || contextVariantSelected) &&
                            "bg-muted/70 text-foreground",
                        )}
                      >
                        <button
                          type="button"
                          disabled={controlPending}
                          onClick={() =>
                            selectSystemModel(pickerModelValue(model))
                          }
                          className="flex min-w-0 flex-1 items-center justify-between gap-2 rounded-md px-2 text-left"
                        >
                          <span className="truncate">
                            {model.display_name || model.name}
                          </span>
                          {selected || contextVariantSelected ? (
                            <CheckCircle2Icon className="size-3.5 shrink-0 text-muted-foreground" />
                          ) : null}
                        </button>
                      </div>
                    );
                  })}
                </>
              ) : accountQuery.data?.account &&
                (!nativeKernel || chatGPTSubscriptionConnected) ? (
                <>
                  {(modelsQuery.data?.models ?? []).map((model) => (
                    <button
                      key={model.id}
                      type="button"
                      disabled={controlPending}
                      className={cn(
                        "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted",
                        (nativeKernel
                          ? nativeAccountSelected &&
                            nativeAccountModel === model.id
                          : profile.source === "codex_account" &&
                            profile.effective_model === model.id) &&
                          "bg-muted/70 text-foreground",
                      )}
                      onClick={() => selectAccountModel(model.id)}
                    >
                      <span className="truncate">
                        {model.display_name || model.id}
                      </span>
                      {(
                        nativeKernel
                          ? nativeAccountSelected &&
                            nativeAccountModel === model.id
                          : profile.source === "codex_account" &&
                            profile.effective_model === model.id
                      ) ? (
                        <CheckCircle2Icon className="size-3.5 shrink-0" />
                      ) : null}
                    </button>
                  ))}
                  {modelsQuery.isLoading ? (
                    <div className="flex items-center gap-2 px-2 py-1.5 text-xs text-muted-foreground">
                      <Loader2Icon className="size-3 animate-spin" />{" "}
                      {copy.loading}
                    </div>
                  ) : null}
                </>
              ) : (
                <button
                  type="button"
                  onClick={openModelSettings}
                  className="flex h-9 w-full items-center gap-2 rounded-md px-2 text-left text-xs text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                >
                  <UserRoundIcon className="size-3.5" />
                  {copy.signInToChoose}
                </button>
              )}
            </div>

            {offeredEfforts.length > 0 ? (
              <div className="mt-1 border-t border-border-default pt-1">
                <div className="flex items-center justify-between px-1 pb-1 text-xs text-muted-foreground">
                  <span>{copy.reasoningShort}</span>
                </div>
                <div
                  className="grid gap-0.5 rounded-md bg-muted/35 p-0.5"
                  style={{
                    gridTemplateColumns: `repeat(${offeredEfforts.length}, minmax(0, 1fr))`,
                  }}
                >
                  {offeredEfforts.map((effort) => (
                    <button
                      key={effort}
                      type="button"
                      disabled={controlPending}
                      onClick={() => changeReasoningEffort(effort)}
                      className={cn(
                        "h-6 rounded px-1 text-xs transition",
                        (
                          nativeKernel
                            ? selectedReasoningEffort === effort
                            : profile.source === viewSource &&
                              profile.reasoning_effort === effort
                        )
                          ? "bg-background text-foreground shadow-sm"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {reasoningLabel(effort, locale)}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="mt-1 flex items-center justify-between border-t border-border-default px-1 pt-1.5 text-xs text-muted-foreground">
                <span>{copy.reasoningShort}</span>
                <span>{copy.reasoningAdaptive}</span>
              </div>
            )}

            {viewSource === "follow_system" && activeSystemModel ? (
              <ModelContextSetting
                models={systemModels}
                selected={activeSystemModel}
                value={activeSystemSelectionValue}
                disabled={controlPending}
                onChange={selectSystemModel}
                className="mx-1 mt-1 border-t border-border-default px-0.5 pt-1.5"
              />
            ) : null}

            {!profile.compatible ? (
              <div className="mt-1">
                <ProfileCompatibility profile={profile} compact />
              </div>
            ) : null}
          </>
        ) : (
          <div className="flex items-center gap-2 p-2 text-xs text-muted-foreground">
            <Loader2Icon className="size-3.5 animate-spin" /> {copy.loading}
          </div>
        )}
        <button
          type="button"
          onClick={openModelSettings}
          className="mt-1 flex h-7 w-full items-center justify-center gap-1.5 rounded-md border-t border-border px-2 pt-1 text-xs text-muted-foreground transition hover:text-foreground"
        >
          <KeyRoundIcon className="size-3.5" />
          {copy.openSettings}
        </button>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function CoderEngineSettings() {
  const { locale } = useI18n();
  const copy = copyForLocale(locale);
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const principalKey = user?.actor_id || user?.user_id || "local";
  const queryKeys = useMemo(() => coderQueryKeys(principalKey), [principalKey]);
  const [activeLogin, setActiveLogin] = useState<CoderLoginResult | null>(null);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [loginNotice, setLoginNotice] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [loginBusy, setLoginBusy] = useState<
    CoderLoginType | "cancel" | "logout" | null
  >(null);
  const loginStartedAtRef = useRef(0);
  const mountedRef = useRef(true);
  const activeLoginRef = useRef<CoderLoginResult | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      // Login is backend-owned and intentionally survives renderer reloads or
      // settings-route unmounts. The next instance rehydrates the opaque id;
      // only the explicit Cancel/Logout controls terminate the operation.
    };
  }, []);

  const profileQuery = useQuery({
    queryKey: queryKeys.profile,
    queryFn: ({ signal }) => getCoderModelProfile(signal),
    staleTime: 30_000,
  });
  const accountQuery = useQuery({
    queryKey: queryKeys.account,
    queryFn: ({ signal }) => getCoderAccount(signal),
    staleTime: 5_000,
  });
  const refetchAccount = accountQuery.refetch;

  useEffect(() => {
    const state = accountQuery.data;
    if (state?.login_error) setLoginError(state.login_error);
    if (state?.login_pending && state.login_id && !activeLoginRef.current) {
      // Login operations outlive a renderer reload. Rehydrate the opaque id
      // from the backend so polling and explicit cancellation remain
      // available without persisting an OAuth URL or device code locally.
      const recovered: CoderLoginResult = {
        type: "chatgpt",
        login_id: state.login_id,
      };
      activeLoginRef.current = recovered;
      loginStartedAtRef.current = 0;
      setActiveLogin(recovered);
    }
  }, [accountQuery.data]);
  const modelsQuery = useQuery({
    queryKey: queryKeys.models,
    queryFn: ({ signal }) => getCoderModels(signal),
    enabled: Boolean(accountQuery.data?.account),
    staleTime: 60_000,
  });
  const hasChatGPTUsage = accountQuery.data?.account?.type === "chatgpt";
  const rateLimitsQuery = useQuery({
    queryKey: queryKeys.rateLimits,
    queryFn: ({ signal }) => getCoderRateLimits(signal),
    enabled: hasChatGPTUsage,
    staleTime: 30_000,
    refetchInterval: hasChatGPTUsage ? 60_000 : false,
  });
  const usageQuery = useQuery({
    queryKey: queryKeys.usage,
    queryFn: ({ signal }) => getCoderUsage(signal),
    enabled: hasChatGPTUsage,
    staleTime: 5 * 60_000,
  });
  const appsQuery = useQuery({
    queryKey: queryKeys.apps,
    queryFn: ({ signal }) => getCoderApps(signal),
    enabled: hasChatGPTUsage,
    staleTime: 60_000,
  });
  const saveApps = useMutation({
    scope: { id: "coder-app-selection" },
    mutationFn: updateCoderApps,
    onSuccess: (apps) => queryClient.setQueryData(queryKeys.apps, apps),
    onError: (error) => {
      setLoginError(error instanceof Error ? error.message : String(error));
    },
  });
  const saveProfile = useMutation({
    scope: { id: "coder-model-profile" },
    mutationFn: updateCoderModelProfile,
    onSuccess: (profile) => {
      queryClient.setQueryData(queryKeys.profile, profile);
      setLoginNotice(copy.saved);
    },
    onError: (error) => {
      setLoginError(error instanceof Error ? error.message : String(error));
    },
  });

  const completeAccountLogin = useCallback(async () => {
    activeLoginRef.current = null;
    setActiveLogin(null);
    setLoginNotice(copy.loginComplete);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.models }),
      queryClient.invalidateQueries({ queryKey: queryKeys.rateLimits }),
      queryClient.invalidateQueries({ queryKey: queryKeys.usage }),
      queryClient.invalidateQueries({ queryKey: queryKeys.apps }),
    ]);
    const profile = await updateCoderModelProfile({
      source: "codex_account",
    });
    queryClient.setQueryData(queryKeys.profile, profile);
  }, [
    copy.loginComplete,
    queryClient,
    queryKeys.apps,
    queryKeys.models,
    queryKeys.profile,
    queryKeys.rateLimits,
    queryKeys.usage,
  ]);

  useEffect(() => {
    if (!activeLogin?.login_id) return;
    let stopped = false;
    const poll = async () => {
      const result = await refetchAccount();
      if (stopped) return;
      const state = result.data;
      const expected = activeLogin.type === "apiKey" ? "apiKey" : "chatgpt";
      if (
        state?.account?.type === expected &&
        !state.login_pending &&
        Date.now() - loginStartedAtRef.current > 500
      ) {
        try {
          await completeAccountLogin();
        } catch (error) {
          setLoginError(error instanceof Error ? error.message : String(error));
        }
      } else if (
        state &&
        !state.login_pending &&
        !state.account &&
        Date.now() - loginStartedAtRef.current > 500
      ) {
        // App Server reports failed/cancelled login completion by clearing the
        // pending id. Do not retain the stale renderer operation forever.
        activeLoginRef.current = null;
        setActiveLogin(null);
        setLoginError(state.login_error || copy.loginFailed);
      }
    };
    const timer = window.setInterval(() => void poll(), 1500);
    void poll();
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [activeLogin, completeAccountLogin, copy.loginFailed, refetchAccount]);

  const beginLogin = async (type: CoderLoginType, apiKey?: string) => {
    setLoginBusy(type);
    setLoginError(null);
    setLoginNotice(null);
    setCopied(false);
    try {
      const result = await startCoderLogin(type, apiKey);
      loginStartedAtRef.current = Date.now();
      if (type === "apiKey") {
        if (!mountedRef.current) return;
        await accountQuery.refetch();
        await completeAccountLogin();
        setLoginNotice(copy.apiKeyConnected);
        return;
      }
      if (!mountedRef.current) {
        // The backend operation may have committed after this renderer went
        // away. Leave it intact so a replacement renderer can rehydrate it.
        return;
      }
      activeLoginRef.current = result;
      setActiveLogin(result);
      setLoginNotice(copy.loginStarted);
      const url = result.auth_url || result.verification_url;
      if (url) await openSensitiveAuthorizationUrl(url);
    } catch (error) {
      if (mountedRef.current) {
        setLoginError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (mountedRef.current) setLoginBusy(null);
    }
  };

  const submitApiKey = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const key = String(new FormData(form).get("api_key") ?? "").trim();
    // Clear the DOM immediately. The key is never copied into React state,
    // browser storage, a URL, telemetry, or a log message.
    form.reset();
    if (key) void beginLogin("apiKey", key);
  };

  const cancelLogin = async () => {
    if (!activeLogin?.login_id) return;
    setLoginBusy("cancel");
    setLoginError(null);
    try {
      const cancelled = await cancelCoderLogin(activeLogin.login_id);
      activeLoginRef.current = null;
      setActiveLogin(null);
      if (cancelled) {
        setLoginNotice(copy.loginCancelled);
      } else {
        setLoginError(copy.loginFailed);
      }
      await accountQuery.refetch();
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : String(error));
    } finally {
      setLoginBusy(null);
    }
  };

  const logout = async () => {
    setLoginBusy("logout");
    setLoginError(null);
    try {
      await logoutCoderAccount();
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : String(error));
    } finally {
      // Logout resets the model preference server-side before touching the
      // account. Always discard optimistic/stale account state, even when the
      // remote logout operation reports a recoverable failure.
      activeLoginRef.current = null;
      setActiveLogin(null);
      queryClient.removeQueries({ queryKey: queryKeys.models });
      queryClient.removeQueries({ queryKey: queryKeys.rateLimits });
      queryClient.removeQueries({ queryKey: queryKeys.usage });
      queryClient.removeQueries({ queryKey: queryKeys.apps });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.account }),
        queryClient.invalidateQueries({ queryKey: queryKeys.profile }),
      ]);
      setLoginBusy(null);
    }
  };

  const profile = profileQuery.data;
  const account = accountQuery.data?.account;
  const currentModel =
    modelsQuery.data?.models.find(
      (model) => model.id === profile?.effective_model,
    ) ??
    modelsQuery.data?.models.find((model) => model.is_default) ??
    null;
  const reasoningOptions = currentModel?.reasoning_efforts ?? [];
  const busy = saveProfile.isPending || loginBusy !== null;
  const loginPending = Boolean(accountQuery.data?.login_pending || activeLogin);

  return (
    <section
      data-testid="coder-engine-settings"
      className="rounded-lg border border-border bg-card/45 p-3"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold">{copy.title}</h2>
            <span
              className={cn(
                "rounded-md px-2 py-0.5 text-xs font-medium",
                account
                  ? "bg-success/10 text-success"
                  : loginPending
                    ? "bg-info/10 text-info"
                    : "bg-muted text-muted-foreground",
              )}
            >
              {account
                ? copy.connected
                : loginPending
                  ? copy.pending
                  : copy.notConnected}
            </span>
          </div>
          <p className="mt-0.5 max-w-3xl text-xs leading-5 text-muted-foreground">
            {copy.subtitle}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant={
            profileQuery.isError || accountQuery.isError ? "outline" : "ghost"
          }
          aria-label={copy.retry}
          title={copy.retry}
          onClick={() => {
            void profileQuery.refetch();
            void accountQuery.refetch();
            if (account) void modelsQuery.refetch();
            if (hasChatGPTUsage) {
              void rateLimitsQuery.refetch();
              void usageQuery.refetch();
              void appsQuery.refetch();
            }
          }}
          disabled={profileQuery.isFetching || accountQuery.isFetching}
        >
          <RefreshCwIcon
            className={cn(
              "size-3.5",
              (profileQuery.isFetching || accountQuery.isFetching) &&
                "animate-spin",
            )}
          />
          {profileQuery.isError || accountQuery.isError ? copy.retry : null}
        </Button>
      </div>

      {profileQuery.isLoading || accountQuery.isLoading ? (
        <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2Icon className="size-4 animate-spin" /> {copy.loading}
        </div>
      ) : profileQuery.isError || accountQuery.isError || !profile ? (
        <div
          role="alert"
          className="mt-4 rounded-lg border border-destructive/20 bg-destructive/[0.04] p-3 text-sm text-destructive"
        >
          {copy.loadFailed}
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          <div>
            <div className="mb-1.5 text-xs font-medium text-muted-foreground">
              {copy.sourceTitle}
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              <button
                type="button"
                aria-pressed={profile.source === "follow_system"}
                disabled={busy}
                className={cn(
                  "flex min-h-11 items-center gap-2.5 rounded-lg border px-3 py-2 text-left transition",
                  profile.source === "follow_system"
                    ? "border-primary/40 bg-primary/[0.07] ring-1 ring-primary/15"
                    : "border-border hover:border-border-strong hover:bg-muted/30",
                )}
                onClick={() => saveProfile.mutate({ source: "follow_system" })}
              >
                <ShieldCheckIcon className="size-4 shrink-0 text-primary" />
                <span className="min-w-0">
                  <span className="block text-sm font-medium">
                    {copy.followSystem}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {copy.followSystemDescription}
                  </span>
                </span>
              </button>
              <button
                type="button"
                aria-pressed={profile.source === "codex_account"}
                disabled={busy || !account}
                className={cn(
                  "flex min-h-11 items-center gap-2.5 rounded-lg border px-3 py-2 text-left transition disabled:cursor-not-allowed disabled:opacity-60",
                  profile.source === "codex_account"
                    ? "border-primary/40 bg-primary/[0.07] ring-1 ring-primary/15"
                    : "border-border hover:border-border-strong hover:bg-muted/30",
                )}
                onClick={() => saveProfile.mutate({ source: "codex_account" })}
              >
                <UserRoundIcon className="size-4 shrink-0 text-primary" />
                <span className="min-w-0">
                  <span className="block text-sm font-medium">
                    {copy.accountMode}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {copy.accountModeDescription}
                  </span>
                </span>
              </button>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-background/45 px-3 py-2 text-xs">
            <span className="min-w-0 flex-1 truncate font-medium text-foreground">
              {copy.activeSummary(profile.effective_model || "—")}
            </span>
            <span
              className={cn(
                "ml-auto inline-flex items-center gap-1.5 font-medium",
                profile.compatible ? "text-success" : "text-warning",
              )}
            >
              {profile.compatible ? (
                <CheckCircle2Icon className="size-3.5" />
              ) : (
                <AlertTriangleIcon className="size-3.5" />
              )}
              {profile.compatible ? copy.compatible : copy.incompatible}
            </span>
            <details className="basis-full text-muted-foreground">
              <summary className="cursor-pointer select-none text-xs hover:text-foreground">
                {copy.technicalDetails}
              </summary>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 border-t border-border pt-2">
                <span>
                  {copy.systemModel}{" "}
                  <code className="text-foreground/80">
                    {profile.system_model || "—"}
                  </code>
                </span>
                <span>
                  {copy.provider}{" "}
                  <code className="text-foreground/80">
                    {profile.provider || "—"}
                  </code>
                </span>
              </div>
            </details>
          </div>
          {!profile.compatible && profile.compatibility_reason ? (
            <p className="text-xs text-warning">
              {profile.compatibility_reason}
            </p>
          ) : null}

          {account ? (
            <div className="space-y-3">
              <div className="grid gap-3 rounded-lg bg-muted/25 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <CheckCircle2Icon className="size-4 text-success" />
                    {copy.account}
                  </div>
                  <div className="mt-1 truncate text-xs text-muted-foreground">
                    {copy.accountSummary(
                      account.email || "",
                      account.plan_type || account.type,
                    )}
                  </div>
                </div>
                <label className="space-y-1.5 text-xs text-muted-foreground">
                  <span>{copy.model}</span>
                  <Select
                    value={
                      profile.source === "codex_account"
                        ? profile.effective_model || "__default__"
                        : "__default__"
                    }
                    disabled={busy || modelsQuery.isLoading}
                    onValueChange={(model) =>
                      saveProfile.mutate({
                        source: "codex_account",
                        ...(model === "__default__" ? {} : { model }),
                      })
                    }
                  >
                    <SelectTrigger className="w-full" aria-label={copy.model}>
                      <SelectValue placeholder={copy.modelDefault} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__default__">
                        {copy.modelDefault}
                      </SelectItem>
                      {(modelsQuery.data?.models ?? []).map((model) => (
                        <SelectItem key={model.id} value={model.id}>
                          {model.display_name || model.id}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </label>
                <label className="space-y-1.5 text-xs text-muted-foreground">
                  <span>{copy.reasoning}</span>
                  <Select
                    value={profile.reasoning_effort || "__default__"}
                    disabled={busy || reasoningOptions.length === 0}
                    onValueChange={(reasoning) =>
                      saveProfile.mutate({
                        source: "codex_account",
                        model: profile.effective_model || undefined,
                        reasoning_effort:
                          reasoning === "__default__" ? null : reasoning,
                      })
                    }
                  >
                    <SelectTrigger
                      className="w-full"
                      aria-label={copy.reasoning}
                    >
                      <SelectValue placeholder={copy.reasoningDefault} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__default__">
                        {copy.reasoningDefault}
                      </SelectItem>
                      {reasoningOptions.map((reasoning) => (
                        <SelectItem key={reasoning} value={reasoning}>
                          {reasoning}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </label>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => void logout()}
                >
                  <LogOutIcon className="size-3.5" /> {copy.logout}
                </Button>
              </div>

              {hasChatGPTUsage ? (
                <details className="group rounded-lg border border-border bg-background/40 px-3 py-2">
                  <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium marker:content-none">
                    <ChevronDownIcon className="size-3.5 text-muted-foreground transition-transform group-open:rotate-180" />
                    {copy.accountDetails}
                  </summary>
                  <div className="mt-3 space-y-4 border-t border-border pt-3">
                    <div className="space-y-2">
                      <div>
                        <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                          {copy.connectors}
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {copy.connectorsHint}
                        </p>
                      </div>
                      <div className="grid gap-2 sm:grid-cols-2">
                        {(appsQuery.data?.apps ?? [])
                          .filter((app) => app.is_accessible)
                          .map((app) => (
                            <button
                              key={app.id}
                              type="button"
                              aria-pressed={app.selected}
                              disabled={saveApps.isPending}
                              className={cn(
                                "rounded-lg border p-3 text-left transition",
                                app.selected
                                  ? "border-primary/40 bg-primary/[0.07]"
                                  : "border-border hover:bg-muted/30",
                              )}
                              onClick={() => {
                                const selected = (appsQuery.data?.apps ?? [])
                                  .filter((item) =>
                                    item.id === app.id
                                      ? !item.selected
                                      : item.selected,
                                  )
                                  .map((item) => item.id);
                                saveApps.mutate(selected);
                              }}
                            >
                              <span className="flex items-center justify-between gap-2 text-sm font-medium">
                                <span className="truncate">{app.name}</span>
                                {app.selected ? (
                                  <CheckCircle2Icon className="size-4 shrink-0 text-primary" />
                                ) : null}
                              </span>
                              {app.description ? (
                                <span className="mt-1 line-clamp-2 block text-xs text-muted-foreground">
                                  {app.description}
                                </span>
                              ) : null}
                            </button>
                          ))}
                      </div>
                      {!appsQuery.isLoading &&
                      (appsQuery.data?.apps ?? []).filter(
                        (app) => app.is_accessible,
                      ).length === 0 ? (
                        <p className="text-xs text-muted-foreground">
                          {copy.connectorUnavailable}
                        </p>
                      ) : null}
                    </div>
                    <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      {copy.allowance}
                    </div>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {(rateLimitsQuery.data?.buckets ?? []).map((bucket) => {
                        const window = bucket.primary;
                        return (
                          <div
                            key={bucket.limit_id}
                            className="rounded-lg border border-border bg-background/60 p-3"
                          >
                            <div className="flex items-center justify-between gap-2 text-xs">
                              <span className="truncate font-medium">
                                {bucket.limit_name || bucket.limit_id}
                              </span>
                              {window ? (
                                <span className="text-muted-foreground">
                                  {Math.round(window.remaining_percent)}%{" "}
                                  {copy.remaining}
                                </span>
                              ) : null}
                            </div>
                            {window ? (
                              <>
                                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
                                  <div
                                    className="h-full rounded-full bg-primary transition-[width]"
                                    style={{
                                      width: `${Math.max(0, Math.min(100, window.used_percent))}%`,
                                    }}
                                  />
                                </div>
                                <div className="mt-1.5 text-xs text-muted-foreground">
                                  {copy.resetsAt}:{" "}
                                  {new Date(
                                    window.resets_at * 1000,
                                  ).toLocaleString(locale)}
                                </div>
                              </>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                    <div className="grid gap-2 sm:grid-cols-3">
                      <UsageStat
                        label={copy.lifetimeTokens}
                        value={usageQuery.data?.summary?.lifetime_tokens}
                        locale={locale}
                      />
                      <UsageStat
                        label={copy.peakDailyTokens}
                        value={usageQuery.data?.summary?.peak_daily_tokens}
                        locale={locale}
                      />
                      <UsageStat
                        label={copy.resetCredits}
                        value={rateLimitsQuery.data?.reset_credits_available}
                        locale={locale}
                      />
                    </div>
                  </div>
                </details>
              ) : (
                <p className="border-t border-border pt-3 text-xs text-muted-foreground">
                  {copy.usageUnavailable}
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-3 rounded-lg border border-border p-3">
              <div>
                <div className="text-sm font-medium">{copy.account}</div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {copy.browserLoginHint}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  disabled={busy || loginPending}
                  onClick={() => void beginLogin("chatgpt")}
                >
                  <UserRoundIcon className="size-3.5" /> {copy.connectChatGPT}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy || loginPending}
                  onClick={() => void beginLogin("chatgptDeviceCode")}
                >
                  <ExternalLinkIcon className="size-3.5" /> {copy.connectDevice}
                </Button>
              </div>

              {activeLogin ? (
                <div className="rounded-lg border border-info/25 bg-info/[0.05] p-3 text-sm">
                  <div className="flex items-center gap-2 font-medium text-info">
                    <Loader2Icon className="size-4 animate-spin" />{" "}
                    {copy.pending}
                  </div>
                  {activeLogin.user_code ? (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <span className="text-xs text-muted-foreground">
                        {copy.deviceCode}
                      </span>
                      <code className="rounded-md border border-border bg-background px-2 py-1 font-mono text-base tracking-wider">
                        {activeLogin.user_code}
                      </code>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={async () => {
                          try {
                            await navigator.clipboard.writeText(
                              activeLogin.user_code || "",
                            );
                            setCopied(true);
                          } catch {
                            setCopied(false);
                          }
                        }}
                      >
                        <ClipboardIcon className="size-3.5" />{" "}
                        {copied ? copy.copied : copy.copyCode}
                      </Button>
                    </div>
                  ) : null}
                  <div className="mt-3 flex flex-wrap gap-2">
                    {activeLogin.auth_url || activeLogin.verification_url ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          void openSensitiveAuthorizationUrl(
                            activeLogin.auth_url ||
                              activeLogin.verification_url ||
                              "",
                          )
                        }
                      >
                        <ExternalLinkIcon className="size-3.5" />{" "}
                        {copy.openAuthorization}
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={loginBusy === "cancel"}
                      onClick={() => void cancelLogin()}
                    >
                      {copy.cancelLogin}
                    </Button>
                  </div>
                </div>
              ) : null}

              <details className="rounded-lg border border-border bg-muted/20 px-3 py-2">
                <summary className="cursor-pointer text-sm font-medium">
                  {copy.connectApiKey}
                </summary>
                <form
                  className="mt-3 space-y-2"
                  onSubmit={submitApiKey}
                  autoComplete="off"
                >
                  <label className="block space-y-1.5 text-xs text-muted-foreground">
                    <span>{copy.apiKeyLabel}</span>
                    <Input
                      name="api_key"
                      type="password"
                      required
                      autoComplete="new-password"
                      data-1p-ignore="true"
                      data-lpignore="true"
                      spellCheck={false}
                      placeholder={copy.apiKeyPlaceholder}
                    />
                  </label>
                  <p className="flex items-start gap-1.5 text-xs leading-5 text-muted-foreground">
                    <KeyRoundIcon className="mt-0.5 size-3.5 shrink-0" />{" "}
                    {copy.apiKeyHint}
                  </p>
                  <Button
                    type="submit"
                    size="sm"
                    disabled={busy || loginPending}
                  >
                    {loginBusy === "apiKey" ? (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    ) : (
                      <KeyRoundIcon className="size-3.5" />
                    )}
                    {copy.submitApiKey}
                  </Button>
                </form>
              </details>
            </div>
          )}

          {loginError ? (
            <div
              role="alert"
              className="flex gap-2 rounded-lg border border-destructive/20 bg-destructive/[0.04] p-3 text-xs text-destructive"
            >
              <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />{" "}
              {loginError}
            </div>
          ) : null}
          {loginNotice ? (
            <div
              role="status"
              className="flex gap-2 rounded-lg border border-success/20 bg-success/[0.04] p-3 text-xs text-success"
            >
              <CheckCircle2Icon className="mt-0.5 size-3.5 shrink-0" />{" "}
              {loginNotice}
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
