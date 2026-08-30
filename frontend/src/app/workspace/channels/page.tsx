/* Implementation note. */
import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import {
  CheckIcon,
  ChevronRightIcon,
  MessageCircleIcon,
  PlusIcon,
  SearchIcon,
  SettingsIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { RoutedWebLink } from "@/components/ui/routed-web-link";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { ErrorState, LoadingState, StatusBadge } from "@/components/ui/state";
import { ChannelCredentialDialog } from "@/components/workspace/channel-credential-dialog";
import { ChannelPairingsSheet } from "@/components/workspace/channel-pairings-sheet";
import {
  WorkspaceBody,
  WorkspaceContainer,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

type ChannelRow = {
  channel_id: string;
  type: string;
  platform: string;
  connected: boolean;
  display_name: string;
  description: string;
  help_url: string;
  metrics: {
    pairings_count: number;
    group_count: number;
    pending_count: number;
  };
  assigned_agent_id?: string | null;
};

type AgentLite = {
  id: string;
  display_name?: string;
  avatar_url?: string | null;
};

type ChannelFilter = "all" | "connected" | "unlinked";

// Implementation note.
const PLATFORM_COLORS: Record<string, string> = {
  wechat: "bg-[#07c160] text-white",
  dingtalk: "bg-[#1677ff] text-white",
  feishu: "bg-[#3370ff] text-white",
  telegram: "bg-[#26a5e4] text-white",
  slack: "bg-[#4a154b] text-white",
  discord: "bg-[#5865f2] text-white",
  signal: "bg-[#3a76f0] text-white",
  whatsapp: "bg-[#25d366] text-white",
  email: "bg-[#ea4335] text-white",
  sms: "bg-[#f22f46] text-white",
  mattermost: "bg-[#0058cc] text-white",
  matrix: "bg-[#0dbd8b] text-white",
  wecom: "bg-[#07c160] text-white",
  qqbot: "bg-[#12b7f5] text-white",
  teams: "bg-[#6264a7] text-white",
  line: "bg-[#06c755] text-white",
  homeassistant: "bg-[#41bdf5] text-white",
  bluebubbles: "bg-[#34c759] text-white",
  ntfy: "bg-[#317f6e] text-white",
  webhooks: "bg-[#f59e0b] text-white",
  google_chat: "bg-[#00ac47] text-white",
  simplex: "bg-[#f76b1c] text-white",
  open_webui: "bg-[#8b5cf6] text-white",
  yuanbao: "bg-[#006eff] text-white",
  other: "bg-muted/70 text-muted-foreground",
};

// Implementation note.
const PLATFORM_ICONS: Record<string, string> = {
  wechat: "💬",
  dingtalk: "📌",
  feishu: "🐦",
  telegram: "✈️",
  slack: "#",
  discord: "🎮",
  signal: "🔒",
  whatsapp: "📱",
  email: "✉️",
  sms: "📩",
  mattermost: "📡",
  matrix: "🟢",
  wecom: "💼",
  qqbot: "🐧",
  teams: "👥",
  line: "💬",
  homeassistant: "🏠",
  bluebubbles: "🔵",
  ntfy: "🔔",
  webhooks: "🪝",
  google_chat: "💬",
  simplex: "🔐",
  open_webui: "🌐",
  yuanbao: "💎",
  other: "•",
};

const PLATFORM_CATEGORIES: Record<string, string> = {
  im: "即时通讯",
  china: "国内平台",
  email_sms: "邮件/短信",
  smart_home: "智能家居",
  dev_tools: "开发工具",
};

const PLATFORM_CATEGORY_MAP: Record<string, string> = {
  telegram: "im",
  discord: "im",
  slack: "im",
  signal: "im",
  whatsapp: "im",
  matrix: "im",
  mattermost: "im",
  simplex: "im",
  line: "im",
  wechat: "china",
  feishu: "china",
  dingtalk: "china",
  wecom: "china",
  qqbot: "china",
  yuanbao: "china",
  email: "email_sms",
  sms: "email_sms",
  homeassistant: "smart_home",
  ntfy: "smart_home",
  webhooks: "dev_tools",
  open_webui: "dev_tools",
  google_chat: "dev_tools",
  bluebubbles: "dev_tools",
  teams: "dev_tools",
};

const CATEGORY_ORDER = ["im", "china", "email_sms", "smart_home", "dev_tools"];

export default function ChannelsPage() {
  const { t } = useI18n();
  const { confirm, confirmDialog } = useConfirmDialog();
  const [rows, setRows] = useState<ChannelRow[]>([]);
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ChannelFilter>("all");
  /* Implementation note. */
  const [assigningId, setAssigningId] = useState<string | null>(null);
  /* Implementation note. */
  const [credPlatform, setCredPlatform] = useState<string | null>(null);
  /* Implementation note. */
  const [pairingsForId, setPairingsForId] = useState<string | null>(null);

  // Implementation note.
  const loadAll = async () => {
    setLoading(true);
    try {
      const [chRes, agRes] = await Promise.all([
        fetch(`${getBackendBaseURL()}/api/channels`),
        fetch(`${getBackendBaseURL()}/api/agents`),
      ]);
      if (!chRes.ok)
        throw new Error(`Failed to load channels: ${chRes.status}`);
      const ch = (await chRes.json()) as ChannelRow[];
      setRows(ch);
      if (agRes.ok) {
        const ag = (await agRes.json()) as AgentLite[];
        setAgents(ag);
      }
      setError(null);
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (cancelled) return;
      await loadAll();
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const assignRow = rows.find((r) => r.channel_id === assigningId);

  async function assignAgent(channelId: string, agentId: string) {
    try {
      const r = await fetch(
        `${getBackendBaseURL()}/api/channels/${channelId}/assistant`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ agent_id: agentId }),
        },
      );
      if (!r.ok) {
        const detail = await r.text();
        throw new Error(detail || r.statusText);
      }
      toast.success(t.channels.toastAgentBound);
      setAssigningId(null);
      await loadAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t.channels.toastBindFailed);
    }
  }

  async function unassignAgent(channelId: string) {
    if (
      !(await confirm({
        title: t.channels.unassignConfirmTitle,
        description: t.channels.unassignConfirmDescription,
        confirmLabel: t.channels.unassignCurrent,
      }))
    )
      return;
    try {
      const r = await fetch(
        `${getBackendBaseURL()}/api/channels/${channelId}/assistant`,
        { method: "DELETE" },
      );
      if (!r.ok) throw new Error(r.statusText);
      toast.success(t.channels.toastAgentUnbound);
      await loadAll();
    } catch (e) {
      toast.error(
        e instanceof Error ? e.message : t.channels.toastUnbindFailed,
      );
    }
  }

  const connectedCount = useMemo(
    () => rows.filter((r) => r.connected).length,
    [rows],
  );
  const filteredRows = useMemo(() => {
    const term = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (filter === "connected" && !row.connected) return false;
      if (filter === "unlinked" && row.connected) return false;
      if (!term) return true;
      const assignedAgent = agents.find((a) => a.id === row.assigned_agent_id);
      return [
        row.display_name,
        row.description,
        row.platform,
        row.type,
        row.channel_id,
        row.assigned_agent_id ?? "",
        assignedAgent?.display_name ?? "",
      ]
        .join(" ")
        .toLowerCase()
        .includes(term);
    });
  }, [agents, filter, query, rows]);

  const channelSections = useMemo(() => {
    const grouped: Record<string, ChannelRow[]> = {};
    const otherRows: ChannelRow[] = [];
    for (const row of filteredRows) {
      const cat = PLATFORM_CATEGORY_MAP[row.platform];
      if (cat) {
        if (!grouped[cat]) grouped[cat] = [];
        grouped[cat].push(row);
      } else {
        otherRows.push(row);
      }
    }
    const sections: {
      key: string;
      label: string;
      items: ChannelRow[];
    }[] = [];
    for (const cat of CATEGORY_ORDER) {
      if (grouped[cat] && grouped[cat].length > 0) {
        sections.push({
          key: cat,
          label: PLATFORM_CATEGORIES[cat] ?? cat,
          items: grouped[cat],
        });
      }
    }
    if (otherRows.length > 0) {
      sections.push({
        key: "other",
        label: t.channels.categoryOther,
        items: otherRows,
      });
    }
    return sections;
  }, [filteredRows, t.channels.categoryOther]);

  return (
    <WorkspaceContainer>
      <WorkspaceBody className="px-4 pb-4">
        <div className="ui-density-stack mx-auto flex w-full max-w-6xl flex-col">
          {confirmDialog}
          {/* Implementation note. */}
          <section className="workspace-panel ui-density-panel">
            <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
              <div className="min-w-0">
                <div className="flex items-center gap-3">
                  <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary ring-1 ring-primary/10">
                    <MessageCircleIcon className="size-5" />
                  </div>
                  <div>
                    <div className="text-muted-foreground text-xs font-medium uppercase tracking-eyebrow">
                      Channel Ops
                    </div>
                    <h1 className="text-xl font-semibold tracking-tight">
                      {t.channels.title}
                    </h1>
                  </div>
                </div>
                <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">
                  {t.channels.pageDescription}
                  <span className="ml-1 text-muted-foreground/80">
                    {t.channels.localDataNote}
                  </span>
                </p>
              </div>

              <div className="grid min-w-[220px] grid-cols-2 gap-2 tabular-nums">
                <div className="rounded-lg border border-border-default bg-background/62 px-3 py-2">
                  <div className="text-xs text-muted-foreground">
                    {t.channels.channelCount(rows.length)}
                  </div>
                  <div className="mt-1 text-lg font-semibold">
                    {rows.length}
                  </div>
                </div>
                <div className="rounded-lg border border-success/20 bg-success/50/[0.06] px-3 py-2">
                  <div className="flex items-center gap-1 text-xs text-success">
                    <span
                      className={cn(
                        "inline-block size-1.5 rounded-full",
                        connectedCount > 0
                          ? "bg-success"
                          : "bg-muted-foreground/40",
                      )}
                    />
                    {t.channels.connectedCount(connectedCount)}
                  </div>
                  <div className="mt-1 text-lg font-semibold text-success">
                    {connectedCount}
                  </div>
                </div>
              </div>
            </div>
          </section>

          {loading && (
            <LoadingState
              title={t.channels.loading}
              variant="skeleton"
              className="workspace-panel p-5"
            />
          )}

          {error && (
            <ErrorState
              title={t.channels.loadFailed}
              detail={error}
              actionLabel={t.channels.retry}
              onAction={() => void loadAll()}
              className="workspace-panel"
            />
          )}

          {!loading && !error && rows.length === 0 && (
            <Empty className="workspace-panel min-h-[260px]">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <MessageCircleIcon />
                </EmptyMedia>
                <EmptyTitle>{t.channels.noRegistered}</EmptyTitle>
                <EmptyDescription>
                  {t.channels.noRegisteredDescription}
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}

          {!loading && !error && rows.length > 0 && (
            <>
              <section className="workspace-panel flex flex-col gap-3 p-3 md:flex-row md:items-center md:justify-between">
                <div className="relative min-w-0 flex-1">
                  <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder={t.channels.searchPlaceholder}
                    className="h-10 rounded-lg pl-9"
                  />
                </div>
                <div className="grid shrink-0 grid-cols-3 gap-1 rounded-lg border border-border-default bg-muted/25 p-1">
                  {[
                    { value: "all", label: t.channels.filterAll },
                    { value: "connected", label: t.channels.filterConnected },
                    { value: "unlinked", label: t.channels.filterUnlinked },
                  ].map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      onClick={() => setFilter(item.value as ChannelFilter)}
                      className={cn(
                        "h-8 rounded-lg px-3 text-xs font-medium transition-colors",
                        filter === item.value
                          ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </section>

              {channelSections.length === 0 ? (
                <Empty className="workspace-panel min-h-[240px]">
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <SearchIcon />
                    </EmptyMedia>
                    <EmptyTitle>{t.channels.noSearchResults}</EmptyTitle>
                    <EmptyDescription>
                      {t.channels.noSearchResultsDescription}
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              ) : (
                channelSections.map((section) => (
                  <div key={section.key}>
                    <h2 className="mb-3 text-sm font-semibold text-muted-foreground">
                      {section.label}
                    </h2>
                    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                      {section.items.map((row, index) => (
                        <ChannelCard
                          key={row.channel_id || `${row.platform}-${index}`}
                          row={row}
                          agents={agents}
                          onRequestAssign={() => setAssigningId(row.channel_id)}
                          onRequestCredential={() =>
                            setCredPlatform(row.platform)
                          }
                          onRequestPairings={() =>
                            setPairingsForId(row.channel_id)
                          }
                        />
                      ))}
                    </div>
                  </div>
                ))
              )}
            </>
          )}
        </div>
      </WorkspaceBody>

      {/* Implementation note. */}
      {pairingsForId &&
        (() => {
          const row = rows.find((r) => r.channel_id === pairingsForId);
          if (!row) return null;
          return (
            <ChannelPairingsSheet
              open={true}
              onOpenChange={(o) => !o && setPairingsForId(null)}
              channelId={pairingsForId}
              displayName={row.display_name}
            />
          );
        })()}

      {/* Implementation note. */}
      {credPlatform &&
        (() => {
          const row = rows.find((r) => r.platform === credPlatform);
          if (!row) return null;
          return (
            <ChannelCredentialDialog
              open={true}
              onOpenChange={(o) => !o && setCredPlatform(null)}
              platform={credPlatform}
              displayName={row.display_name}
              helpUrl={row.help_url}
              hasExisting={row.connected}
              onSaved={() => void loadAll()}
              onDeleted={() => void loadAll()}
            />
          );
        })()}

      {/* Implementation note. */}
      <Dialog
        open={!!assigningId}
        onOpenChange={(open) => !open && setAssigningId(null)}
      >
        <DialogContent className="max-w-md max-h-[70vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <PlusIcon className="size-4" />
              {t.channels.assignDialogTitle(assignRow?.display_name ?? "")}
            </DialogTitle>
            <DialogDescription className="text-xs">
              {t.channels.assignDialogDesc}
            </DialogDescription>
          </DialogHeader>

          <div className="overflow-y-auto flex-1 mt-2">
            {agents.length === 0 ? (
              <Empty className="min-h-[180px] rounded-lg px-4 py-8">
                <EmptyHeader>
                  <EmptyTitle>{t.channels.noAgentsAvailable}</EmptyTitle>
                </EmptyHeader>
              </Empty>
            ) : (
              <ul className="space-y-1">
                {agents.map((a, index) => {
                  const isCurrent = assignRow?.assigned_agent_id === a.id;
                  const agentKey =
                    a.id?.trim() || `${a.display_name ?? "agent"}-${index}`;
                  return (
                    <li key={agentKey}>
                      <button
                        type="button"
                        onClick={() =>
                          assigningId && void assignAgent(assigningId, a.id)
                        }
                        className={cn(
                          "w-full flex items-center gap-3 rounded-lg border border-border-subtle",
                          "bg-background px-3 py-2 text-left transition-colors",
                          "hover:bg-muted/40 hover:border-border-strong",
                          isCurrent &&
                            "border-primary/40 bg-primary/5 hover:border-primary/60",
                        )}
                      >
                        {a.avatar_url ? (
                          <img
                            src={a.avatar_url}
                            alt=""
                            className="size-8 rounded-md object-cover"
                          />
                        ) : (
                          <div className="flex size-8 items-center justify-center rounded-md bg-muted text-xs font-medium text-muted-foreground">
                            {(a.display_name ?? a.id).charAt(0).toUpperCase()}
                          </div>
                        )}
                        <div className="min-w-0 flex-1">
                          <div className="text-sm font-medium truncate">
                            {a.display_name || a.id}
                          </div>
                          <div className="text-xs text-muted-foreground truncate">
                            {a.id}
                          </div>
                        </div>
                        {isCurrent && (
                          <CheckIcon className="size-4 text-primary shrink-0" />
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {assignRow?.assigned_agent_id && (
            <div className="mt-3 pt-3 border-t border-border-subtle">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  void unassignAgent(assignRow.channel_id);
                  setAssigningId(null);
                }}
                className="w-full text-xs text-muted-foreground hover:text-destructive"
              >
                <XIcon className="mr-1.5 size-3.5" />
                {t.channels.unassignCurrent}
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </WorkspaceContainer>
  );
}

function ChannelCard({
  row,
  agents,
  onRequestAssign,
  onRequestCredential,
  onRequestPairings,
}: {
  row: ChannelRow;
  agents: AgentLite[];
  onRequestAssign: () => void;
  onRequestCredential: () => void;
  onRequestPairings: () => void;
}) {
  const { t } = useI18n();
  const colorCls = row.connected
    ? (PLATFORM_COLORS[row.platform] ?? PLATFORM_COLORS.other)
    : "bg-muted/70 text-muted-foreground";
  const icon = PLATFORM_ICONS[row.platform] ?? "•";
  const assignedAgent = agents.find((a) => a.id === row.assigned_agent_id);

  return (
    <div
      className={cn(
        "workspace-panel ui-density-panel group relative overflow-hidden rounded-lg border bg-background/60",
        "transition-colors hover:border-border-strong",
        row.connected ? "border-success/20" : "border-border-default",
      )}
    >
      <div
        className={cn(
          "absolute inset-x-0 top-0 h-1",
          row.connected ? "bg-success/55" : "bg-muted",
        )}
      />
      {/* Implementation note. */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div
            className={cn(
              "flex size-11 items-center justify-center rounded-lg text-lg shadow-[var(--shadow-xs)] ring-1 ring-black/[0.04] dark:ring-white/[0.06]",
              colorCls,
            )}
          >
            <span>{icon}</span>
          </div>
          <div className="min-w-0">
            <div className="text-sm font-semibold leading-5">
              {row.display_name}
            </div>
            <div className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
              {row.description}
              {row.help_url && !row.connected && (
                <RoutedWebLink
                  href={row.help_url}
                  openTargetSource="channel-help"
                  className="ml-1 text-primary underline underline-offset-2 hover:opacity-80 cursor-pointer"
                  onClick={(e) => {
                    if (!row.help_url || row.help_url === "#") {
                      e.preventDefault();
                      toast.info(t.channels.helpDocsComingSoon);
                    }
                  }}
                >
                  {t.channels.howToSetup}
                </RoutedWebLink>
              )}
            </div>
          </div>
        </div>
        <StatusBadge
          tone={row.connected ? "success" : "idle"}
          className="h-6 shrink-0 rounded-full px-2 text-xs"
        >
          {row.connected ? t.channels.connectedBadge : t.channels.notLinked}
        </StatusBadge>
      </div>

      {/* Implementation note. */}
      <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <Metric
          label={t.channels.pairedUsers}
          value={row.metrics.pairings_count}
          onClick={row.connected ? onRequestPairings : undefined}
        />
        <Metric
          label={t.channels.pairedGroups}
          value={row.metrics.group_count}
          onClick={row.connected ? onRequestPairings : undefined}
        />
        <Metric
          label={t.channels.pendingRequests}
          value={row.metrics.pending_count}
          onClick={row.connected ? onRequestPairings : undefined}
        />
      </div>

      {/* Implementation note. */}
      <div className="mt-3">
        {row.assigned_agent_id ? (
          <button
            type="button"
            onClick={onRequestAssign}
            className={cn(
              "group flex w-full items-center justify-between rounded-lg",
              "border border-primary/20 bg-primary/[0.04] px-3 py-2.5 text-left",
              "hover:border-primary/40 hover:bg-primary/[0.08] transition-colors",
            )}
            title={t.channels.clickToChangeAgent}
          >
            <div className="flex items-center gap-2">
              {assignedAgent?.avatar_url ? (
                <img
                  src={assignedAgent.avatar_url}
                  alt=""
                  className="size-6 rounded-md object-cover"
                />
              ) : (
                <div className="flex size-6 items-center justify-center rounded-md bg-primary/15 text-xs font-medium text-primary">
                  {(assignedAgent?.display_name ?? row.assigned_agent_id)
                    .charAt(0)
                    .toUpperCase()}
                </div>
              )}
              <div>
                <div className="text-xs font-medium">
                  {assignedAgent?.display_name ?? row.assigned_agent_id}
                </div>
                <div className="text-xs text-muted-foreground">
                  {t.channels.handlingMessages}
                </div>
              </div>
            </div>
            <ChevronRightIcon className="size-3.5 text-muted-foreground opacity-60 transition-transform group-hover:translate-x-0.5" />
          </button>
        ) : (
          <button
            type="button"
            onClick={onRequestAssign}
            className={cn(
              "group flex w-full items-center justify-between rounded-lg",
              "border border-dashed border-border-default bg-muted/18 px-3 py-2.5 text-left",
              "hover:border-border hover:bg-muted/40 transition-colors",
            )}
          >
            <div className="flex items-center gap-2">
              <PlusIcon className="size-3.5 text-muted-foreground" />
              <div>
                <div className="text-xs font-medium">
                  {t.channels.configureAgent}
                </div>
                <div className="text-xs text-muted-foreground">
                  {t.channels.configureAgentHint}
                </div>
              </div>
            </div>
            <ChevronRightIcon className="size-3.5 text-muted-foreground opacity-60 transition-transform group-hover:translate-x-0.5" />
          </button>
        )}
      </div>

      {/* Implementation note. */}
      <div className="mt-3 flex gap-2 border-t border-border-default pt-3">
        {row.connected ? (
          <Button
            variant="outline"
            size="sm"
            className="h-8 w-full text-xs"
            onClick={onRequestCredential}
          >
            <SettingsIcon className="mr-1.5 size-3" />
            {t.channels.rebindOrUnbind}
          </Button>
        ) : (
          <Button
            variant="outline"
            size="sm"
            className="h-8 w-full text-xs"
            onClick={onRequestCredential}
          >
            <SettingsIcon className="mr-1.5 size-3" />
            {t.channels.connectBot}
          </Button>
        )}
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  onClick,
}: {
  label: string;
  value: number;
  /* Implementation note. */
  onClick?: () => void;
}) {
  const body = (
    <>
      <div className="text-sm font-semibold tabular-nums">{value}</div>
      <div className="mt-0.5 text-xs text-muted-foreground">{label}</div>
    </>
  );
  const baseClass = cn(
    "rounded-lg border border-border-subtle bg-background/70",
    "px-2 py-1.5 text-center",
  );
  if (!onClick) {
    return <div className={baseClass}>{body}</div>;
  }
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        baseClass,
        "transition-colors cursor-pointer",
        "hover:border-primary/40 hover:bg-primary/[0.04]",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30",
      )}
      title="点击查看详情"
    >
      {body}
    </button>
  );
}
