import { UserIcon, UsersIcon, InboxIcon, CopyIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { toast } from "sonner";

import { swallow } from "@/core/utils/log";
import { copyTextToClipboard } from "@/core/clipboard";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { ErrorState, LoadingState } from "@/components/ui/state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  channelId: string;
  displayName: string;
}

interface PairingsPayload {
  channel_id: string;
  users: string[];
  groups: string[];
  pending: Array<Record<string, unknown>>;
  metrics: {
    pairings_count: number;
    group_count: number;
    pending_count: number;
  };
}

export function ChannelPairingsSheet({
  open,
  onOpenChange,
  channelId,
  displayName,
}: Props) {
  const { t } = useI18n();
  const [data, setData] = useState<PairingsPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPairings = useCallback(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const r = await fetch(
          `${getBackendBaseURL()}/api/channels/${encodeURIComponent(channelId)}/pairings`,
        );
        if (!r.ok) throw new Error(r.statusText);
        const body = (await r.json()) as PairingsPayload;
        if (!cancelled) setData(body);
      } catch (e) {
        swallow(e);
        if (!cancelled) {
          setError(
            e instanceof Error ? e.message : t.channelPairings.loadFailed,
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [channelId, t.channelPairings.loadFailed]);

  useEffect(() => {
    if (!open) return;
    const cleanup = loadPairings();
    return () => {
      cleanup();
    };
  }, [loadPairings, open]);

  const users = data?.users ?? [];
  const groups = data?.groups ?? [];
  const pending = data?.pending ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[75vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="text-base">
            {displayName} · {t.channelPairings.pairingDetails}
          </DialogTitle>
          <DialogDescription className="text-xs">
            {t.channelPairings.autoRegisterDesc}
            {t.channelPairings.metadataDesc}
          </DialogDescription>
        </DialogHeader>

        {loading && <LoadingState title={t.channelPairings.loading} />}
        {error && (
          <ErrorState
            title={t.channelPairings.loadFailed}
            detail={error}
            actionLabel={t.channelPairings.retry}
            onAction={() => {
              loadPairings();
            }}
          />
        )}

        {data && !loading && !error && (
          <Tabs defaultValue="users" className="flex-1 flex flex-col mt-2">
            <TabsList className="w-full grid grid-cols-3 h-9">
              <TabsTrigger value="users" className="text-xs gap-1.5">
                <UserIcon className="size-3.5" />
                {t.channelPairings.users}
                <span className="text-muted-foreground tabular-nums">
                  {users.length}
                </span>
              </TabsTrigger>
              <TabsTrigger value="groups" className="text-xs gap-1.5">
                <UsersIcon className="size-3.5" />
                {t.channelPairings.groups}
                <span className="text-muted-foreground tabular-nums">
                  {groups.length}
                </span>
              </TabsTrigger>
              <TabsTrigger value="pending" className="text-xs gap-1.5">
                <InboxIcon className="size-3.5" />
                {t.channelPairings.pending}
                <span className="text-muted-foreground tabular-nums">
                  {pending.length}
                </span>
              </TabsTrigger>
            </TabsList>

            <TabsContent
              value="users"
              className="flex-1 overflow-y-auto mt-3 outline-none"
            >
              <IdList ids={users} emptyHint={t.channelPairings.noUsers} />
            </TabsContent>
            <TabsContent
              value="groups"
              className="flex-1 overflow-y-auto mt-3 outline-none"
            >
              <IdList ids={groups} emptyHint={t.channelPairings.noGroups} />
            </TabsContent>
            <TabsContent
              value="pending"
              className="flex-1 overflow-y-auto mt-3 outline-none"
            >
              {pending.length === 0 ? (
                <EmptyState
                  icon={<InboxIcon />}
                  title={t.channelPairings.noPendingTitle}
                  hint={t.channelPairings.noPending}
                />
              ) : (
                <ul className="space-y-1">
                  {pending.map((p, i) => (
                    <li
                      key={i}
                      className="rounded-md border border-border-subtle bg-muted/30 px-3 py-2 text-xs font-mono break-all"
                    >
                      {JSON.stringify(p)}
                    </li>
                  ))}
                </ul>
              )}
            </TabsContent>
          </Tabs>
        )}
      </DialogContent>
    </Dialog>
  );
}

function IdList({ ids, emptyHint }: { ids: string[]; emptyHint: string }) {
  const { t } = useI18n();
  if (ids.length === 0) {
    return (
      <EmptyState
        icon={<UserIcon />}
        title={t.channelPairings.emptyListTitle}
        hint={emptyHint}
      />
    );
  }
  return (
    <ul className="space-y-1">
      {ids.map((id) => (
        <li
          key={id}
          className={cn(
            "group flex items-center gap-2 rounded-md border border-border-subtle",
            "bg-muted/30 px-2 py-1.5 hover:border-border-strong transition-colors",
          )}
        >
          <div className="flex size-6 items-center justify-center rounded-md bg-muted text-xs font-medium text-muted-foreground">
            {id.charAt(0).toUpperCase()}
          </div>
          <span className="flex-1 font-mono text-xs truncate">{id}</span>
          <button
            type="button"
            onClick={() => {
              void copyTextToClipboard(id)
                .then(() => toast.success(t.channelPairings.copiedId))
                .catch(() => toast.error(t.clipboard.failedToCopyToClipboard));
            }}
            className={cn(
              "size-6 rounded-md flex items-center justify-center",
              "text-muted-foreground hover:bg-muted hover:text-foreground",
              "opacity-0 group-hover:opacity-100 transition-opacity",
            )}
            title={t.channelPairings.copyId}
          >
            <CopyIcon className="size-3" />
          </button>
        </li>
      ))}
    </ul>
  );
}

function EmptyState({
  hint,
  icon,
  title,
}: {
  hint: string;
  icon: ReactNode;
  title: string;
}) {
  return (
    <Empty className="min-h-[var(--panel-height-sm)] rounded-lg">
      <EmptyHeader>
        <EmptyMedia variant="icon">{icon}</EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{hint}</EmptyDescription>
      </EmptyHeader>
    </Empty>
  );
}
