import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  ChevronRightIcon,
  InfoIcon,
  EyeIcon,
  EyeOffIcon,
  Loader2Icon,
  PlusIcon,
  RefreshCwIcon,
  SearchIcon,
  Trash2Icon,
  WifiIcon,
  XCircleIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { RoutedWebLink } from "@/components/ui/routed-web-link";
import { cn } from "@/lib/utils";
import { swallow } from "@/core/utils/log";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import {
  disableNASModel,
  downloadNASModel,
  enableNASModel,
  listNASModels,
  startNASService,
  type NASModel,
} from "@/core/storage/api";
import { useI18n } from "@/core/i18n/hooks";
import {
  clearThreadModelReferences,
  getLocalSettings,
  saveLocalSettings,
} from "@/core/settings/local";
import { registerPageAgentCapability } from "@/core/page-agent-bridge";
import { ModelCookbook } from "@/components/workspace/model-cookbook";
import { CoderEngineSettings } from "@/components/workspace/coder-engine-control";

import { MixSettingsSection } from "./mix-settings-section";
import { SettingsSection } from "./settings-section";

// Per-model default reasoning effort, persisted via the custom-models
// API. Mirrors the backend vocabulary (off | high | max | none) and
// lets DeepSeek-style models default to deliberate thinking without
// every caller having to pass it explicitly.
type DefaultReasoningEffort = "off" | "high" | "max" | "none";

// The picker's UI vocabulary (off/low/medium/high/xhigh) is broader than the
// persisted default-effort vocabulary (off/high/max/none). Map a capability
// set down to the settings vocabulary so the "default reasoning effort"
// dropdown only offers tiers the model genuinely accepts on the wire.
function defaultEffortsForCapability(
  reasoningEfforts?: string[] | null,
): DefaultReasoningEffort[] | null {
  // No capability info → keep the full default set.
  if (!reasoningEfforts) return null;
  // Explicitly empty → no meaningful effort control (adaptive / unsupported
  // thinking) → callers hide the whole dropdown.
  if (reasoningEfforts.length === 0) return [];
  const map: Record<string, DefaultReasoningEffort> = {
    off: "off",
    low: "high",
    medium: "high",
    high: "high",
    xhigh: "max",
    max: "max",
  };
  const out: DefaultReasoningEffort[] = [];
  for (const tier of reasoningEfforts) {
    const mapped = map[tier];
    if (mapped && !out.includes(mapped)) out.push(mapped);
  }
  // "none" (no injection) is always a valid meta-option.
  if (out.length === 0) return null;
  return out;
}

/** Lightweight client-side mirror of the backend's OpenAI-compatible profile
 *  resolution, used by the add-new form before an entry exists. Returns the
 *  picker-vocabulary tiers (off/low/medium/high/xhigh), null for the full
 *  default set, or [] when the profile has no meaningful effort control. */
function clientSideReasoningEfforts(
  baseUrl: string,
  model: string,
): string[] | null {
  const url = (baseUrl || "").toLowerCase();
  const m = (model || "").toLowerCase();
  if (
    url.includes("api.deepseek.com") ||
    m.startsWith("deepseek-") ||
    m.includes("deepseek/")
  ) {
    return ["off", "high", "xhigh"];
  }
  if (
    url.includes("minimax") ||
    m.startsWith("minimax") ||
    m.startsWith("abab")
  ) {
    return [];
  }
  return null;
}

/** Capability-aware "default reasoning effort" select. Filters the options
 *  to the tiers the resolved model profile genuinely accepts; hides entirely
 *  when the model has no meaningful effort control (e.g. MiniMax adaptive).
 *  ``reasoningEfforts`` is the picker vocabulary (off/low/medium/high/xhigh). */
function DefaultEffortSelect({
  value,
  reasoningEfforts,
  onChange,
}: {
  value: DefaultReasoningEffort | undefined;
  reasoningEfforts?: string[] | null;
  onChange: (v: DefaultReasoningEffort | undefined) => void;
}) {
  const { t } = useI18n();
  const offered = defaultEffortsForCapability(reasoningEfforts);
  // No meaningful tiers → no control to show.
  if (offered && offered.length === 0) return null;
  const options: DefaultReasoningEffort[] = offered ?? ["off", "high", "max"];
  const labelFor = (v: DefaultReasoningEffort) =>
    v === "off"
      ? t.settings.model.defaultReasoningEffortOff
      : v === "high"
        ? t.settings.model.defaultReasoningEffortHigh
        : t.settings.model.defaultReasoningEffortMax;
  return (
    <div className="mt-3 flex items-center justify-between rounded-lg border border-border px-3 py-2">
      <label
        htmlFor="default-reasoning-effort"
        className="text-xs text-muted-foreground"
      >
        {t.settings.model.defaultReasoningEffortLabel}
      </label>
      <select
        id="default-reasoning-effort"
        value={value ?? ""}
        onChange={(e) => {
          const v = e.target.value;
          onChange(v === "" ? undefined : (v as DefaultReasoningEffort));
        }}
        className="rounded-md border border-input bg-transparent px-2 py-1 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      >
        <option value="">
          {t.settings.model.defaultReasoningEffortFollow}
        </option>
        {options.map((v) => (
          <option key={v} value={v}>
            {labelFor(v)}
          </option>
        ))}
        <option value="none">
          {t.settings.model.defaultReasoningEffortNone}
        </option>
      </select>
    </div>
  );
}

// ── Provider presets ────────────────────────────────────────────
//
// Each preset auto-fills base URL + protocol when selected. Optional
// Implementation note.
// API key input · ``suggestedModels`` shows a small hint below the
// Model ID field so users don't have to remember model names.
//
// Compatibility note · all entries work with Echo's native
// tool_use pipeline as long as the backing model supports function
// calling (see docs/custom-models.md for per-provider gotchas).
interface ProviderPreset {
  label: string;
  value: string;
  baseUrl: string;
  protocol: "openai" | "anthropic";
  consoleUrl?: string;
  suggestedModels?: string[];
}

const PROVIDERS: readonly ProviderPreset[] = [
  // Implementation note.
  {
    label: "OpenAI",
    value: "openai",
    baseUrl: "https://api.openai.com/v1",
    protocol: "openai",
    consoleUrl: "https://platform.openai.com/api-keys",
    suggestedModels: ["gpt-4o-mini", "gpt-4o", "o1", "o3-mini"],
  },
  {
    label: "Anthropic (Claude)",
    value: "anthropic",
    baseUrl: "https://api.anthropic.com/v1",
    protocol: "anthropic",
    consoleUrl: "https://console.anthropic.com/",
    suggestedModels: [
      "claude-sonnet-4-6-20250514",
      "claude-haiku-4-5-20251001",
      "claude-opus-4-7-20250805",
    ],
  },
  {
    label: "Google Gemini",
    value: "gemini",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    protocol: "openai",
    consoleUrl: "https://aistudio.google.com/apikey",
    suggestedModels: ["gemini-2.5-flash", "gemini-2.5-pro"],
  },
  {
    label: "xAI (Grok)",
    value: "xai",
    baseUrl: "https://api.x.ai/v1",
    protocol: "openai",
    consoleUrl: "https://console.x.ai/",
    suggestedModels: ["grok-4-mini", "grok-4"],
  },
  {
    label: "DeepSeek",
    value: "deepseek",
    baseUrl: "https://api.deepseek.com/v1",
    protocol: "openai",
    consoleUrl: "https://platform.deepseek.com/",
    suggestedModels: ["deepseek-chat", "deepseek-reasoner"],
  },

  // Implementation note.
  {
    label: "Moonshot · Kimi",
    value: "kimi",
    baseUrl: "https://api.moonshot.cn/v1",
    protocol: "openai",
    consoleUrl: "https://platform.moonshot.cn/console/api-keys",
    suggestedModels: [
      "kimi-k2-0711-preview",
      "moonshot-v1-128k",
      "moonshot-v1-32k",
    ],
  },
  {
    label: "Kimi Coding",
    value: "kimi-coding",
    baseUrl: "https://api.kimi.com/coding/v1",
    protocol: "openai",
    consoleUrl: "https://platform.moonshot.cn/console/api-keys",
    suggestedModels: ["K2.7-Code", "kimi-k2.7-code"],
  },
  {
    label: "Zhipu · GLM",
    value: "zhipu",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    protocol: "openai",
    consoleUrl: "https://bigmodel.cn/usercenter/apikeys",
    suggestedModels: ["glm-4.6", "glm-4-flash", "glm-4-plus", "glm-4v-plus"],
  },
  {
    label: "MiniMax",
    value: "minimax",
    baseUrl: "https://api.minimaxi.com/v1",
    protocol: "openai",
    consoleUrl:
      "https://platform.minimaxi.com/user-center/basic-information/interface-key",
    suggestedModels: ["MiniMax-M2", "abab7-chat-preview"],
  },
  {
    label: "Alibaba Cloud · Tongyi Qwen (Qwen)",
    value: "aliyun",
    // NB · must be ``compatible-mode`` · DashScope native proto
    // does not support the standard ``tools`` field shape.
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    protocol: "openai",
    consoleUrl: "https://bailian.console.aliyun.com/?tab=model#/api-key",
    suggestedModels: [
      "qwen-max-latest",
      "qwen-plus",
      "qwen-turbo",
      "qwen3-max",
      "qvq-max-latest",
    ],
  },
  {
    label: "Tencent Cloud · Hunyuan",
    value: "tencent",
    baseUrl: "https://api.hunyuan.cloud.tencent.com/v1",
    protocol: "openai",
    consoleUrl: "https://console.cloud.tencent.com/hunyuan/api-key",
    suggestedModels: ["hunyuan-turbos-latest", "hunyuan-large", "hunyuan-lite"],
  },
  {
    label: "Volcano Engine · Doubao (Ark)",
    value: "volcengine",
    baseUrl: "https://ark.cn-beijing.volces.com/api/v3",
    protocol: "openai",
    consoleUrl: "https://console.volcengine.com/ark",
    suggestedModels: [
      "doubao-pro-256k",
      "doubao-1-5-pro-256k",
      "doubao-pro-32k",
    ],
  },
  {
    label: "Baichuan",
    value: "baichuan",
    baseUrl: "https://api.baichuan-ai.com/v1",
    protocol: "openai",
    consoleUrl: "https://platform.baichuan-ai.com/console/apikey",
    suggestedModels: ["Baichuan4", "Baichuan3-Turbo"],
  },
  {
    label: "01.AI · Yi",
    value: "lingyiwanwu",
    baseUrl: "https://api.lingyiwanwu.com/v1",
    protocol: "openai",
    consoleUrl: "https://platform.lingyiwanwu.com/",
    suggestedModels: ["yi-lightning", "yi-large"],
  },
  {
    label: "StepFun",
    value: "stepfun",
    baseUrl: "https://api.stepfun.com/v1",
    protocol: "openai",
    consoleUrl: "https://platform.stepfun.com/",
    suggestedModels: ["step-2-mini", "step-1-8k"],
  },
  {
    label: "SiliconFlow",
    value: "siliconflow",
    baseUrl: "https://api.siliconflow.cn/v1",
    protocol: "openai",
    consoleUrl: "https://cloud.siliconflow.cn/account/ak",
    suggestedModels: [
      "deepseek-ai/DeepSeek-V3",
      "Qwen/Qwen3-Coder-480B-A35B-Instruct",
    ],
  },
  {
    label: "Baidu · Qianfan",
    value: "qianfan",
    baseUrl: "https://qianfan.baidubce.com/v2",
    protocol: "openai",
    consoleUrl:
      "https://console.bce.baidu.com/qianfan/ais/console/applicationConsole/application",
    suggestedModels: ["ernie-4.5-turbo-128k", "ernie-x1-turbo-32k"],
  },

  // Implementation note.
  {
    label: "Ollama (local)",
    value: "ollama",
    baseUrl: "http://localhost:11434/v1",
    protocol: "openai",
    consoleUrl: "https://ollama.com",
    suggestedModels: [
      "llama3.3:70b",
      "qwen3:7b",
      "qwen3-coder:32b",
      "deepseek-r1:7b",
    ],
  },
  {
    label: "LM Studio (local)",
    value: "lmstudio",
    baseUrl: "http://localhost:1234/v1",
    protocol: "openai",
  },
  {
    label: "OpenRouter (200+ models)",
    value: "openrouter",
    baseUrl: "https://openrouter.ai/api/v1",
    protocol: "openai",
    consoleUrl: "https://openrouter.ai/keys",
  },
  {
    label: "Agnes AI (gateway)",
    value: "agnes",
    baseUrl: "https://apihub.agnes-ai.com/v1",
    protocol: "openai",
    consoleUrl: "https://agnes-ai.com/dashboard",
    suggestedModels: [
      "agnes-2.0-flash",
      "agnes-1.5-flash",
      "agnes-image-2.1-flash",
      "agnes-image-2.0-flash",
      "agnes-video-v2.0",
    ],
  },
  { label: "Custom", value: "custom", baseUrl: "", protocol: "openai" },
] as const;

const PROTOCOLS = [
  { label: "OpenAI", value: "openai" },
  { label: "Anthropic", value: "anthropic" },
] as const;

type TestStatus = "idle" | "testing" | "success" | "fail";

interface ModelConfig {
  id?: string;
  name: string;
  /** Open-ended list of upstream model ids this entry can dispatch
   *  to. Index 0 is the picker default, index -1 is the strongest
   *  slot for Auto mode's performance verdict. Backend stores this
   *  as ``models`` on the custom-model entry. */
  models: string[];
  selection_ids?: string[];
  display_name?: string | null;
  description?: string | null;
  provider?: string | null;
  supports_thinking?: boolean;
  supports_vision?: boolean;
  base_url?: string;
  has_api_key?: boolean;
  default_header_names?: string[];
  has_default_headers?: boolean;
  max_tokens?: number | null;
  context_window?: number | null;
  enable_1m_context?: boolean;
}

function customModelReferences(model: ModelConfig): string[] {
  return Array.from(
    new Set(
      [model.name, model.id, ...model.models, ...(model.selection_ids ?? [])]
        .map((value) => value?.trim())
        .filter((value): value is string => Boolean(value)),
    ),
  );
}

function customModelEntryId(model: ModelConfig): string {
  return model.id?.trim() || model.name.trim();
}

function customModelMatchesIdentity(
  model: ModelConfig,
  identity: string,
): boolean {
  const normalized = identity.trim();
  return Boolean(
    normalized &&
    (customModelEntryId(model) === normalized ||
      model.name.trim() === normalized),
  );
}

export function customModelMatchesSelection(
  model: ModelConfig,
  selection?: string | null,
): boolean {
  const normalized = selection?.trim();
  return Boolean(
    normalized && customModelReferences(model).includes(normalized),
  );
}

export function customModelPreferredSelection(model: ModelConfig): string {
  return (
    model.selection_ids?.find((value) => value.trim())?.trim() ||
    model.models.find((value) => value.trim())?.trim() ||
    customModelEntryId(model)
  );
}

interface CompatDiagnosticUpstream {
  model: string;
  profile?: string | null;
  profile_display_name?: string | null;
  profile_summary?: {
    id?: string;
    display_name?: string;
    compat_score?: number;
    normalization_hints?: string[];
    notes?: string[];
  };
  compat_score?: number | null;
  normalization_hints?: string[];
  compatibility_notes?: string[];
  normalization?: {
    removed_fields?: string[];
    added_fields?: string[];
    changed_fields?: string[];
    normalized_fields?: string[];
  };
  fallback_retries?: Array<{
    reason?: string;
    removed_fields?: string[];
    added_fields?: string[];
    changed_fields?: string[];
  }>;
}

interface CompatDiagnostic {
  id: string;
  provider?: string | null;
  applicable: boolean;
  reason?: string | null;
  has_api_key?: boolean;
  default_header_names?: string[];
  built_in?: boolean;
  sample_base_url?: string;
  upstreams?: CompatDiagnosticUpstream[];
}

type CompatDiagnosticState =
  | { status: "idle" | "loading"; byId: Record<string, CompatDiagnostic> }
  | { status: "ready"; byId: Record<string, CompatDiagnostic> }
  | { status: "error"; byId: Record<string, CompatDiagnostic>; error: string };

type CompatProfileCatalogState =
  | { status: "idle" | "loading"; items: CompatDiagnostic[] }
  | { status: "ready"; items: CompatDiagnostic[] }
  | { status: "error"; items: CompatDiagnostic[]; error: string };

// Parse `Header-Name: value` lines into a dict. Blank lines and lines
// lacking a colon are ignored so users can leave helper comments.
function parseHeadersText(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const idx = line.indexOf(":");
    if (idx <= 0) continue;
    const k = line.slice(0, idx).trim();
    const v = line.slice(idx + 1).trim();
    if (k && v) out[k] = v;
  }
  return out;
}

// Mirror of the backend base_url guard (config_router._validate_base_url)
// for instant feedback before the network round-trip. Loopback / private
// hosts stay allowed (local servers like Ollama / LM Studio); only
// non-http(s) schemes and link-local / cloud-metadata endpoints are
// rejected. Returns an error reason, or null when acceptable (empty
// included — the backend owns the per-provider "required" check).
function validateBaseUrl(url: string): string | null {
  if (!url) return null;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return "invalid base_url: unparseable URL";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return `invalid base_url: scheme must be http/https (got ${parsed.protocol.replace(":", "")})`;
  }
  const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (host === "metadata.google.internal" || host === "metadata.goog") {
    return "invalid base_url: blocked cloud-metadata host";
  }
  if (/^169\.254\./.test(host) || host.startsWith("fe80:")) {
    return "invalid base_url: blocked link-local address";
  }
  return null;
}

function collectCompatFields(
  diagnostic: CompatDiagnostic | undefined,
  key: "removed_fields" | "added_fields" | "changed_fields",
): string[] {
  const seen = new Set<string>();
  for (const upstream of diagnostic?.upstreams ?? []) {
    const values = upstream.normalization?.[key] ?? [];
    for (const value of values) {
      if (typeof value === "string" && value.trim()) seen.add(value);
    }
  }
  return Array.from(seen).sort();
}

function countCompatRetries(diagnostic: CompatDiagnostic | undefined): number {
  return (diagnostic?.upstreams ?? []).reduce(
    (total, upstream) =>
      total +
      (Array.isArray(upstream.fallback_retries)
        ? upstream.fallback_retries.length
        : 0),
    0,
  );
}

function summarizeCompatProfiles(
  diagnostic: CompatDiagnostic | undefined,
): string[] {
  const seen = new Set<string>();
  for (const upstream of diagnostic?.upstreams ?? []) {
    const label = upstream.profile_display_name || upstream.profile;
    if (typeof label === "string" && label.trim()) seen.add(label);
  }
  return Array.from(seen);
}

function summarizeCompatRetryReasons(
  diagnostic: CompatDiagnostic | undefined,
): string[] {
  const seen = new Set<string>();
  for (const upstream of diagnostic?.upstreams ?? []) {
    for (const retry of upstream.fallback_retries ?? []) {
      if (retry.reason) seen.add(retry.reason);
    }
  }
  return Array.from(seen).sort();
}

function summarizeCompatScoreRange(
  diagnostic: CompatDiagnostic | undefined,
): { min: number; max: number } | null {
  const scores: number[] = [];
  for (const upstream of diagnostic?.upstreams ?? []) {
    const candidates = [
      upstream.compat_score,
      upstream.profile_summary?.compat_score,
    ];
    for (const value of candidates) {
      if (typeof value === "number" && Number.isFinite(value)) {
        scores.push(Math.round(value));
      }
    }
  }
  if (scores.length === 0) return null;
  return {
    min: Math.min(...scores),
    max: Math.max(...scores),
  };
}

function collectCompatProfileText(
  diagnostic: CompatDiagnostic | undefined,
  key: "normalization_hints" | "compatibility_notes",
): string[] {
  const seen = new Set<string>();
  for (const upstream of diagnostic?.upstreams ?? []) {
    const direct =
      key === "normalization_hints"
        ? upstream.normalization_hints
        : upstream.compatibility_notes;
    const summary =
      key === "normalization_hints"
        ? upstream.profile_summary?.normalization_hints
        : upstream.profile_summary?.notes;
    for (const value of [...(direct ?? []), ...(summary ?? [])]) {
      if (typeof value === "string" && value.trim()) seen.add(value);
    }
  }
  return Array.from(seen).sort();
}

const MODEL_SETTINGS_PAGE_COPY: Record<
  "zh" | "en" | "ja" | "ko",
  {
    overviewTitle: string;
    overviewSubtitle: string;
    currentDefault: string;
    noDefault: string;
    configuredModels: string;
    configuredSummary: (connections: number, models: number) => string;
    connectionsTitle: string;
    connectionsSubtitle: string;
    gateway: string;
    gatewayConnected: string;
    gatewayDisconnected: string;
    gatewayChecking: string;
    addApiModel: string;
    scanLocalModels: string;
    advancedTitle: string;
    advancedSubtitle: string;
    advancedBadge: string;
    sameOriginProxy: string;
    compatDetails: string;
    compatMatrixTitle: string;
    compatMatrixDescription: string;
    compatProfileCount: (count: number) => string;
    compatProfilesLoading: string;
    compatProfilesUnavailable: string;
    compatScore: (score: string) => string;
    compatFallbacks: (count: number) => string;
    compatNormalize: (values: string) => string;
    compatDrops: (values: string) => string;
    compatRetries: (values: string) => string;
    compatRemaining: (count: number) => string;
    configuredDiagnosticsTitle: string;
    configuredDiagnosticsSubtitle: string;
    connectionToolsTitle: string;
    connectionToolsSubtitle: string;
    localToolsTitle: string;
    localToolsSubtitle: string;
    mixToolsTitle: string;
    mixToolsSubtitle: string;
    providerToolsTitle: string;
    providerToolsSubtitle: string;
    deletingDefault: (replacement: string | null) => string;
    deletedAndSwitched: (replacement: string) => string;
    deletedAndReset: string;
    retryLoad: string;
  }
> = {
  zh: {
    overviewTitle: "当前模型",
    overviewSubtitle:
      "选择对话与自动路由使用的模型；新的服务可通过 API 连接或本地扫描接入。",
    currentDefault: "默认模型",
    noDefault: "未设置",
    configuredModels: "可用资源",
    configuredSummary: (connections, models) =>
      `${connections} 个连接 · ${models} 个模型`,
    connectionsTitle: "API 模型连接",
    connectionsSubtitle: "管理已接入的模型服务。展开连接可查看模型与路由角色。",
    gateway: "模型网关",
    gatewayConnected: "已连接",
    gatewayDisconnected: "未连接",
    gatewayChecking: "检查中",
    addApiModel: "接入 API 模型",
    scanLocalModels: "扫描本地模型",
    advancedTitle: "高级能力与兼容诊断",
    advancedSubtitle:
      "网关排障、连接兼容详情、Cookbook、Echo Mix 和 OpenAI-compatible 矩阵统一收在这里。",
    advancedBadge: "高级",
    sameOriginProxy: "同源代理",
    compatDetails: "查看兼容处理规则",
    compatMatrixTitle: "OpenAI 兼容配置矩阵",
    compatMatrixDescription:
      "内置的 OpenAI 兼容提供方预检矩阵。无需配置 API Key，即可查看请求归一化规则和失败后的兼容重试策略。",
    compatProfileCount: (count) => `${count} 个配置`,
    compatProfilesLoading: "正在加载兼容配置",
    compatProfilesUnavailable: "兼容配置目录暂不可用",
    compatScore: (score) => `兼容分 ${score}`,
    compatFallbacks: (count) => `${count} 次回退`,
    compatNormalize: (values) => `归一化：${values}`,
    compatDrops: (values) => `移除：${values}`,
    compatRetries: (values) => `重试：${values}`,
    compatRemaining: (count) => `后端目录中还有 ${count} 个配置。`,
    configuredDiagnosticsTitle: "已接入连接的兼容详情",
    configuredDiagnosticsSubtitle:
      "仅在排查提供方兼容问题时需要查看，不影响日常选择和使用模型。",
    connectionToolsTitle: "连接与网关诊断",
    connectionToolsSubtitle: "检查网关状态和已接入连接的兼容处理。",
    localToolsTitle: "本地模型推荐",
    localToolsSubtitle: "按当前设备能力查看可运行模型和下载建议。",
    mixToolsTitle: "多模型协同",
    mixToolsSubtitle: "配置多个模型共同起草和汇总答案。",
    providerToolsTitle: "提供方兼容矩阵",
    providerToolsSubtitle: "查看内置提供方的请求归一化与回退规则。",
    deletingDefault: (replacement) =>
      replacement
        ? `这是当前默认模型。删除后将自动切换到“${replacement}”。`
        : "这是当前默认模型。删除后将恢复为自动选择可用模型。",
    deletedAndSwitched: (replacement) =>
      `模型已删除，默认模型已切换到“${replacement}”。`,
    deletedAndReset: "模型已删除，默认模型已恢复为自动选择。",
    retryLoad: "重新加载",
  },
  en: {
    overviewTitle: "Model setup overview",
    overviewSubtitle:
      "Manage models used by Echo chat and automatic routing here. Add hosted providers through explicitly installed API model adapters or scan local models; external CLIs are not auto-detected.",
    currentDefault: "Current default",
    noDefault: "Not set",
    configuredModels: "API model connections",
    configuredSummary: (connections, models) =>
      `${connections} connection${connections === 1 ? "" : "s"} · ${models} model${models === 1 ? "" : "s"}`,
    connectionsTitle: "API model connections",
    connectionsSubtitle:
      "A connection can contain multiple models. The first is the default choice and the last powers the performance route.",
    gateway: "Model gateway",
    gatewayConnected: "Connected",
    gatewayDisconnected: "Disconnected",
    gatewayChecking: "Checking",
    addApiModel: "Add API model",
    scanLocalModels: "Scan local models",
    advancedTitle: "Advanced capabilities and diagnostics",
    advancedSubtitle:
      "Gateway troubleshooting, connection compatibility, Cookbook, Echo Mix, and the OpenAI-compatible matrix stay grouped here.",
    advancedBadge: "Advanced",
    sameOriginProxy: "Same-origin proxy",
    compatDetails: "View compatibility rules",
    compatMatrixTitle: "OpenAI-compatible profile matrix",
    compatMatrixDescription:
      "Built-in preflight matrix for OpenAI-compatible providers. Review request normalization and compatibility retries before configuring an API key.",
    compatProfileCount: (count) => `${count} profile${count === 1 ? "" : "s"}`,
    compatProfilesLoading: "Loading compatibility profiles",
    compatProfilesUnavailable: "Compatibility profile catalog unavailable",
    compatScore: (score) => `Compatibility ${score}`,
    compatFallbacks: (count) => `${count} fallback${count === 1 ? "" : "s"}`,
    compatNormalize: (values) => `Normalize: ${values}`,
    compatDrops: (values) => `Remove: ${values}`,
    compatRetries: (values) => `Retry: ${values}`,
    compatRemaining: (count) =>
      `${count} more profile${count === 1 ? "" : "s"} in the backend catalog.`,
    configuredDiagnosticsTitle: "Connection compatibility details",
    configuredDiagnosticsSubtitle:
      "Use these details only when troubleshooting a provider compatibility issue.",
    connectionToolsTitle: "Connections and gateway",
    connectionToolsSubtitle:
      "Inspect gateway health and compatibility handling for configured connections.",
    localToolsTitle: "Local model recommendations",
    localToolsSubtitle:
      "Review models that fit this device and optional download suggestions.",
    mixToolsTitle: "Multi-model collaboration",
    mixToolsSubtitle:
      "Configure models that draft independently and aggregate a final answer.",
    providerToolsTitle: "Provider compatibility matrix",
    providerToolsSubtitle:
      "Review built-in request normalization and fallback rules by provider.",
    deletingDefault: (replacement) =>
      replacement
        ? `This is the current default. Deleting it will switch the default to “${replacement}”.`
        : "This is the current default. Deleting it will restore automatic model selection.",
    deletedAndSwitched: (replacement) =>
      `Model deleted. The default is now “${replacement}”.`,
    deletedAndReset:
      "Model deleted. The default has returned to automatic selection.",
    retryLoad: "Reload",
  },
  ja: {
    overviewTitle: "モデル設定の概要",
    overviewSubtitle:
      "ここでは Echo の会話と自動ルーティング用モデルを管理します。外部サービスは明示的にインストールした API モデルアダプター、端末内推論はローカルスキャンから追加します。外部 CLI は自動検出しません。",
    currentDefault: "現在の既定",
    noDefault: "未設定",
    configuredModels: "API モデル接続",
    configuredSummary: (connections, models) =>
      `${connections} 接続 · ${models} モデル`,
    connectionsTitle: "API モデル接続",
    connectionsSubtitle:
      "1 つの接続に複数モデルを登録できます。先頭は既定、末尾は高性能ルートに使われます。",
    gateway: "モデルゲートウェイ",
    gatewayConnected: "接続済み",
    gatewayDisconnected: "未接続",
    gatewayChecking: "確認中",
    addApiModel: "API モデルを追加",
    scanLocalModels: "ローカルモデルをスキャン",
    advancedTitle: "高度な機能と互換診断",
    advancedSubtitle:
      "ゲートウェイ診断、接続互換性、Cookbook、Echo Mix、OpenAI 互換マトリクスをここにまとめています。",
    advancedBadge: "上級",
    sameOriginProxy: "同一オリジンプロキシ",
    compatDetails: "互換処理ルールを表示",
    compatMatrixTitle: "OpenAI 互換プロファイル一覧",
    compatMatrixDescription:
      "OpenAI 互換プロバイダー向けの組み込み事前確認一覧です。API キーを設定する前に、リクエストの正規化と互換リトライを確認できます。",
    compatProfileCount: (count) => `${count} 件のプロファイル`,
    compatProfilesLoading: "互換プロファイルを読み込み中",
    compatProfilesUnavailable: "互換プロファイル一覧を利用できません",
    compatScore: (score) => `互換性 ${score}`,
    compatFallbacks: (count) => `${count} 回のフォールバック`,
    compatNormalize: (values) => `正規化：${values}`,
    compatDrops: (values) => `削除：${values}`,
    compatRetries: (values) => `再試行：${values}`,
    compatRemaining: (count) =>
      `バックエンドの一覧に残り ${count} 件あります。`,
    configuredDiagnosticsTitle: "設定済み接続の互換性詳細",
    configuredDiagnosticsSubtitle:
      "プロバイダー互換性の問題を調査するときだけ確認してください。",
    connectionToolsTitle: "接続とゲートウェイ診断",
    connectionToolsSubtitle:
      "ゲートウェイ状態と接続ごとの互換処理を確認します。",
    localToolsTitle: "ローカルモデルの推奨",
    localToolsSubtitle: "この端末で動作するモデルと取得候補を確認します。",
    mixToolsTitle: "複数モデル連携",
    mixToolsSubtitle: "複数モデルによる下書きと最終統合を設定します。",
    providerToolsTitle: "プロバイダー互換マトリクス",
    providerToolsSubtitle: "組み込みの正規化とフォールバック規則を確認します。",
    deletingDefault: (replacement) =>
      replacement
        ? `現在の既定モデルです。削除後は「${replacement}」へ自動的に切り替わります。`
        : "現在の既定モデルです。削除後は利用可能なモデルの自動選択に戻ります。",
    deletedAndSwitched: (replacement) =>
      `モデルを削除し、既定モデルを「${replacement}」に切り替えました。`,
    deletedAndReset: "モデルを削除し、既定モデルを自動選択に戻しました。",
    retryLoad: "再読み込み",
  },
  ko: {
    overviewTitle: "모델 설정 개요",
    overviewSubtitle:
      "여기서는 Echo 대화와 자동 라우팅 모델을 관리합니다. 외부 서비스는 명시적으로 설치한 API 모델 어댑터로 연결하고 기기 내 추론은 로컬 스캔으로 추가합니다. 외부 CLI는 자동 감지하지 않습니다.",
    currentDefault: "현재 기본값",
    noDefault: "미설정",
    configuredModels: "API 모델 연결",
    configuredSummary: (connections, models) =>
      `연결 ${connections}개 · 모델 ${models}개`,
    connectionsTitle: "API 모델 연결",
    connectionsSubtitle:
      "하나의 연결에 여러 모델을 등록할 수 있습니다. 첫 항목은 기본 선택, 마지막 항목은 고성능 경로에 사용됩니다.",
    gateway: "모델 게이트웨이",
    gatewayConnected: "연결됨",
    gatewayDisconnected: "연결 안 됨",
    gatewayChecking: "확인 중",
    addApiModel: "API 모델 추가",
    scanLocalModels: "로컬 모델 스캔",
    advancedTitle: "고급 기능 및 호환성 진단",
    advancedSubtitle:
      "게이트웨이 진단, 연결 호환성, Cookbook, Echo Mix, OpenAI 호환 매트릭스를 여기에 모았습니다.",
    advancedBadge: "고급",
    sameOriginProxy: "동일 출처 프록시",
    compatDetails: "호환 처리 규칙 보기",
    compatMatrixTitle: "OpenAI 호환 프로필 목록",
    compatMatrixDescription:
      "OpenAI 호환 제공자를 위한 내장 사전 점검 목록입니다. API 키를 설정하기 전에 요청 정규화와 호환 재시도 규칙을 확인할 수 있습니다.",
    compatProfileCount: (count) => `프로필 ${count}개`,
    compatProfilesLoading: "호환 프로필 불러오는 중",
    compatProfilesUnavailable: "호환 프로필 목록을 사용할 수 없음",
    compatScore: (score) => `호환성 ${score}`,
    compatFallbacks: (count) => `대체 시도 ${count}회`,
    compatNormalize: (values) => `정규화: ${values}`,
    compatDrops: (values) => `제거: ${values}`,
    compatRetries: (values) => `재시도: ${values}`,
    compatRemaining: (count) => `백엔드 목록에 프로필 ${count}개 더 있음.`,
    configuredDiagnosticsTitle: "연결된 모델의 호환성 상세",
    configuredDiagnosticsSubtitle:
      "제공자 호환성 문제를 진단할 때만 확인하면 됩니다.",
    connectionToolsTitle: "연결 및 게이트웨이 진단",
    connectionToolsSubtitle: "게이트웨이 상태와 연결별 호환 처리를 확인합니다.",
    localToolsTitle: "로컬 모델 추천",
    localToolsSubtitle:
      "이 기기에서 실행 가능한 모델과 다운로드 제안을 확인합니다.",
    mixToolsTitle: "다중 모델 협업",
    mixToolsSubtitle: "여러 모델의 초안 작성과 최종 통합을 설정합니다.",
    providerToolsTitle: "제공자 호환성 매트릭스",
    providerToolsSubtitle: "내장 요청 정규화와 대체 규칙을 확인합니다.",
    deletingDefault: (replacement) =>
      replacement
        ? `현재 기본 모델입니다. 삭제하면 기본 모델이 “${replacement}”(으)로 자동 전환됩니다.`
        : "현재 기본 모델입니다. 삭제하면 사용 가능한 모델 자동 선택으로 돌아갑니다.",
    deletedAndSwitched: (replacement) =>
      `모델을 삭제했고 기본 모델을 “${replacement}”(으)로 전환했습니다.`,
    deletedAndReset: "모델을 삭제했고 기본 모델을 자동 선택으로 되돌렸습니다.",
    retryLoad: "다시 불러오기",
  },
};

function modelSettingsPageCopy(locale: string) {
  const lang = (locale || "en").slice(0, 2).toLowerCase();
  if (lang === "zh") return MODEL_SETTINGS_PAGE_COPY.zh;
  if (lang === "ja") return MODEL_SETTINGS_PAGE_COPY.ja;
  if (lang === "ko") return MODEL_SETTINGS_PAGE_COPY.ko;
  return MODEL_SETTINGS_PAGE_COPY.en;
}

const LOCAL_MODEL_SCAN_EVENT = "echo:model-settings:scan-local";

function ModelSettingsOverview({
  copy,
  defaultModelName,
  customModelCount,
  modelCount,
  gatewayStatus,
  onAddModel,
  onScanLocal,
  onOpenZen,
}: {
  copy: ReturnType<typeof modelSettingsPageCopy>;
  defaultModelName: string;
  customModelCount: number;
  modelCount: number;
  gatewayStatus: "connected" | "disconnected" | "checking";
  onAddModel: () => void;
  onScanLocal: () => void;
  onOpenZen: () => void;
}) {
  const gatewayLabel =
    gatewayStatus === "connected"
      ? copy.gatewayConnected
      : gatewayStatus === "checking"
        ? copy.gatewayChecking
        : copy.gatewayDisconnected;

  return (
    <section className="rounded-lg border border-border bg-card/45 p-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h2 className="text-base font-semibold">{copy.overviewTitle}</h2>
          <p className="mt-0.5 max-w-3xl text-xs leading-5 text-muted-foreground">
            {copy.overviewSubtitle}
          </p>
        </div>
        <div className="grid w-full grid-cols-1 gap-2 sm:flex sm:w-auto sm:flex-wrap">
          <Button size="sm" className="w-full sm:w-auto" onClick={onAddModel}>
            <PlusIcon className="mr-1.5 size-3.5" />
            {copy.addApiModel}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="w-full sm:w-auto"
            onClick={onScanLocal}
          >
            <WifiIcon className="mr-1.5 size-3.5" />
            {copy.scanLocalModels}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="w-full sm:w-auto"
            onClick={onOpenZen}
          >
            OpenCode Zen
            <ChevronRightIcon className="ml-1 size-3.5" />
          </Button>
        </div>
      </div>

      <div className="mt-3 flex flex-col border-t border-border pt-2.5 sm:flex-row sm:items-center sm:divide-x sm:divide-border">
        <div className="min-w-0 py-1 sm:flex-1 sm:py-0 sm:pr-4">
          <div className="text-[11px] text-muted-foreground">
            {copy.currentDefault}
          </div>
          <div className="truncate font-mono text-xs font-medium text-foreground">
            {defaultModelName || copy.noDefault}
          </div>
        </div>
        <div className="min-w-0 py-1 sm:flex-1 sm:px-4 sm:py-0">
          <div className="text-[11px] text-muted-foreground">
            {copy.configuredModels}
          </div>
          <div className="text-xs font-medium text-foreground">
            {copy.configuredSummary(customModelCount, modelCount)}
          </div>
        </div>
        <div className="flex items-center justify-between gap-3 py-1 sm:flex-1 sm:py-0 sm:pl-4">
          <div>
            <div className="text-[11px] text-muted-foreground">
              {copy.gateway}
            </div>
            <div
              role="status"
              aria-live="polite"
              className={cn(
                "inline-flex items-center gap-1.5 text-xs font-medium",
                gatewayStatus === "connected" && "text-success",
                gatewayStatus === "checking" && "text-info",
                gatewayStatus === "disconnected" && "text-destructive",
              )}
            >
              <span className="size-1.5 rounded-full bg-current" />
              {gatewayLabel}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ── Main page ──────────────────────────────────────────────────
export default function ModelSettingsPage() {
  const { t, locale } = useI18n();
  const navigate = useNavigate();
  const pageCopy = modelSettingsPageCopy(locale);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [compatDiagnostics, setCompatDiagnostics] =
    useState<CompatDiagnosticState>({
      status: "idle",
      byId: {},
    });
  const [compatProfileCatalog, setCompatProfileCatalog] =
    useState<CompatProfileCatalogState>({
      status: "idle",
      items: [],
    });
  const [loading, setLoading] = useState(true);
  const [modelsLoadError, setModelsLoadError] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [editingModel, setEditingModel] = useState<string | null>(null);
  const [modelToDelete, setModelToDelete] = useState<string | null>(null);

  // Gateway connection state
  const [gatewayStatus, setGatewayStatus] = useState<
    "connected" | "disconnected" | "checking"
  >("checking");

  // List / CRUD all target the new hot-register dispatcher endpoints
  // (/api/config/custom-models/*). The legacy /api/models was the
  // OpenAI-compat gateway's *skills-as-models* listing and had no
  // writable CRUD on this backend — writing to it was silently no-op.
  const fetchCompatDiagnostics = useCallback(async () => {
    setCompatDiagnostics((prev) => ({
      status: "loading",
      byId: prev.byId,
    }));
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/config/custom-models/compat-diagnostics`,
        {
          headers: authHeaders(),
        },
      );
      if (!res.ok) {
        throw new Error(
          `Failed to fetch compatibility diagnostics: ${res.status}`,
        );
      }
      const data = await res.json();
      const diagnostics = Array.isArray(data?.diagnostics)
        ? (data.diagnostics as CompatDiagnostic[])
        : [];
      const byId: Record<string, CompatDiagnostic> = {};
      for (const row of diagnostics) {
        if (typeof row?.id === "string" && row.id.trim()) {
          byId[row.id] = row;
        }
      }
      setCompatDiagnostics({ status: "ready", byId });
    } catch (error) {
      swallow(error);
      setCompatDiagnostics((prev) => ({
        status: "error",
        byId: prev.byId,
        error:
          error instanceof Error
            ? error.message
            : t.settings.model.compatDiagnostics.loadFailed,
      }));
    }
  }, [t.settings.model.compatDiagnostics.loadFailed]);

  const fetchCompatProfileCatalog = useCallback(async () => {
    setCompatProfileCatalog((prev) => ({
      status: "loading",
      items: prev.items,
    }));
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/config/openai-compat-profiles`,
        {
          headers: authHeaders(),
        },
      );
      if (!res.ok) {
        throw new Error(`Failed to fetch profile catalog: ${res.status}`);
      }
      const data = await res.json();
      const items = Array.isArray(data?.diagnostics)
        ? (data.diagnostics as CompatDiagnostic[])
        : [];
      setCompatProfileCatalog({ status: "ready", items });
    } catch (error) {
      swallow(error);
      setCompatProfileCatalog((prev) => ({
        status: "error",
        items: prev.items,
        error:
          error instanceof Error
            ? error.message
            : t.settings.model.compatDiagnostics.loadFailed,
      }));
    }
  }, [t.settings.model.compatDiagnostics.loadFailed]);

  const fetchModels = useCallback(async () => {
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/config/custom-models`,
        {
          headers: authHeaders(),
        },
      );
      if (!res.ok) {
        throw new Error(`Failed to fetch models: ${res.status}`);
      }
      const data = await res.json();
      const list = data.models || [];
      setModels(list);
      setModelsLoadError(false);
      setGatewayStatus("connected");
      void fetchCompatDiagnostics();
      void fetchCompatProfileCatalog();
    } catch (error) {
      console.error("[model-settings] load models failed:", error);
      setModelsLoadError(true);
      setGatewayStatus("disconnected");
      toast.error(t.settings.model.loadFailed);
    } finally {
      setLoading(false);
    }
  }, [
    fetchCompatDiagnostics,
    fetchCompatProfileCatalog,
    t.settings.model.loadFailed,
  ]);

  const checkGateway = useCallback(async () => {
    setGatewayStatus("checking");
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/config/custom-models`,
        {
          headers: authHeaders(),
          signal: AbortSignal.timeout(5000),
        },
      );
      setGatewayStatus(res.ok ? "connected" : "disconnected");
    } catch (e) {
      swallow(e);
      setGatewayStatus("disconnected");
    }
  }, []);

  useEffect(() => {
    fetchModels();
  }, [fetchModels]);

  const handleSetDefault = async (name: string) => {
    try {
      // Implementation note.
      const settings = getLocalSettings();
      saveLocalSettings({
        ...settings,
        context: {
          ...settings.context,
          model_name: name,
        },
      });
      toast.success(t.settings.model.setDefaultSuccess);
      await fetchModels();
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t.settings.model.setDefaultFailed,
      );
    }
  };

  const handleRetryModels = () => {
    if (models.length === 0) setLoading(true);
    void fetchModels();
  };

  const [deletingModel, setDeletingModel] = useState(false);

  const reconcileDeletedModel = useCallback(
    (modelId: string) => {
      const deletedModel = models.find(
        (model) => customModelEntryId(model) === modelId,
      );
      const deletedReferences = deletedModel
        ? customModelReferences(deletedModel)
        : [modelId];
      deletedReferences.forEach((reference) =>
        clearThreadModelReferences(reference),
      );
      const settings = getLocalSettings();
      const deletedWasDefault = deletedModel
        ? customModelMatchesSelection(deletedModel, settings.context.model_name)
        : settings.context.model_name === modelId;
      if (!deletedWasDefault) {
        setModels((current) =>
          current.filter((model) => customModelEntryId(model) !== modelId),
        );
        return null;
      }

      const replacementModel = models.find(
        (model) => customModelEntryId(model) !== modelId,
      );
      const replacement = replacementModel
        ? customModelPreferredSelection(replacementModel)
        : "";
      saveLocalSettings({
        ...settings,
        context: {
          ...settings.context,
          model_name: replacement,
        },
      });
      setModels((current) =>
        current.filter((model) => customModelEntryId(model) !== modelId),
      );
      return replacement;
    },
    [models],
  );

  const doDeleteModel = async (modelId: string) => {
    setDeletingModel(true);
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/config/custom-models/${encodeURIComponent(modelId)}`,
        {
          method: "DELETE",
          headers: authHeaders(),
        },
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Delete failed: ${res.status}`);
      }
      const replacement = reconcileDeletedModel(modelId);
      if (replacement === null) {
        toast.success(t.settings.model.deleteSuccess);
      } else if (replacement) {
        const replacementLabel =
          models.find((model) =>
            customModelMatchesSelection(model, replacement),
          )?.display_name || replacement;
        toast.success(pageCopy.deletedAndSwitched(replacementLabel));
      } else {
        toast.success(pageCopy.deletedAndReset);
      }
      await fetchModels();
      return true;
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t.settings.model.deleteFailed,
      );
      return false;
    } finally {
      setDeletingModel(false);
    }
  };

  const handleDelete = (modelId: string) => {
    setModelToDelete(modelId);
  };

  const handleReconnect = () => {
    checkGateway();
  };

  const handleDiagnose = useCallback(async () => {
    const issues: string[] = [];
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/config/custom-models`,
        {
          headers: authHeaders(),
          signal: AbortSignal.timeout(5000),
        },
      );
      if (!res.ok) issues.push(t.settings.model.gatewayReturned(res.status));
    } catch (e) {
      swallow(e);
      issues.push(t.settings.model.cannotReachGateway);
    }

    if (issues.length === 0) {
      toast.success(t.settings.model.diagnoseHealthy);
    } else {
      toast.error(
        t.settings.model.diagnoseIssues(issues.map((i) => `• ${i}`).join(" ")),
      );
    }
  }, [t.settings.model]);

  const scrollToSection = useCallback((id: string) => {
    requestAnimationFrame(() => {
      document
        .getElementById(id)
        ?.scrollIntoView({ block: "start", behavior: "smooth" });
    });
  }, []);

  const handleOverviewAddModel = useCallback(() => {
    setEditingModel(null);
    setShowAdd(true);
    scrollToSection("model-settings-custom");
  }, [scrollToSection]);

  const handleOverviewScanLocal = useCallback(() => {
    scrollToSection("model-settings-local");
    window.dispatchEvent(new Event(LOCAL_MODEL_SCAN_EVENT));
  }, [scrollToSection]);

  // Implementation note.
  const defaultModelName = getLocalSettings().context.model_name;

  useEffect(() => {
    const unregisters = [
      registerPageAgentCapability({
        id: "models.custom.list",
        label: "List custom models",
        description:
          "Return current custom model summaries and gateway status.",
        risk: "low",
        riskReasons: [],
        requiresConfirmation: false,
        run: () => ({
          gatewayStatus,
          defaultModelName,
          models: models.map((model) => ({
            id: customModelEntryId(model),
            name: model.name,
            display_name: model.display_name,
            provider: model.provider,
            base_url: model.base_url,
            models: model.models,
            supports_thinking: model.supports_thinking,
            supports_vision: model.supports_vision,
            isDefault: customModelMatchesSelection(model, defaultModelName),
            compat_diagnostic:
              compatDiagnostics.byId[customModelEntryId(model)] ?? null,
          })),
          builtInCompatProfiles: compatProfileCatalog.items.map((item) => ({
            id: item.id,
            profile: item.upstreams?.[0]?.profile,
            score: item.upstreams?.[0]?.compat_score,
            model: item.upstreams?.[0]?.model,
            fallbackCount: countCompatRetries(item),
          })),
        }),
      }),
      registerPageAgentCapability({
        id: "models.custom.openAdd",
        label: "Open add custom model form",
        description: "Open the custom model creation form.",
        risk: "low",
        riskReasons: [],
        requiresConfirmation: false,
        run: () => {
          setEditingModel(null);
          setShowAdd(true);
          return { opened: true };
        },
      }),
      registerPageAgentCapability({
        id: "models.custom.diagnoseGateway",
        label: "Diagnose custom model gateway",
        description: "Check whether the custom model backend API is reachable.",
        risk: "low",
        riskReasons: [],
        requiresConfirmation: false,
        run: async () => {
          await checkGateway();
          return { requested: true };
        },
      }),
      registerPageAgentCapability({
        id: "models.custom.testExisting",
        label: "Test existing custom model",
        description: "Run the backend diagnostic for an existing custom model.",
        risk: "low",
        riskReasons: [],
        requiresConfirmation: false,
        inputSchema: {
          type: "object",
          required: ["name"],
          properties: {
            name: { type: "string" },
          },
        },
        run: async (input) => {
          const name = String(input?.name || "").trim();
          if (!name) throw new Error("name is required");
          const model = models.find((candidate) =>
            customModelMatchesIdentity(candidate, name),
          );
          if (!model) {
            throw new Error(`custom model not found: ${name}`);
          }
          const started = performance.now();
          const res = await fetch(
            `${getBackendBaseURL()}/api/config/custom-models/test`,
            {
              method: "POST",
              headers: jsonAuthHeaders(),
              body: JSON.stringify({ id: customModelEntryId(model) }),
              // Test runs a text ping + a vision canary; give both probes
              // room even on slow reasoning models.
              signal: AbortSignal.timeout(20000),
            },
          );
          const data = await res.json().catch(() => ({}));
          return {
            ok: res.ok && data.ok !== false,
            status: res.status,
            latencyMs: Math.round(performance.now() - started),
            ...data,
          };
        },
      }),
      registerPageAgentCapability({
        id: "models.custom.setDefault",
        label: "Set default custom model",
        description: "Set the local default model by custom model name.",
        risk: "medium",
        riskReasons: ["save"],
        requiresConfirmation: false,
        inputSchema: {
          type: "object",
          required: ["name"],
          properties: {
            name: { type: "string" },
          },
        },
        run: async (input) => {
          const name = String(input?.name || "").trim();
          if (!name) throw new Error("name is required");
          const model = models.find((candidate) =>
            customModelMatchesIdentity(candidate, name),
          );
          if (!model) {
            throw new Error(`custom model not found: ${name}`);
          }
          const selection = customModelPreferredSelection(model);
          const settings = getLocalSettings();
          saveLocalSettings({
            ...settings,
            context: {
              ...settings.context,
              model_name: selection,
            },
          });
          await fetchModels();
          return { defaultModelName: selection };
        },
      }),
      registerPageAgentCapability({
        id: "models.custom.delete",
        label: "Delete custom model",
        description: "Delete a custom model configuration by name.",
        risk: "high",
        riskReasons: ["delete"],
        requiresConfirmation: true,
        inputSchema: {
          type: "object",
          required: ["name"],
          properties: {
            name: { type: "string" },
          },
        },
        run: async (input) => {
          const name = String(input?.name || "").trim();
          if (!name) throw new Error("name is required");
          const model = models.find((candidate) =>
            customModelMatchesIdentity(candidate, name),
          );
          if (!model) {
            throw new Error(`custom model not found: ${name}`);
          }
          const modelId = customModelEntryId(model);
          const res = await fetch(
            `${getBackendBaseURL()}/api/config/custom-models/${encodeURIComponent(modelId)}`,
            {
              method: "DELETE",
              headers: authHeaders(),
            },
          );
          if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.detail || `Delete failed: ${res.status}`);
          }
          const replacement = reconcileDeletedModel(modelId);
          await fetchModels();
          return {
            deleted: modelId,
            defaultModelName:
              replacement === null ? defaultModelName : replacement,
          };
        },
      }),
    ];
    return () => {
      unregisters.forEach((unregister) => unregister());
    };
  }, [
    checkGateway,
    compatDiagnostics.byId,
    compatProfileCatalog.items,
    defaultModelName,
    fetchModels,
    gatewayStatus,
    models,
    reconcileDeletedModel,
  ]);

  const deleteModelConfig = modelToDelete
    ? models.find((model) => customModelEntryId(model) === modelToDelete)
    : undefined;
  const deletingCurrentDefault = deleteModelConfig
    ? customModelMatchesSelection(deleteModelConfig, defaultModelName)
    : modelToDelete !== null && modelToDelete === defaultModelName;
  const deleteReplacementModel = deletingCurrentDefault
    ? models.find((model) => customModelEntryId(model) !== modelToDelete)
    : undefined;
  const deleteReplacement =
    deleteReplacementModel?.display_name ??
    deleteReplacementModel?.name ??
    null;
  const configuredModelCount = models.reduce(
    (count, model) =>
      count + (Array.isArray(model.models) ? model.models.length : 0),
    0,
  );
  const visibleDefaultModel = useMemo(() => {
    const matched = models.find((model) =>
      customModelMatchesSelection(model, defaultModelName),
    );
    if (matched) {
      const configured = Array.isArray(matched.models) ? matched.models : [];
      const selectedId = defaultModelName?.includes("::")
        ? defaultModelName.split("::").at(-1)
        : null;
      return (
        (selectedId && configured.includes(selectedId) ? selectedId : null) ||
        matched.display_name ||
        matched.name
      );
    }
    if (!defaultModelName) return "";
    if (defaultModelName.startsWith("echo-custom-model:v1:")) {
      return pageCopy.noDefault;
    }
    return defaultModelName;
  }, [defaultModelName, models, pageCopy.noDefault]);

  return (
    <div className="min-w-0 max-w-full space-y-6 overflow-x-hidden">
      <ModelSettingsOverview
        copy={pageCopy}
        defaultModelName={visibleDefaultModel}
        customModelCount={models.length}
        modelCount={configuredModelCount}
        gatewayStatus={gatewayStatus}
        onAddModel={handleOverviewAddModel}
        onScanLocal={handleOverviewScanLocal}
        onOpenZen={() =>
          navigate("/workspace/agents?tab=plugins&connect=opencode")
        }
      />

      <CoderEngineSettings />

      {/* ── Models Section ── */}
      <SettingsSection
        className="scroll-mt-6"
        title={
          <span id="model-settings-custom">{pageCopy.connectionsTitle}</span>
        }
        description={pageCopy.connectionsSubtitle}
      >
        {loading ? (
          <div role="status" className="text-muted-foreground text-sm">
            {t.common.loading}
          </div>
        ) : (
          <div className="flex w-full flex-col gap-3">
            {modelsLoadError ? (
              <div
                role="alert"
                className="flex flex-col items-start justify-between gap-3 rounded-lg border border-destructive/20 bg-destructive/[0.04] px-4 py-3 text-sm sm:flex-row sm:items-center"
              >
                <span className="flex items-center gap-2 text-destructive">
                  <AlertTriangleIcon className="size-4 shrink-0" />
                  {t.settings.model.loadFailed}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  className="w-full sm:w-auto"
                  onClick={handleRetryModels}
                >
                  <RefreshCwIcon className="mr-1.5 size-3.5" />
                  {pageCopy.retryLoad}
                </Button>
              </div>
            ) : null}
            {/* Model list */}
            {models.length > 0 || !modelsLoadError ? (
              <ul className="divide-y divide-border rounded-lg border border-border">
                {models.map((m) => {
                  const modelId = customModelEntryId(m);
                  const editRegionId = `model-settings-edit-${encodeURIComponent(modelId)}`;
                  const list = Array.isArray(m.models) ? m.models : [];
                  const displayName = m.display_name || m.name;
                  const isDefault = customModelMatchesSelection(
                    m,
                    defaultModelName,
                  );
                  const modelSlots = (
                    <ul className="mt-1.5 space-y-0.5 font-mono text-xs text-foreground/80">
                      {list.map((id, idx) => (
                        <li
                          key={`${modelId}:${idx}:${id}`}
                          className="flex min-w-0 items-center gap-2"
                        >
                          <span className="shrink-0 rounded border border-border/70 bg-muted/40 px-1.5 py-0.5 font-sans text-xs font-medium text-muted-foreground">
                            {idx === 0 && idx === list.length - 1
                              ? t.settings.model.modelList
                                  .pickerDefaultAndPerformance
                              : idx === 0
                                ? t.settings.model.modelList.pickerDefault
                                : idx === list.length - 1
                                  ? t.settings.model.modelList.performanceTier
                                  : t.settings.model.modelList.fallback}
                          </span>
                          <code
                            className={cn(
                              "truncate rounded bg-muted/60 px-1.5 py-0.5",
                              (displayName.includes("免费") ||
                                /(?:^|[-_])free(?:$|[-_])/i.test(id)) &&
                                "text-success",
                            )}
                          >
                            {id}
                          </code>
                        </li>
                      ))}
                    </ul>
                  );
                  return (
                    <li
                      key={modelId}
                      className={cn(
                        "overflow-hidden",
                        isDefault && "bg-success/50/[0.035]",
                      )}
                    >
                      <div className="flex flex-col items-stretch justify-between gap-4 px-4 py-4 sm:flex-row sm:items-start sm:px-5">
                        <div className="min-w-0 flex-1">
                          <div className="flex min-w-0 flex-wrap items-center gap-2">
                            <div className="truncate text-sm font-medium">
                              {displayName}
                            </div>
                            <span
                              className="shrink-0 rounded-md border border-border bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground dark:border-muted-foreground/40 dark:bg-muted-foreground/10 dark:text-muted-foreground"
                              title={t.settings.model.modelList.hint}
                            >
                              {t.settings.model.modelCount(list.length)}
                            </span>
                          </div>
                          {m.name !== displayName && (
                            <div className="mt-0.5 truncate text-xs text-muted-foreground">
                              {m.name}
                            </div>
                          )}
                          {list.length === 1 ? modelSlots : null}
                          {list.length > 1 ? (
                            <details className="group/models mt-1.5">
                              <summary className="cursor-pointer select-none text-xs text-muted-foreground hover:text-foreground">
                                查看 {list.length} 个模型
                              </summary>
                              {modelSlots}
                            </details>
                          ) : null}
                        </div>
                        <div className="flex shrink-0 flex-wrap items-center justify-start gap-x-3 gap-y-2 sm:max-w-64 sm:justify-end">
                          {isDefault ? (
                            <span className="inline-flex items-center rounded-lg bg-success/10 px-3 py-1 text-xs font-medium text-success dark:bg-success/20 dark:text-success">
                              {t.settings.model.systemDefault}
                            </span>
                          ) : (
                            <button
                              type="button"
                              className="text-xs font-medium text-muted-foreground hover:text-foreground"
                              onClick={() =>
                                handleSetDefault(
                                  customModelPreferredSelection(m),
                                )
                              }
                              aria-label={`${t.settings.model.setAsDefault}: ${displayName}`}
                            >
                              {t.settings.model.setAsDefault}
                            </button>
                          )}
                          <button
                            type="button"
                            className="text-xs font-medium text-chart-7 hover:text-chart-7"
                            onClick={() => {
                              setShowAdd(false);
                              setEditingModel((current) =>
                                current === modelId ? null : modelId,
                              );
                            }}
                            aria-label={`${t.common.edit}: ${displayName}`}
                            aria-expanded={editingModel === modelId}
                            aria-controls={editRegionId}
                          >
                            {t.common.edit}
                          </button>
                          <button
                            type="button"
                            className="text-xs font-medium text-chart-7 hover:text-chart-7"
                            onClick={() => handleDelete(modelId)}
                            aria-label={`${t.common.delete}: ${displayName}`}
                          >
                            {t.common.delete}
                          </button>
                        </div>
                      </div>
                      {editingModel === modelId && (
                        <div
                          id={editRegionId}
                          className="border-t border-border bg-muted/15 px-4 py-4 sm:px-5"
                        >
                          <EditModelForm
                            key={modelId}
                            modelName={modelId}
                            onCancel={() => setEditingModel(null)}
                            onSaved={() => {
                              setEditingModel(null);
                              fetchModels();
                            }}
                          />
                        </div>
                      )}
                    </li>
                  );
                })}
                {models.length === 0 && (
                  <li className="px-5 py-8 text-center text-sm text-muted-foreground">
                    {t.settings.model.emptyCustomModels}
                  </li>
                )}
              </ul>
            ) : null}

            {/* Add form */}
            {showAdd && (
              <div className="mt-4">
                <AddModelForm
                  onCancel={() => setShowAdd(false)}
                  onSaved={() => {
                    setShowAdd(false);
                    fetchModels();
                  }}
                />
              </div>
            )}
          </div>
        )}
      </SettingsSection>

      {/* ── Local-model one-click import ──
          Sits between the custom-models list and the gateway config
          because it's the lowest-friction path *into* the
          custom-models list — a successful import re-runs
          ``fetchModels`` via ``onImported`` so the new row appears
          in the section above without a manual refresh. */}
      <div id="model-settings-local" className="scroll-mt-6">
        <LocalModelsSection onImported={fetchModels} />
      </div>

      <LocalVisionSection />

      {/* Official models */}
      <OfficialModelsSection />

      <details className="group rounded-lg border border-border bg-card/40 p-4">
        <summary
          aria-label={pageCopy.advancedTitle}
          className="cursor-pointer list-none"
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-lg font-semibold">
                {pageCopy.advancedTitle}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                {pageCopy.advancedSubtitle}
              </div>
            </div>
            <span className="rounded-md border border-border px-2 py-1 text-xs text-muted-foreground transition-colors group-open:bg-muted">
              {pageCopy.advancedBadge}
            </span>
          </div>
        </summary>
        <div className="mt-5 space-y-3">
          <AdvancedDisclosure
            title={pageCopy.connectionToolsTitle}
            description={pageCopy.connectionToolsSubtitle}
          >
            <div className="space-y-8">
              <GatewayDiagnosticsSection
                gatewayStatus={gatewayStatus}
                copy={pageCopy}
                onReconnect={handleReconnect}
                onDiagnose={handleDiagnose}
              />
              <ConfiguredCompatDiagnosticsCard
                models={models}
                diagnostics={compatDiagnostics}
                copy={pageCopy}
              />
            </div>
          </AdvancedDisclosure>

          <AdvancedDisclosure
            title={pageCopy.localToolsTitle}
            description={pageCopy.localToolsSubtitle}
          >
            <ModelCookbook />
          </AdvancedDisclosure>

          <AdvancedDisclosure
            title={pageCopy.mixToolsTitle}
            description={pageCopy.mixToolsSubtitle}
          >
            <MixSettingsSection />
          </AdvancedDisclosure>

          <AdvancedDisclosure
            title={pageCopy.providerToolsTitle}
            description={pageCopy.providerToolsSubtitle}
          >
            <BuiltInCompatProfilesCard
              catalog={compatProfileCatalog}
              copy={pageCopy}
            />
          </AdvancedDisclosure>
        </div>
      </details>

      <Dialog
        open={modelToDelete !== null}
        onOpenChange={(nextOpen) => {
          if (!nextOpen && !deletingModel) setModelToDelete(null);
        }}
      >
        <DialogContent
          showCloseButton={false}
          className="w-[min(360px,calc(100vw-2rem))] gap-3 rounded-lg p-4 shadow-xl sm:max-w-[360px]"
        >
          <DialogHeader className="gap-1 text-left">
            <DialogTitle className="text-base">
              {t.settings.model.deleteModelTitle}
            </DialogTitle>
            <DialogDescription className="text-caption leading-5">
              {modelToDelete
                ? t.settings.model.deleteConfirm(
                    deleteModelConfig?.display_name ||
                      deleteModelConfig?.name ||
                      modelToDelete,
                  )
                : ""}
            </DialogDescription>
            {deletingCurrentDefault && (
              <div className="mt-2 flex gap-2 rounded-md border border-warning/30 bg-warning/5 p-2.5 text-xs leading-5 text-warning dark:border-warning/25 dark:bg-warning/10 dark:text-warning">
                <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
                <span>{pageCopy.deletingDefault(deleteReplacement)}</span>
              </div>
            )}
          </DialogHeader>
          <DialogFooter className="mt-1 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              disabled={deletingModel}
              onClick={() => setModelToDelete(null)}
              className="inline-flex h-8 items-center justify-center rounded-md border border-border bg-background px-3 text-caption font-medium text-foreground/80 transition-colors hover:bg-muted disabled:pointer-events-none disabled:opacity-60"
            >
              {t.common.cancel}
            </button>
            <button
              type="button"
              disabled={deletingModel}
              onClick={async () => {
                if (!modelToDelete) return;
                const target = modelToDelete;
                const deleted = await doDeleteModel(target);
                if (deleted) setModelToDelete(null);
              }}
              className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-destructive/25 bg-destructive/[0.07] px-3 text-caption font-medium text-destructive transition-colors hover:border-destructive/35 hover:bg-destructive/[0.11] disabled:pointer-events-none disabled:opacity-60"
            >
              {deletingModel ? (
                <span className="size-3 animate-spin rounded-full border border-current border-t-transparent" />
              ) : (
                <Trash2Icon className="size-3.5" />
              )}
              {t.common.delete}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function LocalVisionSection() {
  const [model, setModel] = useState<NASModel | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const presentError = useCallback((error: unknown) => {
    const raw = error instanceof Error ? error.message : String(error);
    if (/403|admin\/operator role required/i.test(raw)) {
      setMessage("当前账号无权管理本地图片理解，请联系管理员。");
      return;
    }
    setMessage("本地图片理解服务暂时不可用，请稍后重试。");
  }, []);

  const refresh = useCallback(async () => {
    try {
      await startNASService();
      const models = await listNASModels();
      setModel(
        models.find((item) => item.model_id === "vision-default") ?? null,
      );
    } catch (error) {
      presentError(error);
    }
  }, [presentError]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const run = async (action: () => Promise<NASModel>, pending: string) => {
    setBusy(true);
    setMessage(pending);
    try {
      const next = await action();
      setModel(next);
      if (next.status === "loading") {
        const timer = window.setInterval(() => {
          void listNASModels()
            .then((models) => {
              const current = models.find(
                (item) => item.model_id === "vision-default",
              );
              if (current) setModel(current);
              if (current && current.status !== "loading") {
                window.clearInterval(timer);
                setBusy(false);
                setMessage(current.notes);
              }
            })
            .catch(() => undefined);
        }, 2000);
        window.setTimeout(
          () => {
            window.clearInterval(timer);
            setBusy(false);
          },
          15 * 60 * 1000,
        );
      } else {
        setBusy(false);
        setMessage(next.notes);
      }
    } catch (error) {
      setBusy(false);
      presentError(error);
    }
  };

  const downloaded = model?.provider === "local" && Boolean(model.endpoint);
  const enabled = model?.status === "running";

  return (
    <SettingsSection
      title="本地图片理解（CLIP）"
      description="用于图片、视频关键帧的语义检索。模型下载到本机，不上传原始文件。"
    >
      <div className="flex flex-col gap-3 rounded-lg border border-border bg-card/40 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <span
              className={cn(
                "size-2 rounded-full",
                enabled ? "bg-success" : "bg-muted-foreground/40",
              )}
            />
            {enabled ? "已启用" : downloaded ? "已下载，未启用" : "尚未下载"}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            sentence-transformers/clip-ViT-B-32 · 约 600MB
          </p>
          {message ? (
            <p
              role={message.includes("无权") ? "alert" : undefined}
              className={cn(
                "mt-1 text-xs",
                message.includes("无权")
                  ? "text-warning"
                  : "text-muted-foreground",
              )}
            >
              {message}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 gap-2">
          {!downloaded ? (
            <Button
              size="sm"
              onClick={() =>
                void run(
                  () => downloadNASModel("vision-default"),
                  "正在下载模型…",
                )
              }
              disabled={busy || Boolean(message?.includes("无权"))}
            >
              {busy ? "下载中…" : "下载并启用"}
            </Button>
          ) : enabled ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                void run(() => disableNASModel("vision-default"), "正在关闭…")
              }
              disabled={busy}
            >
              关闭
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={() =>
                void run(() => enableNASModel("vision-default"), "正在启用…")
              }
              disabled={busy}
            >
              启用
            </Button>
          )}
        </div>
      </div>
    </SettingsSection>
  );
}

function AdvancedDisclosure({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <details className="group/advanced-item rounded-lg border border-border bg-background/55 px-4 py-3">
      <summary
        aria-label={title}
        className="cursor-pointer list-none rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-foreground">{title}</div>
            <div className="mt-0.5 text-xs leading-5 text-muted-foreground">
              {description}
            </div>
          </div>
          <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground transition-transform group-open/advanced-item:rotate-90" />
        </div>
      </summary>
      <div className="mt-5 border-t border-border pt-5">{children}</div>
    </details>
  );
}

function GatewayDiagnosticsSection({
  gatewayStatus,
  copy,
  onReconnect,
  onDiagnose,
}: {
  gatewayStatus: "connected" | "disconnected" | "checking";
  copy: ReturnType<typeof modelSettingsPageCopy>;
  onReconnect: () => void;
  onDiagnose: () => void;
}) {
  const { t } = useI18n();
  const statusLabel =
    gatewayStatus === "connected"
      ? t.settings.model.connected
      : gatewayStatus === "checking"
        ? t.common.loading
        : t.settings.model.disconnected;

  return (
    <SettingsSection
      title={
        <span id="model-settings-gateway">{t.settings.model.gatewayUrl}</span>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm font-medium">
              {t.settings.model.gatewayUrl}
            </span>
            <span
              role="status"
              className={cn(
                "inline-flex items-center gap-1 rounded-lg px-3 py-1 text-xs font-medium",
                gatewayStatus === "connected" &&
                  "bg-success/10 text-success dark:bg-success/20 dark:text-success",
                gatewayStatus === "disconnected" &&
                  "bg-destructive/10 text-destructive dark:bg-destructive/20 dark:text-destructive",
                gatewayStatus === "checking" && "bg-info/15 text-info",
              )}
            >
              {gatewayStatus === "checking" && (
                <Loader2Icon className="size-3 animate-spin" />
              )}
              {statusLabel}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:flex">
            <Button variant="outline" size="sm" onClick={onReconnect}>
              <RefreshCwIcon className="mr-1 size-3" />
              {t.settings.model.reconnect}
            </Button>
            <Button variant="outline" size="sm" onClick={onDiagnose}>
              <SearchIcon className="mr-1 size-3" />
              {t.settings.model.diagnose}
            </Button>
          </div>
        </div>

        <div className="flex flex-col gap-3 rounded-lg border border-border p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <div className="text-sm font-medium">{t.settings.model.port}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              {t.settings.model.backendUrlHint}
            </div>
          </div>
          <Input
            aria-label={t.settings.model.port}
            className="w-full font-mono text-xs sm:w-56 sm:text-right"
            value={getBackendBaseURL() || copy.sameOriginProxy}
            readOnly
          />
        </div>

        <div className="rounded-lg border border-info/30 bg-info/10 p-4 text-sm">
          <div className="mb-2 font-medium text-info dark:text-info">
            {t.settings.model.connectionHelp}
          </div>
          <ul className="list-disc space-y-1 pl-4 text-xs text-info dark:text-info">
            <li>{t.settings.model.connectionHelpReconnect}</li>
            <li>{t.settings.model.setDefaultHint}</li>
            <li>{t.settings.model.connectionHelpDiagnose}</li>
          </ul>
        </div>
      </div>
    </SettingsSection>
  );
}

function ConfiguredCompatDiagnosticsCard({
  models,
  diagnostics,
  copy,
}: {
  models: ModelConfig[];
  diagnostics: CompatDiagnosticState;
  copy: ReturnType<typeof modelSettingsPageCopy>;
}) {
  const relevantModels =
    diagnostics.status === "ready"
      ? models.filter((model) =>
          Boolean(diagnostics.byId[customModelEntryId(model)]),
        )
      : models;
  if (relevantModels.length === 0) return null;

  return (
    <SettingsSection
      title={copy.configuredDiagnosticsTitle}
      description={copy.configuredDiagnosticsSubtitle}
    >
      <div className="divide-y divide-border rounded-lg border border-border">
        {relevantModels.map((model) => {
          const modelId = customModelEntryId(model);
          return (
            <div
              key={modelId}
              role="group"
              aria-label={model.display_name || model.name}
              className="min-w-0 px-4 py-3"
            >
              <CompatDiagnosticSummary
                diagnostic={diagnostics.byId[modelId]}
                status={diagnostics.status}
              />
            </div>
          );
        })}
      </div>
    </SettingsSection>
  );
}

function BuiltInCompatProfilesCard({
  catalog,
  copy,
}: {
  catalog: CompatProfileCatalogState;
  copy: ReturnType<typeof modelSettingsPageCopy>;
}) {
  const visible = catalog.items.slice(0, 8);
  const remaining = Math.max(0, catalog.items.length - visible.length);
  const loaded = catalog.status === "ready" || catalog.items.length > 0;

  return (
    <SettingsSection
      title={
        <div className="flex w-full items-center justify-between gap-3">
          <span>{copy.compatMatrixTitle}</span>
          <span className="text-xs font-normal text-muted-foreground">
            {loaded
              ? copy.compatProfileCount(catalog.items.length)
              : copy.compatProfilesLoading}
          </span>
        </div>
      }
    >
      <div className="space-y-3">
        <div className="text-sm leading-6 text-muted-foreground">
          {copy.compatMatrixDescription}
        </div>
        {catalog.status === "loading" && catalog.items.length === 0 ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2Icon className="size-4 animate-spin" />
            {copy.compatProfilesLoading}
          </div>
        ) : catalog.status === "error" && catalog.items.length === 0 ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <AlertTriangleIcon className="size-4 text-warning" />
            {copy.compatProfilesUnavailable}
          </div>
        ) : (
          <div className="grid gap-2 xl:grid-cols-2">
            {visible.map((item) => {
              const upstream = item.upstreams?.[0];
              const score = summarizeCompatScoreRange(item);
              const removed = collectCompatFields(item, "removed_fields");
              const retryReasons = summarizeCompatRetryReasons(item);
              const hints = collectCompatProfileText(
                item,
                "normalization_hints",
              );
              return (
                <div
                  key={item.id}
                  className="rounded-lg border border-border-default bg-background/50 px-3 py-2"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-medium">
                          {upstream?.profile_display_name || item.id}
                        </span>
                        {score && (
                          <span className="rounded border border-border px-1.5 py-0.5 text-xs text-muted-foreground">
                            {copy.compatScore(
                              score.min === score.max
                                ? `${score.min}`
                                : `${score.min}-${score.max}`,
                            )}
                          </span>
                        )}
                      </div>
                      <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
                        {upstream?.model || item.id}
                      </div>
                    </div>
                    <span className="shrink-0 rounded border border-border px-1.5 py-0.5 text-xs text-muted-foreground">
                      {copy.compatFallbacks(countCompatRetries(item))}
                    </span>
                  </div>
                  <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                    {hints.length > 0 && (
                      <div title={hints.join(", ")}>
                        {copy.compatNormalize(
                          `${hints.slice(0, 4).join(", ")}${hints.length > 4 ? "…" : ""}`,
                        )}
                      </div>
                    )}
                    {removed.length > 0 && (
                      <div title={removed.join(", ")}>
                        {copy.compatDrops(
                          `${removed.slice(0, 5).join(", ")}${removed.length > 5 ? "…" : ""}`,
                        )}
                      </div>
                    )}
                    {retryReasons.length > 0 && (
                      <div title={retryReasons.join(", ")}>
                        {copy.compatRetries(
                          `${retryReasons.slice(0, 4).join(", ")}${retryReasons.length > 4 ? "…" : ""}`,
                        )}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
        {remaining > 0 && (
          <div className="text-xs text-muted-foreground">
            {copy.compatRemaining(remaining)}
          </div>
        )}
      </div>
    </SettingsSection>
  );
}

function CompatDiagnosticSummary({
  diagnostic,
  status,
}: {
  diagnostic?: CompatDiagnostic;
  status: CompatDiagnosticState["status"];
}) {
  const { t, locale } = useI18n();
  const pageCopy = modelSettingsPageCopy(locale);

  if (status === "loading" && !diagnostic) {
    return (
      <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2Icon className="size-3 animate-spin" />
        {t.settings.model.compatDiagnostics.loading}
      </div>
    );
  }

  if (!diagnostic) {
    return status === "error" ? (
      <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
        <AlertTriangleIcon className="size-3.5 text-warning" />
        {t.settings.model.compatDiagnostics.unavailable}
      </div>
    ) : null;
  }

  if (!diagnostic.applicable) {
    return (
      <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
        <InfoIcon className="size-3.5" />
        <span>
          {diagnostic.reason ||
            t.settings.model.compatDiagnostics.notApplicable}
        </span>
      </div>
    );
  }

  const profiles = summarizeCompatProfiles(diagnostic);
  const removed = collectCompatFields(diagnostic, "removed_fields");
  const changed = collectCompatFields(diagnostic, "changed_fields");
  const added = collectCompatFields(diagnostic, "added_fields");
  const fallbackCount = countCompatRetries(diagnostic);
  const retryReasons = summarizeCompatRetryReasons(diagnostic);
  const scoreRange = summarizeCompatScoreRange(diagnostic);
  const normalizationHints = collectCompatProfileText(
    diagnostic,
    "normalization_hints",
  );
  const compatibilityNotes = collectCompatProfileText(
    diagnostic,
    "compatibility_notes",
  );
  const headerNames = diagnostic.default_header_names ?? [];
  const scoreLabel = scoreRange
    ? scoreRange.min === scoreRange.max
      ? `${scoreRange.min}`
      : `${scoreRange.min}-${scoreRange.max}`
    : null;
  const hasDetails =
    removed.length > 0 ||
    changed.length > 0 ||
    added.length > 0 ||
    normalizationHints.length > 0 ||
    compatibilityNotes.length > 0 ||
    retryReasons.length > 0;

  return (
    <div className="mt-3 min-w-0 max-w-full space-y-2 overflow-hidden border-l border-border pl-3 text-xs text-muted-foreground">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="inline-flex items-center gap-1 font-medium text-foreground/80">
          <CheckCircle2Icon className="size-3.5 text-success" />
          {t.settings.model.compatDiagnostics.title}
        </span>
        {profiles.length > 0 && (
          <span className="max-w-full truncate rounded border border-border px-1.5 py-0.5">
            {profiles.join(", ")}
          </span>
        )}
        <span className="rounded border border-border px-1.5 py-0.5">
          {t.settings.model.compatDiagnostics.fallbacks(fallbackCount)}
        </span>
        {scoreLabel && (
          <span className="rounded border border-border px-1.5 py-0.5">
            {t.settings.model.compatDiagnostics.compatScore(scoreLabel)}
          </span>
        )}
        {headerNames.length > 0 && (
          <span className="rounded border border-border px-1.5 py-0.5">
            {t.settings.model.compatDiagnostics.headers(headerNames.join(", "))}
          </span>
        )}
      </div>
      {hasDetails && (
        <details className="group/compat">
          <summary className="w-fit cursor-pointer select-none rounded px-1 py-0.5 font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
            {pageCopy.compatDetails}
          </summary>
          <div className="mt-2 space-y-1.5 rounded-md bg-muted/35 p-2.5">
            {(removed.length > 0 || changed.length > 0 || added.length > 0) && (
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                {removed.length > 0 && (
                  <span title={removed.join(", ")}>
                    {t.settings.model.compatDiagnostics.removedFields(
                      removed.slice(0, 5).join(", "),
                      removed.length,
                    )}
                  </span>
                )}
                {changed.length > 0 && (
                  <span title={changed.join(", ")}>
                    {t.settings.model.compatDiagnostics.changedFields(
                      changed.slice(0, 5).join(", "),
                      changed.length,
                    )}
                  </span>
                )}
                {added.length > 0 && (
                  <span title={added.join(", ")}>
                    {t.settings.model.compatDiagnostics.addedFields(
                      added.slice(0, 5).join(", "),
                      added.length,
                    )}
                  </span>
                )}
              </div>
            )}
            {normalizationHints.length > 0 && (
              <div title={normalizationHints.join(", ")}>
                {t.settings.model.compatDiagnostics.normalizationHints(
                  normalizationHints.slice(0, 5).join(", "),
                  normalizationHints.length,
                )}
              </div>
            )}
            {compatibilityNotes.length > 0 && (
              <div title={compatibilityNotes.join("; ")}>
                {t.settings.model.compatDiagnostics.compatibilityNotes(
                  compatibilityNotes.slice(0, 2).join("; "),
                  compatibilityNotes.length,
                )}
              </div>
            )}
            {retryReasons.length > 0 && (
              <div title={retryReasons.join(", ")}>
                {t.settings.model.compatDiagnostics.retryReasons(
                  retryReasons.slice(0, 4).join(", "),
                  retryReasons.length,
                )}
              </div>
            )}
          </div>
        </details>
      )}
    </div>
  );
}

// Official models from the account-backed gateway.
//
// Reads from the oct gateway model list when the official gateway is enabled.
// When the bridge is disabled (503) or the user hasn't linked their account
// yet (404), hides the section entirely.
//
interface UpstreamModel {
  id: string;
  display_name?: string | null;
  owned_by?: string | null;
  multiplier?: string | null;
  recommended?: boolean;
}

function OfficialModelsSection() {
  const { t } = useI18n();
  const [models, setModels] = useState<UpstreamModel[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailableReason, setUnavailableReason] = useState<string | null>(
    null,
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(
          `${getBackendBaseURL()}/api/oct/openai/v1/models`,
          { headers: authHeaders() },
        );
        if (cancelled) return;
        if (r.status === 404) {
          setUnavailableReason(t.settings.model.accountNotLinked);
          setModels([]);
          return;
        }
        if (r.status === 503) {
          setUnavailableReason(t.settings.model.gatewayNotEnabled);
          setModels([]);
          return;
        }
        if (!r.ok) {
          setUnavailableReason(`upstream ${r.status}`);
          setModels([]);
          return;
        }
        const j = await r.json();
        setModels(Array.isArray(j?.data) ? j.data : []);
      } catch (err) {
        swallow(err);
        if (!cancelled) {
          setUnavailableReason(
            err instanceof Error ? err.message : String(err),
          );
          setModels([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [t.settings.model.gatewayNotEnabled, t.settings.model.accountNotLinked]);

  if (loading) {
    return (
      <SettingsSection title={t.settings.model.officialModels}>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2Icon className="size-4 animate-spin" /> {t.common.loading}
        </div>
      </SettingsSection>
    );
  }

  // Hide the whole section when the bridge isn't usable — the rest of
  // the settings page (custom models + gateway) is fully self-contained.
  if (unavailableReason) {
    return null;
  }

  // Build rows from the backend catalog. Skip the synthetic "auto"
  // / provider-specific pseudo-models the gateway may advertise.
  const rows = (models ?? [])
    .filter((m) => !/^auto$/i.test(m.id))
    .map((m) => ({ upstream: m }));

  return (
    <SettingsSection title={t.settings.model.officialModels}>
      <div className="rounded-lg border border-border divide-y divide-border">
        {rows.length === 0 && (
          <div className="px-5 py-8 text-center text-sm text-muted-foreground">
            {t.settings.model.noOfficialModels}
          </div>
        )}
        {rows.map(({ upstream }) => (
          <div
            key={upstream.id}
            className="flex items-center justify-between px-5 py-4"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">
                  {upstream.display_name || upstream.id}
                </span>
                {upstream.recommended && (
                  <span className="rounded border border-success/40 px-1.5 py-0.5 text-xs font-medium text-success">
                    {t.modelPicker.recommended}
                  </span>
                )}
              </div>
              <div className="text-xs text-muted-foreground">{upstream.id}</div>
            </div>
            <div className="flex items-center gap-3">
              <span className="rounded-lg bg-muted px-2 py-0.5 text-xs tabular-nums text-muted-foreground">
                {upstream.multiplier ?? "1.0x"}
              </span>
              <span className="text-xs text-muted-foreground">
                {t.settings.model.gatewayHosted}
              </span>
            </div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        {t.settings.model.officialModelsHint}
      </p>
    </SettingsSection>
  );
}

// ── Edit model form ────────────────────────────────────────────
//
// On mount, fetches the model's full config via the /edit endpoint so
// every field is round-tripped from config.yaml instead of showing blank
// placeholders. The API key itself is never returned in cleartext — if
// it was stored as `$ENV_VAR` the form displays the variable name (safe)
// and the user can leave the field blank to keep it; typing a new value
// overrides. Literal keys are shown as a "•••" placeholder.
function EditModelForm({
  modelName,
  onCancel,
  onSaved,
}: {
  modelName: string;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const [displayName, setDisplayName] = useState("");
  // Open-ended list of upstream model ids this entry can dispatch
  // to. Index 0 is the picker default, index -1 is the strongest
  // slot for Auto mode's performance verdict. Mirrors the
  // ``models`` field on the custom-model entry.
  const [models, setModels] = useState<string[]>([""]);
  const [apiKey, setApiKey] = useState("");
  const [apiKeyPlaceholder, setApiKeyPlaceholder] = useState(
    t.settings.model.apiKeyPlaceholder,
  );
  const [baseUrl, setBaseUrl] = useState("");
  const [thinking, setThinking] = useState(false);
  const [defaultReasoningEffort, setDefaultReasoningEffort] = useState<
    DefaultReasoningEffort | undefined
  >(undefined);
  const [reasoningEfforts, setReasoningEfforts] = useState<
    string[] | null | undefined
  >(undefined);
  const [vision, setVision] = useState(false);
  // True when the last connection test confirmed the model has no
  // vision — the toggle is then locked off until a new test says
  // otherwise.
  const [visionLocked, setVisionLocked] = useState(false);
  const [millionContext, setMillionContext] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [headersText, setHeadersText] = useState("");
  const [showHeaders, setShowHeaders] = useState(false);
  const [loading, setLoading] = useState(true);
  // Provider is derived from the Base URL — no manual entry. Anthropic
  // base URLs need their own protocol; everything else speaks
  // OpenAI-compatible. The chip surfaces what the runtime will actually
  // do with this entry, so the user can spot a wrong base URL early.
  const detectedProvider = (() => {
    const u = (baseUrl || "").toLowerCase();
    if (!u) return "—";
    if (u.includes("anthropic.com")) return "anthropic";
    if (u.includes("googleapis.com") || u.includes("generativelanguage"))
      return "gemini";
    if (u.includes("ollama") || /:11434(\b|\/)/.test(u)) return "ollama";
    return "openai";
  })();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [testStatus, setTestStatus] = useState<TestStatus>("idle");
  const [testMessage, setTestMessage] = useState("");
  const [testLatency, setTestLatency] = useState<number | null>(null);

  // One-shot config fetch. Empty deps — the component remounts when a
  // different model is selected (key={modelName} at the call site).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Backend doesn't return api_key (stays on server). List
        // endpoint gives us everything else; we look up this model id.
        const res = await fetch(
          `${getBackendBaseURL()}/api/config/custom-models`,
          { headers: authHeaders() },
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const list = (await res.json()).models as Array<
          Record<string, unknown>
        >;
        const d = list.find((m) => m.id === modelName) ?? {};
        if (cancelled) return;
        setDisplayName((d.display_name as string) || (d.name as string) || "");
        const rawModels = Array.isArray(d.models)
          ? (d.models as unknown[])
          : [];
        const normalised = rawModels
          .map((m) => (typeof m === "string" ? m.trim() : ""))
          .filter((m) => m.length > 0);
        setModels(normalised.length > 0 ? normalised : [""]);
        setBaseUrl((d.base_url as string) || "");
        setThinking(!!d.supports_thinking);
        setDefaultReasoningEffort(
          (d.default_reasoning_effort as DefaultReasoningEffort | undefined) ??
            undefined,
        );
        setReasoningEfforts(
          Array.isArray(d.reasoning_efforts)
            ? (d.reasoning_efforts as string[])
            : undefined,
        );
        setVision(!!d.supports_vision);
        setMillionContext(
          d.enable_1m_context === true ||
            Number(d.context_window || 0) >= 1_000_000,
        );
        setHeadersText("");
        setShowHeaders(false);
        setApiKeyPlaceholder(
          d.has_api_key
            ? `••• · ${t.settings.model.keepApiKeyHint}`
            : t.settings.model.apiKeyPlaceholder,
        );
      } catch (e) {
        swallow(e);
        if (!cancelled)
          setError(
            e instanceof Error ? e.message : t.settings.model.networkError,
          );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    modelName,
    t.settings.model.apiKeyPlaceholder,
    t.settings.model.keepApiKeyHint,
    t.settings.model.networkError,
  ]);

  const handleModelChange = (idx: number, value: string) => {
    setModels((prev) => prev.map((m, i) => (i === idx ? value : m)));
  };
  const handleModelAdd = () => {
    setModels((prev) => [...prev, ""]);
  };
  const handleModelRemove = (idx: number) => {
    setModels((prev) =>
      prev.length <= 1 ? prev : prev.filter((_, i) => i !== idx),
    );
  };

  const handleSave = async () => {
    setSaving(true);
    setError("");
    // Drop empty rows before persisting so the backend never sees
    // a trailing blank in the models list. The UI still shows the
    // last row even if it's empty, so the user can keep typing.
    const cleanedModels = models
      .map((m) => m.trim())
      .filter((m) => m.length > 0);
    if (cleanedModels.length === 0) {
      setError(t.settings.model.modelList.empty);
      setSaving(false);
      return;
    }
    const baseUrlErr = validateBaseUrl(baseUrl);
    if (baseUrlErr) {
      setError(baseUrlErr);
      setSaving(false);
      return;
    }
    // Save is gated on the connection test: a model that can't be
    // reached is useless to persist. Re-run it here so a config change
    // made after a successful test can't slip through.
    const testError = await handleTest();
    if (testError) {
      setError(t.settings.model.saveRequiresTestPass + "：" + testError);
      setSaving(false);
      return;
    }
    const body: Record<string, unknown> = {};
    // Keep a display name on every entry. It is editable independently
    // of the stable connection id and the upstream model ids.
    body.display_name = displayName || modelName;
    if (apiKey) body.api_key = apiKey;
    if (baseUrl) body.base_url = baseUrl;
    body.supports_thinking = thinking;
    body.default_reasoning_effort = defaultReasoningEffort ?? null;
    body.supports_vision = vision;
    body.context_window = 256_000;
    body.enable_1m_context = millionContext;
    // Always send the full models list — backend normalises and
    // persists verbatim, replacing any prior binding.
    body.models = cleanedModels;
    // Always send default_headers so clearing the textarea clears the
    // persisted yaml entry. The backend treats {} as "remove the key".
    body.default_headers = parseHeadersText(headersText);

    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/config/custom-models/${encodeURIComponent(modelName)}`,
        {
          method: "PUT",
          headers: jsonAuthHeaders(),
          body: JSON.stringify(body),
        },
      );
      if (!res.ok) {
        const data = await res.json();
        setError(data.detail || t.settings.model.updateFailed);
        return;
      }
      toast.success(t.settings.model.saveSuccess);
      onSaved();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t.settings.model.networkError);
    } finally {
      setSaving(false);
    }
  };

  // Returns null when the connection test passed, otherwise the error
  // message. ``handleSave`` calls this first and refuses to persist a
  // model that has not passed the test.
  const handleTest = async (): Promise<string | null> => {
    if (!baseUrl) {
      const msg = t.settings.model.fillRequiredBeforeTest;
      setTestStatus("fail");
      setTestMessage(msg);
      return msg;
    }
    const baseUrlErr = validateBaseUrl(baseUrl);
    if (baseUrlErr) {
      setTestStatus("fail");
      setTestMessage(baseUrlErr);
      return baseUrlErr;
    }
    setTestStatus("testing");
    setTestMessage("");
    setTestLatency(null);
    const started = performance.now();
    // Test against the first non-empty model id (the picker default).
    const firstModel =
      models.map((m) => m.trim()).find((m) => m.length > 0) || modelName;
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/config/custom-models/test`,
        {
          method: "POST",
          headers: jsonAuthHeaders(),
          body: JSON.stringify({
            id: modelName,
            base_url: baseUrl,
            api_key: apiKey || undefined,
            model: firstModel,
            provider: baseUrl.includes("anthropic.com")
              ? "anthropic"
              : undefined,
            default_headers: parseHeadersText(headersText),
          }),
          // Test runs a text ping + a vision canary; give both probes
          // room even on slow reasoning models.
          signal: AbortSignal.timeout(20000),
        },
      );
      const latency = Math.round(performance.now() - started);
      setTestLatency(latency);
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        const msg = data.error || `HTTP ${res.status}`;
        setTestStatus("fail");
        setTestMessage(msg);
        return msg;
      } else {
        setTestStatus("success");
        // Vision auto-detection from the test probe. A confirmed
        // ``false`` locks the toggle off; ``true`` enables + checks it.
        const visionNote =
          data.supports_vision === true
            ? t.settings.model.visionDetected
            : data.supports_vision === false
              ? t.settings.model.visionNotSupported
              : "";
        if (data.supports_vision === true) {
          setVision(true);
          setVisionLocked(false);
        } else if (data.supports_vision === false) {
          setVision(false);
          setVisionLocked(true);
        }
        setTestMessage(
          [data.message || t.settings.model.saveSuccess, visionNote]
            .filter(Boolean)
            .join(" · "),
        );
        return null;
      }
    } catch (e: unknown) {
      const msg =
        e instanceof Error ? e.message : t.settings.model.networkError;
      setTestStatus("fail");
      setTestMessage(msg);
      return msg;
    }
  };

  return (
    <div className="rounded-lg border border-border p-4 space-y-3">
      <div className="text-sm font-medium">
        {t.settings.model.editModelTitle(modelName)}
      </div>
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2Icon className="size-3.5 animate-spin" />
          {t.common.loading}
        </div>
      ) : (
        <>
          <div>
            <label
              htmlFor="edit-model-display-name"
              className="text-xs text-muted-foreground"
            >
              {t.settings.model.displayName}
            </label>
            <Input
              id="edit-model-display-name"
              name="echo-edit-model-display-name"
              className="mt-1"
              autoComplete="off"
              data-1p-ignore="true"
              data-lpignore="true"
              data-form-type="other"
              placeholder={t.settings.model.displayNamePlaceholder}
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-muted-foreground">
                {t.settings.model.providerLabel}
              </label>
              <div className="flex h-9 items-center rounded-md border border-input bg-muted/40 px-3 text-sm">
                <span className="font-mono">{detectedProvider}</span>
                <span className="ml-2 text-xs text-muted-foreground/70">
                  {t.settings.model.providerAutoHint}
                </span>
              </div>
            </div>
            <div>
              <label
                htmlFor="edit-model-api-key"
                className="text-xs text-muted-foreground"
              >
                {t.settings.model.keepApiKeyHint}
              </label>
              <div className="relative">
                <Input
                  id="edit-model-api-key"
                  name="echo-edit-model-api-key"
                  className="pr-10"
                  type={showKey ? "text" : "password"}
                  autoComplete="new-password"
                  data-1p-ignore="true"
                  data-lpignore="true"
                  data-form-type="other"
                  spellCheck={false}
                  placeholder={apiKeyPlaceholder}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                />
                <button
                  type="button"
                  aria-label={
                    showKey
                      ? t.settings.model.hideApiKey
                      : t.settings.model.showApiKey
                  }
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  onClick={() => setShowKey(!showKey)}
                >
                  {showKey ? (
                    <EyeOffIcon className="size-4" />
                  ) : (
                    <EyeIcon className="size-4" />
                  )}
                </button>
              </div>
            </div>
          </div>
          <div>
            <label className="text-xs text-muted-foreground">
              {t.settings.model.baseUrlLabel}
            </label>
            <Input
              placeholder={t.settings.model.baseUrlPlaceholder}
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </div>

          <div>
            <div className="mb-1.5 flex items-baseline justify-between">
              <label className="text-xs text-muted-foreground">
                {t.settings.model.modelList.label}
              </label>
              <span className="text-xs text-muted-foreground/70">
                {t.settings.model.modelList.hint}
              </span>
            </div>
            <ul className="space-y-1.5">
              {models.map((id, idx) => (
                <li
                  key={`edit-model-${idx}`}
                  className="flex items-center gap-1.5"
                >
                  <span
                    className="w-4 shrink-0 text-right text-xs text-muted-foreground/60 tabular-nums"
                    title={
                      idx === 0
                        ? t.settings.model.modelList.label
                        : idx === models.length - 1
                          ? t.settings.model.modelList.label
                          : ""
                    }
                  >
                    {idx === 0 ? "★" : idx === models.length - 1 ? "▴" : "·"}
                  </span>
                  <Input
                    className="flex-1 font-mono text-xs"
                    placeholder={t.settings.model.modelList.label}
                    value={id}
                    onChange={(e) => handleModelChange(idx, e.target.value)}
                    disabled={loading}
                  />
                  <button
                    type="button"
                    className={cn(
                      "flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-border-default text-muted-foreground transition-colors",
                      "hover:border-destructive/50 hover:bg-destructive/10 hover:text-destructive",
                      "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-border-default disabled:hover:bg-transparent disabled:hover:text-muted-foreground",
                    )}
                    onClick={() => handleModelRemove(idx)}
                    disabled={loading || models.length <= 1}
                    title={t.settings.model.modelList.removeTooltip}
                    aria-label={t.settings.model.modelList.removeTooltip}
                  >
                    <XCircleIcon className="size-4" />
                  </button>
                </li>
              ))}
            </ul>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-2 w-full border-dashed"
              onClick={handleModelAdd}
              disabled={loading}
            >
              <PlusIcon className="mr-1 h-3 w-3" />{" "}
              {t.settings.model.modelList.addButton}
            </Button>
          </div>

          <div className="rounded-lg border border-border-default bg-muted/20">
            <button
              type="button"
              aria-expanded={showHeaders}
              onClick={() => setShowHeaders((v) => !v)}
              className="flex w-full items-center justify-between px-3 py-2 text-xs font-medium hover:bg-muted/40"
            >
              <span>
                {t.settings.model.extraHeadersTitle}
                {(() => {
                  const n = Object.keys(parseHeadersText(headersText)).length;
                  return n > 0 ? ` (${n})` : "";
                })()}
              </span>
              <span className="text-xs text-muted-foreground">
                {showHeaders ? "▾" : "▸"}
              </span>
            </button>
            {showHeaders && (
              <div className="space-y-2 border-t border-border-default px-3 py-3">
                <textarea
                  value={headersText}
                  onChange={(e) => setHeadersText(e.target.value)}
                  placeholder={t.settings.model.extraHeadersPlaceholder}
                  spellCheck={false}
                  rows={3}
                  className="w-full resize-y rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs shadow-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                />
                <p className="text-xs text-muted-foreground">
                  {t.settings.model.extraHeadersHint}
                </p>
              </div>
            )}
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="flex items-center gap-2 pt-5">
              <Switch checked={thinking} onCheckedChange={setThinking} />{" "}
              <span className="text-xs">{t.settings.model.thinkingLabel}</span>
            </div>
            <div className="flex flex-col items-start gap-1 pt-5">
              <div className="flex items-center gap-2">
                <Switch
                  checked={vision}
                  onCheckedChange={setVision}
                  disabled={visionLocked}
                />{" "}
                <span className="text-xs">{t.settings.model.visionLabel}</span>
              </div>
              {visionLocked && (
                <span
                  className="text-[11px] leading-tight text-muted-foreground"
                  role="status"
                >
                  {t.settings.model.visionNotSupported}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 pt-5">
              <Switch
                checked={millionContext}
                onCheckedChange={setMillionContext}
              />{" "}
              <span className="text-xs">
                {t.settings.model.millionContextLabel}
              </span>
            </div>
          </div>

          <DefaultEffortSelect
            value={defaultReasoningEffort}
            reasoningEfforts={reasoningEfforts}
            onChange={setDefaultReasoningEffort}
          />

          {/* Test status + buttons */}
          <div className="flex items-center justify-between rounded-lg border border-border px-4 py-3">
            <div className="flex items-center gap-2 text-sm">
              {testStatus === "idle" && (
                <>
                  <div className="h-2.5 w-2.5 rounded-lg bg-muted-foreground/40" />
                  <span className="text-muted-foreground">
                    {t.settings.model.notTested}
                  </span>
                </>
              )}
              {testStatus === "testing" && (
                <>
                  <Loader2Icon className="h-4 w-4 animate-spin text-info" />
                  <span className="text-info">{t.common.loading}</span>
                </>
              )}
              {testStatus === "success" && (
                <>
                  <CheckCircle2Icon className="h-4 w-4 text-success" />
                  <span className="text-success">
                    {testMessage}
                    {testLatency != null ? ` (${testLatency}ms)` : ""}
                  </span>
                </>
              )}
              {testStatus === "fail" && (
                <>
                  <XCircleIcon className="h-4 w-4 text-destructive" />
                  <span className="text-destructive">{testMessage}</span>
                </>
              )}
              <span className="text-xs text-muted-foreground ml-2">
                {t.settings.model.testEndpointHint}
              </span>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleTest}
                disabled={testStatus === "testing" || loading}
              >
                <WifiIcon className="mr-1 h-3 w-3" />{" "}
                {t.settings.model.testConnection}
              </Button>
            </div>
          </div>
        </>
      )}
      {error && <div className="text-xs text-destructive">{error}</div>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          {t.common.cancel}
        </Button>
        <Button
          size="sm"
          className="bg-chart-7 hover:bg-chart-7/90 text-white"
          onClick={handleSave}
          disabled={saving || loading}
        >
          {saving ? t.common.loading : t.common.save}
        </Button>
      </div>
    </div>
  );
}

// ── Add model form ─────────────────────────────────────────────
function AddModelForm({
  onCancel,
  onSaved,
}: {
  onCancel: () => void;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const getProviderLabel = (value: string): string => {
    switch (value) {
      case "zhipu":
        return t.settings.model.providers.zhipu;
      case "aliyun":
        return t.settings.model.providers.aliyun;
      case "tencent":
        return t.settings.model.providers.tencent;
      case "volcengine":
        return t.settings.model.providers.volcengine;
      default:
        return PROVIDERS.find((p) => p.value === value)?.label ?? value;
    }
  };
  const [provider, setProvider] = useState("openai");
  const [protocol, setProtocol] = useState("openai");
  // Open-ended list of upstream model ids — matches the edit form
  // shape. Index 0 is the picker default, index -1 is the strongest
  // slot for Auto mode.
  const [models, setModels] = useState<string[]>([""]);
  const [displayName, setDisplayName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1");
  const [thinking, setThinking] = useState(false);
  const [defaultReasoningEffort, setDefaultReasoningEffort] = useState<
    DefaultReasoningEffort | undefined
  >(undefined);
  const [vision, setVision] = useState(false);
  // True when the last connection test confirmed the model has no
  // vision — the toggle is then locked off until a new test says
  // otherwise.
  const [visionLocked, setVisionLocked] = useState(false);
  const [millionContext, setMillionContext] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [testStatus, setTestStatus] = useState<TestStatus>("idle");
  const [testMessage, setTestMessage] = useState("");
  const [testLatency, setTestLatency] = useState<number | null>(null);
  // Extra HTTP headers. Stored as freeform text so users can paste
  // multiple lines; parsed into a dict only when submitting.
  const [headersText, setHeadersText] = useState("");
  const [showHeaders, setShowHeaders] = useState(false);

  const handleProviderChange = (value: string) => {
    setProvider(value);
    const preset = PROVIDERS.find((p) => p.value === value);
    if (preset) {
      setBaseUrl(preset.baseUrl);
      setProtocol(preset.protocol);
    }
  };

  const handleModelChange = (idx: number, value: string) => {
    setModels((prev) => prev.map((m, i) => (i === idx ? value : m)));
    if (/glm-5\.2|deepseek-v4-(flash|pro)/i.test(value)) {
      setMillionContext(true);
    }
  };
  const handleModelAdd = () => {
    setModels((prev) => [...prev, ""]);
  };
  const handleModelRemove = (idx: number) => {
    setModels((prev) =>
      prev.length <= 1 ? prev : prev.filter((_, i) => i !== idx),
    );
  };

  // Returns null when the connection test passed, otherwise the error
  // message. ``handleSave`` calls this first and refuses to persist a
  // model that has not passed the test.
  const handleTest = async (): Promise<string | null> => {
    const firstModel = models.map((m) => m.trim()).find((m) => m.length > 0);
    if (!apiKey || !baseUrl || !firstModel) {
      const msg = t.settings.model.fillRequiredBeforeTest;
      setTestStatus("fail");
      setTestMessage(msg);
      return msg;
    }
    const baseUrlErr = validateBaseUrl(baseUrl);
    if (baseUrlErr) {
      setTestStatus("fail");
      setTestMessage(baseUrlErr);
      return baseUrlErr;
    }
    setTestStatus("testing");
    setTestMessage("");
    setTestLatency(null);
    const started = performance.now();
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/config/custom-models/test`,
        {
          method: "POST",
          headers: jsonAuthHeaders(),
          body: JSON.stringify({
            provider: protocol === "anthropic" ? "anthropic" : "openai",
            base_url: baseUrl,
            api_key: apiKey,
            model: firstModel,
            default_headers: parseHeadersText(headersText),
          }),
          // Test runs a text ping + a vision canary; give both probes
          // room even on slow reasoning models.
          signal: AbortSignal.timeout(20000),
        },
      );
      const latency = Math.round(performance.now() - started);
      setTestLatency(latency);
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        const msg = data.error || `HTTP ${res.status}`;
        setTestStatus("fail");
        setTestMessage(msg);
        return msg;
      } else {
        setTestStatus("success");
        // Vision auto-detection from the test probe. A confirmed
        // ``false`` locks the toggle off; ``true`` enables + checks it.
        const visionNote =
          data.supports_vision === true
            ? t.settings.model.visionDetected
            : data.supports_vision === false
              ? t.settings.model.visionNotSupported
              : "";
        if (data.supports_vision === true) {
          setVision(true);
          setVisionLocked(false);
        } else if (data.supports_vision === false) {
          setVision(false);
          setVisionLocked(true);
        }
        setTestMessage(
          [data.message || t.settings.model.saveSuccess, visionNote]
            .filter(Boolean)
            .join(" · "),
        );
        return null;
      }
    } catch (e: unknown) {
      const msg =
        e instanceof Error ? e.message : t.settings.model.networkError;
      setTestStatus("fail");
      setTestMessage(msg);
      return msg;
    }
  };

  const handleSave = async () => {
    const cleanedModels = models
      .map((m) => m.trim())
      .filter((m) => m.length > 0);
    if (!apiKey || !baseUrl || cleanedModels.length === 0) {
      setError(
        cleanedModels.length === 0
          ? t.settings.model.modelList.empty
          : t.settings.model.requiredFields,
      );
      return;
    }
    setSaving(true);
    setError("");
    const baseUrlErr = validateBaseUrl(baseUrl);
    if (baseUrlErr) {
      setError(baseUrlErr);
      setSaving(false);
      return;
    }
    // Save is gated on the connection test: a model that can't be
    // reached is useless to persist. Re-run it here so a config change
    // made after a successful test can't slip through.
    const testError = await handleTest();
    if (testError) {
      setError(t.settings.model.saveRequiresTestPass + "：" + testError);
      setSaving(false);
      return;
    }
    // The first non-empty model id doubles as the entry id, since
    // ids have to be filename-safe and the picker shows the model
    // name the user just typed. Same convention as the previous
    // single-model layout. We already early-returned when
    // ``cleanedModels`` is empty, so index 0 is safe.
    const firstModel = cleanedModels[0] ?? "";
    const id = firstModel.replace(/[^a-zA-Z0-9._-]/g, "-");
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/config/custom-models/${encodeURIComponent(id)}`,
        {
          method: "PUT",
          headers: jsonAuthHeaders(),
          body: JSON.stringify({
            name: id,
            display_name: displayName || firstModel,
            provider: protocol === "anthropic" ? "anthropic" : "openai",
            base_url: baseUrl,
            api_key: apiKey,
            models: cleanedModels,
            supports_thinking: thinking,
            default_reasoning_effort: defaultReasoningEffort ?? null,
            supports_vision: vision,
            context_window: 256_000,
            enable_1m_context: millionContext,
            default_headers: parseHeadersText(headersText),
          }),
        },
      );
      if (!res.ok) {
        const data = await res.json();
        setError(data.detail || t.settings.model.updateFailed);
        return;
      }
      toast.success(t.settings.model.saveSuccess);
      onSaved();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t.settings.model.networkError);
    } finally {
      setSaving(false);
    }
  };

  return (
    <form
      className="space-y-4 rounded-lg border border-border p-4 sm:p-5"
      autoComplete="off"
      onSubmit={(event) => {
        event.preventDefault();
        void handleSave();
      }}
    >
      <div className="flex items-center gap-2 rounded-lg bg-warning/10 border border-warning/30 px-3 py-2 text-sm text-warning">
        <AlertTriangleIcon className="h-4 w-4 shrink-0" />
        <span>{t.settings.model.externalModelRisk}</span>
      </div>

      <div>
        <label htmlFor="add-model-provider" className="text-sm font-medium">
          <span className="text-destructive">*</span>{" "}
          {t.settings.model.provider}
        </label>
        <select
          id="add-model-provider"
          name="echo-model-provider"
          className="mt-1 flex h-9 w-full rounded-lg border border-input bg-transparent px-3 py-1 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          value={provider}
          onChange={(e) => handleProviderChange(e.target.value)}
        >
          {PROVIDERS.map((p) => (
            <option key={p.value} value={p.value}>
              {getProviderLabel(p.value)}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="text-sm font-medium">
          <span className="text-destructive">*</span>{" "}
          {t.settings.model.modelList.label}
        </label>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {t.settings.model.modelList.hint}
        </p>
        <ul className="mt-2 space-y-1.5">
          {models.map((id, idx) => (
            <li key={`add-model-${idx}`} className="flex items-center gap-1.5">
              <span className="w-4 shrink-0 text-right text-xs text-muted-foreground/60 tabular-nums">
                {idx === 0 ? "★" : idx === models.length - 1 ? "▴" : "·"}
              </span>
              <Input
                name={`echo-model-id-${idx}`}
                autoComplete="off"
                data-1p-ignore="true"
                data-lpignore="true"
                aria-label={`${t.settings.model.modelList.label} ${idx + 1}`}
                className="flex-1 font-mono text-xs"
                placeholder={
                  idx === 0
                    ? t.settings.model.modelIdPlaceholder
                    : t.settings.model.modelIdPlaceholder
                }
                value={id}
                onChange={(e) => handleModelChange(idx, e.target.value)}
              />
              <button
                type="button"
                className={cn(
                  "flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-border-default text-muted-foreground transition-colors",
                  "hover:border-destructive/50 hover:bg-destructive/10 hover:text-destructive",
                  "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-border-default disabled:hover:bg-transparent disabled:hover:text-muted-foreground",
                )}
                onClick={() => handleModelRemove(idx)}
                disabled={models.length <= 1}
                title={t.settings.model.modelList.removeTooltip}
                aria-label={t.settings.model.modelList.removeTooltip}
              >
                <XCircleIcon className="size-4" />
              </button>
            </li>
          ))}
        </ul>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="mt-2 w-full border-dashed"
          onClick={handleModelAdd}
        >
          <PlusIcon className="mr-1 h-3 w-3" />{" "}
          {t.settings.model.modelList.addButton}
        </Button>
        {/* Click-to-fill suggested model IDs · lets users skip
            "go look up the exact model name" · each chip populates
            the FIRST row of the models list. Renders only when the
            current preset ships a suggestion list. */}
        {(() => {
          const preset = PROVIDERS.find((p) => p.value === provider);
          if (!preset?.suggestedModels?.length) return null;
          return (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {preset.suggestedModels.map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => handleModelChange(0, m)}
                  className="rounded-md border border-border-default bg-muted/40 px-2 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                  title={t.settings.model.fillModelId ?? "Fill this model ID"}
                >
                  {m}
                </button>
              ))}
            </div>
          );
        })()}
      </div>

      <div>
        <label htmlFor="add-model-display-name" className="text-sm font-medium">
          {t.settings.model.displayName}
        </label>
        <Input
          id="add-model-display-name"
          name="echo-model-display-name"
          autoComplete="off"
          data-1p-ignore="true"
          data-lpignore="true"
          data-form-type="other"
          className="mt-1"
          placeholder={t.settings.model.displayNamePlaceholder}
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <div className="flex items-center justify-between gap-2">
            <label htmlFor="add-model-api-key" className="text-sm font-medium">
              <span className="text-destructive">*</span>{" "}
              {getProviderLabel(provider) || t.settings.model.provider}{" "}
              {t.settings.model.apiKey}
            </label>
            {/* Console link · opens the provider's dashboard in a
                new tab so users don't have to hunt for the API
                key page. Renders only when the preset carries one. */}
            {(() => {
              const preset = PROVIDERS.find((p) => p.value === provider);
              if (!preset?.consoleUrl) return null;
              return (
                <RoutedWebLink
                  href={preset.consoleUrl}
                  openTargetSource="model-provider-console"
                  className="text-xs text-primary hover:underline font-normal"
                >
                  {t.settings.model.getApiKey}
                </RoutedWebLink>
              );
            })()}
          </div>
          <div className="relative mt-1">
            <Input
              id="add-model-api-key"
              name="echo-new-model-api-key"
              className="pr-10"
              type={showKey ? "text" : "password"}
              autoComplete="new-password"
              data-1p-ignore="true"
              data-lpignore="true"
              data-form-type="other"
              spellCheck={false}
              placeholder={t.settings.model.apiKeyPlaceholder}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
            <button
              type="button"
              aria-label={
                showKey
                  ? t.settings.model.hideApiKey
                  : t.settings.model.showApiKey
              }
              className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              onClick={() => setShowKey(!showKey)}
            >
              {showKey ? (
                <EyeOffIcon className="size-4" />
              ) : (
                <EyeIcon className="size-4" />
              )}
            </button>
          </div>
        </div>
        <div>
          <label htmlFor="add-model-protocol" className="text-sm font-medium">
            {t.settings.model.apiProtocol}
          </label>
          <select
            id="add-model-protocol"
            name="echo-model-protocol"
            className="mt-1 flex h-9 w-full rounded-lg border border-input bg-transparent px-3 py-1 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            value={protocol}
            onChange={(e) => setProtocol(e.target.value)}
          >
            {PROTOCOLS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label htmlFor="add-model-base-url" className="text-sm font-medium">
          <span className="text-destructive">*</span>{" "}
          {t.settings.model.baseUrlLabel}
        </label>
        <Input
          id="add-model-base-url"
          name="echo-model-base-url"
          autoComplete="url"
          className="mt-1"
          placeholder={t.settings.model.baseUrlPlaceholder}
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
        />
      </div>

      {/* Extra HTTP headers — collapsed by default to keep the form
          uncluttered for the 95% case. Needed for APIs that gate on
          User-Agent (Kimi Coding) or require custom routing headers. */}
      <div className="rounded-lg border border-border-default bg-muted/20">
        <button
          type="button"
          aria-expanded={showHeaders}
          onClick={() => setShowHeaders((v) => !v)}
          className="flex w-full items-center justify-between px-3 py-2 text-sm font-medium hover:bg-muted/40"
        >
          <span>
            {t.settings.model.extraHeadersTitle}
            {(() => {
              const n = Object.keys(parseHeadersText(headersText)).length;
              return n > 0 ? ` (${n})` : "";
            })()}
          </span>
          <span className="text-xs text-muted-foreground">
            {showHeaders ? "▾" : "▸"}
          </span>
        </button>
        {showHeaders && (
          <div className="space-y-2 border-t border-border-default px-3 py-3">
            <textarea
              value={headersText}
              onChange={(e) => setHeadersText(e.target.value)}
              placeholder={t.settings.model.extraHeadersPlaceholder}
              spellCheck={false}
              rows={3}
              className="w-full resize-y rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs shadow-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
            <p className="text-xs text-muted-foreground">
              {t.settings.model.extraHeadersHint}
            </p>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="flex items-center gap-2">
          <Switch
            aria-label={t.settings.model.thinkingLabel}
            checked={thinking}
            onCheckedChange={setThinking}
          />{" "}
          <span className="text-sm">{t.settings.model.thinkingLabel}</span>
        </div>
        <div className="flex flex-col items-start gap-1">
          <div className="flex items-center gap-2">
            <Switch
              aria-label={t.settings.model.visionLabel}
              checked={vision}
              onCheckedChange={setVision}
              disabled={visionLocked}
            />{" "}
            <span className="text-sm">{t.settings.model.visionLabel}</span>
          </div>
          {visionLocked && (
            <span
              className="text-[11px] leading-tight text-muted-foreground"
              role="status"
            >
              {t.settings.model.visionNotSupported}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Switch
            aria-label={t.settings.model.millionContextLabel}
            checked={millionContext}
            onCheckedChange={setMillionContext}
          />{" "}
          <span className="text-sm">
            {t.settings.model.millionContextLabel}
          </span>
        </div>
      </div>

      <DefaultEffortSelect
        value={defaultReasoningEffort}
        reasoningEfforts={clientSideReasoningEfforts(baseUrl, models[0] || "")}
        onChange={setDefaultReasoningEffort}
      />

      {/* Test status + buttons */}
      <div className="flex flex-col gap-3 rounded-lg border border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div
          role="status"
          className="flex min-w-0 flex-wrap items-center gap-2 text-sm"
        >
          {testStatus === "idle" && (
            <>
              <div className="h-2.5 w-2.5 rounded-lg bg-muted-foreground/40" />
              <span className="text-muted-foreground">
                {t.settings.model.notTested}
              </span>
            </>
          )}
          {testStatus === "testing" && (
            <>
              <Loader2Icon className="h-4 w-4 animate-spin text-info" />
              <span className="text-info">{t.common.loading}</span>
            </>
          )}
          {testStatus === "success" && (
            <>
              <CheckCircle2Icon className="h-4 w-4 text-success" />
              <span className="text-success">
                {testMessage}
                {testLatency != null ? ` (${testLatency}ms)` : ""}
              </span>
            </>
          )}
          {testStatus === "fail" && (
            <>
              <XCircleIcon className="h-4 w-4 text-destructive" />
              <span className="text-destructive">{testMessage}</span>
            </>
          )}
          <span className="text-xs text-muted-foreground ml-2">
            {t.settings.model.testEndpointHint}
          </span>
        </div>
        <div className="flex gap-2 self-end sm:self-auto">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleTest}
            disabled={testStatus === "testing"}
          >
            <WifiIcon className="mr-1 h-3 w-3" />
            {t.settings.model.testConnection}
          </Button>
        </div>
      </div>

      {error && (
        <div role="alert" className="text-sm text-destructive">
          {error}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onCancel}>
          {t.common.cancel}
        </Button>
        <Button
          type="submit"
          className="bg-chart-7 hover:bg-chart-7/90 text-white"
          disabled={saving}
        >
          {saving ? t.common.loading : t.common.save}
        </Button>
      </div>
    </form>
  );
}

// ─── Local-model one-click import ─────────────────────────────
//
// Backs the "本地模型" SettingsSection. Sits next to the custom-
// models list so the operator's path from "I have Ollama running
// on my box" to "Echo is routing to it" is one click: scan →
// import. The scan probes a small set of well-known ports in
// parallel; the import writes directly into ``custom_models_state``
// (same on-disk shape as the manual add form), and re-runs the
// parent's ``fetchModels`` so the new row appears in the list
// above without a manual refresh.
//
// The header row carries a live status badge so the operator can
// see whether a scan has run and what it found without expanding
// the results list. Hard-cut borders (not fade / height
// animation) read as "real section boundary" rather than "fancy
// dropdown" — the section is short enough that animation would
// just be visual noise.
interface DiscoveredService {
  provider: string;
  base_url: string;
  probe_path: string;
  models: string[];
  status: "ok" | "empty" | "error";
  error?: string;
}

function LocalModelsSection({ onImported }: { onImported?: () => void }) {
  const { t } = useI18n();
  const [services, setServices] = useState<DiscoveredService[]>([]);
  const [scanStatus, setScanStatus] = useState<
    "idle" | "scanning" | "done" | "error"
  >("idle");
  // Per-row import-in-flight flag, keyed by base_url so a slow
  // import on one service doesn't lock out importing the others.
  const [importing, setImporting] = useState<Record<string, boolean>>({});

  const handleScan = useCallback(async () => {
    setScanStatus("scanning");
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/config/local-models/scan`,
        { headers: authHeaders() },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setServices(Array.isArray(data.services) ? data.services : []);
      setScanStatus("done");
    } catch (e) {
      swallow(e);
      setScanStatus("error");
    }
  }, []);

  useEffect(() => {
    const handler = () => {
      void handleScan();
    };
    window.addEventListener(LOCAL_MODEL_SCAN_EVENT, handler);
    return () => window.removeEventListener(LOCAL_MODEL_SCAN_EVENT, handler);
  }, [handleScan]);

  const handleImport = useCallback(
    async (svc: DiscoveredService) => {
      if (svc.status !== "ok" || svc.models.length === 0) return;
      setImporting((prev) => ({ ...prev, [svc.base_url]: true }));
      try {
        const res = await fetch(
          `${getBackendBaseURL()}/api/config/local-models/import`,
          {
            method: "POST",
            headers: jsonAuthHeaders(),
            body: JSON.stringify({
              base_url: svc.base_url,
              models: svc.models,
              // Display name falls back to the first model id; the
              // operator can rename in the edit form afterwards.
              display_name: svc.models[0] ?? svc.base_url,
            }),
          },
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          toast.error(
            `${t.settings.model.localModels.importFailed}: ${
              data.error || `HTTP ${res.status}`
            }`,
          );
          return;
        }
        toast.success(t.settings.model.localModels.imported);
        onImported?.();
      } catch (e) {
        swallow(e);
        toast.error(t.settings.model.localModels.importFailed);
      } finally {
        setImporting((prev) => {
          const next = { ...prev };
          delete next[svc.base_url];
          return next;
        });
      }
    },
    [
      onImported,
      t.settings.model.localModels.importFailed,
      t.settings.model.localModels.imported,
    ],
  );

  return (
    <SettingsSection
      title={t.settings.model.localModels.title}
      description={t.settings.model.localModels.subtitle}
    >
      <div className="rounded-lg border border-border overflow-hidden">
        {/* Header bar · scan button + live status badge. Lives
            outside the collapsible so the operator can see whether
            a scan has run at a glance, even when the results list
            is collapsed. */}
        <div className="flex items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">
              {t.settings.model.localModels.providerHint}
            </span>
            {scanStatus === "scanning" && (
              <Loader2Icon className="size-3.5 animate-spin text-info" />
            )}
            {scanStatus === "done" && services.length > 0 && (
              <span className="inline-flex items-center rounded-md border border-success/30 bg-success/5 px-1.5 py-0.5 text-xs font-medium text-success dark:border-success/40 dark:bg-success/10 dark:text-success">
                {t.settings.model.localModels.modelsCount(services.length)}
              </span>
            )}
            {scanStatus === "done" && services.length === 0 && (
              <span className="inline-flex items-center rounded-md border border-border bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground dark:border-muted-foreground/40 dark:bg-muted-foreground/10 dark:text-muted-foreground">
                {t.settings.model.localModels.empty}
              </span>
            )}
            {scanStatus === "error" && (
              <span className="inline-flex items-center rounded-md border border-destructive/30 bg-destructive/10 px-1.5 py-0.5 text-xs font-medium text-destructive">
                {t.settings.model.localModels.serviceStatus.error}
              </span>
            )}
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={handleScan}
            disabled={scanStatus === "scanning"}
          >
            {scanStatus === "scanning" ? (
              <Loader2Icon className="mr-1.5 size-3.5 animate-spin" />
            ) : (
              <RefreshCwIcon className="mr-1.5 size-3.5" />
            )}
            {scanStatus === "scanning"
              ? t.settings.model.localModels.scanButtonScanning
              : t.settings.model.localModels.scanButton}
          </Button>
        </div>

        {/* Results list · only renders after a scan has been run.
            Empty state is inline rather than a separate screen so
            the operator's eye doesn't have to leave the section. */}
        {scanStatus !== "idle" && (
          <div className="border-t border-border divide-y divide-border">
            {services.length === 0 ? (
              <div className="px-4 py-6 text-sm text-muted-foreground">
                {t.settings.model.localModels.emptyHint}
              </div>
            ) : (
              services.map((svc) => {
                const busy = !!importing[svc.base_url];
                const canImport =
                  svc.status === "ok" && svc.models.length > 0 && !busy;
                return (
                  <div
                    key={svc.base_url}
                    className="flex items-center gap-3 px-4 py-3"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <code className="truncate font-mono text-sm">
                          {svc.base_url}
                        </code>
                        {svc.status === "ok" && (
                          <span className="inline-flex shrink-0 items-center rounded-md border border-success/30 bg-success/5 px-1.5 py-0.5 text-xs font-medium text-success dark:border-success/40 dark:bg-success/10 dark:text-success">
                            {t.settings.model.localModels.serviceStatus.ok}
                          </span>
                        )}
                        {svc.status === "empty" && (
                          <span className="inline-flex shrink-0 items-center rounded-md border border-warning/30 bg-warning/5 px-1.5 py-0.5 text-xs font-medium text-warning dark:border-warning/40 dark:bg-warning/10 dark:text-warning">
                            {t.settings.model.localModels.serviceStatus.empty}
                          </span>
                        )}
                        {svc.status === "error" && (
                          <span className="inline-flex shrink-0 items-center rounded-md border border-destructive/30 bg-destructive/10 px-1.5 py-0.5 text-xs font-medium text-destructive">
                            {t.settings.model.localModels.serviceStatus.error}
                          </span>
                        )}
                      </div>
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        {svc.status === "ok" &&
                          t.settings.model.localModels.modelsCount(
                            svc.models.length,
                          )}
                        {svc.status === "empty" &&
                          t.settings.model.localModels.serviceStatus.empty}
                        {svc.error &&
                          `${t.settings.model.localModels.serviceStatus.error}: ${svc.error}`}
                      </div>
                    </div>
                    <Button
                      variant="default"
                      size="sm"
                      onClick={() => handleImport(svc)}
                      disabled={!canImport}
                    >
                      {busy
                        ? t.settings.model.localModels.importingButton
                        : t.settings.model.localModels.importButton}
                    </Button>
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>
    </SettingsSection>
  );
}
