import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  ChevronDown,
  CloudDownload,
  KeyRound,
  Loader2,
  Plug,
  PlugZap,
  RefreshCw,
  Search,
  ShieldCheck,
  SquareTerminal,
  Trash2,
  Unplug,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { RoutedWebLink } from "@/components/ui/routed-web-link";
import {
  deleteOAuthApp,
  getOAuthApp,
  oauthAuthorize,
  oauthStatus,
  saveOAuthApp,
} from "@/core/mcp/api";
import {
  cancelCapabilityDeviceFlow,
  connectCapability,
  disconnectCapability,
  getCapabilityInstallPlan,
  getCapabilityDeviceFlow,
  getCapabilityStatus,
  installCapability,
  listCapabilities,
  loadCapabilityIcon,
  setCapabilityEnabled,
  uninstallCapability,
  type CapabilityConnectResult,
  type CapabilityDeviceFlow,
  type CapabilityInfo,
  type CapabilityInstallPlan,
  type CapabilitySource,
} from "@/core/agents/agent-world-api";
import { getBackendBaseURL } from "@/core/config";
import { CAPABILITY_SURFACE_QUERY_KEY } from "@/core/plugins/use-capability-surface";
import { cn } from "@/lib/utils";

// 统一「插件」市场 —— 所有外部能力(WorkBuddy MCP 服务、Codex 插件、注册表插件)统一叫插件。
// 一个市场统一管理:安装→技能/MCP,连接→认证编排,插件直接就绪。
// 数据来自后端 /api/capabilities(见 runtime/sensing/gateway/capability_router.py)。

const TYPE_META: Record<string, { badge: string; label: string }> = {
  mcp: { badge: "bg-primary/10 text-primary", label: "MCP" },
  cli: {
    badge: "bg-chart-3/10 text-chart-3 dark:text-chart-3",
    label: "CLI",
  },
  "skill-only": {
    badge: "bg-chart-2/10 text-chart-2 dark:text-chart-2",
    label: "技能",
  },
  plugin: {
    badge: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400",
    label: "插件",
  },
  other: { badge: "bg-muted text-muted-foreground", label: "其他" },
};

const DEFAULT_TYPE_META = {
  badge: "bg-muted text-muted-foreground",
  label: "其他",
};

function capabilityIconUrl(capability: CapabilityInfo): string | null {
  const raw = capability.icon?.trim();
  if (!raw) return null;
  if (/^https?:\/\//i.test(raw)) return raw;
  if (raw.startsWith("/")) return `${getBackendBaseURL()}${raw}`;
  if (capability.source !== "codex_plugin") return null;
  if (capability.is_codex_marketplace) {
    return `${getBackendBaseURL()}/api/capabilities/${encodeURIComponent(capability.id)}/icon`;
  }
  const relative = raw.replace(/^\.\//, "");
  if (!relative || relative.split("/").some((part) => !part || part === "..")) {
    return null;
  }
  const encodedPath = relative
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  return `${getBackendBaseURL()}/api/plugins/${encodeURIComponent(capability.id)}/assets/${encodedPath}`;
}

function useCapabilityIconUrl(capability: CapabilityInfo): string | null {
  const directUrl = capabilityIconUrl(capability);
  const backendApiPrefix = `${getBackendBaseURL()}/api/`;
  const requiresAuthentication = Boolean(
    directUrl && directUrl.startsWith(backendApiPrefix),
  );
  const [resolvedUrl, setResolvedUrl] = useState<string | null>(() =>
    requiresAuthentication ? null : directUrl,
  );

  useEffect(() => {
    if (!directUrl || !requiresAuthentication) {
      setResolvedUrl(directUrl);
      return;
    }
    const controller = new AbortController();
    setResolvedUrl(null);
    void loadCapabilityIcon(directUrl, controller.signal)
      .then((url) => setResolvedUrl(url || null))
      .catch(() => {
        if (!controller.signal.aborted) setResolvedUrl(null);
      });
    return () => controller.abort();
  }, [directUrl, requiresAuthentication]);

  return resolvedUrl;
}

function CapabilityIcon({ capability }: { capability: CapabilityInfo }) {
  const [failed, setFailed] = useState(false);
  const imageUrl = useCapabilityIconUrl(capability);
  useEffect(() => setFailed(false), [imageUrl]);
  if (imageUrl && !failed) {
    return (
      <img
        src={imageUrl}
        alt=""
        data-testid={`capability-icon-${capability.id}`}
        className="size-8 object-contain"
        onError={() => setFailed(true)}
      />
    );
  }
  const raw = capability.icon?.trim();
  if (raw && !raw.includes("/") && !/^https?:/i.test(raw)) {
    return <span className="text-lg leading-none">{raw}</span>;
  }
  if (capability.source === "codex_plugin") {
    return <Boxes className="size-4 text-indigo-500" />;
  }
  if (capability.type === "mcp") {
    return <Plug className="size-4 text-primary" />;
  }
  if (capability.type === "cli") {
    return <SquareTerminal className="size-4 text-chart-3" />;
  }
  return <Boxes className="size-4 text-chart-2" />;
}

type CapabilityCategoryId =
  | "installed"
  | "featured"
  | "productivity"
  | "creative"
  | "developer"
  | "business"
  | "other";

const CAPABILITY_CATEGORIES: ReadonlyArray<{
  id: CapabilityCategoryId;
  label: string;
}> = [
  { id: "installed", label: "已安装" },
  { id: "featured", label: "精选" },
  { id: "productivity", label: "效率" },
  { id: "creative", label: "创意" },
  { id: "developer", label: "开发者工具" },
  { id: "business", label: "业务与运营" },
  { id: "other", label: "其他" },
];

const FEATURED_CAPABILITY_IDS = new Set([
  "browser",
  "documents",
  "spreadsheets",
  "presentations",
  "pdf",
  "visualize",
]);

function capabilityCategory(capability: CapabilityInfo): CapabilityCategoryId {
  if (capability.featured || FEATURED_CAPABILITY_IDS.has(capability.id)) {
    return "featured";
  }
  const haystack = [
    capability.category,
    capability.id,
    capability.name,
    capability.name_zh,
    capability.description,
    capability.description_zh,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  if (
    /(creative|design|image|video|audio|media|canvas|figma|canva|runway|higgsfield|创意|设计|图像|视频|音频|媒体)/.test(
      haystack,
    )
  ) {
    return "creative";
  }
  if (
    /(developer|development|devops|code|database|hosting|deploy|cloud|github|gitlab|vercel|supabase|neon|datadog|开发|代码|数据库|部署|云服务)/.test(
      haystack,
    )
  ) {
    return "developer";
  }
  if (
    /(business|operations|sales|marketing|crm|commerce|shop|seo|analytics|finance|trading|hubspot|shopify|apollo|业务|运营|销售|营销|电商|金融|交易|分析)/.test(
      haystack,
    )
  ) {
    return "business";
  }
  if (
    /(productivity|office|calendar|meeting|mail|email|docs|sheets|drive|notion|slack|效率|办公|日历|会议|邮件|文档|表格|协作)/.test(
      haystack,
    )
  ) {
    return "productivity";
  }
  return "other";
}
const AUTH_LABEL: Record<string, string> = {
  none: "无需认证",
  token: "Token",
  oauth: "OAuth",
  "server-side": "服务端",
  "oneid-token": "OneID",
};

const PERMISSION_LABELS: Record<string, string> = {
  "content.read": "读取工作内容",
  "content.write": "修改或创建内容",
  "interaction.user": "发起交互与提示",
  "network.remote": "访问外部网络服务",
  "account.credentials": "使用本机加密保存的账号凭据",
  "process.local": "在本机启动受控进程",
};

/** 轮询 MCP OAuth 授权结果,直到已授权或超时(默认 90s)。 */
function pollOAuth(server: string, timeoutMs = 90_000): Promise<boolean> {
  return new Promise((resolve) => {
    const startedAt = Date.now();
    const tick = async () => {
      try {
        const st = await oauthStatus(server);
        if (st.authorized) {
          resolve(true);
          return;
        }
      } catch {
        // 网络抖动忽略,继续轮询
      }
      if (Date.now() - startedAt >= timeoutMs) {
        resolve(false);
        return;
      }
      window.setTimeout(tick, 1500);
    };
    void tick();
  });
}

const DEVICE_FLOW_POLL_INTERVAL_MS = 2_000;
const DEVICE_FLOW_CLOSE_TIMEOUT_MS = 10_000;

class DeviceFlowCloseTimeoutError extends Error {}

function waitForDeviceFlowClose<T>(operation: Promise<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(
      () => reject(new DeviceFlowCloseTimeoutError()),
      DEVICE_FLOW_CLOSE_TIMEOUT_MS,
    );
    void operation.then(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        window.clearTimeout(timer);
        reject(error);
      },
    );
  });
}

function supportsDeviceFlow(capability: CapabilityInfo): boolean {
  return (
    capability.source === "connector" &&
    (capability.type === "cli" || Boolean(capability.has_cli_auth))
  );
}

function waitForDeviceFlowPoll(
  delayMs: number,
  signal: AbortSignal,
): Promise<boolean> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve(false);
      return;
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve(true);
    }, delayMs);
    const onAbort = () => {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      resolve(false);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

async function pollCapabilityDeviceFlow(
  capabilityId: string,
  expectedFlowId: string,
  timeoutMs: number,
  signal: AbortSignal,
): Promise<"connected" | "inactive" | "replaced" | "timeout" | "cancelled"> {
  const startedAt = Date.now();
  while (!signal.aborted) {
    try {
      const status = await getCapabilityStatus(capabilityId);
      if (signal.aborted) return "cancelled";
      if (status.connected) return "connected";
    } catch {
      // A transient status failure is retried while the server still reports
      // an active device-flow session.
    }

    try {
      const flow = await getCapabilityDeviceFlow(capabilityId);
      if (signal.aborted) return "cancelled";
      if (!flow.active || !flow.device_flow) return "inactive";
      if (flow.device_flow.flow_id !== expectedFlowId) return "replaced";
    } catch {
      // Fail closed at the deadline; never infer success from a network error.
    }

    const elapsed = Date.now() - startedAt;
    if (elapsed >= timeoutMs) return "timeout";
    const shouldContinue = await waitForDeviceFlowPoll(
      Math.min(DEVICE_FLOW_POLL_INTERVAL_MS, timeoutMs - elapsed),
      signal,
    );
    if (!shouldContinue) return "cancelled";
  }
  return "cancelled";
}

function ConnectDialog({
  capability,
  open,
  onOpenChange,
  onConnected,
}: {
  capability: CapabilityInfo;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConnected: () => void;
}) {
  const [accessToken, setAccessToken] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [oneIdToken, setOneIdToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [closing, setClosing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [deviceFlow, setDeviceFlow] = useState<
    CapabilityConnectResult["device_flow"] | null
  >(null);

  const isCli = capability.type === "cli";
  const isFreebuffCli = isCli && capability.id === "freebuff-cli";
  const isPlugin = capability.source === "codex_plugin";
  const modelProvider = capability.model_provider ?? null;
  const isModelProvider = Boolean(modelProvider);
  const isOneId = capability.auth_mode === "oneid-token";
  const canUseDeviceFlow = supportsDeviceFlow(capability);
  const mountedRef = useRef(true);
  const openRef = useRef(open);
  const onConnectedRef = useRef(onConnected);
  const pollAbortRef = useRef<AbortController | null>(null);
  const operationEpochRef = useRef(0);
  const closingRef = useRef(false);
  const submitOperationRef = useRef<Promise<void> | null>(null);
  const recoveryOperationRef = useRef<Promise<void> | null>(null);
  const lateDeviceFlowRef = useRef<CapabilityDeviceFlow | null>(null);
  const lateRecoveryDeviceFlowRef = useRef<CapabilityDeviceFlow | null>(null);
  const activeDeviceFlowRef = useRef<CapabilityDeviceFlow | null>(null);

  const stopDeviceFlowPolling = useCallback(() => {
    pollAbortRef.current?.abort();
    pollAbortRef.current = null;
  }, []);

  const monitorDeviceFlow = useCallback(
    (flow: CapabilityDeviceFlow, operationEpoch: number) => {
      stopDeviceFlowPolling();
      const controller = new AbortController();
      pollAbortRef.current = controller;
      const expiresInSeconds = Math.min(
        900,
        Math.max(1, Number(flow.expires_in) || 240),
      );

      void pollCapabilityDeviceFlow(
        capability.id,
        flow.flow_id,
        expiresInSeconds * 1000,
        controller.signal,
      ).then(async (outcome) => {
        if (
          outcome === "cancelled" ||
          controller.signal.aborted ||
          operationEpochRef.current !== operationEpoch ||
          !mountedRef.current ||
          !openRef.current
        ) {
          return;
        }
        if (pollAbortRef.current === controller) {
          pollAbortRef.current = null;
        }
        if (outcome === "connected") {
          setMessage("设备流登录完成 ✓");
          activeDeviceFlowRef.current = null;
          setDeviceFlow(null);
          onConnectedRef.current();
          return;
        }

        try {
          await cancelCapabilityDeviceFlow(capability.id, flow.flow_id);
        } catch {
          if (
            operationEpochRef.current === operationEpoch &&
            mountedRef.current &&
            openRef.current
          ) {
            setMessage("设备流已结束，但无法确认后台进程已清理，请重试取消。");
          }
          return;
        }
        if (
          operationEpochRef.current !== operationEpoch ||
          !mountedRef.current ||
          !openRef.current
        ) {
          return;
        }
        activeDeviceFlowRef.current = null;
        setDeviceFlow(null);
        setMessage(
          outcome === "replaced"
            ? "设备流已被另一个窗口的新授权替换。"
            : "设备流授权未在有效期内完成,可重试或手动执行 CLI 登录。",
        );
      });
    },
    [capability.id, stopDeviceFlowPolling],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      openRef.current = false;
      closingRef.current = false;
      operationEpochRef.current += 1;
      stopDeviceFlowPolling();
      const activeFlow = activeDeviceFlowRef.current;
      activeDeviceFlowRef.current = null;
      if (activeFlow) {
        void cancelCapabilityDeviceFlow(
          capability.id,
          activeFlow.flow_id,
        ).catch(() => undefined);
      }
    };
  }, [capability.id, stopDeviceFlowPolling]);

  useEffect(() => {
    onConnectedRef.current = onConnected;
  }, [onConnected]);

  useEffect(() => {
    openRef.current = open;
    if (!open) {
      operationEpochRef.current += 1;
      stopDeviceFlowPolling();
      return;
    }

    const operationEpoch = operationEpochRef.current + 1;
    operationEpochRef.current = operationEpoch;
    closingRef.current = false;
    setAccessToken("");
    setApiKey("");
    setBaseUrl("");
    setOneIdToken("");
    setMessage(null);
    setBusy(false);
    setClosing(false);
    activeDeviceFlowRef.current = null;
    setDeviceFlow(null);
    if (!canUseDeviceFlow) return;

    setBusy(true);
    const recoveryOperation = (async () => {
      try {
        const status = await getCapabilityDeviceFlow(capability.id);
        const isCurrentOperation =
          operationEpochRef.current === operationEpoch &&
          mountedRef.current &&
          openRef.current;
        if (status.active && status.device_flow) {
          if (!isCurrentOperation) {
            lateRecoveryDeviceFlowRef.current = status.device_flow;
            // closeDialog owns cleanup while waiting for this recovery. A
            // forced unmount has no waiter, so the late response cleans itself.
            if (!closingRef.current) {
              try {
                await cancelCapabilityDeviceFlow(
                  capability.id,
                  status.device_flow.flow_id,
                );
              } finally {
                if (
                  lateRecoveryDeviceFlowRef.current?.flow_id ===
                  status.device_flow.flow_id
                ) {
                  lateRecoveryDeviceFlowRef.current = null;
                }
              }
            }
            return;
          }
          activeDeviceFlowRef.current = status.device_flow;
          setDeviceFlow(status.device_flow);
          monitorDeviceFlow(status.device_flow, operationEpoch);
        }
      } catch {
        if (
          operationEpochRef.current === operationEpoch &&
          mountedRef.current &&
          openRef.current
        ) {
          setMessage("无法恢复进行中的设备流授权，请重试。");
        }
      } finally {
        if (
          operationEpochRef.current === operationEpoch &&
          mountedRef.current &&
          openRef.current
        ) {
          setBusy(false);
        }
      }
    })();
    recoveryOperationRef.current = recoveryOperation;
    void recoveryOperation.finally(() => {
      if (recoveryOperationRef.current === recoveryOperation) {
        recoveryOperationRef.current = null;
      }
    });
  }, [
    canUseDeviceFlow,
    capability.id,
    monitorDeviceFlow,
    open,
    stopDeviceFlowPolling,
  ]);

  const closeDialog = useCallback(async () => {
    if (closingRef.current) return;
    closingRef.current = true;
    operationEpochRef.current += 1;
    openRef.current = false;
    stopDeviceFlowPolling();
    setBusy(true);
    setClosing(true);
    try {
      // Keep the dialog in a closing state until in-flight connect/recovery
      // requests settle. The final generation-scoped DELETE then cannot lose
      // a device flow that arrives between the user's close and the response.
      const pendingOperations = [
        submitOperationRef.current,
        recoveryOperationRef.current,
      ].filter((operation): operation is Promise<void> => operation !== null);
      if (pendingOperations.length > 0) {
        await waitForDeviceFlowClose(
          Promise.all(pendingOperations).then(() => undefined),
        );
      }
      const ownedFlow =
        lateDeviceFlowRef.current ??
        lateRecoveryDeviceFlowRef.current ??
        activeDeviceFlowRef.current ??
        deviceFlow;
      if (canUseDeviceFlow && ownedFlow) {
        await waitForDeviceFlowClose(
          cancelCapabilityDeviceFlow(capability.id, ownedFlow.flow_id),
        );
      }
    } catch (error) {
      if (mountedRef.current) {
        openRef.current = true;
        closingRef.current = false;
        setBusy(submitOperationRef.current !== null);
        setClosing(false);
        setMessage(
          error instanceof DeviceFlowCloseTimeoutError
            ? "连接操作仍在处理中，已隔离迟到结果；请稍后重试关闭。"
            : error instanceof Error
              ? error.message
              : String(error),
        );
        const recoverableFlow =
          lateDeviceFlowRef.current ??
          lateRecoveryDeviceFlowRef.current ??
          activeDeviceFlowRef.current ??
          deviceFlow;
        if (recoverableFlow) {
          activeDeviceFlowRef.current = recoverableFlow;
          setDeviceFlow(recoverableFlow);
          monitorDeviceFlow(recoverableFlow, operationEpochRef.current);
        }
      }
      return;
    }
    if (!mountedRef.current) return;
    closingRef.current = false;
    setBusy(false);
    setClosing(false);
    activeDeviceFlowRef.current = null;
    setDeviceFlow(null);
    lateDeviceFlowRef.current = null;
    lateRecoveryDeviceFlowRef.current = null;
    onOpenChange(false);
  }, [
    canUseDeviceFlow,
    capability.id,
    deviceFlow,
    monitorDeviceFlow,
    onOpenChange,
    stopDeviceFlowPolling,
  ]);

  const onSubmit = () => {
    if (submitOperationRef.current || closingRef.current) {
      return Promise.resolve();
    }
    const operationEpoch = operationEpochRef.current + 1;
    operationEpochRef.current = operationEpoch;
    lateDeviceFlowRef.current = null;
    stopDeviceFlowPolling();
    setBusy(true);
    setMessage(null);
    const operation = (async () => {
      try {
        const tokens: Record<string, string> = {};
        if (isOneId) {
          if (oneIdToken.trim()) tokens.oneid_token = oneIdToken.trim();
        } else {
          if (accessToken.trim()) tokens.access_token = accessToken.trim();
          if (apiKey.trim()) tokens.api_key = apiKey.trim();
          if (isModelProvider && baseUrl.trim()) {
            tokens.base_url = baseUrl.trim();
          }
        }
        const res = await connectCapability(capability.id, {
          tokens: Object.keys(tokens).length ? tokens : undefined,
          run_cli: isCli && Object.keys(tokens).length === 0,
          grant_permissions:
            isModelProvider && capability.permission_review_required
              ? capability.permissions
              : undefined,
        });
        const isCurrentOperation =
          operationEpochRef.current === operationEpoch &&
          mountedRef.current &&
          openRef.current;
        if (!isCurrentOperation) {
          // A non-close unmount still owns cleanup for a device flow created by
          // its late response. During close, closeDialog waits for this task
          // and performs the final DELETE itself.
          if (res.device_flow) {
            lateDeviceFlowRef.current = res.device_flow;
            if (!closingRef.current) {
              try {
                await cancelCapabilityDeviceFlow(
                  capability.id,
                  res.device_flow.flow_id,
                );
                lateDeviceFlowRef.current = null;
              } catch (cleanupError) {
                if (mountedRef.current && openRef.current) {
                  const recoveryEpoch = operationEpochRef.current;
                  activeDeviceFlowRef.current = res.device_flow;
                  setDeviceFlow(res.device_flow);
                  setMessage(
                    cleanupError instanceof Error
                      ? cleanupError.message
                      : String(cleanupError),
                  );
                  monitorDeviceFlow(res.device_flow, recoveryEpoch);
                }
              }
            }
          }
          return;
        }
        if (res.connected) {
          stopDeviceFlowPolling();
          setMessage(isPlugin ? "插件无需认证,已就绪 ✓" : "已连接 ✓");
          activeDeviceFlowRef.current = null;
          setDeviceFlow(null);
          onConnectedRef.current();
        } else if (res.device_flow) {
          // CLI 设备流:展示授权地址 + 自动打开 + 轮询状态
          activeDeviceFlowRef.current = res.device_flow;
          setDeviceFlow(res.device_flow);
          const uri = res.device_flow.verification_uri;
          if (uri) {
            const popup = window.open(
              uri,
              "echo-device-flow",
              "popup=yes,width=560,height=720",
            );
            if (!popup)
              setMessage("已复制授权地址,请手动打开(浏览器拦截了弹窗)。");
          }
          if (
            operationEpochRef.current === operationEpoch &&
            mountedRef.current &&
            openRef.current
          ) {
            monitorDeviceFlow(res.device_flow, operationEpoch);
          }
        } else if (res.command) {
          setMessage(`请在终端执行:\n${res.command}`);
        } else {
          setMessage(res.message || "连接未确认");
        }
      } catch (err) {
        if (
          operationEpochRef.current === operationEpoch &&
          mountedRef.current &&
          openRef.current
        ) {
          setMessage(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (
          operationEpochRef.current === operationEpoch &&
          mountedRef.current &&
          openRef.current
        ) {
          setBusy(false);
        }
      }
    })();
    submitOperationRef.current = operation;
    void operation.finally(() => {
      if (submitOperationRef.current === operation) {
        submitOperationRef.current = null;
        if (mountedRef.current && openRef.current && !closingRef.current) {
          setBusy(false);
        }
      }
    });
    return operation;
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) void closeDialog();
      }}
    >
      <DialogContent
        showCloseButton={false}
        className="w-[min(420px,calc(100vw-2rem))] gap-3 rounded-lg p-4 sm:max-w-[420px]"
      >
        <DialogHeader className="gap-1 text-left">
          <DialogTitle className="text-[15px]">
            连接 · {capability.name_zh}
          </DialogTitle>
          <DialogDescription className="text-caption leading-5">
            类型 {(TYPE_META[capability.type] ?? DEFAULT_TYPE_META).label} ·
            认证 {AUTH_LABEL[capability.auth_mode] ?? capability.auth_mode}
          </DialogDescription>
        </DialogHeader>

        {isPlugin && (
          <p className="text-xs leading-5 text-muted-foreground">
            插件(Echo
            插件)无需认证,安装后技能即可用。点「保存凭据」直接确认就绪。
          </p>
        )}

        {!isCli && !isPlugin && isOneId && (
          <div className="flex flex-col gap-2">
            <label className="text-xs text-muted-foreground">
              OneID Token(腾讯统一身份)
            </label>
            <Input
              value={oneIdToken}
              onChange={(e) => setOneIdToken(e.target.value)}
              placeholder="粘贴 OneID access token"
              className="h-8 text-sm"
            />
          </div>
        )}

        {!isCli && !isPlugin && !isOneId && !isModelProvider && (
          <div className="flex flex-col gap-2">
            <label className="text-xs text-muted-foreground">
              access_token
            </label>
            <Input
              value={accessToken}
              onChange={(e) => setAccessToken(e.target.value)}
              placeholder="粘贴 access_token"
              className="h-8 text-sm"
            />
            <label className="text-xs text-muted-foreground">api_key</label>
            <Input
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="粘贴 api_key(可选)"
              className="h-8 text-sm"
            />
          </div>
        )}

        {isModelProvider && modelProvider ? (
          <div className="space-y-3">
            <div className="rounded-md border border-border-default bg-muted/25 p-2.5 text-xs leading-5">
              <p className="font-medium text-foreground">
                {modelProvider.connection_note_zh ??
                  "直连模型 API，不安装或检测任何 CLI"}
              </p>
              <p className="mt-0.5 text-muted-foreground">
                Key 会加密保存在本机；模型继续使用 Echo
                的流式输出、工具调用和记忆。
              </p>
              {modelProvider.dashboard_url ? (
                <RoutedWebLink
                  href={modelProvider.dashboard_url}
                  openTargetSource="model-provider-api-key"
                  className="mt-1.5 inline-flex text-primary underline underline-offset-2"
                >
                  {modelProvider.login_cta_zh ?? "登录服务商并获取 API Key"}
                </RoutedWebLink>
              ) : null}
            </div>
            <div className="space-y-1.5">
              <label
                htmlFor="model-provider-api-key"
                className="text-xs font-medium"
              >
                {modelProvider.api_key_label_zh ??
                  `${modelProvider.display_name_zh ?? modelProvider.display_name ?? capability.name_zh} API Key`}
              </label>
              <Input
                id="model-provider-api-key"
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="粘贴 API Key"
                className="h-9 text-sm"
              />
            </div>
            {modelProvider.configurable_base_url ? (
              <div className="space-y-1.5">
                <label
                  htmlFor="model-provider-base-url"
                  className="text-xs font-medium"
                >
                  服务地址
                </label>
                <Input
                  id="model-provider-base-url"
                  type="url"
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder={modelProvider.base_url}
                  className="h-9 text-sm"
                />
                <p className="text-[11px] text-muted-foreground">
                  留空使用默认地址；自托管服务请填写以 /v1 结尾的地址。
                </p>
              </div>
            ) : null}
            <div className="rounded-md bg-muted/40 p-2.5 text-xs">
              <p className="font-medium text-foreground">
                {modelProvider.model_list_label_zh ?? "预置模型"}
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1">
                {modelProvider.free_models.map((model) => (
                  <code
                    key={model}
                    className="rounded bg-background px-1.5 py-0.5 text-[11px] text-muted-foreground"
                  >
                    {model}
                  </code>
                ))}
              </div>
            </div>
            {(modelProvider.privacy_notices_zh ?? []).length > 0 ? (
              <div className="rounded-md border border-warning/30 bg-warning/5 px-2.5 py-2 text-[11px] leading-5 text-warning">
                {(modelProvider.privacy_notices_zh ?? []).map((notice) => (
                  <p key={notice}>• {notice}</p>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

        {isCli &&
          !deviceFlow &&
          (isFreebuffCli ? (
            <div className="space-y-1.5 rounded-md border border-warning/30 bg-warning/5 p-2.5 text-xs leading-5">
              <p className="font-medium text-foreground">
                官方 Freebuff CLI · 交互式本地 Agent
              </p>
              <p className="text-muted-foreground">
                登录会打开 freebuff.com 官方授权页。Echo
                不读取或保存其登录凭据；Freebuff
                会处理你交给它的对话、代码和工作区文件。
              </p>
              <p className="text-warning">
                当前官方免费 CLI 不提供非交互
                Prompt/API，因此不会出现在模型选择器中。
              </p>
            </div>
          ) : (
            <p className="text-xs leading-5 text-muted-foreground">
              CLI 型插件将执行 cli.json 的登录命令(浏览器/设备流)。点「执行 CLI
              登录」自动开始。
            </p>
          ))}

        {deviceFlow ? (
          <div className="space-y-1.5 rounded-md bg-muted/60 px-2.5 py-2 text-xs">
            <p className="text-foreground">
              {deviceFlow.message || "请在浏览器完成授权"}
            </p>
            {deviceFlow.user_code && !deviceFlow.code_embedded_in_uri ? (
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">验证码</span>
                <code className="rounded bg-background px-1.5 py-0.5 font-mono text-[12px]">
                  {deviceFlow.user_code}
                </code>
              </div>
            ) : null}
            <div className="flex items-center gap-2 pt-0.5">
              <Button
                type="button"
                size="sm"
                variant="secondary"
                className="h-7 px-2 text-xs"
                onClick={() =>
                  deviceFlow.verification_uri &&
                  window.open(deviceFlow.verification_uri, "_blank")
                }
              >
                打开授权页
              </Button>
              <span className="text-muted-foreground">
                等待授权完成…({deviceFlow.expires_in || 240}s)
              </span>
            </div>
          </div>
        ) : null}

        {message ? (
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-muted/60 px-2 py-1.5 text-xs text-foreground">
            {message}
          </pre>
        ) : null}

        <DialogFooter className="gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={closing}
            onClick={() => void closeDialog()}
          >
            {closing ? "正在关闭…" : deviceFlow ? "关闭" : "取消"}
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={busy || (isModelProvider && !apiKey.trim())}
            onClick={() => void onSubmit()}
          >
            {busy ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <KeyRound className="mr-1 h-3 w-3" />
            )}
            {isCli
              ? deviceFlow
                ? "重新登录"
                : isFreebuffCli
                  ? "登录 Freebuff"
                  : "执行 CLI 登录"
              : isModelProvider
                ? "验证并接入免费模型"
                : "保存凭据"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** 服务商 OAuth App 凭据配置弹窗(BYO OAuth)。
 *
 * GitHub / GitLab 等连接器不暴露 .well-known 元数据,网页登录靠用户在自己账号下
 * 注册一个 OAuth App(免费、几分钟)。这里收集 client_id + client_secret,加密存
 * 到后端(绝不返回明文 secret),保存后自动继续网页授权。
 */
function OAuthAppDialog({
  open,
  provider,
  providerName,
  docsUrl,
  redirectUri,
  onOpenChange,
  onSaved,
}: {
  open: boolean;
  provider: string;
  providerName: string;
  docsUrl: string;
  redirectUri: string;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [hasExisting, setHasExisting] = useState(false);
  const [existingMask, setExistingMask] = useState("");

  useEffect(() => {
    if (!open) return;
    setClientId("");
    setClientSecret("");
    setMessage(null);
    setBusy(false);
    void getOAuthApp(provider)
      .then((info) => {
        setHasExisting(info.configured);
        setExistingMask(info.client_id_masked);
      })
      .catch(() => setHasExisting(false));
  }, [open, provider]);

  const onSubmit = async () => {
    if (!clientId.trim()) {
      setMessage("请填写 client_id。");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      await saveOAuthApp(provider, clientId.trim(), clientSecret.trim());
      setMessage("凭据已保存,正在打开授权页…");
      onSaved();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  const onRemove = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await deleteOAuthApp(provider);
      setHasExisting(false);
      setExistingMask("");
      setMessage("已移除本地保存的 OAuth App 凭据。");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader className="gap-1 text-left">
          <DialogTitle className="text-[15px]">
            🔗 配置 {providerName} OAuth App
          </DialogTitle>
          <DialogDescription className="text-caption leading-5">
            该服务商不支持自动发现(MCP .well-known),网页登录需要你注册一个 OAuth
            App 获取凭据,和 WorkBuddy 用自己平台注册的 App 一个原理。
            凭据仅保存在本机(加密),不会上传。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2 text-xs">
          {docsUrl ? (
            <RoutedWebLink
              href={docsUrl}
              openTargetSource="oauth-app-documentation"
              className="inline-flex items-center gap-1 text-primary underline underline-offset-2"
            >
              如何创建 {providerName} OAuth App(官方文档)
            </RoutedWebLink>
          ) : null}
          <div className="rounded-md bg-muted/60 px-2.5 py-2 leading-5">
            <div className="font-medium text-foreground">
              回调地址(注册 App 时填写)
            </div>
            <code className="mt-0.5 block break-all text-[11px] text-muted-foreground">
              {redirectUri}
            </code>
          </div>
        </div>

        {hasExisting ? (
          <div className="flex items-center justify-between gap-2 rounded-md border border-border-default px-2.5 py-2 text-xs">
            <span className="text-muted-foreground">
              已保存 OAuth App:{" "}
              <code className="text-foreground">
                {existingMask || "已配置"}
              </code>
            </span>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => void onRemove()}
            >
              移除
            </Button>
          </div>
        ) : null}

        <div className="space-y-2">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              client_id
            </label>
            <Input
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="Iv23xxxxxxxxxxxxxxxx"
              autoComplete="off"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              client_secret
            </label>
            <Input
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              placeholder="gho_xxxxxxxxxxxxxxxx"
              autoComplete="off"
              type="password"
            />
          </div>
        </div>

        {message ? (
          <pre className="max-h-24 overflow-auto whitespace-pre-wrap rounded-md bg-muted/60 px-2 py-1.5 text-xs text-foreground">
            {message}
          </pre>
        ) : null}

        <DialogFooter className="gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
          >
            取消
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={busy}
            onClick={() => void onSubmit()}
          >
            {busy ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <KeyRound className="mr-1 h-3 w-3" />
            )}
            保存并继续授权
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PermissionReviewDialog({
  capability,
  mode,
  plan,
  loading,
  busy,
  error,
  onClose,
  onConfirm,
}: {
  capability: CapabilityInfo;
  mode: "install" | "enable";
  plan: CapabilityInstallPlan | null;
  loading: boolean;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const permissions = plan?.permissions ?? capability.permissions ?? [];
  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="w-[min(480px,calc(100vw-2rem))] gap-4 sm:max-w-[480px]">
        <DialogHeader className="text-left">
          <DialogTitle className="flex items-center gap-2 text-base">
            <ShieldCheck className="size-4 text-primary" />
            确认插件权限 · {capability.name_zh}
          </DialogTitle>
          <DialogDescription className="text-xs leading-5">
            {mode === "install"
              ? capability.model_provider
                ? "先核对签名包会使用的能力；安装后继续配置模型服务凭据。"
                : "先核对签名包会使用的能力；确认后才安装并启用。"
              : "该插件已经安装，但仍处于停用状态。确认权限后才会进入运行时。"}
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex min-h-28 items-center justify-center text-muted-foreground">
            <Loader2 className="size-5 animate-spin" />
          </div>
        ) : plan ? (
          <div className="space-y-3 text-xs">
            <div className="rounded-md border border-border-default bg-muted/25 px-3 py-2 leading-5">
              <div className="flex justify-between gap-3">
                <span className="text-muted-foreground">发布版本</span>
                <span className="font-medium">
                  {plan.version || capability.version}
                </span>
              </div>
              <div className="flex justify-between gap-3">
                <span className="text-muted-foreground">安装结果</span>
                <span>
                  {capability.model_provider
                    ? "验证签名 · 写入适配器 · 等待配置"
                    : "验证签名 · 写入技能 · 默认停用"}
                </span>
              </div>
            </div>

            <div>
              <p className="mb-1.5 font-medium">请求的权限</p>
              {permissions.length ? (
                <div className="space-y-1.5">
                  {permissions.map((permission) => (
                    <div
                      key={permission}
                      className="flex items-center justify-between gap-3 rounded-md border border-border-subtle px-2.5 py-2"
                    >
                      <span>{PERMISSION_LABELS[permission] ?? permission}</span>
                      <code className="text-[10px] text-muted-foreground">
                        {permission}
                      </code>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="rounded-md bg-muted/40 px-2.5 py-2 text-muted-foreground">
                  该签名版本没有声明额外运行权限。
                </p>
              )}
            </div>

            {plan.dependencies.length ? (
              <div>
                <p className="mb-1.5 font-medium">依赖</p>
                <div className="space-y-1">
                  {plan.dependencies.map((dependency) => (
                    <div
                      key={dependency.id}
                      className="flex justify-between rounded-md bg-muted/40 px-2.5 py-1.5"
                    >
                      <span>{dependency.id}</span>
                      <span
                        className={
                          dependency.ready
                            ? "text-emerald-600"
                            : "text-amber-600"
                        }
                      >
                        {dependency.ready ? "已验证" : "待安装"}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {!plan.can_install ? (
              <div className="rounded-md bg-destructive/10 px-3 py-2 text-destructive">
                当前不能安全安装：{plan.blockers.join("，")}
              </div>
            ) : null}
          </div>
        ) : null}

        {error ? (
          <div className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </div>
        ) : null}

        <DialogFooter className="gap-2">
          <Button variant="outline" size="sm" disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button
            size="sm"
            disabled={loading || busy || !plan?.can_install}
            onClick={onConfirm}
          >
            {busy ? <Loader2 className="mr-1 size-3 animate-spin" /> : null}
            {mode === "install"
              ? capability.model_provider
                ? "确认并配置"
                : "确认并启用"
              : "确认权限并启用"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export type CapabilityMarketView = "featured" | "all" | "installed";

export interface CapabilityMarketPanelProps {
  /** 上层市场搜索词；与面板自己的搜索词同时生效。 */
  searchQuery?: string;
  /** 精选、全部或仅已安装。默认保持原来的全部市场。 */
  view?: CapabilityMarketView;
  /** 精选视图按这里的 ID 顺序展示，不补造排行或热度数据。 */
  featuredIds?: readonly string[];
  /** 最多展示多少项。 */
  maxItems?: number;
  /** 是否显示原有类型、搜索和刷新工具栏。 */
  showToolbar?: boolean;
  /** 只展示指定来源；Codex 插件与连接器仍复用同一套生命周期。 */
  source?: CapabilitySource | "";
  /** 使用接近 Codex 桌面端插件目录的紧凑双列列表。 */
  compact?: boolean;
}

const CAPABILITY_PAGE_SIZE = 60;
const CAPABILITY_CATEGORY_STATE_KEY = "echoai.plugin-category-collapse.v1";

function capabilityMatchesQuery(capability: CapabilityInfo, query: string) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  return [
    capability.name,
    capability.name_zh,
    capability.id,
    capability.description,
    capability.description_zh,
    capability.author,
  ]
    .join(" ")
    .toLowerCase()
    .includes(normalized);
}

export function CapabilityMarketPanel({
  searchQuery = "",
  view = "all",
  featuredIds = [],
  maxItems,
  showToolbar = true,
  source = "",
  compact = false,
}: CapabilityMarketPanelProps = {}) {
  const queryClient = useQueryClient();
  const [items, setItems] = useState<CapabilityInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const serverQuery = useDeferredValue(searchQuery.trim() || query.trim());
  const [typeFilter, setTypeFilter] = useState<
    "all" | "mcp" | "cli" | "skill-only" | "plugin"
  >("all");
  const [busyMap, setBusyMap] = useState<Record<string, boolean>>({});
  const [statusMap, setStatusMap] = useState<Record<string, boolean>>({});
  /** 显示只能手动填 token 的插件(默认隐藏,对齐「都能跳网页授权」)。 */
  const [showManual, setShowManual] = useState(false);
  const [collapsedCategories, setCollapsedCategories] = useState<
    Partial<Record<CapabilityCategoryId, boolean>>
  >(() => {
    if (typeof window === "undefined") return {};
    try {
      return JSON.parse(
        window.localStorage.getItem(CAPABILITY_CATEGORY_STATE_KEY) || "{}",
      ) as Partial<Record<CapabilityCategoryId, boolean>>;
    } catch {
      return {};
    }
  });
  const [connectTarget, setConnectTarget] = useState<CapabilityInfo | null>(
    null,
  );
  const [permissionReview, setPermissionReview] = useState<{
    capability: CapabilityInfo;
    mode: "install" | "enable";
    plan: CapabilityInstallPlan | null;
    loading: boolean;
    busy: boolean;
    error: string | null;
  } | null>(null);
  const [oauthAppDialog, setOAuthAppDialog] = useState<{
    provider: string;
    providerName: string;
    docsUrl: string;
    redirectUri: string;
    server: string;
    url: string;
    cap: CapabilityInfo;
  } | null>(null);

  const load = useCallback(
    async (offset = 0) => {
      const append = offset > 0;
      if (append) setLoadingMore(true);
      else setLoading(true);
      setError(null);
      try {
        // Featured and installed views need the complete catalog for their
        // client-side predicates. The high-volume full market is page-backed.
        const paged = view === "all";
        const res = await listCapabilities({
          search: serverQuery || undefined,
          source,
          limit: paged ? CAPABILITY_PAGE_SIZE : 500,
          offset: paged ? offset : 0,
          includeManual: showManual,
        });
        setItems((current) =>
          append ? [...current, ...res.capabilities] : res.capabilities,
        );
        setTotal(res.total);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (append) setLoadingMore(false);
        else setLoading(false);
      }
    },
    [serverQuery, showManual, source, view],
  );

  useEffect(() => {
    void load();
  }, [load]);

  // 拉取已安装能力的连接状态
  const refreshStatus = useCallback(async (installed: CapabilityInfo[]) => {
    const next: Record<string, boolean> = {};
    await Promise.all(
      installed.map(async (c) => {
        try {
          const st = await getCapabilityStatus(c.id);
          next[c.id] = !!st.connected;
        } catch {
          next[c.id] = false;
        }
      }),
    );
    setStatusMap((prev) => ({ ...prev, ...next }));
  }, []);

  useEffect(() => {
    const installed = items.filter((c) => c.installed);
    if (installed.length) void refreshStatus(installed);
  }, [items, refreshStatus]);

  const setBusy = (id: string, busy: boolean) =>
    setBusyMap((m) => ({ ...m, [id]: busy }));

  const viewItems = useMemo(() => {
    if (view === "installed") {
      return items.filter((capability) => capability.installed);
    }
    if (view !== "featured") return items;

    const firstById = new Map<string, CapabilityInfo>();
    for (const capability of items) {
      const identities = [
        capability.id,
        capability.provider_id,
        capability.codex_plugin_id,
        capability.plugin_name,
      ].filter((identity): identity is string => Boolean(identity));
      for (const identity of identities) {
        if (!firstById.has(identity)) {
          firstById.set(identity, capability);
        }
      }
    }
    const seen = new Set<string>();
    return featuredIds.flatMap((id) => {
      if (seen.has(id)) return [];
      seen.add(id);
      const capability = firstById.get(id);
      return capability ? [capability] : [];
    });
  }, [featuredIds, items, view]);

  const filtered = viewItems.filter((c) => {
    if (typeFilter !== "all" && c.type !== typeFilter) return false;
    return (
      capabilityMatchesQuery(c, searchQuery) && capabilityMatchesQuery(c, query)
    );
  });
  const visibleItems =
    typeof maxItems === "number"
      ? filtered.slice(0, Math.max(0, Math.floor(maxItems)))
      : filtered;
  const isFeaturedView = view === "featured";
  const categorizedSections = useMemo(() => {
    if (!compact || view !== "all" || typeof maxItems === "number") return [];
    const buckets = new Map<CapabilityCategoryId, CapabilityInfo[]>();
    for (const category of CAPABILITY_CATEGORIES) buckets.set(category.id, []);
    for (const capability of visibleItems) {
      if (capability.installed) {
        buckets.get("installed")?.push(capability);
      } else {
        buckets.get(capabilityCategory(capability))?.push(capability);
      }
    }
    return CAPABILITY_CATEGORIES.map((category) => ({
      ...category,
      items: buckets.get(category.id) ?? [],
    })).filter((category) => category.items.length > 0);
  }, [compact, maxItems, view, visibleItems]);
  const showCategorizedSections = categorizedSections.length > 0;
  const searchActive = Boolean(searchQuery.trim() || query.trim());
  const marketRows = useMemo(() => {
    if (!showCategorizedSections) {
      return visibleItems.map((capability) => ({
        kind: "capability" as const,
        capability,
      }));
    }
    return categorizedSections.flatMap((section) => {
      const collapsed =
        !searchActive && Boolean(collapsedCategories[section.id]);
      return [
        { kind: "section" as const, section, collapsed },
        ...(collapsed
          ? []
          : section.items.map((capability) => ({
              kind: "capability" as const,
              capability,
            }))),
      ];
    });
  }, [
    categorizedSections,
    collapsedCategories,
    searchActive,
    showCategorizedSections,
    visibleItems,
  ]);

  const toggleCategory = (category: CapabilityCategoryId) => {
    setCollapsedCategories((current) => {
      const next = { ...current, [category]: !current[category] };
      try {
        window.localStorage.setItem(
          CAPABILITY_CATEGORY_STATE_KEY,
          JSON.stringify(next),
        );
      } catch {
        // Browsers with storage disabled still keep the state for this visit.
      }
      return next;
    });
  };

  const counts = useMemo(() => {
    const out: Record<string, number> = { all: items.length };
    for (const c of items) {
      out[c.type] = (out[c.type] ?? 0) + 1;
    }
    return out;
  }, [items]);

  const performInstall = async (
    cap: CapabilityInfo,
    reviewedPlan: CapabilityInstallPlan | null,
  ) => {
    setBusy(cap.id, true);
    if (reviewedPlan) {
      setPermissionReview((current) =>
        current ? { ...current, busy: true, error: null } : current,
      );
    }
    setError(null);
    setNotice(null);
    try {
      const res = await installCapability(cap.id, reviewedPlan?.plan_id);
      const permissions =
        res.permissions ?? reviewedPlan?.permissions ?? cap.permissions ?? [];
      let enabledAfterInstall = res.enabled ?? false;
      setItems((prev) =>
        prev.map((c) =>
          c.id === cap.id
            ? {
                ...c,
                installed: true,
                enabled: enabledAfterInstall,
                permissions,
                permission_review_required:
                  res.permission_review_required ?? permissions.length > 0,
                permission_active: enabledAfterInstall,
              }
            : c,
        ),
      );
      const needsModelProviderConnection = Boolean(
        reviewedPlan && cap.model_provider,
      );
      if (reviewedPlan && !needsModelProviderConnection) {
        await setCapabilityEnabled(cap.id, true, permissions);
        enabledAfterInstall = true;
        setItems((prev) =>
          prev.map((c) =>
            c.id === cap.id
              ? {
                  ...c,
                  installed: true,
                  enabled: true,
                  permissions,
                  permissions_granted: permissions,
                  permission_review_required: false,
                  permission_active: true,
                }
              : c,
          ),
        );
      }
      // CLI 连接器生命周期提示(init/版本)不阻断安装
      const cli = res.cli_lifecycle;
      if (cli?.has_cli) {
        const msgs: string[] = [];
        if (cli.init && !cli.init.ok && cli.init.error) {
          msgs.push(`CLI 工具未装好:${cli.init.error}`);
        }
        if (cli.version && !cli.version.ok && cli.version.error) {
          msgs.push(`版本提示:${cli.version.error}`);
        }
        if (msgs.length) setNotice(msgs.join(" "));
      }
      void refreshStatus([
        { ...cap, installed: true, enabled: enabledAfterInstall },
      ]);
      void queryClient.invalidateQueries({
        queryKey: CAPABILITY_SURFACE_QUERY_KEY,
      });
      if (reviewedPlan) {
        setPermissionReview(null);
        if (needsModelProviderConnection) {
          setConnectTarget({
            ...cap,
            installed: true,
            enabled: false,
            permissions,
            permission_review_required: permissions.length > 0,
            permission_active: false,
          });
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (reviewedPlan) {
        setPermissionReview((current) =>
          current ? { ...current, busy: false, error: message } : current,
        );
      } else {
        setError(message);
      }
    } finally {
      setBusy(cap.id, false);
      if (reviewedPlan) {
        setPermissionReview((current) =>
          current ? { ...current, busy: false } : current,
        );
      }
    }
  };

  const openPermissionReview = async (
    cap: CapabilityInfo,
    mode: "install" | "enable",
  ) => {
    setPermissionReview({
      capability: cap,
      mode,
      plan: null,
      loading: true,
      busy: false,
      error: null,
    });
    try {
      const plan = await getCapabilityInstallPlan(cap.id);
      setPermissionReview((current) =>
        current?.capability.id === cap.id
          ? { ...current, plan, loading: false }
          : current,
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setPermissionReview((current) =>
        current?.capability.id === cap.id
          ? { ...current, loading: false, error: message }
          : current,
      );
    }
  };

  const onInstall = async (cap: CapabilityInfo) => {
    if (cap.is_codex_marketplace) {
      await performInstall(cap, null);
      return;
    }
    await openPermissionReview(cap, "install");
  };

  const onUninstall = async (cap: CapabilityInfo) => {
    setBusy(cap.id, true);
    setError(null);
    setNotice(null);
    try {
      if (supportsDeviceFlow(cap)) {
        const status = await getCapabilityDeviceFlow(cap.id);
        if (status.active && status.device_flow) {
          const cancellation = await cancelCapabilityDeviceFlow(
            cap.id,
            status.device_flow.flow_id,
          );
          if (!cancellation.cancelled) {
            setNotice(
              cancellation.reason === "generation_mismatch"
                ? "授权会话已被新窗口替换，将由卸载操作统一回收最新会话。"
                : "未发现可取消的授权会话，继续执行卸载。",
            );
          }
        }
      }
      await uninstallCapability(cap.id);
      setItems((prev) =>
        prev.map((c) =>
          c.id === cap.id ? { ...c, installed: false, enabled: false } : c,
        ),
      );
      setStatusMap((m) => ({ ...m, [cap.id]: false }));
      void queryClient.invalidateQueries({
        queryKey: CAPABILITY_SURFACE_QUERY_KEY,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(cap.id, false);
    }
  };

  const onToggleEnabled = async (cap: CapabilityInfo) => {
    if (!cap.enabled && cap.permission_review_required) {
      if (cap.model_provider) {
        await openConnect(cap);
        return;
      }
      await openPermissionReview(cap, "enable");
      return;
    }
    setBusy(cap.id, true);
    setError(null);
    try {
      await setCapabilityEnabled(cap.id, !cap.enabled);
      setItems((prev) =>
        prev.map((c) =>
          c.id === cap.id
            ? { ...c, enabled: !c.enabled, permission_active: !c.enabled }
            : c,
        ),
      );
      void queryClient.invalidateQueries({
        queryKey: CAPABILITY_SURFACE_QUERY_KEY,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(cap.id, false);
    }
  };

  const confirmPermissionReview = async () => {
    const review = permissionReview;
    if (!review?.plan?.can_install) return;
    if (review.mode === "install") {
      await performInstall(review.capability, review.plan);
      return;
    }
    const permissions = review.plan.permissions;
    setBusy(review.capability.id, true);
    setPermissionReview({ ...review, busy: true, error: null });
    try {
      await setCapabilityEnabled(
        review.capability.id,
        true,
        permissions,
        review.plan.plan_id,
      );
      setItems((current) =>
        current.map((capability) =>
          capability.id === review.capability.id
            ? {
                ...capability,
                enabled: true,
                permissions,
                permissions_granted: permissions,
                permission_review_required: false,
                permission_active: true,
              }
            : capability,
        ),
      );
      setPermissionReview(null);
      void queryClient.invalidateQueries({
        queryKey: CAPABILITY_SURFACE_QUERY_KEY,
      });
    } catch (err) {
      setPermissionReview((current) =>
        current
          ? {
              ...current,
              busy: false,
              error: err instanceof Error ? err.message : String(err),
            }
          : current,
      );
    } finally {
      setBusy(review.capability.id, false);
    }
  };

  const onDisconnect = async (cap: CapabilityInfo) => {
    setBusy(cap.id, true);
    setError(null);
    try {
      await disconnectCapability(cap.id);
      setStatusMap((m) => ({ ...m, [cap.id]: false }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(cap.id, false);
    }
  };

  // 跑一遍「网页授权」:authorize → 弹窗 → 轮询回调结果。
  const runWebOAuth = async (
    cap: CapabilityInfo,
    server: string,
    url: string,
  ) => {
    const {
      authorize_url,
      needs_app_credentials,
      provider,
      provider_name,
      docs_url,
      redirect_uri,
      callback_transport,
    } = await oauthAuthorize(server, url, cap.oauth_provider ?? undefined);
    // 服务商直连 OAuth(GitHub 等)还没配置 OAuth App 凭据 → 引导用户填写
    if (needs_app_credentials && provider) {
      setOAuthAppDialog({
        provider,
        providerName: provider_name ?? provider,
        docsUrl: docs_url ?? "",
        redirectUri: redirect_uri ?? "",
        server,
        url,
        cap,
      });
      return;
    }
    if (
      callback_transport === "desktop-deep-link" &&
      !window.echo?.isElectron
    ) {
      setError(
        "该服务商使用桌面客户端回跳，请在 EchoAI 桌面版中完成授权。浏览器版无法安全接收授权结果。",
      );
      return;
    }
    const popup = window.open(
      authorize_url,
      "echo-mcp-oauth",
      "popup=yes,width=560,height=720",
    );
    if (!popup) {
      setError("授权窗口被浏览器拦截,请允许弹窗后重试");
      return;
    }
    const ok = await pollOAuth(server);
    if (ok) {
      setStatusMap((m) => ({ ...m, [cap.id]: true }));
    } else {
      setError("未完成网页授权(超时或取消),可重试或改用手动填写凭据");
    }
  };

  const openConnect = async (cap: CapabilityInfo) => {
    // 插件(Codex)无需认证,直接确认就绪
    if (cap.source === "codex_plugin") {
      setBusy(cap.id, true);
      void connectCapability(cap.id)
        .then(() => {
          setStatusMap((m) => ({ ...m, [cap.id]: true }));
        })
        .catch((err) =>
          setError(err instanceof Error ? err.message : String(err)),
        )
        .finally(() => setBusy(cap.id, false));
      return;
    }

    // OneID(腾讯统一身份)特例:走 oneid-token 专用流程,不尝试网页 OAuth。
    if (cap.auth_mode === "oneid-token") {
      setConnectTarget(cap);
      return;
    }

    // MCP 型插件:优先走「网页登录授权」——打开服务商授权页,登录授权后回调。
    // 服务商不支持网页授权时才回退到手动填 token。
    const mcp = (cap.mcp_servers ?? []).find((s) => s && s.url);
    if (mcp) {
      setBusy(cap.id, true);
      setError(null);
      try {
        await runWebOAuth(cap, mcp.name, mcp.url);
      } catch {
        // 无 .well-known 发现 + 非服务商直连 OAuth → 回退到手动填写凭据
        setConnectTarget(cap);
      } finally {
        setBusy(cap.id, false);
      }
      return;
    }

    // 其余类型:打开手动填写凭据对话框
    setConnectTarget(cap);
  };

  return (
    <div className="space-y-3">
      {/* 来源 + 类型 + 搜索 */}
      {showToolbar ? (
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div className="flex flex-wrap items-center gap-1.5">
            <Button
              type="button"
              size="sm"
              variant={typeFilter === "all" ? "secondary" : "ghost"}
              onClick={() => setTypeFilter("all")}
              className="h-8 px-2.5 text-xs"
            >
              全部
              <span className="ml-1 text-xs text-muted-foreground">
                {counts.all}
              </span>
            </Button>
            {(["all", "mcp", "cli", "skill-only", "plugin"] as const).map(
              (tp) => (
                <Button
                  key={tp}
                  type="button"
                  size="sm"
                  variant={typeFilter === tp ? "secondary" : "ghost"}
                  onClick={() => setTypeFilter(tp)}
                  className="h-8 px-2.5 text-xs"
                >
                  {tp === "all"
                    ? "全部类型"
                    : tp === "mcp"
                      ? "MCP"
                      : tp === "cli"
                        ? "CLI"
                        : tp === "plugin"
                          ? "插件"
                          : "技能"}
                  <span className="ml-1 text-xs text-muted-foreground">
                    {counts[tp] ?? 0}
                  </span>
                </Button>
              ),
            )}
          </div>

          <div className="flex shrink-0 items-center gap-1.5">
            <span className="text-xs text-muted-foreground">
              {visibleItems.length}/{viewItems.length}
            </span>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索插件"
                aria-label="搜索插件"
                className="h-8 w-44 rounded-md border border-border-default bg-background pl-7 pr-2 text-sm outline-none focus:border-primary/50"
              />
            </div>
            <Button
              size="sm"
              variant={showManual ? "secondary" : "ghost"}
              disabled={loading}
              onClick={() => setShowManual((v) => !v)}
              title="显示只能手动填 token、不能跳网页授权的插件"
            >
              {showManual ? "隐藏手动填" : "显示手动填"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={loading}
              onClick={() => void load()}
              title="刷新能力列表"
            >
              <RefreshCw
                className={cn("size-3.5", loading && "animate-spin")}
              />
            </Button>
          </div>
        </div>
      ) : null}

      {error ? (
        <div className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      ) : null}

      {notice ? (
        <div className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
          {notice}
        </div>
      ) : null}

      {loading ? (
        <div className="flex min-h-[200px] items-center justify-center text-muted-foreground">
          <Loader2 className="size-5 animate-spin" />
        </div>
      ) : (
        <div
          className={cn(
            "grid grid-cols-1",
            compact ? "gap-2 lg:grid-cols-2" : "gap-3 sm:grid-cols-2",
            !compact && isFeaturedView
              ? "xl:grid-cols-3"
              : !compact && "lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5",
          )}
        >
          {marketRows.map((row) => {
            if (row.kind === "section") {
              return (
                <button
                  key={`section-${row.section.id}`}
                  type="button"
                  aria-expanded={!row.collapsed}
                  data-testid={`plugin-category-${row.section.id}`}
                  onClick={() => toggleCategory(row.section.id)}
                  className="group col-span-full flex h-10 items-center gap-2 border-b border-border-subtle px-1 text-left"
                >
                  <span className="text-sm font-semibold text-foreground">
                    {row.section.label}
                  </span>
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {row.section.items.length}
                  </span>
                  <ChevronDown
                    className={cn(
                      "ml-auto size-4 text-muted-foreground transition-transform duration-base",
                      row.collapsed && "-rotate-90",
                    )}
                  />
                </button>
              );
            }
            const cap = row.capability;
            const typeMeta = TYPE_META[cap.type] ?? DEFAULT_TYPE_META;
            const busy = busyMap[cap.id];
            const connected = statusMap[cap.id];
            const isPlugin = cap.source === "codex_plugin";
            const isCodexMarketplace = cap.is_codex_marketplace === true;
            return (
              <Card
                key={`${cap.source}:${cap.id}`}
                data-capability-id={cap.id}
                className={cn(
                  "gap-2.5 py-3 transition-colors hover:border-primary/40",
                  isFeaturedView && "min-h-44 rounded-xl py-4",
                  compact &&
                    "min-h-16 rounded-none border-0 border-b border-border-subtle bg-transparent py-1.5 shadow-none hover:bg-muted/25 sm:grid sm:grid-cols-[minmax(0,1fr)_auto] sm:grid-rows-1 sm:items-center sm:gap-x-3",
                )}
              >
                <CardHeader
                  className={cn(
                    "flex-row items-center gap-2.5 px-3 pt-0",
                    compact && "sm:col-start-1 sm:row-start-1",
                  )}
                >
                  <div
                    className={cn(
                      "flex size-10 shrink-0 items-center justify-center rounded-lg border border-border-default bg-muted text-base",
                      compact && "size-9 rounded-none border-0 bg-transparent",
                    )}
                  >
                    <CapabilityIcon capability={cap} />
                  </div>
                  <div className="min-w-0">
                    <CardTitle className="truncate text-sm">
                      {cap.name_zh}
                    </CardTitle>
                    <CardDescription className="truncate text-xs">
                      {compact
                        ? cap.description_zh || cap.description
                        : isFeaturedView
                          ? `来自 ${cap.author || "EchoOS"}`
                          : `${cap.id} · ${AUTH_LABEL[cap.auth_mode] ?? cap.auth_mode}`}
                    </CardDescription>
                  </div>
                </CardHeader>
                <div
                  className={cn(
                    "flex flex-wrap items-center gap-1 px-3",
                    compact && "hidden",
                  )}
                >
                  <Badge
                    className={cn(
                      "border-transparent text-[11px]",
                      typeMeta.badge,
                    )}
                  >
                    {typeMeta.label}
                  </Badge>
                  {isPlugin ? (
                    <Badge className="border-transparent bg-sky-500/10 text-[11px] text-sky-600 dark:text-sky-300">
                      Codex
                    </Badge>
                  ) : null}
                  {isCodexMarketplace && cap.marketplace_name ? (
                    <Badge
                      variant="outline"
                      className="text-[11px] font-normal text-muted-foreground"
                    >
                      {cap.marketplace_name}
                    </Badge>
                  ) : null}
                  {!isFeaturedView &&
                    (cap.oauth_supported || cap.has_cli_auth) && (
                      <Badge
                        className="border-transparent bg-sky-500/15 text-[11px] text-sky-600 dark:text-sky-400"
                        title="支持跳转网页登录授权,无需手动填 token"
                      >
                        🔗 网页登录
                      </Badge>
                    )}
                  {!isFeaturedView ? (
                    <Badge
                      variant="outline"
                      className="text-[11px] font-normal text-muted-foreground"
                    >
                      技能 ×{cap.skill_count}
                    </Badge>
                  ) : null}
                  {!isFeaturedView && cap.mcp_servers.length > 0 && (
                    <Badge
                      variant="outline"
                      className="text-[11px] font-normal text-muted-foreground"
                    >
                      MCP ×{cap.mcp_servers.length}
                    </Badge>
                  )}
                  {cap.installed && (
                    <Badge
                      className={cn(
                        "border-transparent text-[11px]",
                        connected
                          ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                          : "bg-amber-500/15 text-amber-600 dark:text-amber-400",
                      )}
                    >
                      {connected ? "已连接" : "未连接"}
                    </Badge>
                  )}
                  {cap.permission_review_required ? (
                    <Badge className="border-transparent bg-amber-500/15 text-[11px] text-amber-700 dark:text-amber-300">
                      待确认权限
                    </Badge>
                  ) : null}
                </div>

                <div className={cn("px-3", compact && "hidden")}>
                  <p
                    className={cn(
                      "text-xs leading-5 text-muted-foreground",
                      compact ? "line-clamp-1" : "line-clamp-2",
                    )}
                  >
                    {cap.description_zh || cap.description}
                  </p>
                  {cap.author && !isFeaturedView ? (
                    <p className="mt-0.5 text-[11px] text-muted-foreground/70">
                      作者:{cap.author}
                    </p>
                  ) : null}
                </div>

                <CardFooter
                  className={cn(
                    "flex flex-wrap gap-1.5 px-3 pb-0",
                    compact &&
                      "sm:col-start-2 sm:row-start-1 sm:self-center sm:justify-end sm:pr-3",
                  )}
                >
                  {!cap.installed ? (
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 rounded-sm px-3 text-xs"
                      disabled={busy || cap.installable === false}
                      onClick={() => void onInstall(cap)}
                      title={
                        cap.installable === false
                          ? "当前账号或工作区不允许安装"
                          : "按需安装"
                      }
                    >
                      {busy ? (
                        <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                      ) : (
                        <CloudDownload className="mr-1 h-3 w-3" />
                      )}
                      {cap.installable === false ? "不可安装" : "安装"}
                    </Button>
                  ) : isCodexMarketplace ? (
                    <>
                      <span className="px-1 text-xs text-emerald-600 dark:text-emerald-400">
                        已安装
                      </span>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 rounded-sm px-2 text-xs text-muted-foreground"
                        disabled={busy || cap.lifecycle_manageable === false}
                        onClick={() => void onUninstall(cap)}
                        title={
                          cap.lifecycle_manageable === false
                            ? "当前账号或工作区不允许卸载"
                            : "卸载 Codex 插件"
                        }
                      >
                        {busy ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Trash2 className="h-3 w-3" />
                        )}
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 rounded-sm px-3 text-xs"
                        disabled={busy}
                        onClick={() => void onToggleEnabled(cap)}
                        title={
                          cap.enabled
                            ? "禁用"
                            : cap.model_provider &&
                                cap.permission_review_required
                              ? "配置模型服务并确认权限"
                            : cap.permission_review_required
                              ? "查看并确认签名权限"
                              : "启用"
                        }
                      >
                        {busy ? (
                          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                        ) : cap.enabled ? (
                          <PlugZap className="mr-1 h-3 w-3 text-emerald-500" />
                        ) : cap.model_provider &&
                          cap.permission_review_required ? (
                          <KeyRound className="mr-1 h-3 w-3" />
                        ) : (
                          <Plug className="mr-1 h-3 w-3" />
                        )}
                        {cap.enabled
                          ? "启用中"
                          : cap.model_provider &&
                              cap.permission_review_required
                            ? "配置并启用"
                          : cap.permission_review_required
                            ? "确认权限"
                            : "已禁用"}
                      </Button>
                      {!(
                        cap.model_provider && cap.permission_review_required
                      ) ? (
                        <Button
                          size="sm"
                          variant={connected ? "outline" : "secondary"}
                          className="h-7 rounded-sm px-3 text-xs"
                          disabled={busy || cap.permission_review_required}
                          onClick={() =>
                            connected
                              ? void onDisconnect(cap)
                              : void openConnect(cap)
                          }
                          title={
                            cap.permission_review_required
                              ? "请先确认权限并启用"
                              : connected
                                ? "断开并清除凭据"
                                : "连接/认证"
                          }
                        >
                          {busy ? (
                            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                          ) : connected ? (
                            <Unplug className="mr-1 h-3 w-3" />
                          ) : (
                            <KeyRound className="mr-1 h-3 w-3" />
                          )}
                          {connected ? "断开" : "连接"}
                        </Button>
                      ) : null}
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 rounded-sm px-2 text-xs text-muted-foreground"
                        disabled={busy || cap.lifecycle_manageable === false}
                        onClick={() => void onUninstall(cap)}
                        title={
                          cap.lifecycle_manageable === false
                            ? "当前账号或工作区不允许卸载"
                            : "卸载能力包"
                        }
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </>
                  )}
                </CardFooter>
              </Card>
            );
          })}
          {visibleItems.length === 0 && !loading && (
            <div className="col-span-full py-8 text-center text-sm text-muted-foreground">
              {view === "featured"
                ? "暂无精选应用"
                : view === "installed"
                  ? "还没有已安装应用"
                  : "没有匹配的应用"}
            </div>
          )}
        </div>
      )}

      {!loading && view === "all" && items.length < total ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="mx-auto flex"
          disabled={loadingMore}
          onClick={() => void load(items.length)}
        >
          {loadingMore ? "加载中…" : `加载更多(${total - items.length})`}
        </Button>
      ) : null}

      {permissionReview ? (
        <PermissionReviewDialog
          capability={permissionReview.capability}
          mode={permissionReview.mode}
          plan={permissionReview.plan}
          loading={permissionReview.loading}
          busy={permissionReview.busy}
          error={permissionReview.error}
          onClose={() => setPermissionReview(null)}
          onConfirm={() => void confirmPermissionReview()}
        />
      ) : null}

      {connectTarget ? (
        <ConnectDialog
          capability={connectTarget}
          open={!!connectTarget}
          onOpenChange={(open) => {
            if (!open) setConnectTarget(null);
          }}
          onConnected={() => {
            setStatusMap((m) => ({
              ...m,
              [connectTarget.id]: true,
            }));
          }}
        />
      ) : null}

      {oauthAppDialog ? (
        <OAuthAppDialog
          open
          provider={oauthAppDialog.provider}
          providerName={oauthAppDialog.providerName}
          docsUrl={oauthAppDialog.docsUrl}
          redirectUri={oauthAppDialog.redirectUri}
          onOpenChange={(open) => {
            if (!open) setOAuthAppDialog(null);
          }}
          onSaved={async () => {
            const dlg = oauthAppDialog;
            setOAuthAppDialog(null);
            if (!dlg) return;
            setBusy(dlg.cap.id, true);
            setError(null);
            try {
              await runWebOAuth(dlg.cap, dlg.server, dlg.url);
            } catch {
              setConnectTarget(dlg.cap);
            } finally {
              setBusy(dlg.cap.id, false);
            }
          }}
        />
      ) : null}
    </div>
  );
}
