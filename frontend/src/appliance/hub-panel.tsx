import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ActivityIcon,
  BotIcon,
  CheckCircle2Icon,
  CloudIcon,
  CopyIcon,
  CpuIcon,
  DownloadIcon,
  ExternalLinkIcon,
  FileTextIcon,
  FilmIcon,
  HardDriveIcon,
  HomeIcon,
  ImageIcon,
  InfoIcon,
  LibraryIcon,
  Loader2Icon,
  LockKeyholeIcon,
  NetworkIcon,
  PanelsTopLeftIcon,
  PuzzleIcon,
  PowerIcon,
  RefreshCwIcon,
  SearchIcon,
  ServerIcon,
  ShieldCheckIcon,
  ShoppingBagIcon,
  SparklesIcon,
  Trash2Icon,
  UnplugIcon,
  XIcon,
  type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";

import { requestHighRiskApproval } from "@/appliance/approval";
import {
  applyAgentCapabilityLifecycle,
  authorizeAgentCapability,
  connectAgentCapability,
  createAgentCapabilityPlan,
  disconnectAgentCapability,
  disableAgentCapability,
  fetchAgentCapabilityConnectionProfile,
  fetchAgentHubCatalog,
  type AgentCapabilityConnectionProfile,
  type AgentCapabilityPlan,
  type AgentHubAsset,
  type AgentHubCatalog,
} from "@/appliance/agent-assets";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import {
  claimHubOperationCredentials,
  createHubControlPlan,
  createHubInstallPlan,
  createHubUninstallPlan,
  createHubUpdatePlan,
  fetchHubAppDetail,
  fetchHubCatalog,
  fetchHubOperations,
  queueHubInstall,
  queueHubControl,
  queueHubUninstall,
  queueHubUpdate,
  type HubApp,
  type HubAppDetailResponse,
  type HubAppRuntime,
  type HubCatalogResponse,
  type HubControlOperation,
  type HubControlPlan,
  type HubDiagnostics,
  type HubInstallBlocker,
  type HubInstallPlan,
  type HubOperation,
  type HubResourcePreflight,
  type HubUninstallPlan,
  type HubUpdatePlan,
} from "@/appliance/hub";
import { cn } from "@/lib/utils";
import { copyTextToClipboard } from "@/core/clipboard";

const CATEGORY_LABELS: Record<string, string> = {
  all: "全部",
  installed: "已安装",
  updates: "可更新",
  photos: "照片",
  media: "影音",
  sync: "同步",
  automation: "自动化",
  downloads: "下载",
  productivity: "效率",
  documents: "文档",
  ai: "AI",
  backup: "备份",
  system: "系统",
};

type AgentAssetFilter =
  | "all"
  | "installed"
  | "updates"
  | "workbench"
  | "plugin"
  | "connector"
  | "skill";

const AGENT_ASSET_FILTERS: AgentAssetFilter[] = [
  "all",
  "installed",
  "updates",
  "workbench",
  "plugin",
  "connector",
  "skill",
];

const AGENT_ASSET_LABELS: Record<AgentHubAsset["kind"], string> = {
  workbench: "工作台",
  plugin: "插件",
  connector: "连接器",
  skill: "技能",
};

const AGENT_PERMISSION_LABELS: Record<string, string> = {
  "account.credentials": "使用账户凭据",
  "content.read": "读取内容",
  "content.write": "修改内容",
  "interaction.user": "发起交互操作",
  "network.remote": "访问远程服务",
  "process.local": "运行本地进程",
};

const AGENT_AUTH_LABELS: Record<string, string> = {
  "connected-account": "已连接账户",
  mcp: "MCP 服务认证",
  oauth: "OAuth 授权",
  "oneid-token": "OneID 凭据",
  "server-side": "服务端代管认证",
  token: "访问令牌",
};

const ICONS = {
  photos: ImageIcon,
  media: FilmIcon,
  sync: RefreshCwIcon,
  home: HomeIcon,
  download: DownloadIcon,
  cloud: CloudIcon,
  documents: FileTextIcon,
  ai: BotIcon,
} as const;

const ICON_GRADIENTS: Record<string, string> = {
  photos: "from-rose-400 to-orange-400",
  media: "from-violet-500 to-indigo-600",
  sync: "from-cyan-400 to-blue-600",
  home: "from-amber-400 to-orange-500",
  download: "from-emerald-400 to-teal-600",
  cloud: "from-sky-400 to-blue-600",
  documents: "from-slate-500 to-slate-700",
  ai: "from-fuchsia-500 to-violet-700",
};

const BLOCKER_LABELS: Record<HubInstallBlocker, string> = {
  PACKAGE_NOT_PUBLISHED: "安全安装包正在接入",
  ARCHITECTURE_UNSUPPORTED: "当前设备暂不兼容",
  DOCKER_RUNTIME_UNAVAILABLE: "应用服务暂时离线",
  DOCKER_STORAGE_UNAVAILABLE: "暂时无法核对应用数据盘",
  DOCKER_STORAGE_INSUFFICIENT: "应用数据盘空间不足",
  PORT_IN_USE: "所需端口已被占用",
  ALREADY_INSTALLED: "已经安装到设备",
  NOT_INSTALLED: "应用尚未安装",
  INSTALLATION_AMBIGUOUS: "存在多个同名容器，请先检查 Docker",
  INSTALLATION_NOT_MANAGED: "当前容器不属于 Echo Hub",
  ALREADY_CURRENT: "已经是当前版本",
  ALREADY_RUNNING: "全部服务已经在运行",
  ALREADY_STOPPED: "全部服务已经停止",
  RUNTIME_STATE_UNAVAILABLE: "无法安全确认全部受管服务",
  UPGRADE_PATH_UNSUPPORTED: "需要逐个大版本升级",
  REQUIRED_PROVIDER_UNAVAILABLE: "局域网发现服务尚未就绪",
};

const INCIDENT_LABELS: Record<
  HubDiagnostics["incidents"][number]["code"],
  string
> = {
  OOM_KILLED: "内存达到上限，服务被系统终止",
  HEALTHCHECK_FAILED: "服务没有通过健康检查",
  RESTART_LOOP: "服务正在反复重启",
  CRASHED: "服务异常退出",
  SERVICE_STOPPED: "部分配套服务没有运行",
  STATE_UNAVAILABLE: "服务处于无法自动恢复的状态",
};

const OPERATION_FAILURE_LABELS: Record<string, string> = {
  CONTROL_DENIED: "系统拒绝了这次操作，请刷新后重新检查",
  STATE_CHANGED: "设备状态已经变化，请刷新并重新确认计划",
  RUNTIME_UNAVAILABLE: "应用服务暂时离线，恢复后可重新尝试",
  OPERATION_FAILED: "任务没有完成，请先刷新确认应用当前状态",
  RUNTIME_RESTARTED: "Echo 重启时任务尚未结束，请刷新确认后再决定是否重试",
  OPERATION_QUEUE_FULL: "后台任务较多，请稍后重新尝试",
};

const OPERATION_LABELS: Record<HubOperation["operation"], string> = {
  install: "安装",
  update: "更新",
  uninstall: "卸载",
  start: "启动",
  stop: "停止",
  restart: "安全重启",
};

function operationProgressLabel(operation: HubOperation) {
  const progress = operation.progress;
  const count =
    progress.completed !== null && progress.total !== null
      ? `${progress.completed}/${progress.total}`
      : null;
  const item =
    progress.item !== null && progress.items !== null && progress.items > 1
      ? ` · 镜像 ${progress.item}/${progress.items}`
      : "";
  if (progress.stage === "queued") return "等待执行";
  if (progress.stage === "validating") return "正在复核安全计划";
  if (progress.stage === "pulling") {
    if (progress.unit === "layers" && count) return `镜像层 ${count}${item}`;
    if (progress.unit === "images" && count)
      return `正在拉取镜像 ${count}${item}`;
    return `正在拉取镜像${item}`;
  }
  if (progress.stage === "preparing") return "正在创建隔离资源";
  if (progress.stage === "snapshotting")
    return count ? `正在保护数据卷 ${count}` : "正在保护数据卷";
  if (progress.stage === "stopping")
    return count ? `正在停止服务 ${count}` : "正在停止服务";
  if (progress.stage === "starting")
    return count ? `正在启动服务 ${count}` : "正在启动服务";
  if (progress.stage === "verifying")
    return count ? `正在检查健康状态 ${count}` : "正在检查健康状态";
  if (progress.stage === "switching") return "正在切换到新版本";
  if (progress.stage === "removing")
    return count ? `正在移除服务 ${count}` : "正在移除服务";
  if (progress.stage === "rolling-back") return "正在恢复原状态";
  if (progress.stage === "completed") return "已完成";
  return (
    OPERATION_FAILURE_LABELS[operation.error?.code ?? ""] ??
    "未完成，请刷新确认"
  );
}

type PendingOperation =
  | { operation: "install"; app: HubApp; plan: HubInstallPlan }
  | { operation: "update"; app: HubApp; plan: HubUpdatePlan }
  | { operation: "uninstall"; app: HubApp; plan: HubUninstallPlan }
  | {
      operation: HubControlOperation;
      app: HubApp;
      plan: HubControlPlan;
    };

type PendingAgentOperation = {
  operation: "install" | "authorize" | "uninstall" | "rollback";
  asset: AgentHubAsset;
  plan: AgentCapabilityPlan;
};

type RevealedCredentials = {
  appName: string;
  secrets: Record<string, string>;
};

function appStatus(app: HubApp) {
  if (app.installation.installed) {
    if (app.updateAvailable) {
      return { label: "可更新", className: "text-amber-700 bg-amber-50" };
    }
    return app.installation.state === "running"
      ? { label: "运行中", className: "text-emerald-700 bg-emerald-50" }
      : { label: "已安装", className: "text-slate-600 bg-slate-100" };
  }
  if (app.installable) {
    return { label: "可安装", className: "text-blue-700 bg-blue-50" };
  }
  if (app.integrationStatus === "integration-pending") {
    return { label: "接入中", className: "text-amber-700 bg-amber-50" };
  }
  return { label: "暂不可用", className: "text-slate-600 bg-slate-100" };
}

function usesHostLan(app: HubApp) {
  return app.bundle?.services.some((service) => service.networkMode === "host");
}

function AppCard({
  app,
  planning,
  onOpen,
  onStart,
  onStop,
  onRestart,
  onInstall,
  onUpdate,
  onUninstall,
  onDetails,
}: {
  app: HubApp;
  planning: boolean;
  onOpen?: (app: HubApp) => void;
  onStart: (app: HubApp) => void;
  onStop: (app: HubApp) => void;
  onRestart: (app: HubApp) => void;
  onInstall: (app: HubApp) => void;
  onUpdate: (app: HubApp) => void;
  onUninstall: (app: HubApp) => void;
  onDetails: (app: HubApp) => void;
}) {
  const Icon = ICONS[app.icon as keyof typeof ICONS] ?? ShoppingBagIcon;
  const status = appStatus(app);
  const installedVersion = app.installation.version;
  const versionLabel = app.installation.installed
    ? app.updateAvailable && installedVersion
      ? `v${installedVersion} → v${app.version}`
      : installedVersion
        ? `v${installedVersion}`
        : "版本待识别"
    : `v${app.version}`;
  const primaryBlocker = app.installBlockers[0];
  const pendingIntegration = app.integrationStatus === "integration-pending";
  const unavailableAction = pendingIntegration
    ? "接入中"
    : app.installBlockers.includes("DOCKER_RUNTIME_UNAVAILABLE")
      ? "服务离线"
      : "暂不可用";
  return (
    <article className="group flex min-h-[194px] flex-col rounded-[18px] bg-white/66 p-4 shadow-[0_1px_0_rgba(255,255,255,.9),0_10px_30px_rgba(51,65,85,.07)] transition hover:-translate-y-0.5 hover:bg-white/82 hover:shadow-[0_16px_36px_rgba(51,65,85,.12)]">
      <div className="flex items-start gap-3">
        <div
          className={cn(
            "grid size-12 shrink-0 place-items-center rounded-[14px] bg-gradient-to-br text-white shadow-[inset_0_1px_0_rgba(255,255,255,.4),0_7px_18px_rgba(51,65,85,.18)]",
            ICON_GRADIENTS[app.icon] ?? "from-blue-400 to-indigo-600",
          )}
        >
          <Icon className="size-6" strokeWidth={1.8} />
        </div>
        <div className="min-w-0 flex-1 pt-0.5">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-[15px] font-semibold text-slate-900">
              {app.nameZh}
            </h3>
            <span
              className={cn(
                "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium",
                status.className,
              )}
            >
              {status.label}
            </span>
          </div>
          <p className="mt-0.5 truncate text-[11px] text-slate-400">
            {app.name}
          </p>
        </div>
      </div>
      <p className="mt-3 line-clamp-2 text-[12px] leading-5 text-slate-600">
        {app.summary}
      </p>
      <div className="mt-auto flex items-end justify-between gap-3 pt-3">
        <div className="min-w-0">
          <div className="text-[10px] font-medium text-slate-400">
            {versionLabel} ·{" "}
            {usesHostLan(app)
              ? "局域网发现 · 无硬件直通"
              : (CATEGORY_LABELS[app.category] ?? app.category)}
          </div>
          {primaryBlocker && !app.installation.installed && (
            <p className="mt-0.5 truncate text-[10px] text-slate-500">
              {pendingIntegration && app.bundle
                ? "多容器合同已锁定"
                : BLOCKER_LABELS[primaryBlocker]}
            </p>
          )}
          {app.installation.installed && (
            <p className="mt-0.5 truncate text-[10px] text-slate-500">
              {app.installation.status || "已就绪"}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={() => onDetails(app)}
            aria-label={`查看 ${app.nameZh} 详情`}
            title="应用详情"
            className="grid size-8 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-blue-600"
          >
            <InfoIcon className="size-3.5" />
          </button>
          <a
            href={app.sourceUrl}
            target="_blank"
            rel="noreferrer"
            aria-label={`查看 ${app.nameZh} 项目主页`}
            className="grid size-8 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
          >
            <ExternalLinkIcon className="size-3.5" />
          </a>
          {app.installation.installed && app.updateAvailable && (
            <button
              type="button"
              disabled={planning}
              onClick={() => onUpdate(app)}
              className="inline-flex h-8 min-w-[58px] items-center justify-center gap-1.5 rounded-full bg-amber-500 px-3 text-[11px] font-semibold text-white shadow-sm transition hover:bg-amber-600 disabled:opacity-60"
            >
              {planning && <Loader2Icon className="size-3 animate-spin" />}
              {planning ? "检查中" : "更新"}
            </button>
          )}
          {app.installation.installed && (
            <button
              type="button"
              disabled={
                planning || (app.installation.state === "running" && !onOpen)
              }
              onClick={() =>
                app.installation.state === "running"
                  ? onOpen?.(app)
                  : onStart(app)
              }
              className="inline-flex h-8 min-w-[52px] items-center justify-center rounded-full bg-blue-600 px-3 text-[11px] font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:opacity-50"
            >
              {app.installation.state === "running" ? "打开" : "启动"}
            </button>
          )}
          {app.installation.installed &&
            app.installation.state === "running" && (
              <button
                type="button"
                disabled={planning}
                onClick={() => onRestart(app)}
                aria-label={`安全重启 ${app.nameZh}`}
                title="安全重启整组服务"
                className="grid size-8 place-items-center rounded-full text-slate-400 transition hover:bg-blue-50 hover:text-blue-700 disabled:opacity-40"
              >
                <RefreshCwIcon className="size-3.5" />
              </button>
            )}
          {app.installation.installed &&
            app.installation.state === "running" && (
              <button
                type="button"
                disabled={planning}
                onClick={() => onStop(app)}
                aria-label={`停止 ${app.nameZh}`}
                title="停止应用"
                className="grid size-8 place-items-center rounded-full text-slate-400 transition hover:bg-amber-50 hover:text-amber-700 disabled:opacity-40"
              >
                <PowerIcon className="size-3.5" />
              </button>
            )}
          <button
            type="button"
            disabled={
              planning ||
              (!app.installation.installed && !app.installable) ||
              app.installBlockers.includes("INSTALLATION_AMBIGUOUS")
            }
            onClick={() =>
              app.installation.installed ? onUninstall(app) : onInstall(app)
            }
            aria-label={
              app.installation.installed ? `卸载 ${app.nameZh}` : undefined
            }
            title={app.installation.installed ? "卸载并保留数据" : undefined}
            className={cn(
              "inline-flex h-8 items-center justify-center gap-1.5 rounded-full text-[11px] font-semibold transition",
              app.installation.installed
                ? "w-8 text-slate-400 hover:bg-rose-50 hover:text-rose-700 disabled:opacity-50"
                : app.installable
                  ? "min-w-[68px] bg-blue-600 px-3 text-white shadow-sm hover:bg-blue-700 disabled:opacity-60"
                  : "min-w-[68px] cursor-default bg-slate-100 px-3 text-slate-400",
            )}
          >
            {planning && <Loader2Icon className="size-3 animate-spin" />}
            {app.installation.installed ? (
              <Trash2Icon className="size-3.5" />
            ) : planning ? (
              "检查中"
            ) : app.installable ? (
              "安装"
            ) : (
              unavailableAction
            )}
          </button>
        </div>
      </div>
    </article>
  );
}

type AppStorageRow = {
  key: string;
  name: string;
  location: string;
  target: string;
  access: "只读" | "读写";
  snapshotOnUpdate: boolean;
};

function appArchitectures(app: HubApp): string[] {
  return app.package?.architectures ?? app.bundle?.architectures ?? [];
}

function architectureLabel(architecture: string) {
  if (architecture === "amd64") return "x86-64";
  if (architecture === "arm64") return "ARM64";
  return architecture;
}

function formatStorageBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  );
  const value = bytes / 1024 ** index;
  return `${value >= 100 || index === 0 || Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

function formatMemoryLimit(memoryMiB: number) {
  if (memoryMiB >= 1024) {
    const gib = memoryMiB / 1024;
    return `${Number.isInteger(gib) ? gib.toFixed(0) : gib.toFixed(1)} GB`;
  }
  return `${memoryMiB} MB`;
}

function runtimeStatus(runtime: HubAppRuntime) {
  const statuses = {
    healthy: {
      label: "运行健康",
      className: "bg-emerald-50 text-emerald-700",
      description: "全部受管服务运行正常",
    },
    degraded: {
      label: "需要处理",
      className: "bg-rose-50 text-rose-700",
      description: "至少一个受管服务状态异常",
    },
    starting: {
      label: "正在启动",
      className: "bg-amber-50 text-amber-700",
      description: "服务仍在启动或健康检查中",
    },
    stopped: {
      label: "已停止",
      className: "bg-slate-100 text-slate-600",
      description: "全部受管服务当前已停止",
    },
    "not-installed": {
      label: "未安装",
      className: "bg-slate-100 text-slate-600",
      description: "应用尚未安装",
    },
    unavailable: {
      label: "暂不可读",
      className: "bg-amber-50 text-amber-700",
      description: "无法安全确认容器归属或运行状态",
    },
  } as const;
  return statuses[runtime.status];
}

function runtimeServiceState(service: HubAppRuntime["services"][number]) {
  if (service.health === "unhealthy" || service.oomKilled) return "异常";
  if (service.health === "starting" || service.state === "restarting")
    return "启动中";
  if (service.state === "running") return "运行中";
  if (service.state === "exited") return "已停止";
  return "待确认";
}

function installResourceSummary(preflight: HubResourcePreflight) {
  const capacity = preflight.storage.nasCapacity;
  const capacityLabel =
    capacity.status === "observed" && capacity.freeBytes !== null
      ? `NAS 可用 ${formatStorageBytes(capacity.freeBytes)}`
      : preflight.storage.nasVolumes
        ? "NAS 容量当前不可读"
        : "不新增 NAS 写入目录";
  const imageStorage = preflight.storage.imageStorage;
  const dockerLabel =
    imageStorage.status === "sufficient" &&
    imageStorage.capacity.freeBytes !== null &&
    imageStorage.requiredFreeBytes !== null
      ? `Docker 可用 ${formatStorageBytes(imageStorage.capacity.freeBytes)}，预留 ${formatStorageBytes(imageStorage.requiredFreeBytes)}`
      : imageStorage.status === "insufficient"
        ? "Docker 数据盘空间不足"
        : "Docker 数据盘余量未核对";
  return `内存上限 ${formatMemoryLimit(preflight.runtime.memoryLimitMiB)} · ${preflight.network.ports.length} 个固定端口已核对 · ${dockerLabel} · ${capacityLabel}`;
}

function operationDescription(pending: PendingOperation | null) {
  if (!pending) return "";
  if (pending.operation === "start") {
    return "Echo 将按依赖顺序启动全部受管服务，并逐项确认运行与健康状态；失败时会停止本次新启动的服务，保留原状态和全部数据。";
  }
  if (pending.operation === "stop") {
    return "Echo 将按反向依赖顺序停止全部受管服务，不移除容器、配置卷或 NAS 数据；失败时会尝试恢复原来正在运行的服务。";
  }
  if (pending.operation === "restart") {
    return "Echo 将整组停止后按依赖顺序重新启动，并等待健康检查；若中途失败，会尝试恢复操作前的运行集合，全部数据保持不变。";
  }
  if (pending.operation === "uninstall") {
    return "Echo 只移除受管容器，应用配置卷和 NAS 文件都会保留。计划若发生变化，本次操作会自动停止。";
  }
  if (pending.operation === "update") {
    return "Echo 会先验证候选容器，再替换旧版本；应用配置卷、NAS 文件和运行状态都会保留，失败时恢复旧容器。";
  }
  const scope = usesHostLan(pending.app)
    ? "该应用会直接接入局域网以完成 mDNS/SSDP 设备发现；Echo 仍禁止提权、Docker socket、设备与 D-Bus 直通。当前不支持 USB、Bluetooth 或 Zigbee 直连。"
    : "Echo 将按刚刚校验的固定镜像、端口和存储范围安装。计划若发生变化，本次操作会自动停止。";
  const installPlan = pending.plan as HubInstallPlan;
  return `${scope} ${installResourceSummary(installPlan.resourcePreflight)}。下载量来自受信 OCI 清单，解压空间按固定保守规则预留；Docker 拉取仍会执行最终校验。`;
}

function operationTargetLabel(pending: PendingOperation | null) {
  if (!pending) return undefined;
  const base = `${pending.app.name} · ${pending.plan.changes.length} 项受控变更`;
  if (
    pending.operation === "start" ||
    pending.operation === "stop" ||
    pending.operation === "restart"
  ) {
    return `${base} · ${pending.plan.desired.serviceOrder.length} 个受管服务 · 保留数据`;
  }
  if (pending.operation === "uninstall") return `${base} · 保留数据`;
  if (pending.operation === "update") return `${base} · 保留数据 · 失败回滚`;
  const installPlan = pending.plan as HubInstallPlan;
  return `${base} · ${installResourceSummary(installPlan.resourcePreflight)}${
    usesHostLan(pending.app) ? " · 局域网发现模式" : ""
  }`;
}

function publicService(app: HubApp) {
  if (!app.bundle) return null;
  return (
    app.bundle.services.find(
      (service) => service.id === app.bundle?.publicService,
    ) ?? null
  );
}

function appPublicPorts(app: HubApp) {
  if (app.package) return app.package.ports;
  return publicService(app)?.ports ?? [];
}

function appStorageRows(app: HubApp): AppStorageRow[] {
  if (app.package) {
    return app.package.volumes.map((volume) => ({
      key: volume.name,
      name: volume.name,
      location: volume.source === "nas-root" ? "NAS 全盘" : "应用私有数据",
      target: volume.target,
      access: volume.readOnly ? "只读" : "读写",
      snapshotOnUpdate:
        volume.source === "app-data" && volume.readOnly === false,
    }));
  }
  if (!app.bundle) return [];
  return app.bundle.volumes.map((volume) => {
    const mounts = app.bundle?.services.flatMap((service) =>
      service.mounts
        .filter((mount) => mount.volume === volume.name)
        .map((mount) => ({ ...mount, service: service.id })),
    );
    const target = (mounts ?? [])
      .map((mount) => `${mount.service}:${mount.target}`)
      .join(" · ");
    return {
      key: volume.name,
      name: volume.name,
      location:
        volume.source === "nas-data"
          ? `NAS 数据${volume.relativePath ? `/${volume.relativePath}` : ""}`
          : "应用私有数据",
      target: target || "由应用内部管理",
      access: mounts?.some((mount) => !mount.readOnly) ? "读写" : "只读",
      snapshotOnUpdate: volume.snapshotOnUpdate,
    };
  });
}

function serviceSummary(app: HubApp) {
  if (!app.bundle) return "1 个独立应用容器";
  const labels = {
    app: "应用",
    database: "数据库",
    cache: "缓存",
    worker: "后台任务",
  } as const;
  const parts = (["app", "database", "cache", "worker"] as const)
    .map((role) => {
      const count = app.bundle?.services.filter(
        (service) => service.role === role,
      ).length;
      return count ? `${count} 个${labels[role]}` : null;
    })
    .filter(Boolean);
  return `${app.bundle.services.length} 个受管服务 · ${parts.join(" · ")}`;
}

function DetailSection({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon;
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="grid gap-3 border-t border-slate-200/75 py-5 sm:grid-cols-[132px_minmax(0,1fr)]">
      <h3 className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">
        <Icon className="size-3.5" />
        {title}
      </h3>
      <div className="min-w-0 text-[12px] leading-5 text-slate-600">
        {children}
      </div>
    </section>
  );
}

function AppDetailSheet({
  target,
  detail,
  loading,
  error,
  planning,
  onClose,
  onRetry,
  onStart,
  onStop,
  onRestart,
}: {
  target: HubApp | null;
  detail: HubAppDetailResponse | null;
  loading: boolean;
  error: string | null;
  planning: boolean;
  onClose: () => void;
  onRetry: () => void;
  onStart: (app: HubApp) => void;
  onStop: (app: HubApp) => void;
  onRestart: (app: HubApp) => void;
}) {
  if (!target) return null;
  const app = detail?.app ?? target;
  const preflight = detail?.resourcePreflight;
  const appRuntime = detail?.appRuntime;
  const diagnostics = detail?.diagnostics;
  const Icon = ICONS[app.icon as keyof typeof ICONS] ?? ShoppingBagIcon;
  const status = appStatus(app);
  const ports =
    preflight?.network.ports ??
    appPublicPorts(app).map((port) => ({
      ...port,
      status: "available" as const,
    }));
  const storage = appStorageRows(app);
  const hostLan = usesHostLan(app);
  const providers = app.bundle?.providers ?? [];
  const architectures = appArchitectures(app);
  const currentArchitecture = detail?.architecture;
  const compatible = currentArchitecture
    ? architectures.includes(currentArchitecture)
    : null;
  const imageCount = app.package ? 1 : (app.bundle?.services.length ?? 0);
  const publicAppService = publicService(app);
  const revealOnceSecrets =
    app.bundle?.secrets.filter((secret) => secret.revealOnce).length ?? 0;
  const snapshotCount =
    preflight?.storage.snapshotVolumes ??
    app.bundle?.volumes.filter((volume) => volume.snapshotOnUpdate).length ??
    0;

  return (
    <div
      data-desktop-interactive
      className="fixed inset-0 z-[128] flex justify-end bg-slate-950/24 p-2 backdrop-blur-[3px] sm:p-3"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={`${app.nameZh} 应用详情`}
        aria-busy={loading}
        className="flex h-full w-[min(570px,100%)] flex-col overflow-hidden rounded-[26px] border border-white/80 bg-white/96 text-slate-900 shadow-[0_32px_100px_rgba(15,23,42,.34)] backdrop-blur-3xl"
      >
        <header className="flex h-14 shrink-0 items-center border-b border-slate-200/70 px-5">
          <span className="text-[12px] font-semibold text-slate-500">
            应用详情
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭应用详情"
            className="ml-auto grid size-8 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
          >
            <XIcon className="size-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-7 pt-6 sm:px-7">
          <div className="flex items-start gap-4">
            <div
              className={cn(
                "grid size-16 shrink-0 place-items-center rounded-[18px] bg-gradient-to-br text-white shadow-[inset_0_1px_0_rgba(255,255,255,.42),0_10px_24px_rgba(51,65,85,.2)]",
                ICON_GRADIENTS[app.icon] ?? "from-blue-400 to-indigo-600",
              )}
            >
              <Icon className="size-8" strokeWidth={1.7} />
            </div>
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-xl font-semibold tracking-tight text-slate-950">
                  {app.nameZh}
                </h2>
                <span
                  className={cn(
                    "rounded-full px-2.5 py-1 text-[10px] font-semibold",
                    status.className,
                  )}
                >
                  {status.label}
                </span>
              </div>
              <p className="mt-0.5 text-[11px] text-slate-400">
                {app.name} · v{app.version}
              </p>
              <p className="mt-3 text-[13px] leading-5 text-slate-600">
                {app.summary}
              </p>
            </div>
          </div>

          {loading && !detail ? (
            <div className="grid min-h-56 place-items-center text-sm text-slate-400">
              <span className="flex items-center gap-2">
                <Loader2Icon className="size-4 animate-spin" />
                正在核对安装范围…
              </span>
            </div>
          ) : error ? (
            <div className="grid min-h-56 place-items-center text-center">
              <div>
                <UnplugIcon className="mx-auto size-7 text-slate-300" />
                <p className="mt-3 text-sm font-medium text-slate-700">
                  {error}
                </p>
                <button
                  type="button"
                  onClick={onRetry}
                  className="mt-4 rounded-full bg-slate-900 px-4 py-2 text-xs font-semibold text-white"
                >
                  重新读取
                </button>
              </div>
            </div>
          ) : detail ? (
            <div className="mt-7">
              {app.installation.installed && appRuntime && (
                <DetailSection icon={ActivityIcon} title="运行健康">
                  {(() => {
                    const health = runtimeStatus(appRuntime);
                    const summary = appRuntime.summary;
                    return (
                      <>
                        <div className="flex flex-wrap items-center gap-2">
                          <span
                            className={cn(
                              "rounded-full px-2.5 py-1 text-[10px] font-semibold",
                              health.className,
                            )}
                          >
                            {health.label}
                          </span>
                          <span className="font-medium text-slate-700">
                            {health.description}
                          </span>
                        </div>
                        {appRuntime.status === "unavailable" ? (
                          <p className="mt-2 text-slate-500">
                            Echo
                            已停止读取更深层信息；原始日志、环境变量、挂载路径和网络地址不会显示在商城中。
                          </p>
                        ) : (
                          <>
                            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                              <div className="rounded-xl bg-slate-50 px-3 py-2">
                                <p className="text-[10px] text-slate-400">
                                  服务
                                </p>
                                <p className="mt-0.5 font-semibold text-slate-800">
                                  {summary.runningServices}/
                                  {summary.serviceCount}
                                </p>
                              </div>
                              <div className="rounded-xl bg-slate-50 px-3 py-2">
                                <p className="text-[10px] text-slate-400">
                                  CPU
                                </p>
                                <p className="mt-0.5 font-semibold text-slate-800">
                                  {summary.cpuPercent === null
                                    ? "—"
                                    : `${summary.cpuPercent.toFixed(1)}%`}
                                </p>
                              </div>
                              <div className="rounded-xl bg-slate-50 px-3 py-2">
                                <p className="text-[10px] text-slate-400">
                                  内存
                                </p>
                                <p className="mt-0.5 font-semibold text-slate-800">
                                  {summary.memoryUsageBytes === null
                                    ? "—"
                                    : formatStorageBytes(
                                        summary.memoryUsageBytes,
                                      )}
                                </p>
                              </div>
                              <div className="rounded-xl bg-slate-50 px-3 py-2">
                                <p className="text-[10px] text-slate-400">
                                  进程
                                </p>
                                <p className="mt-0.5 font-semibold text-slate-800">
                                  {summary.pids ?? "—"}
                                </p>
                              </div>
                            </div>
                            {appRuntime.services.length > 1 && (
                              <ul className="mt-3 space-y-1.5">
                                {appRuntime.services.map((service) => (
                                  <li
                                    key={service.id}
                                    className="flex items-center justify-between gap-3 rounded-lg bg-slate-50/70 px-3 py-1.5"
                                  >
                                    <span className="truncate text-slate-600">
                                      {service.id}
                                      {service.public ? " · 对外服务" : ""}
                                    </span>
                                    <span
                                      className={cn(
                                        "shrink-0 text-[10px] font-semibold",
                                        runtimeServiceState(service) === "异常"
                                          ? "text-rose-600"
                                          : service.state === "running"
                                            ? "text-emerald-600"
                                            : "text-amber-600",
                                      )}
                                    >
                                      {runtimeServiceState(service)}
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            )}
                            {diagnostics &&
                              diagnostics.incidents.length > 0 && (
                                <ul className="mt-3 space-y-1.5">
                                  {diagnostics.incidents.map(
                                    (incident, index) => (
                                      <li
                                        key={`${incident.serviceId}-${incident.code}-${index}`}
                                        className={cn(
                                          "rounded-xl px-3 py-2",
                                          incident.severity === "warning"
                                            ? "bg-amber-50 text-amber-800"
                                            : "bg-rose-50 text-rose-800",
                                        )}
                                      >
                                        <p className="font-semibold">
                                          {INCIDENT_LABELS[incident.code]}
                                        </p>
                                        <p className="mt-0.5 text-[10px] opacity-75">
                                          {incident.serviceId} ·
                                          {incident.recovery === "restart"
                                            ? " 可执行安全重启"
                                            : " 需要先在 Docker 管理页确认状态"}
                                        </p>
                                      </li>
                                    ),
                                  )}
                                </ul>
                              )}
                            {summary.restartCount > 0 && (
                              <p className="mt-2 text-[10px] text-amber-700">
                                {`受管服务累计重启 ${summary.restartCount} 次；若持续增加，建议检查应用状态。`}
                              </p>
                            )}
                            <p className="mt-2 text-[10px] text-slate-400">
                              仅显示容器健康与聚合资源；不读取应用内容和原始日志。
                            </p>
                            <div className="mt-3 flex flex-wrap gap-2">
                              {appRuntime.status === "stopped" ? (
                                <button
                                  type="button"
                                  disabled={planning}
                                  onClick={() => onStart(app)}
                                  className="rounded-full bg-blue-600 px-3.5 py-2 text-[11px] font-semibold text-white transition hover:bg-blue-700 disabled:opacity-50"
                                >
                                  启动全部服务
                                </button>
                              ) : (
                                <>
                                  <button
                                    type="button"
                                    disabled={planning}
                                    onClick={() => onRestart(app)}
                                    className="rounded-full bg-blue-600 px-3.5 py-2 text-[11px] font-semibold text-white transition hover:bg-blue-700 disabled:opacity-50"
                                  >
                                    安全重启
                                  </button>
                                  <button
                                    type="button"
                                    disabled={planning}
                                    onClick={() => onStop(app)}
                                    className="rounded-full bg-slate-100 px-3.5 py-2 text-[11px] font-semibold text-slate-600 transition hover:bg-slate-200 disabled:opacity-50"
                                  >
                                    停止全部服务
                                  </button>
                                </>
                              )}
                            </div>
                          </>
                        )}
                      </>
                    );
                  })()}
                </DetailSection>
              )}
              <DetailSection icon={CpuIcon} title="设备兼容">
                <p className="font-medium text-slate-800">
                  {architectures.length
                    ? architectures.map(architectureLabel).join("、")
                    : "安装合同尚未发布"}
                </p>
                {currentArchitecture && (
                  <p className="mt-1 text-slate-500">
                    当前设备：{architectureLabel(currentArchitecture)} ·{" "}
                    {compatible ? "已兼容" : "暂不兼容"}
                  </p>
                )}
                <p className="mt-1 text-slate-500">{serviceSummary(app)}</p>
                {preflight && preflight.runtime.serviceCount > 0 && (
                  <>
                    <p className="mt-2 font-medium text-slate-800">
                      {`运行上限：${formatMemoryLimit(preflight.runtime.memoryLimitMiB)} 内存 · ${preflight.runtime.pidsLimit} 个进程`}
                    </p>
                    <p className="mt-0.5 text-[10px] text-slate-400">
                      这是系统强制的合计上限，不代表应用会持续占满。
                    </p>
                  </>
                )}
              </DetailSection>

              <DetailSection icon={NetworkIcon} title="端口与网络">
                <p className="font-medium text-slate-800">
                  {hostLan
                    ? "直接接入家庭局域网，用于 mDNS/SSDP 设备发现"
                    : providers.includes("lan-discovery")
                      ? "应用保持隔离，通过受控发现服务接入局域网发现"
                      : "使用隔离网络，只开放下列固定端口"}
                </p>
                {ports.length ? (
                  <ul className="mt-2 space-y-1">
                    {ports.map((port) => (
                      <li
                        key={`${port.host}-${port.container}-${port.protocol}`}
                      >
                        <span className="font-mono font-semibold text-slate-800">
                          {port.host}/{port.protocol.toUpperCase()}
                        </span>
                        {!hostLan && port.host !== port.container
                          ? ` → 应用内部 ${port.container}`
                          : " · 设备访问端口"}
                        <span
                          className={cn(
                            "ml-1.5 text-[10px] font-semibold",
                            port.status === "conflict"
                              ? "text-rose-600"
                              : "text-emerald-600",
                          )}
                        >
                          {port.status === "conflict"
                            ? "已被占用"
                            : port.status === "owned"
                              ? "当前应用使用中"
                              : "可用"}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-2 text-slate-500">不直接公开宿主端口</p>
                )}
                {app.bundle && (
                  <p className="mt-2 text-slate-500">
                    仅“{publicAppService?.id ?? app.bundle.publicService}
                    ”服务可对外提供网页，其余数据库、缓存和后台服务不公开端口。
                  </p>
                )}
              </DetailSection>

              <DetailSection icon={HardDriveIcon} title="存储权限">
                {storage.length ? (
                  <ul className="divide-y divide-slate-200/70">
                    {storage.map((volume) => (
                      <li
                        key={volume.key}
                        className="py-2.5 first:pt-0 last:pb-0"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="font-medium text-slate-800">
                            {volume.location}
                          </span>
                          <span
                            className={cn(
                              "text-[10px] font-semibold",
                              volume.access === "只读"
                                ? "text-emerald-700"
                                : "text-blue-700",
                            )}
                          >
                            {volume.access}
                          </span>
                        </div>
                        <p className="mt-0.5 break-all font-mono text-[10px] text-slate-400">
                          {volume.name} · {volume.target}
                        </p>
                        {volume.snapshotOnUpdate && (
                          <p className="mt-0.5 text-[10px] text-slate-500">
                            更新前创建回退快照
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-slate-500">没有请求持久存储目录</p>
                )}
                {preflight?.storage.nasCapacity.status === "observed" &&
                preflight.storage.nasCapacity.freeBytes !== null &&
                preflight.storage.nasCapacity.totalBytes !== null ? (
                  <p className="mt-3 font-medium text-slate-800">
                    {`NAS 当前可用 ${formatStorageBytes(
                      preflight.storage.nasCapacity.freeBytes,
                    )}，总容量 ${formatStorageBytes(
                      preflight.storage.nasCapacity.totalBytes,
                    )}`}
                  </p>
                ) : preflight && preflight.storage.nasVolumes > 0 ? (
                  <p className="mt-3 font-medium text-amber-700">
                    当前无法读取 NAS 剩余容量，未用估算值替代。
                  </p>
                ) : null}
                {preflight && (
                  <div className="mt-3 border-t border-slate-200/70 pt-3">
                    {preflight.storage.imageStorage.status === "sufficient" &&
                    preflight.storage.imageStorage.capacity.freeBytes !==
                      null &&
                    preflight.storage.imageStorage.requiredFreeBytes !==
                      null ? (
                      <p className="font-medium text-emerald-700">
                        {`Docker 数据盘可用 ${formatStorageBytes(
                          preflight.storage.imageStorage.capacity.freeBytes,
                        )}，本次保守预留 ${formatStorageBytes(
                          preflight.storage.imageStorage.requiredFreeBytes,
                        )}`}
                      </p>
                    ) : preflight.storage.imageStorage.status ===
                      "insufficient" ? (
                      <p className="font-medium text-rose-700">
                        Docker 数据盘余量不足，安装已停止。
                      </p>
                    ) : preflight.storage.imageStorage.status === "mismatch" ? (
                      <p className="font-medium text-rose-700">
                        Docker 数据根与只读观察挂载不一致，安装已停止。
                      </p>
                    ) : (
                      <p className="font-medium text-amber-700">
                        当前无法核对 Docker 数据盘余量，安装已停止。
                      </p>
                    )}
                    {preflight.storage.imageStorage.downloadBytes !== null && (
                      <p className="mt-1 text-[10px] text-slate-400">
                        {`受信 OCI 清单下载量 ${formatStorageBytes(
                          preflight.storage.imageStorage.downloadBytes,
                        )} · ${preflight.storage.imageStorage.blobCount} 个去重分层。解压与元数据空间按“下载量 3 倍或额外 512 MB，取较大值”预留；Docker 拉取仍执行最终校验。`}
                      </p>
                    )}
                  </div>
                )}
              </DetailSection>

              <DetailSection icon={LockKeyholeIcon} title="安全边界">
                <ul className="space-y-1.5">
                  <li>
                    {imageCount
                      ? `固定摘要镜像 ${imageCount} 个，版本内容不可静默漂移`
                      : "固定摘要镜像尚未发布，当前不会开放安装"}
                  </li>
                  <li>移除全部容器 capability，并禁止进程再次提权</li>
                  <li>不挂载 Docker 管理接口、宿主设备或 D-Bus</li>
                  {hostLan && (
                    <li>当前不开放 USB、Bluetooth 或 Zigbee 硬件直通</li>
                  )}
                  {revealOnceSecrets > 0 && (
                    <li>
                      {revealOnceSecrets} 项初始凭据仅在安装完成时显示一次
                    </li>
                  )}
                </ul>
              </DetailSection>

              <DetailSection icon={RefreshCwIcon} title="数据与更新">
                <p className="font-medium text-slate-800">
                  卸载只移除受管容器，应用配置和 NAS 文件继续保留。
                </p>
                <p className="mt-1 text-slate-500">
                  {snapshotCount > 0
                    ? `更新前会快照 ${snapshotCount} 个配置卷；候选版本验证失败时恢复旧容器。`
                    : "更新复用原数据卷；候选版本验证失败时继续保留旧容器。"}
                </p>
                {app.updateAvailable && app.installation.version && (
                  <p className="mt-2 font-medium text-amber-700">
                    当前 v{app.installation.version}，可更新至 v{app.version}
                  </p>
                )}
              </DetailSection>

              <DetailSection icon={ServerIcon} title="Echo 验证">
                <p>{app.integrationNote}</p>
                <a
                  href={app.sourceUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-3 inline-flex items-center gap-1.5 font-semibold text-blue-600 hover:text-blue-700"
                >
                  查看开源项目主页
                  <ExternalLinkIcon className="size-3.5" />
                </a>
              </DetailSection>
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}

function AgentAssetCard({
  asset,
  onManage,
  onDetails,
}: {
  asset: AgentHubAsset;
  onManage?: (asset: AgentHubAsset) => void;
  onDetails: (asset: AgentHubAsset) => void;
}) {
  const Icon =
    asset.kind === "workbench"
      ? PanelsTopLeftIcon
      : asset.kind === "connector"
        ? NetworkIcon
        : asset.kind === "plugin"
          ? PuzzleIcon
          : SparklesIcon;
  const stateBadge = asset.permissionReviewRequired
    ? { label: "待确认权限", tone: "bg-amber-50 text-amber-700" }
    : asset.lifecycleState === "update_available"
      ? { label: "可更新", tone: "bg-blue-50 text-blue-700" }
      : asset.lifecycleState === "broken"
        ? { label: "需处理", tone: "bg-rose-50 text-rose-700" }
        : asset.lifecycleState === "disabled"
          ? { label: "已停用", tone: "bg-amber-50 text-amber-700" }
          : asset.installed
            ? { label: "已启用", tone: "bg-emerald-50 text-emerald-700" }
            : null;
  const trustBadge =
    asset.trustLevel === "system"
      ? { label: "系统内置", tone: "bg-blue-50 text-blue-700" }
      : asset.trustLevel === "publisher"
        ? { label: "发布者已验证", tone: "bg-emerald-50 text-emerald-700" }
        : asset.trustLevel === "local_integrity"
          ? { label: "完整性已验证", tone: "bg-cyan-50 text-cyan-700" }
          : asset.trustLevel === "unverified"
            ? { label: "来源未验证", tone: "bg-amber-50 text-amber-700" }
            : { label: "Agent 目录", tone: "bg-slate-100 text-slate-500" };
  const compatibilityBadge =
    asset.compatibility === "compatible"
      ? { label: "当前兼容", tone: "bg-emerald-50 text-emerald-700" }
      : asset.compatibility === "incompatible"
        ? { label: "当前不兼容", tone: "bg-rose-50 text-rose-700" }
        : { label: "安装时校验", tone: "bg-slate-100 text-slate-500" };
  const dependencyCount =
    asset.dependencies.length +
    asset.runtimeDependencies.length +
    asset.connectors.length;
  return (
    <article className="group flex min-h-[212px] flex-col rounded-[18px] bg-white/66 p-4 shadow-[0_1px_0_rgba(255,255,255,.9),0_10px_30px_rgba(51,65,85,.07)] transition hover:-translate-y-0.5 hover:bg-white/82 hover:shadow-[0_16px_36px_rgba(51,65,85,.12)]">
      <div className="flex items-start gap-3">
        <div
          className={cn(
            "grid size-12 shrink-0 place-items-center rounded-[14px] bg-gradient-to-br text-white shadow-[inset_0_1px_0_rgba(255,255,255,.4),0_7px_18px_rgba(51,65,85,.18)]",
            asset.kind === "workbench"
              ? "from-fuchsia-500 to-violet-700"
              : asset.kind === "connector"
                ? "from-emerald-400 to-teal-700"
                : asset.kind === "plugin"
                  ? "from-indigo-500 to-violet-700"
                  : "from-cyan-400 to-blue-600",
          )}
        >
          <Icon className="size-6" strokeWidth={1.8} />
        </div>
        <div className="min-w-0 flex-1 pt-0.5">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-[15px] font-semibold text-slate-900">
              {asset.name}
            </h3>
            {stateBadge && (
              <span
                className={cn(
                  "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium",
                  stateBadge.tone,
                )}
              >
                {stateBadge.label}
              </span>
            )}
          </div>
          <p className="mt-0.5 truncate text-[11px] text-slate-400">
            {AGENT_ASSET_LABELS[asset.kind]} · {asset.author || asset.source}
          </p>
        </div>
      </div>
      <p className="mt-3 line-clamp-2 text-[12px] leading-5 text-slate-600">
        {asset.description}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] font-medium">
        <span
          title={
            asset.publisherVerified && asset.verifiedPublisher
              ? `由 ${asset.verifiedPublisher} 的受信密钥验证`
              : asset.trustLevel === "unverified"
                ? "Agent 尚未提供可验证的内容签名"
                : undefined
          }
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-2 py-1",
            trustBadge.tone,
          )}
        >
          <ShieldCheckIcon className="size-3" />
          {trustBadge.label}
        </span>
        {asset.kind !== "skill" && (
          <span
            title={asset.hostApi ? `需要 Agent ${asset.hostApi}` : undefined}
            className={cn("rounded-full px-2 py-1", compatibilityBadge.tone)}
          >
            {compatibilityBadge.label}
          </span>
        )}
        {asset.permissions.length > 0 && (
          <span
            title={asset.permissions
              .map(
                (permission) =>
                  AGENT_PERMISSION_LABELS[permission] || permission,
              )
              .join("、")}
            className="rounded-full bg-violet-50 px-2 py-1 text-violet-700"
          >
            {asset.permissions.length} 项权限
          </span>
        )}
        {asset.authModes.length > 0 && (
          <span
            title={`认证方式：${asset.authModes.join("、")}`}
            className="rounded-full bg-amber-50 px-2 py-1 text-amber-700"
          >
            需认证
          </span>
        )}
        {dependencyCount > 0 && (
          <span
            title={[
              ...asset.dependencies,
              ...asset.runtimeDependencies,
              ...asset.connectors,
            ].join("、")}
            className="rounded-full bg-cyan-50 px-2 py-1 text-cyan-700"
          >
            {dependencyCount} 个依赖
          </span>
        )}
      </div>
      {asset.releaseSummary && (
        <p
          title={asset.releaseSummary}
          className="mt-2 line-clamp-1 text-[10px] leading-4 text-slate-400"
        >
          <span className="font-medium text-slate-500">
            {asset.publisherVerified ? "版本说明（已验证）" : "目录版本说明"}
          </span>{" "}
          · {asset.releaseSummary}
        </p>
      )}
      <div className="mt-auto flex items-center justify-between gap-3 pt-3">
        <div className="flex min-w-0 items-center gap-1.5 text-[10px] text-slate-400">
          <span className="truncate">
            {asset.version
              ? asset.lifecycleState === "update_available" &&
                asset.availableVersion
                ? `v${asset.version} → v${asset.availableVersion}`
                : `v${asset.version}`
              : "由 Agent 运行时管理"}
          </span>
          {asset.rollbackAvailable && (
            <span className="shrink-0 rounded-full bg-slate-100 px-1.5 py-0.5">
              可回滚
            </span>
          )}
          {asset.recoveryCount > 0 && (
            <span className="shrink-0 rounded-full bg-slate-100 px-1.5 py-0.5">
              {asset.recoveryCount} 个恢复点
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={() => onDetails(asset)}
            aria-label={`查看“${asset.name}”详情`}
            className="h-8 rounded-full bg-white/80 px-3 text-[11px] font-semibold text-slate-600 ring-1 ring-inset ring-slate-200 transition hover:bg-white"
          >
            详情
          </button>
          <button
            type="button"
            onClick={() => onManage?.(asset)}
            disabled={!onManage}
            aria-label={`在 Agent 中管理“${asset.name}”`}
            className="h-8 rounded-full bg-slate-100 px-3 text-[11px] font-semibold text-slate-600 transition hover:bg-slate-200 disabled:cursor-default disabled:opacity-50"
          >
            Agent 中管理
          </button>
        </div>
      </div>
    </article>
  );
}

function AgentAssetDetailSheet({
  target,
  onClose,
  onManage,
  busy,
  onInstall,
  onAuthorize,
  onDisable,
  onConnect,
  onRollback,
  onUninstall,
}: {
  target: AgentHubAsset | null;
  onClose: () => void;
  onManage?: (asset: AgentHubAsset) => void;
  busy: boolean;
  onInstall: (asset: AgentHubAsset) => void;
  onAuthorize: (asset: AgentHubAsset) => void;
  onDisable: (asset: AgentHubAsset) => void;
  onConnect: (asset: AgentHubAsset) => void;
  onRollback: (asset: AgentHubAsset) => void;
  onUninstall: (asset: AgentHubAsset) => void;
}) {
  if (!target) return null;
  const trustLabel =
    target.trustLevel === "system"
      ? "随系统交付"
      : target.publisherVerified
        ? `发布者签名已验证${target.verifiedPublisher ? ` · ${target.verifiedPublisher}` : ""}`
        : target.integrityVerified
          ? "已校验本地内容完整性"
          : target.installed
            ? "已安装内容的来源尚未验证"
            : "目录声明，安装时会再验签";
  const compatibilityLabel =
    target.compatibility === "compatible"
      ? "与当前 Agent 兼容"
      : target.compatibility === "incompatible"
        ? "与当前 Agent 不兼容"
        : "安装前校验主机版本";
  const dependencyGroups = [
    { label: "包依赖", values: target.dependencies },
    { label: "随包运行依赖", values: target.runtimeDependencies },
    { label: "外部连接器", values: target.connectors },
  ].filter((group) => group.values.length > 0);
  return (
    <div
      data-desktop-interactive
      className="fixed inset-0 z-[145] flex justify-end bg-slate-950/28 p-2 backdrop-blur-[4px] sm:p-3"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={`${target.name} 详情`}
        className="flex h-full w-full max-w-[520px] flex-col overflow-hidden rounded-[24px] border border-white/75 bg-white/96 text-slate-900 shadow-[0_28px_90px_rgba(15,23,42,.34)]"
      >
        <header className="flex items-start gap-4 border-b border-slate-200/70 px-6 py-5">
          <div className="grid size-12 shrink-0 place-items-center rounded-[15px] bg-gradient-to-br from-indigo-500 to-violet-700 text-white shadow-[0_8px_22px_rgba(79,70,229,.24)]">
            {target.kind === "connector" ? (
              <NetworkIcon className="size-6" />
            ) : target.kind === "workbench" ? (
              <PanelsTopLeftIcon className="size-6" />
            ) : target.kind === "skill" ? (
              <SparklesIcon className="size-6" />
            ) : (
              <PuzzleIcon className="size-6" />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-medium text-slate-400">
              {AGENT_ASSET_LABELS[target.kind]} ·{" "}
              {target.author || target.source}
            </p>
            <h2 className="mt-1 truncate text-[20px] font-semibold tracking-[-0.02em]">
              {target.name}
            </h2>
            <p className="mt-1 text-[12px] leading-5 text-slate-500">
              {target.description}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭 Agent 能力详情"
            className="grid size-9 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200"
          >
            <XIcon className="size-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-6">
          <DetailSection icon={ShieldCheckIcon} title="安全与兼容">
            <div className="space-y-2">
              <p className="font-medium text-slate-700">{trustLabel}</p>
              <p>{compatibilityLabel}</p>
              {target.hostApi && (
                <p className="font-mono text-[11px] text-slate-400">
                  Agent {target.hostApi}
                </p>
              )}
            </div>
          </DetailSection>

          <DetailSection icon={LockKeyholeIcon} title="需要的权限">
            {target.permissionReviewRequired ? (
              <p className="mb-3 rounded-xl bg-amber-50 px-3 py-2 font-medium text-amber-800">
                已安装但尚未授权；Agent 运行时会继续阻止它执行。
              </p>
            ) : target.permissionActive ? (
              <p className="mb-3 rounded-xl bg-emerald-50 px-3 py-2 font-medium text-emerald-800">
                当前签名版本的全部权限已确认并生效。
              </p>
            ) : null}
            {target.permissions.length ? (
              <ul className="grid gap-2 sm:grid-cols-2">
                {target.permissions.map((permission) => {
                  const granted =
                    target.permissionsGranted.includes(permission);
                  return (
                    <li
                      key={permission}
                      className={cn(
                        "flex items-center gap-2 rounded-xl px-3 py-2",
                        granted
                          ? "bg-emerald-50/80 text-emerald-800"
                          : "bg-violet-50/75 text-violet-800",
                      )}
                    >
                      <CheckCircle2Icon className="size-3.5 shrink-0" />
                      <span className="flex-1">
                        {AGENT_PERMISSION_LABELS[permission] || permission}
                      </span>
                      <span className="text-[10px] opacity-70">
                        {granted ? "已授权" : "待确认"}
                      </span>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p>目录未声明额外高权限能力。</p>
            )}
          </DetailSection>

          <DetailSection icon={BotIcon} title="认证与依赖">
            <div className="space-y-3">
              <div>
                <p className="font-medium text-slate-700">认证方式</p>
                <p className="mt-1">
                  {target.authModes.length
                    ? target.authModes
                        .map((mode) => AGENT_AUTH_LABELS[mode] || mode)
                        .join("、")
                    : "无额外账户认证声明"}
                </p>
              </div>
              {dependencyGroups.length ? (
                dependencyGroups.map((group) => (
                  <div key={group.label}>
                    <p className="font-medium text-slate-700">{group.label}</p>
                    <p className="mt-1 break-words text-slate-500">
                      {group.values.join("、")}
                    </p>
                  </div>
                ))
              ) : (
                <p>没有额外依赖声明。</p>
              )}
            </div>
          </DetailSection>

          <DetailSection icon={FileTextIcon} title="版本与恢复">
            <div className="space-y-2">
              <p>
                {target.version
                  ? `当前 v${target.version}`
                  : "当前版本由 Agent 管理"}
                {target.availableVersion &&
                target.availableVersion !== target.version
                  ? ` · 可更新至 v${target.availableVersion}`
                  : ""}
              </p>
              {target.releaseSummary && (
                <p className="rounded-xl bg-slate-50 px-3 py-2 text-slate-600">
                  {target.publisherVerified ? "已验证版本说明" : "目录版本说明"}
                  ：{target.releaseSummary}
                </p>
              )}
              <p className="text-slate-400">
                {target.rollbackAvailable
                  ? "已保留可回滚的上一代。"
                  : "当前没有可用回滚代际。"}
              </p>
            </div>
          </DetailSection>
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200/70 bg-slate-50/80 px-6 py-4">
          <p className="max-w-[220px] text-[10px] leading-4 text-slate-400">
            {target.kind === "plugin" || target.kind === "connector"
              ? "设备安装由管理员确认；权限和账户仍按当前用户隔离。"
              : "这类能力暂时仍由 Agent 自有管理面负责。"}
          </p>
          <div className="flex flex-wrap justify-end gap-2">
            {target.kind === "plugin" || target.kind === "connector" ? (
              <>
                {!target.installed ||
                target.lifecycleState === "update_available" ? (
                  <button
                    type="button"
                    onClick={() => onInstall(target)}
                    disabled={busy || target.compatibility === "incompatible"}
                    className="h-9 rounded-full bg-blue-600 px-4 text-[11px] font-semibold text-white transition hover:bg-blue-700 disabled:opacity-40"
                  >
                    {target.lifecycleState === "update_available"
                      ? "安全更新"
                      : "安装到设备"}
                  </button>
                ) : target.permissionReviewRequired || !target.enabled ? (
                  <button
                    type="button"
                    onClick={() => onAuthorize(target)}
                    disabled={busy}
                    className="h-9 rounded-full bg-blue-600 px-4 text-[11px] font-semibold text-white transition hover:bg-blue-700 disabled:opacity-40"
                  >
                    确认权限并启用
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => onDisable(target)}
                    disabled={busy}
                    className="h-9 rounded-full bg-slate-200 px-4 text-[11px] font-semibold text-slate-700 transition hover:bg-slate-300 disabled:opacity-40"
                  >
                    停用当前用户
                  </button>
                )}
                {target.rollbackAvailable && (
                  <button
                    type="button"
                    onClick={() => onRollback(target)}
                    disabled={busy}
                    className="h-9 rounded-full bg-amber-50 px-3 text-[11px] font-semibold text-amber-700 transition hover:bg-amber-100 disabled:opacity-40"
                  >
                    回滚
                  </button>
                )}
                {target.installed && (
                  <button
                    type="button"
                    onClick={() => onUninstall(target)}
                    disabled={busy}
                    className="h-9 rounded-full bg-rose-50 px-3 text-[11px] font-semibold text-rose-700 transition hover:bg-rose-100 disabled:opacity-40"
                  >
                    卸载
                  </button>
                )}
                {target.authModes.length > 0 && target.enabled && (
                  <button
                    type="button"
                    onClick={() => onConnect(target)}
                    disabled={busy}
                    className="h-9 rounded-full bg-slate-900 px-4 text-[11px] font-semibold text-white transition hover:bg-slate-800 disabled:opacity-40"
                  >
                    连接账户
                  </button>
                )}
              </>
            ) : (
              <button
                type="button"
                onClick={() => onManage?.(target)}
                disabled={!onManage || busy}
                className="h-9 rounded-full bg-slate-900 px-4 text-[11px] font-semibold text-white transition hover:bg-slate-800 disabled:opacity-40"
              >
                前往 Agent 管理
              </button>
            )}
          </div>
        </footer>
      </section>
    </div>
  );
}

function AgentConnectionDialog({
  target,
  onClose,
  onChanged,
  onManage,
}: {
  target: AgentHubAsset | null;
  onClose: () => void;
  onChanged: () => void;
  onManage?: (asset: AgentHubAsset) => void;
}) {
  const [profile, setProfile] =
    useState<AgentCapabilityConnectionProfile | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    setProfile(null);
    setValues({});
    setError(null);
    if (!target) {
      setLoading(false);
      return () => {
        current = false;
      };
    }
    setLoading(true);
    fetchAgentCapabilityConnectionProfile(target.installId)
      .then((result) => {
        if (current) setProfile(result);
      })
      .catch((reason) => {
        if (current) {
          setError(
            reason instanceof Error ? reason.message : "无法读取账户连接说明",
          );
        }
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [target]);

  if (!target) return null;

  const submit = async () => {
    if (!profile || busy) return;
    const tokens = Object.fromEntries(
      Object.entries(values)
        .map(([key, value]) => [key, value.trim()] as const)
        .filter(([, value]) => value.length > 0),
    );
    const missingRequired = profile.fields.some(
      (field) => field.required && !tokens[field.key],
    );
    if (
      missingRequired ||
      Object.keys(tokens).length < profile.minimumCredentials
    ) {
      setError("请至少填写一项有效凭据");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await connectAgentCapability(target.installId, tokens);
      setValues({});
      toast.success(`${target.name} 已连接到当前用户`);
      onChanged();
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "账户连接没有完成");
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    if (!profile || busy) return;
    setBusy(true);
    setError(null);
    try {
      await disconnectAgentCapability(target.installId);
      setValues({});
      toast.success(`${target.name} 已断开当前用户账户`);
      onChanged();
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "账户断开没有完成");
    } finally {
      setBusy(false);
    }
  };

  const isolationBlocked = profile?.blockers.includes(
    "principal_isolation_unavailable",
  );

  return (
    <div
      data-desktop-interactive
      className="fixed inset-0 z-[155] grid place-items-center bg-slate-950/38 p-5 backdrop-blur-[7px]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={`${target.name} 账户连接`}
        className="w-full max-w-[440px] overflow-hidden rounded-[24px] border border-white/75 bg-white/96 text-slate-900 shadow-[0_28px_90px_rgba(15,23,42,.36)]"
      >
        <header className="flex items-start gap-3 border-b border-slate-200/70 px-6 py-5">
          <div className="grid size-10 shrink-0 place-items-center rounded-2xl bg-slate-900 text-white">
            <LockKeyholeIcon className="size-4.5" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-[17px] font-semibold">连接 {target.name}</h2>
            <p className="mt-1 text-[11px] leading-4 text-slate-500">
              凭据由 Agent 加密保存，并绑定当前 Echo 用户；Hub 不读取其数据库。
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="关闭账户连接"
            className="grid size-8 place-items-center rounded-full bg-slate-100 text-slate-500 disabled:opacity-40"
          >
            <XIcon className="size-4" />
          </button>
        </header>

        <div className="space-y-4 px-6 py-5">
          {loading && (
            <div className="flex items-center gap-2 text-[12px] text-slate-500">
              <Loader2Icon className="size-4 animate-spin" />
              正在读取安全连接说明…
            </div>
          )}

          {profile?.connected && (
            <div className="rounded-2xl bg-emerald-50 px-4 py-3 text-[12px] text-emerald-800">
              当前用户已连接；Echo 不会显示或回传已保存的密钥。
            </div>
          )}

          {profile?.mode === "principal_credentials" && !profile.connected && (
            <div className="space-y-3">
              {profile.fields.map((field) => (
                <label key={field.key} className="block">
                  <span className="mb-1.5 block text-[11px] font-medium text-slate-600">
                    {field.labelZh}
                    {field.required ? " · 必填" : ""}
                  </span>
                  <input
                    type="password"
                    autoComplete="off"
                    value={values[field.key] || ""}
                    onChange={(event) =>
                      setValues((current) => ({
                        ...current,
                        [field.key]: event.target.value,
                      }))
                    }
                    placeholder={`粘贴 ${field.label}`}
                    className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-[12px] outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
                  />
                </label>
              ))}
              {profile.minimumCredentials > 0 &&
                !profile.fields.some((field) => field.required) && (
                  <p className="text-[10px] leading-4 text-slate-400">
                    按服务商提供的凭据类型填写其中一项即可。
                  </p>
                )}
            </div>
          )}

          {profile?.mode === "no_credentials" && !profile.connected && (
            <div className="rounded-2xl bg-blue-50 px-4 py-3 text-[12px] text-blue-800">
              这项能力不需要账户密钥，确认后即可为当前用户建立连接。
            </div>
          )}

          {profile?.mode === "agent_managed" && (
            <div className="rounded-2xl bg-amber-50 px-4 py-3 text-[12px] leading-5 text-amber-900">
              {isolationBlocked
                ? "这项能力使用网页 OAuth、CLI 或模型供应商登录，当前仍是 Agent 进程级会话。为避免家庭成员串号，Echo 暂不直接接管。"
                : "这项账户连接暂时由 Agent 管理。"}
            </div>
          )}

          {error && (
            <p
              role="alert"
              className="rounded-xl bg-rose-50 px-3 py-2 text-[11px] text-rose-700"
            >
              {error}
            </p>
          )}
        </div>

        <footer className="flex justify-end gap-2 border-t border-slate-200/70 bg-slate-50/80 px-6 py-4">
          {profile?.mode === "agent_managed" ? (
            <button
              type="button"
              onClick={() => onManage?.(target)}
              disabled={!onManage}
              className="h-9 rounded-full bg-slate-900 px-4 text-[11px] font-semibold text-white disabled:opacity-40"
            >
              前往 Agent 安全登录
            </button>
          ) : profile?.connected ? (
            <button
              type="button"
              onClick={() => void disconnect()}
              disabled={busy}
              className="h-9 rounded-full bg-rose-50 px-4 text-[11px] font-semibold text-rose-700 disabled:opacity-40"
            >
              {busy ? "正在断开…" : "断开当前用户"}
            </button>
          ) : profile?.canConnect ? (
            <button
              type="button"
              onClick={() => void submit()}
              disabled={busy}
              className="h-9 rounded-full bg-blue-600 px-4 text-[11px] font-semibold text-white disabled:opacity-40"
            >
              {busy ? "正在验证…" : "保存并连接"}
            </button>
          ) : null}
        </footer>
      </section>
    </div>
  );
}

export function HubPanel({
  open,
  canManageDevice = true,
  onClose,
  onAppsChanged,
  onOpenDeviceApp,
  onOpenAgentAssets,
}: {
  open: boolean;
  canManageDevice?: boolean;
  onClose: () => void;
  onAppsChanged?: () => void;
  onOpenDeviceApp?: (app: HubApp) => void;
  onOpenAgentAssets?: (asset: AgentHubAsset) => void;
}) {
  const [view, setView] = useState<"device" | "agent">("device");
  const [catalog, setCatalog] = useState<HubCatalogResponse | null>(null);
  const [agentCatalog, setAgentCatalog] = useState<AgentHubCatalog | null>(
    null,
  );
  const [loading, setLoading] = useState(false);
  const [agentLoading, setAgentLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [agentKind, setAgentKind] = useState<AgentAssetFilter>("all");
  const [planningAppId, setPlanningAppId] = useState<string | null>(null);
  const [pendingPlan, setPendingPlan] = useState<PendingOperation | null>(null);
  const [pendingAgentPlan, setPendingAgentPlan] =
    useState<PendingAgentOperation | null>(null);
  const [agentActionId, setAgentActionId] = useState<string | null>(null);
  const [operations, setOperations] = useState<HubOperation[]>([]);
  const [revealedCredentials, setRevealedCredentials] =
    useState<RevealedCredentials | null>(null);
  const [detailTarget, setDetailTarget] = useState<HubApp | null>(null);
  const [agentDetailTarget, setAgentDetailTarget] =
    useState<AgentHubAsset | null>(null);
  const [agentConnectionTarget, setAgentConnectionTarget] =
    useState<AgentHubAsset | null>(null);
  const [appDetail, setAppDetail] = useState<HubAppDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const detailRequest = useRef(0);
  const catalogRef = useRef<HubCatalogResponse | null>(null);
  const onAppsChangedRef = useRef(onAppsChanged);
  const operationStatuses = useRef(new Map<string, HubOperation["status"]>());
  const operationsLoaded = useRef(false);
  const operationsLoading = useRef(false);
  const claimedCredentialOperations = useRef(new Set<string>());
  catalogRef.current = catalog;
  onAppsChangedRef.current = onAppsChanged;

  const closeDetails = () => {
    detailRequest.current += 1;
    setDetailTarget(null);
    setAppDetail(null);
    setDetailError(null);
    setDetailLoading(false);
  };

  const openDetails = (app: HubApp) => {
    const requestId = ++detailRequest.current;
    setDetailTarget(app);
    setAppDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    fetchHubAppDetail(app.id)
      .then((result) => {
        if (detailRequest.current === requestId) setAppDetail(result);
      })
      .catch((reason) => {
        if (detailRequest.current !== requestId) return;
        setDetailError(
          reason instanceof Error ? reason.message : "无法读取应用详情",
        );
      })
      .finally(() => {
        if (detailRequest.current === requestId) setDetailLoading(false);
      });
  };

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchHubCatalog()
      .then(setCatalog)
      .catch((reason) => {
        setCatalog(null);
        setError(
          reason instanceof Error ? reason.message : "Echo Hub 暂时不可用",
        );
      })
      .finally(() => setLoading(false));
  }, []);

  const refreshAgentCatalog = () => {
    setAgentLoading(true);
    setAgentError(null);
    fetchAgentHubCatalog()
      .then((result) => {
        setAgentCatalog(result);
        setAgentError(result.available ? null : result.error);
      })
      .catch((reason) => {
        setAgentCatalog(null);
        setAgentError(
          reason instanceof Error ? reason.message : "Agent 能力目录尚未连接",
        );
      })
      .finally(() => setAgentLoading(false));
  };

  const refreshOperations = useCallback(() => {
    if (!canManageDevice || operationsLoading.current) return;
    operationsLoading.current = true;
    fetchHubOperations(12)
      .then((result) => {
        const nextStatuses = new Map<string, HubOperation["status"]>();
        for (const operation of result.operations) {
          nextStatuses.set(operation.operationId, operation.status);
          if (
            operation.status === "succeeded" &&
            operation.credentialsAvailable &&
            !claimedCredentialOperations.current.has(operation.operationId)
          ) {
            claimedCredentialOperations.current.add(operation.operationId);
            const app = catalogRef.current?.apps.find(
              (candidate) => candidate.id === operation.appId,
            );
            claimHubOperationCredentials(operation.operationId)
              .then((secrets) => {
                setRevealedCredentials({
                  appName: app?.nameZh ?? operation.appId,
                  secrets,
                });
                refreshOperations();
              })
              .catch(() => {
                toast.error("初始凭据未能领取，请确认是否已在其他窗口查看");
              });
          }
          const previous = operationStatuses.current.get(operation.operationId);
          if (
            operationsLoaded.current &&
            previous &&
            ["queued", "running"].includes(previous) &&
            !["queued", "running"].includes(operation.status)
          ) {
            const app = catalogRef.current?.apps.find(
              (candidate) => candidate.id === operation.appId,
            );
            const appName = app?.nameZh ?? operation.appId;
            if (operation.status === "succeeded") {
              const success = {
                install: `${appName} 已安装并启动`,
                update: `${appName} 已安全更新，应用数据保持不变`,
                uninstall: `${appName} 已卸载，应用数据仍保留`,
                start: `${appName} 的全部服务已启动`,
                stop: `${appName} 的全部服务已停止，数据仍保留`,
                restart: `${appName} 已完成整组安全重启`,
              }[operation.operation];
              toast.success(success);
            } else {
              toast.error(
                OPERATION_FAILURE_LABELS[operation.error?.code ?? ""] ??
                  "后台任务没有完成，请刷新确认应用状态",
              );
            }
            refresh();
            onAppsChangedRef.current?.();
          }
        }
        operationStatuses.current = nextStatuses;
        operationsLoaded.current = true;
        setOperations(result.operations);
      })
      .catch(() => {
        // The catalog remains usable if the optional activity view is briefly unavailable.
      })
      .finally(() => {
        operationsLoading.current = false;
      });
  }, [canManageDevice, refresh]);

  useEffect(() => {
    if (open) {
      refresh();
      if (canManageDevice) refreshOperations();
    } else {
      closeDetails();
      setAgentDetailTarget(null);
      setAgentConnectionTarget(null);
    }
  }, [canManageDevice, open, refresh, refreshOperations]);

  useEffect(() => {
    if (!open || !canManageDevice) return;
    const timer = window.setInterval(refreshOperations, 1600);
    return () => window.clearInterval(timer);
  }, [canManageDevice, open, refreshOperations]);

  useEffect(() => {
    if (open && view === "agent" && agentCatalog === null && !agentLoading) {
      refreshAgentCatalog();
    }
  }, [agentCatalog, agentLoading, open, view]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.key !== "Escape" ||
        pendingPlan ||
        pendingAgentPlan ||
        revealedCredentials ||
        agentConnectionTarget
      )
        return;
      if (agentDetailTarget) setAgentDetailTarget(null);
      else if (detailTarget) closeDetails();
      else onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [
    agentDetailTarget,
    agentConnectionTarget,
    detailTarget,
    onClose,
    open,
    pendingAgentPlan,
    pendingPlan,
    revealedCredentials,
  ]);

  const categories = useMemo(() => {
    const present = new Set(catalog?.apps.map((app) => app.category) ?? []);
    return [
      "all",
      "installed",
      "updates",
      ...Object.keys(CATEGORY_LABELS).filter(
        (key) =>
          !["all", "installed", "updates"].includes(key) && present.has(key),
      ),
    ];
  }, [catalog]);

  const deviceCounts = useMemo(
    () => ({
      installed:
        catalog?.apps.filter((app) => app.installation.installed).length ?? 0,
      updates: catalog?.apps.filter((app) => app.updateAvailable).length ?? 0,
    }),
    [catalog],
  );

  const activeOperationAppIds = useMemo(
    () =>
      new Set(
        operations
          .filter((operation) =>
            ["queued", "running"].includes(operation.status),
          )
          .map((operation) => operation.appId),
      ),
    [operations],
  );

  const visibleApps = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return (catalog?.apps ?? []).filter((app) => {
      if (category === "installed" && !app.installation.installed) return false;
      if (category === "updates" && !app.updateAvailable) return false;
      if (
        !["all", "installed", "updates"].includes(category) &&
        app.category !== category
      )
        return false;
      if (!needle) return true;
      return `${app.nameZh} ${app.name} ${app.summary}`
        .toLocaleLowerCase()
        .includes(needle);
    });
  }, [catalog, category, search]);

  const visibleAgentAssets = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return (agentCatalog?.assets ?? []).filter((asset) => {
      if (agentKind === "installed" && !asset.installed) return false;
      if (
        agentKind === "updates" &&
        asset.lifecycleState !== "update_available"
      )
        return false;
      if (
        !["all", "installed", "updates"].includes(agentKind) &&
        asset.kind !== agentKind
      )
        return false;
      if (!needle) return true;
      return `${asset.name} ${asset.description} ${asset.source}`
        .toLocaleLowerCase()
        .includes(needle);
    });
  }, [agentCatalog, agentKind, search]);

  const refreshActiveView = () => {
    if (view === "agent") refreshAgentCatalog();
    else refresh();
  };

  const beginInstall = async (app: HubApp) => {
    if (!canManageDevice) {
      toast.info("设备应用由管理员安装；你仍可浏览目录和连接个人账户");
      return;
    }
    if (!app.installable || planningAppId) return;
    setPlanningAppId(app.id);
    try {
      const plan = await createHubInstallPlan(app.id);
      if (!plan.ready) {
        const reason = plan.blockers[0]?.message || "当前不能安装这个应用";
        toast.error(reason);
        refresh();
        return;
      }
      setPendingPlan({ operation: "install", app, plan });
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "无法检查安装计划",
      );
    } finally {
      setPlanningAppId(null);
    }
  };

  const beginUninstall = async (app: HubApp) => {
    if (!canManageDevice) {
      toast.info("设备应用只能由管理员卸载");
      return;
    }
    if (!app.installation.installed || planningAppId) return;
    setPlanningAppId(app.id);
    try {
      const plan = await createHubUninstallPlan(app.id);
      if (!plan.ready) {
        const reason = plan.blockers[0]?.message || "当前不能卸载这个应用";
        toast.error(reason);
        refresh();
        return;
      }
      setPendingPlan({ operation: "uninstall", app, plan });
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "无法检查卸载计划",
      );
    } finally {
      setPlanningAppId(null);
    }
  };

  const beginUpdate = async (app: HubApp) => {
    if (!canManageDevice) {
      toast.info("设备应用只能由管理员更新");
      return;
    }
    if (!app.installation.installed || !app.updateAvailable || planningAppId)
      return;
    setPlanningAppId(app.id);
    try {
      const plan = await createHubUpdatePlan(app.id);
      if (!plan.ready) {
        const reason = plan.blockers[0]?.message || "当前不能更新这个应用";
        toast.error(reason);
        refresh();
        return;
      }
      setPendingPlan({ operation: "update", app, plan });
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "无法检查更新计划",
      );
    } finally {
      setPlanningAppId(null);
    }
  };

  const beginControl = async (operation: HubControlOperation, app: HubApp) => {
    if (!canManageDevice) {
      toast.info("应用运行状态只能由设备管理员修改");
      return;
    }
    if (!app.installation.installed || planningAppId) return;
    setPlanningAppId(app.id);
    try {
      const plan = await createHubControlPlan(operation, app.id);
      if (!plan.ready) {
        const reason =
          plan.blockers[0]?.message ||
          `当前不能${OPERATION_LABELS[operation]}这个应用`;
        toast.error(reason);
        refresh();
        return;
      }
      closeDetails();
      setPendingPlan({ operation, app, plan });
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "无法检查应用运行计划",
      );
    } finally {
      setPlanningAppId(null);
    }
  };

  const confirmOperation = async (password: string) => {
    if (!pendingPlan) return;
    const { operation, app, plan } = pendingPlan;
    const action = `hub.app.${operation}` as const;
    const approval = await requestHighRiskApproval(
      action,
      plan.planId,
      password,
    );
    if (operation === "install") {
      const result = await queueHubInstall(
        app.id,
        plan.planId,
        approval.approvalToken,
      );
      operationStatuses.current.set(result.operationId, result.status);
      setOperations((current) => [
        result,
        ...current.filter((item) => item.operationId !== result.operationId),
      ]);
    } else if (operation === "update") {
      const result = await queueHubUpdate(
        app.id,
        plan.planId,
        approval.approvalToken,
      );
      operationStatuses.current.set(result.operationId, result.status);
      setOperations((current) => [
        result,
        ...current.filter((item) => item.operationId !== result.operationId),
      ]);
    } else if (operation === "uninstall") {
      const result = await queueHubUninstall(
        app.id,
        plan.planId,
        approval.approvalToken,
      );
      operationStatuses.current.set(result.operationId, result.status);
      setOperations((current) => [
        result,
        ...current.filter((item) => item.operationId !== result.operationId),
      ]);
    } else {
      const result = await queueHubControl(
        operation,
        app.id,
        plan.planId,
        approval.approvalToken,
      );
      operationStatuses.current.set(result.operationId, result.status);
      setOperations((current) => [
        result,
        ...current.filter((item) => item.operationId !== result.operationId),
      ]);
    }
    setPendingPlan(null);
    toast.message(
      `${app.nameZh} 已加入${OPERATION_LABELS[operation]}任务，可关闭商城继续后台执行`,
    );
    refreshOperations();
  };

  const beginAgentLifecycle = async (
    operation: PendingAgentOperation["operation"],
    asset: AgentHubAsset,
  ) => {
    if (!canManageDevice) {
      toast.info("Agent 能力由管理员安装；个人账户连接仍按当前用户隔离");
      return;
    }
    if (agentActionId) return;
    setAgentActionId(asset.id);
    try {
      const plan = await createAgentCapabilityPlan(operation, asset.installId);
      if (!plan.ready) {
        toast.error(
          operation === "rollback"
            ? "当前没有可用的回滚代际"
            : operation === "uninstall"
              ? "当前能力不能卸载"
              : "当前能力不能安装或更新",
        );
        refreshAgentCatalog();
        return;
      }
      setAgentDetailTarget(null);
      setPendingAgentPlan({ operation, asset, plan });
    } catch (reason) {
      toast.error(
        reason instanceof Error
          ? reason.message
          : "无法检查 Agent 能力操作计划",
      );
    } finally {
      setAgentActionId(null);
    }
  };

  const authorizeAgent = async (asset: AgentHubAsset) => {
    if (!canManageDevice) {
      toast.info("Agent 能力权限只能由设备管理员确认");
      return;
    }
    if (agentActionId) return;
    setAgentActionId(asset.id);
    try {
      const plan = await createAgentCapabilityPlan(
        "authorize",
        asset.installId,
      );
      if (!plan.ready) throw new Error("当前能力不能启用");
      setAgentDetailTarget(null);
      setPendingAgentPlan({ operation: "authorize", asset, plan });
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "权限确认没有完成",
      );
    } finally {
      setAgentActionId(null);
    }
  };

  const disableAgent = async (asset: AgentHubAsset) => {
    if (agentActionId) return;
    setAgentActionId(asset.id);
    try {
      await disableAgentCapability(asset.installId);
      setAgentDetailTarget(null);
      toast.success(`${asset.name} 已对当前用户停用`);
      refreshAgentCatalog();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "停用没有完成");
    } finally {
      setAgentActionId(null);
    }
  };

  const confirmAgentOperation = async (password: string) => {
    if (!pendingAgentPlan) return;
    const { operation, asset, plan } = pendingAgentPlan;
    const action = `agent.capability.${operation}` as const;
    const approval = await requestHighRiskApproval(
      action,
      plan.planId,
      password,
    );
    if (operation === "authorize") {
      await authorizeAgentCapability(
        asset.installId,
        plan.planId,
        plan.permissions,
        approval.approvalToken,
      );
    } else {
      await applyAgentCapabilityLifecycle(
        operation,
        asset.installId,
        plan.planId,
        approval.approvalToken,
      );
    }
    setPendingAgentPlan(null);
    toast.success(
      operation === "uninstall"
        ? `${asset.name} 已从设备卸载`
        : operation === "rollback"
          ? `${asset.name} 已恢复上一代`
          : operation === "authorize"
            ? `${asset.name} 已为当前用户启用`
            : `${asset.name} 已安装，确认权限后即可启用`,
    );
    refreshAgentCatalog();
  };

  if (!open) return null;

  return (
    <>
      <div
        data-desktop-interactive
        className="fixed inset-0 z-[88] flex items-center justify-center bg-slate-950/18 p-4 backdrop-blur-[2px]"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget) onClose();
        }}
      >
        <section
          role="dialog"
          aria-modal="true"
          aria-label="Echo Hub"
          className="relative flex h-[min(760px,calc(100vh-64px))] w-[min(1060px,calc(100vw-32px))] flex-col overflow-hidden rounded-[24px] border border-white/72 bg-slate-50/92 text-slate-900 shadow-[0_34px_100px_rgba(15,23,42,.34)] backdrop-blur-3xl"
        >
          <header className="relative flex h-14 shrink-0 items-center border-b border-slate-200/65 bg-white/58 px-5">
            <div className="flex gap-2">
              <button
                type="button"
                aria-label="关闭 Echo Hub"
                onClick={onClose}
                className="grid size-3.5 place-items-center rounded-full bg-[#ff5f57] text-transparent hover:text-red-900/70"
              >
                <XIcon className="size-2.5" />
              </button>
              <span className="size-3.5 rounded-full bg-[#febc2e]" />
              <span className="size-3.5 rounded-full bg-[#28c840]" />
            </div>
            <div className="pointer-events-none absolute left-1/2 flex -translate-x-1/2 items-center gap-2 text-sm font-semibold text-slate-700">
              <ShoppingBagIcon className="size-4 text-blue-600" />
              Echo Hub
            </div>
            <button
              type="button"
              onClick={refreshActiveView}
              disabled={view === "agent" ? agentLoading : loading}
              aria-label="刷新应用目录"
              className="ml-auto grid size-8 place-items-center rounded-full text-slate-500 transition hover:bg-slate-200/70 disabled:opacity-50"
            >
              <RefreshCwIcon
                className={cn(
                  "size-4",
                  (view === "agent" ? agentLoading : loading) && "animate-spin",
                )}
              />
            </button>
          </header>

          <div className="shrink-0 px-6 pb-5 pt-6">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
              <div>
                <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-blue-600">
                  {view === "device" ? (
                    <ShoppingBagIcon className="size-3.5" />
                  ) : (
                    <BotIcon className="size-3.5" />
                  )}
                  {view === "device"
                    ? "为 Echo OS 精选"
                    : "复用 Agent 能力目录"}
                </div>
                <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">
                  {view === "device"
                    ? "让家庭数据服务即装即用"
                    : "把插件与技能带进系统工作流"}
                </h1>
                <p className="mt-1.5 max-w-xl text-[13px] leading-5 text-slate-500">
                  {!canManageDevice
                    ? "你可以浏览设备与 Agent 目录，并连接自己的账户；安装、更新和设备控制由管理员完成。"
                    : view === "device"
                      ? "统一检查架构、端口、目录权限与运行状态。只有通过 Echo 安全合同的版本才会开放安装。"
                      : "直接读取 Agent 已有目录与安装状态，不复制私有数据库，也不在系统层重写插件安装逻辑。"}
                </p>
              </div>
              <label className="flex h-10 w-full items-center gap-2 rounded-full bg-white/82 px-4 shadow-[inset_0_0_0_1px_rgba(148,163,184,.28),0_5px_18px_rgba(51,65,85,.06)] lg:w-72">
                <SearchIcon className="size-4 text-slate-400" />
                <input
                  value={search}
                  onChange={(event) => setSearch(event.currentTarget.value)}
                  placeholder={
                    view === "device" ? "搜索应用" : "搜索插件与技能"
                  }
                  aria-label="搜索 Hub 应用"
                  className="min-w-0 flex-1 bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400"
                />
              </label>
            </div>

            <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-1 rounded-full bg-white/62 p-1">
                <button
                  type="button"
                  onClick={() => {
                    setView("device");
                    setSearch("");
                  }}
                  className={cn(
                    "flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[11px] font-semibold transition",
                    view === "device"
                      ? "bg-slate-900 text-white shadow-sm"
                      : "text-slate-500 hover:text-slate-800",
                  )}
                >
                  <ShoppingBagIcon className="size-3.5" />
                  设备应用
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setView("agent");
                    setSearch("");
                  }}
                  className={cn(
                    "flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[11px] font-semibold transition",
                    view === "agent"
                      ? "bg-slate-900 text-white shadow-sm"
                      : "text-slate-500 hover:text-slate-800",
                  )}
                >
                  <LibraryIcon className="size-3.5" />
                  Agent 能力
                </button>
              </div>
              <div className="flex items-center gap-2 overflow-x-auto pb-1">
                {(view === "device" ? categories : AGENT_ASSET_FILTERS).map(
                  (key) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => {
                        if (view === "device") setCategory(key);
                        else setAgentKind(key as AgentAssetFilter);
                      }}
                      className={cn(
                        "shrink-0 rounded-full px-3.5 py-1.5 text-[11px] font-medium transition",
                        (
                          view === "device"
                            ? category === key
                            : agentKind === key
                        )
                          ? "bg-blue-600 text-white shadow-sm"
                          : "bg-white/65 text-slate-500 hover:bg-white hover:text-slate-800",
                      )}
                    >
                      {view === "device"
                        ? `${CATEGORY_LABELS[key] ?? key}${
                            key === "installed" || key === "updates"
                              ? ` ${deviceCounts[key]}`
                              : ""
                          }`
                        : key === "all"
                          ? "全部"
                          : key === "installed"
                            ? `已安装 ${agentCatalog?.installed ?? 0}`
                            : key === "updates"
                              ? `可更新 ${agentCatalog?.updates ?? 0}`
                              : key === "workbench"
                                ? "工作台"
                                : key === "plugin"
                                  ? "插件"
                                  : key === "connector"
                                    ? "连接器"
                                    : "技能"}
                    </button>
                  ),
                )}
              </div>
            </div>
          </div>

          {view === "device" &&
            !loading &&
            catalog &&
            !catalog.runtime.available && (
              <div className="mx-6 mb-4 flex shrink-0 items-center gap-3 rounded-2xl bg-amber-50/90 px-4 py-3 text-amber-800">
                <UnplugIcon className="size-4 shrink-0" />
                <p className="text-xs">
                  应用目录可浏览，但设备安装服务暂时离线。
                </p>
              </div>
            )}

          {view === "device" && operations.length > 0 && (
            <div className="mx-6 mb-4 shrink-0 rounded-2xl bg-white/66 px-4 py-3 shadow-[inset_0_0_0_1px_rgba(148,163,184,.16)]">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                  后台任务
                </span>
                <span className="text-[10px] text-slate-400">
                  关窗或刷新后仍会保留
                </span>
              </div>
              <div className="space-y-1.5">
                {operations.slice(0, 3).map((operation) => {
                  const app = catalog?.apps.find(
                    (candidate) => candidate.id === operation.appId,
                  );
                  const active = ["queued", "running"].includes(
                    operation.status,
                  );
                  const failed = ["failed", "interrupted"].includes(
                    operation.status,
                  );
                  return (
                    <div
                      key={operation.operationId}
                      className="flex items-center gap-2.5 rounded-xl bg-slate-50/78 px-3 py-2"
                    >
                      {active ? (
                        <Loader2Icon className="size-3.5 shrink-0 animate-spin text-blue-600" />
                      ) : failed ? (
                        <UnplugIcon className="size-3.5 shrink-0 text-rose-500" />
                      ) : (
                        <CheckCircle2Icon className="size-3.5 shrink-0 text-emerald-500" />
                      )}
                      <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-slate-700">
                        {app?.nameZh ?? operation.appId} ·{" "}
                        {OPERATION_LABELS[operation.operation]}
                      </span>
                      <span
                        className={cn(
                          "max-w-[54%] truncate text-[10px]",
                          active
                            ? "text-blue-600"
                            : failed
                              ? "text-rose-600"
                              : "text-emerald-600",
                        )}
                        title={operation.error?.recoveryAction ?? undefined}
                      >
                        {operationProgressLabel(operation)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">
            {view === "agent" ? (
              agentLoading && !agentCatalog ? (
                <div className="grid h-56 place-items-center text-sm text-slate-400">
                  <span className="flex items-center gap-2">
                    <Loader2Icon className="size-4 animate-spin" />
                    正在读取 Agent 能力目录…
                  </span>
                </div>
              ) : agentError ? (
                <div className="grid h-56 place-items-center text-center">
                  <div>
                    <UnplugIcon className="mx-auto size-8 text-slate-300" />
                    <p className="mt-3 text-sm font-medium text-slate-700">
                      {agentError}
                    </p>
                    <button
                      type="button"
                      onClick={refreshAgentCatalog}
                      className="mt-3 rounded-full bg-slate-900 px-4 py-2 text-xs font-medium text-white"
                    >
                      重新连接
                    </button>
                  </div>
                </div>
              ) : visibleAgentAssets.length === 0 ? (
                <div className="grid h-56 place-items-center text-sm text-slate-400">
                  没有找到匹配的 Agent 能力
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {visibleAgentAssets.map((asset) => (
                    <AgentAssetCard
                      key={asset.id}
                      asset={asset}
                      onManage={onOpenAgentAssets}
                      onDetails={setAgentDetailTarget}
                    />
                  ))}
                </div>
              )
            ) : loading && !catalog ? (
              <div className="grid h-56 place-items-center text-sm text-slate-400">
                <span className="flex items-center gap-2">
                  <Loader2Icon className="size-4 animate-spin" />
                  正在校验精选目录…
                </span>
              </div>
            ) : error ? (
              <div className="grid h-56 place-items-center text-center">
                <div>
                  <UnplugIcon className="mx-auto size-8 text-slate-300" />
                  <p className="mt-3 text-sm font-medium text-slate-700">
                    {error}
                  </p>
                  <button
                    type="button"
                    onClick={refresh}
                    className="mt-3 rounded-full bg-slate-900 px-4 py-2 text-xs font-medium text-white"
                  >
                    重新连接
                  </button>
                </div>
              </div>
            ) : visibleApps.length === 0 ? (
              <div className="grid h-56 place-items-center text-sm text-slate-400">
                没有找到匹配的应用
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {visibleApps.map((app) => (
                  <AppCard
                    key={app.id}
                    app={app}
                    planning={
                      planningAppId === app.id ||
                      activeOperationAppIds.has(app.id)
                    }
                    onOpen={onOpenDeviceApp}
                    onStart={(candidate) =>
                      void beginControl("start", candidate)
                    }
                    onStop={(candidate) => void beginControl("stop", candidate)}
                    onRestart={(candidate) =>
                      void beginControl("restart", candidate)
                    }
                    onInstall={(candidate) => void beginInstall(candidate)}
                    onUpdate={(candidate) => void beginUpdate(candidate)}
                    onUninstall={(candidate) => void beginUninstall(candidate)}
                    onDetails={openDetails}
                  />
                ))}
              </div>
            )}
          </div>

          <footer className="flex h-10 shrink-0 items-center justify-between border-t border-slate-200/60 bg-white/48 px-6 text-[10px] text-slate-400">
            <span>
              {view === "agent"
                ? agentCatalog
                  ? `${agentCatalog.workbenches} 个工作台 · ${agentCatalog.plugins} 个插件 · ${agentCatalog.connectors} 个连接器 · ${agentCatalog.skills} 个技能 · 已安装 ${agentCatalog.installed}${agentCatalog.updates ? ` · 可更新 ${agentCatalog.updates}` : ""}`
                  : "Agent 能力目录"
                : catalog
                  ? `${visibleApps.length}/${catalog.total} 个精选应用 · ${catalog.architecture}`
                  : "Echo 受信目录"}
            </span>
            <span className="flex items-center gap-1.5">
              {view === "agent" ? (
                <LibraryIcon className="size-3 text-blue-500" />
              ) : catalog?.runtime.available ? (
                <CheckCircle2Icon className="size-3 text-emerald-500" />
              ) : (
                <ShieldCheckIcon className="size-3 text-slate-400" />
              )}
              {view === "agent"
                ? "目录归 Agent 所有，Echo Hub 只做统一入口"
                : "安装前再次校验并需要管理员确认"}
            </span>
          </footer>
        </section>
      </div>

      <AppDetailSheet
        target={detailTarget}
        detail={appDetail}
        loading={detailLoading}
        error={detailError}
        planning={
          detailTarget !== null &&
          (planningAppId === detailTarget.id ||
            activeOperationAppIds.has(detailTarget.id))
        }
        onClose={closeDetails}
        onRetry={() => {
          if (detailTarget) openDetails(detailTarget);
        }}
        onStart={(candidate) => void beginControl("start", candidate)}
        onStop={(candidate) => void beginControl("stop", candidate)}
        onRestart={(candidate) => void beginControl("restart", candidate)}
      />

      <AgentAssetDetailSheet
        target={agentDetailTarget}
        onClose={() => setAgentDetailTarget(null)}
        onManage={onOpenAgentAssets}
        busy={agentActionId === agentDetailTarget?.id}
        onInstall={(asset) => void beginAgentLifecycle("install", asset)}
        onAuthorize={(asset) => void authorizeAgent(asset)}
        onDisable={(asset) => void disableAgent(asset)}
        onConnect={(asset) => {
          setAgentDetailTarget(null);
          setAgentConnectionTarget(asset);
        }}
        onRollback={(asset) => void beginAgentLifecycle("rollback", asset)}
        onUninstall={(asset) => void beginAgentLifecycle("uninstall", asset)}
      />

      <AgentConnectionDialog
        target={agentConnectionTarget}
        onClose={() => setAgentConnectionTarget(null)}
        onChanged={refreshAgentCatalog}
        onManage={onOpenAgentAssets}
      />

      <HighRiskApprovalDialog
        open={pendingPlan !== null}
        title={`${
          pendingPlan ? OPERATION_LABELS[pendingPlan.operation] : "操作"
        }“${pendingPlan?.app.nameZh ?? "应用"}”？`}
        description={operationDescription(pendingPlan)}
        targetLabel={operationTargetLabel(pendingPlan)}
        confirmLabel={`确认${
          pendingPlan ? OPERATION_LABELS[pendingPlan.operation] : "操作"
        }`}
        destructive={pendingPlan?.operation === "uninstall"}
        onCancel={() => setPendingPlan(null)}
        onConfirm={confirmOperation}
      />

      <HighRiskApprovalDialog
        open={pendingAgentPlan !== null}
        title={`${
          pendingAgentPlan?.operation === "uninstall"
            ? "卸载"
            : pendingAgentPlan?.operation === "rollback"
              ? "回滚"
              : pendingAgentPlan?.operation === "authorize"
                ? "确认权限并启用"
                : pendingAgentPlan?.asset.lifecycleState === "update_available"
                  ? "更新"
                  : "安装"
        }“${pendingAgentPlan?.asset.name ?? "Agent 能力"}”？`}
        description={
          pendingAgentPlan?.operation === "uninstall"
            ? "将从设备移除能力包及其技能投影；个人授权会一并失效。"
            : pendingAgentPlan?.operation === "rollback"
              ? "将停用当前代并恢复上一份已验证的能力包和权限代际。"
              : pendingAgentPlan?.operation === "authorize"
                ? "将把列出的账户、内容、网络或本机权限授予当前用户；启用前需要再次验证设备管理员密码。"
                : "系统会重新核对发布者签名、版本、依赖和权限声明，再切换能力包。"
        }
        targetLabel={pendingAgentPlan?.plan.planId}
        confirmLabel={
          pendingAgentPlan?.operation === "uninstall"
            ? "确认卸载"
            : pendingAgentPlan?.operation === "rollback"
              ? "确认回滚"
              : pendingAgentPlan?.operation === "authorize"
                ? "确认权限并启用"
                : "确认安装"
        }
        destructive={pendingAgentPlan?.operation === "uninstall"}
        onCancel={() => setPendingAgentPlan(null)}
        onConfirm={confirmAgentOperation}
      />

      {revealedCredentials && (
        <div
          className="fixed inset-0 z-[160] grid place-items-center bg-slate-950/44 p-5 backdrop-blur-[8px]"
          data-desktop-interactive
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-label={`${revealedCredentials.appName} 初始凭据`}
            className="w-full max-w-[470px] overflow-hidden rounded-[24px] border border-white/75 bg-white/96 text-slate-900 shadow-[0_30px_100px_rgba(15,23,42,.38)]"
          >
            <div className="px-6 pb-4 pt-6">
              <div className="grid size-11 place-items-center rounded-2xl bg-amber-50 text-amber-600">
                <ShieldCheckIcon className="size-5" />
              </div>
              <h2 className="mt-4 text-lg font-semibold">
                保存 {revealedCredentials.appName} 初始凭据
              </h2>
              <p className="mt-1.5 text-[13px] leading-5 text-slate-500">
                这些密码只显示这一次。关闭后 Echo
                只保留受保护的容器密钥卷，无法再次回显。
              </p>
              {"admin-password" in revealedCredentials.secrets && (
                <p className="mt-3 rounded-xl bg-slate-100 px-3 py-2 text-xs text-slate-600">
                  管理员账号：
                  <span className="font-mono font-semibold">admin</span>
                </p>
              )}
              <div className="mt-3 space-y-2">
                {Object.entries(revealedCredentials.secrets).map(
                  ([name, value]) => {
                    const label =
                      name === "admin-password" ? "管理员密码" : name;
                    return (
                      <div
                        key={name}
                        className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <div className="min-w-0">
                            <div className="text-[11px] font-medium text-slate-500">
                              {label}
                            </div>
                            <code className="mt-1 block select-all break-all text-[12px] text-slate-900">
                              {value}
                            </code>
                          </div>
                          <button
                            type="button"
                            aria-label={`复制 ${label}`}
                            onClick={() => {
                              void copyTextToClipboard(value)
                                .then(() => toast.success(`${label}已复制`))
                                .catch(() => toast.error(`${label}复制失败`));
                            }}
                            className="grid size-9 shrink-0 place-items-center rounded-full bg-white text-slate-500 shadow-sm transition hover:text-blue-600"
                          >
                            <CopyIcon className="size-4" />
                          </button>
                        </div>
                      </div>
                    );
                  },
                )}
              </div>
            </div>
            <div className="border-t border-slate-200 bg-slate-50/80 px-6 py-4">
              <button
                type="button"
                onClick={() => setRevealedCredentials(null)}
                className="h-10 w-full rounded-full bg-slate-900 text-sm font-semibold text-white transition hover:bg-slate-800"
              >
                我已保存，关闭
              </button>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
