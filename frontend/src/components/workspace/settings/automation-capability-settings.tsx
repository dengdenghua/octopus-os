import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2Icon,
  CircleHelpIcon,
  ExternalLinkIcon,
  Globe2Icon,
  Loader2Icon,
  MonitorIcon,
  RefreshCwIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { getBrowserConfig, updateBrowserConfig } from "@/core/browser/api";
import { useI18n } from "@/core/i18n/hooks";
import {
  getLinkOpenTarget,
  setLinkOpenTarget,
  subscribeLinkOpenTarget,
  type LinkOpenTarget,
} from "@/core/settings/automation-preferences";
import {
  getBrowserRelayStatus,
  getDesktopAutomationPermissions,
  openDesktopAutomationPermission,
  subscribeBrowserRelayStatus,
} from "@/core/settings/automation-status-api";
import {
  type Capabilities,
  getCapabilities,
  saveCapabilities,
} from "@/core/settings/capabilities-api";
import { cn } from "@/lib/utils";

const CAPABILITIES_QUERY_KEY = ["automation-capabilities"] as const;
const RELAY_QUERY_KEY = ["browser-relay-status"] as const;
const DESKTOP_PERMISSIONS_QUERY_KEY = [
  "desktop-automation-permissions",
] as const;
const BROWSER_POLICY_QUERY_KEY = ["browser-site-policy"] as const;

function hostPatterns(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[\s,]+/)
        .map((item) => item.trim().toLowerCase())
        .filter(Boolean),
    ),
  );
}

function useCapabilitySettings() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: CAPABILITIES_QUERY_KEY,
    queryFn: getCapabilities,
    staleTime: 30_000,
  });
  const mutation = useMutation({
    mutationFn: (capabilities: Capabilities) => saveCapabilities(capabilities),
    onSuccess: (result) => {
      queryClient.setQueryData(CAPABILITIES_QUERY_KEY, result.capabilities);
    },
  });
  const setCapability = async (key: keyof Capabilities, enabled: boolean) => {
    if (!query.data) return;
    return mutation.mutateAsync({ ...query.data, [key]: enabled });
  };
  return { query, mutation, setCapability };
}

function SettingsHeading({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <header className="flex items-start gap-3">
      <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
        {icon}
      </div>
      <div className="min-w-0">
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
          {description}
        </p>
      </div>
    </header>
  );
}

function CapabilitySwitchCard({
  title,
  description,
  checked,
  disabled,
  onCheckedChange,
}: {
  title: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <section className="flex items-start justify-between gap-4 rounded-xl border border-border-default bg-card/35 p-4">
      <div className="min-w-0">
        <div className="text-sm font-medium">{title}</div>
        <p className="mt-1 max-w-xl text-xs leading-5 text-muted-foreground">
          {description}
        </p>
      </div>
      <Switch
        aria-label={title}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onCheckedChange}
        className="mt-0.5 shrink-0"
      />
    </section>
  );
}

function StatusRow({
  label,
  value,
  tone,
  detail,
  action,
}: {
  label: string;
  value: string;
  tone: "success" | "warning" | "danger" | "neutral";
  detail?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex min-h-12 items-center gap-3 rounded-lg border border-border-subtle px-3 py-2.5">
      <span
        aria-hidden="true"
        className={cn(
          "size-2 shrink-0 rounded-full ring-4",
          tone === "success" && "bg-success ring-success/10",
          tone === "warning" && "bg-warning ring-warning/10",
          tone === "danger" && "bg-destructive ring-destructive/10",
          tone === "neutral" && "bg-muted-foreground/45 ring-muted/60",
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className="text-sm font-medium">{label}</span>
          <span className="text-xs text-muted-foreground">{value}</span>
        </div>
        {detail ? (
          <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
            {detail}
          </p>
        ) : null}
      </div>
      {action}
    </div>
  );
}

function useChineseCopy() {
  const { locale } = useI18n();
  return locale.toLowerCase().startsWith("zh");
}

export function BrowserAutomationSettingsPage() {
  const zh = useChineseCopy();
  const queryClient = useQueryClient();
  const { query, mutation, setCapability } = useCapabilitySettings();
  const relay = useQuery({
    queryKey: RELAY_QUERY_KEY,
    queryFn: getBrowserRelayStatus,
    refetchInterval: 2_000,
    retry: false,
  });
  useEffect(
    () =>
      subscribeBrowserRelayStatus((status) => {
        queryClient.setQueryData(RELAY_QUERY_KEY, status);
      }),
    [queryClient],
  );
  const [linkTarget, setTarget] = useState<LinkOpenTarget>(() =>
    getLinkOpenTarget(),
  );
  const policy = useQuery({
    queryKey: BROWSER_POLICY_QUERY_KEY,
    queryFn: getBrowserConfig,
    staleTime: 15_000,
  });
  const policyMutation = useMutation({
    mutationFn: updateBrowserConfig,
    onSuccess: (result) => {
      void policy.refetch();
      toast.success(zh ? "网站权限已保存" : "Site permissions saved");
      return result;
    },
  });
  const [allowedHosts, setAllowedHosts] = useState("");
  const [blockedHosts, setBlockedHosts] = useState("");
  const [requireAllowlist, setRequireAllowlist] = useState(false);

  useEffect(() => subscribeLinkOpenTarget(setTarget), []);
  useEffect(() => {
    if (!policy.data) return;
    setAllowedHosts((policy.data.relay_allowed_hosts || []).join(", "));
    setBlockedHosts((policy.data.relay_blocked_hosts || []).join(", "));
    setRequireAllowlist(policy.data.relay_require_allowlist === true);
  }, [policy.data]);

  const relayState = relay.isError
    ? "reconnecting"
    : (relay.data?.connection_state ??
      (relay.data?.connected ? "online" : "offline"));
  const relayTone =
    relayState === "online"
      ? "success"
      : relayState === "reconnecting"
        ? "warning"
        : "danger";
  const relayVersion = relay.data?.extension_version
    ? `${zh ? "扩展版本" : "Extension version"} ${relay.data.extension_version}`
    : "";
  const relayReconnectHelp =
    relayState === "offline"
      ? zh
        ? "打开 Chrome 扩展页，确认 EchoAI Browser Relay 已启用；再打开扩展侧栏重连。"
        : "Enable EchoAI Browser Relay in Chrome extensions, then open its side panel to reconnect."
      : "";
  const relayDetail = [relayVersion, relayReconnectHelp]
    .filter(Boolean)
    .join(" · ");

  const toggle = async (enabled: boolean) => {
    try {
      const result = await setCapability("browser_automation", enabled);
      toast.success(
        zh ? "浏览器自动化设置已保存" : "Browser automation setting saved",
        {
          description: result?.restart_required
            ? zh
              ? "设置已保存；重启后端后刷新工具目录"
              : "Saved; restart the backend to refresh the tool catalog"
            : zh
              ? enabled
                ? "已立即加入后续对话的工具目录"
                : "已立即阻止调用并从后续工具目录移除"
              : enabled
                ? "Added to the tool catalog for subsequent conversations"
                : "Calls are blocked and removed from subsequent tool catalogs",
        },
      );
    } catch {
      toast.error(zh ? "保存失败" : "Could not save setting");
    }
  };

  if (query.isLoading) {
    return (
      <LoadingState
        label={zh ? "正在读取浏览器能力…" : "Loading browser capability…"}
      />
    );
  }

  return (
    <div className="space-y-6">
      <SettingsHeading
        icon={<Globe2Icon className="size-5" />}
        title={zh ? "浏览器自动化" : "Browser automation"}
        description={
          zh
            ? "让 Echo 在你授权的浏览器标签页中查看、点击和输入。连接状态和开关都在这里集中管理。"
            : "Let Echo inspect, click, and type in browser tabs you authorize. Manage access and connection status here."
        }
      />
      <CapabilitySwitchCard
        title={zh ? "允许浏览器操作" : "Allow browser actions"}
        description={
          zh
            ? "关闭后，浏览器与 live_browser 工具不会进入 Agent 的可用工具目录。"
            : "When off, browser and live_browser tools are removed from the agent tool catalog."
        }
        checked={query.data?.browser_automation === true}
        disabled={mutation.isPending || !query.data}
        onCheckedChange={(value) => void toggle(value)}
      />

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold">
              {zh ? "浏览器扩展连接" : "Browser extension connection"}
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {zh
                ? "状态每 2 秒自动更新；断开后 10 秒内会变为离线。"
                : "Refreshes every 2 seconds and turns offline within 10 seconds of disconnecting."}
            </p>
          </div>
          {relayState !== "online" ? <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={relay.isFetching}
            onClick={() => void relay.refetch()}
          >
            <RefreshCwIcon
              className={cn(
                "mr-1.5 size-3.5",
                relay.isFetching && "animate-spin",
              )}
            />
            {zh ? "重新连接浏览器扩展" : "Reconnect browser extension"}
          </Button> : null}
        </div>
        <StatusRow
          label={zh ? "Relay" : "Relay"}
          value={
            relayState === "online"
              ? zh
                ? "在线"
                : "Online"
              : relayState === "reconnecting"
                ? zh
                  ? "重连中"
                  : "Reconnecting"
                : zh
                  ? "离线"
                  : "Offline"
          }
          tone={relayTone}
          detail={relayDetail || undefined}
        />
      </section>

      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-semibold">
            {zh ? "网站权限记忆" : "Remember site permissions"}
          </h3>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {zh
              ? "把授权限定在网站范围。阻止列表始终优先；开启白名单后，未列出的网站不会被自动操作。支持 example.com 与 *.example.com。"
              : "Scope automation permission to websites. The block list always wins; allowlist mode prevents automation on unlisted sites. Supports example.com and *.example.com."}
          </p>
        </div>
        <div className="flex items-start justify-between gap-4 rounded-lg border border-border-subtle px-3 py-2.5">
          <div>
            <div className="text-sm font-medium">
              {zh ? "只操作允许的网站" : "Only automate allowed sites"}
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {zh
                ? "关闭时采用全局允许，并继续遵守阻止列表。"
                : "When off, global access is allowed except for blocked sites."}
            </div>
          </div>
          <Switch
            checked={requireAllowlist}
            onCheckedChange={setRequireAllowlist}
            aria-label={zh ? "只操作允许的网站" : "Only automate allowed sites"}
          />
        </div>
        <label className="block space-y-1.5">
          <span className="text-xs font-medium">
            {zh ? "允许的网站" : "Allowed sites"}
          </span>
          <Input
            value={allowedHosts}
            onChange={(event) => setAllowedHosts(event.target.value)}
            placeholder="example.com, *.example.com"
            aria-label={zh ? "允许的网站" : "Allowed sites"}
          />
        </label>
        <label className="block space-y-1.5">
          <span className="text-xs font-medium">
            {zh ? "始终阻止的网站" : "Always blocked sites"}
          </span>
          <Input
            value={blockedHosts}
            onChange={(event) => setBlockedHosts(event.target.value)}
            placeholder="accounts.example.com"
            aria-label={zh ? "始终阻止的网站" : "Always blocked sites"}
          />
        </label>
        <Button
          type="button"
          size="sm"
          disabled={policy.isLoading || policyMutation.isPending}
          onClick={() =>
            policyMutation.mutate({
              relay_allowed_hosts: hostPatterns(allowedHosts),
              relay_blocked_hosts: hostPatterns(blockedHosts),
              relay_require_allowlist: requireAllowlist,
            })
          }
        >
          {policyMutation.isPending
            ? zh
              ? "保存中…"
              : "Saving…"
            : zh
              ? "保存网站权限"
              : "Save site permissions"}
        </Button>
      </section>

      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-semibold">
            {zh ? "链接打开方式" : "Open links in"}
          </h3>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {zh
              ? "用于对话和资料里的普通网页链接；明确标注“外部打开”的按钮不受影响。"
              : "Applies to regular web links in conversations and sources. Explicit external-open actions are unchanged."}
          </p>
        </div>
        <Select
          value={linkTarget}
          onValueChange={(value: LinkOpenTarget) => {
            setLinkOpenTarget(value);
            setTarget(value);
          }}
        >
          <SelectTrigger
            className="w-full sm:w-64"
            aria-label={zh ? "链接打开方式" : "Link open target"}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="external">
              {zh ? "外部浏览器" : "External browser"}
            </SelectItem>
            <SelectItem value="in_app">
              {zh ? "Echo 应用内" : "Inside Echo"}
            </SelectItem>
          </SelectContent>
        </Select>
      </section>
    </div>
  );
}

export function DesktopAutomationSettingsPage() {
  const zh = useChineseCopy();
  const { query, mutation, setCapability } = useCapabilitySettings();
  const permissions = useQuery({
    queryKey: DESKTOP_PERMISSIONS_QUERY_KEY,
    queryFn: getDesktopAutomationPermissions,
    refetchInterval: 4_000,
  });
  const data = permissions.data;

  const toggle = async (enabled: boolean) => {
    try {
      const result = await setCapability("desktop_automation", enabled);
      toast.success(
        zh ? "桌面自动化设置已保存" : "Desktop automation setting saved",
        {
          description: result?.restart_required
            ? zh
              ? "设置已保存；重启后端后刷新工具目录"
              : "Saved; restart the backend to refresh the tool catalog"
            : zh
              ? enabled
                ? "已立即加入后续对话的工具目录"
                : "已立即阻止调用并从后续工具目录移除"
              : enabled
                ? "Added to the tool catalog for subsequent conversations"
                : "Calls are blocked and removed from subsequent tool catalogs",
        },
      );
    } catch {
      toast.error(zh ? "保存失败" : "Could not save setting");
    }
  };

  if (query.isLoading) {
    return (
      <LoadingState
        label={zh ? "正在读取桌面能力…" : "Loading desktop capability…"}
      />
    );
  }

  const permissionValue = (value: string) =>
    value === "granted"
      ? zh
        ? "已允许"
        : "Allowed"
      : value === "denied" || value === "restricted"
        ? zh
          ? "未允许"
          : "Not allowed"
        : zh
          ? "仅桌面端可检测"
          : "Desktop app only";

  return (
    <div className="space-y-6">
      <SettingsHeading
        icon={<MonitorIcon className="size-5" />}
        title={zh ? "桌面自动化" : "Desktop automation"}
        description={
          zh
            ? "让 Echo 读取屏幕并操作本机应用。Web 端只展示能力状态，macOS 桌面端负责真实权限探测。"
            : "Let Echo read the screen and operate local apps. The macOS desktop app performs the real permission checks."
        }
      />
      <CapabilitySwitchCard
        title={zh ? "允许桌面操作" : "Allow desktop actions"}
        description={
          zh
            ? "关闭后，电脑操控工具不会进入 Agent 的可用工具目录。长任务豁免和现有审批规则保持不变。"
            : "When off, computer-use tools are removed from the agent tool catalog. Existing approvals remain unchanged."
        }
        checked={query.data?.desktop_automation === true}
        disabled={mutation.isPending || !query.data}
        onCheckedChange={(value) => void toggle(value)}
      />

      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-semibold">
            {zh ? "macOS 系统权限" : "macOS permissions"}
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {data?.supported
              ? zh
                ? "权限改变后会自动刷新。"
                : "Permission changes refresh automatically."
              : zh
                ? "请在 Echo macOS 桌面端查看并授权。"
                : "Open the Echo macOS desktop app to inspect and grant permissions."}
          </p>
        </div>
        <StatusRow
          label={zh ? "屏幕录制" : "Screen recording"}
          value={permissionValue(data?.screenRecording ?? "unknown")}
          tone={
            data?.screenRecording === "granted"
              ? "success"
              : data?.supported
                ? "danger"
                : "neutral"
          }
          action={
            data?.supported && data.screenRecording !== "granted" ? (
              <PermissionButton
                permission="screen-recording"
                label={zh ? "打开设置" : "Open settings"}
              />
            ) : undefined
          }
        />
        <StatusRow
          label={zh ? "辅助功能" : "Accessibility"}
          value={permissionValue(data?.accessibility ?? "unknown")}
          tone={
            data?.accessibility === "granted"
              ? "success"
              : data?.supported
                ? "danger"
                : "neutral"
          }
          action={
            data?.supported && data.accessibility !== "granted" ? (
              <PermissionButton
                permission="accessibility"
                label={zh ? "打开设置" : "Open settings"}
              />
            ) : undefined
          }
        />
      </section>

      <div className="flex items-start gap-2 rounded-xl border border-border-subtle bg-muted/20 px-3 py-3 text-xs leading-5 text-muted-foreground">
        {data?.supported ? (
          <CheckCircle2Icon className="mt-0.5 size-4 shrink-0 text-success" />
        ) : (
          <CircleHelpIcon className="mt-0.5 size-4 shrink-0" />
        )}
        <span>
          {zh
            ? "系统权限与 Echo 总开关是两层控制：两者都就绪时桌面操控才可用。"
            : "System permissions and the Echo capability switch are separate gates; both must be ready."}
        </span>
      </div>
    </div>
  );
}

function PermissionButton({
  permission,
  label,
}: {
  permission: "screen-recording" | "accessibility";
  label: string;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className="shrink-0"
      onClick={() => void openDesktopAutomationPermission(permission)}
    >
      <ExternalLinkIcon className="mr-1.5 size-3.5" />
      {label}
    </Button>
  );
}

function LoadingState({ label }: { label: string }) {
  return (
    <div
      role="status"
      className="flex items-center py-8 text-sm text-muted-foreground"
    >
      <Loader2Icon className="mr-2 size-4 animate-spin" />
      {label}
    </div>
  );
}
