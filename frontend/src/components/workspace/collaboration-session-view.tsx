import {
  LinkIcon,
  ListTodoIcon,
  MessageSquareIcon,
  UsersIcon,
} from "lucide-react";

import { useCollabSession } from "@/core/cowork/hooks";
import type { CollaborationSession } from "@/core/cowork/types";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { PresenceDots } from "./cowork-collab-bar";
import { getTeamModeMeta, type TeamMode } from "./team-mode-picker";

type T = ReturnType<typeof useI18n>["t"];

function Stat({
  icon: Icon,
  value,
  label,
}: {
  icon: typeof UsersIcon;
  value: number | string;
  label: string;
}) {
  return (
    <span
      className="inline-flex items-center gap-1 text-xs text-muted-foreground"
      title={label}
    >
      <Icon className="size-3.5" aria-hidden="true" />
      <span className="font-medium text-foreground">{value}</span>
    </span>
  );
}

/** Pure render of one unified collaboration session — cowork thread (canonical)
 * plus its linked Team Room surface. Data in, no fetching. */
export function CollaborationSessionView({
  session,
  t,
}: {
  session: CollaborationSession;
  t: T;
}) {
  const teamModeMeta = getTeamModeMeta(t);
  const modeMeta = teamModeMeta[session.mode as TeamMode] ?? teamModeMeta.chat;
  const ModeIcon = modeMeta.icon;
  const hasTasks = session.tasks.length > 0;
  const hasOnlinePresence = session.presence.some((m) => m.online);
  const hasRoomActivity =
    session.room_id &&
    (session.room_messages.length > 0 || session.room_tasks.length > 0);
  if (!hasTasks && !hasOnlinePresence && !hasRoomActivity) return null;
  return (
    <div
      className="flex flex-col gap-2 rounded-lg border border-border-default bg-background/70 p-3"
      data-testid="collab-session-view"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span
          className="inline-flex items-center gap-1 rounded-full bg-muted/60 px-2 py-0.5 text-xs font-medium text-foreground"
          title={modeMeta.description}
        >
          <ModeIcon className="size-3.5" aria-hidden="true" />
          {modeMeta.label}
        </span>
        <Stat
          icon={UsersIcon}
          value={session.roster.length}
          label={t.coworkCollab.members}
        />
        <Stat
          icon={ListTodoIcon}
          value={session.tasks.length}
          label={t.coworkCollab.kindTask}
        />
      </div>

      {hasOnlinePresence && <PresenceDots members={session.presence} t={t} />}

      {hasRoomActivity && (
        <div
          className="flex min-w-0 flex-wrap items-center gap-2 border-t border-border-subtle pt-2"
          data-testid="collab-session-room"
        >
          <span
            className="inline-flex items-center gap-1 truncate text-xs text-muted-foreground"
            title={`${t.coworkCollab.linkedRoom}: ${session.room_id}`}
          >
            <LinkIcon className="size-3.5 shrink-0" aria-hidden="true" />
            <span className="truncate font-medium text-foreground">
              {session.room_id}
            </span>
          </span>
          <Stat
            icon={UsersIcon}
            value={session.room_participants.length}
            label={t.coworkCollab.members}
          />
          <Stat
            icon={MessageSquareIcon}
            value={session.room_messages.length}
            label={t.coworkCollab.kindRoomMessage}
          />
          <Stat
            icon={ListTodoIcon}
            value={session.room_tasks.length}
            label={t.coworkCollab.kindRoomTask}
          />
        </div>
      )}
    </div>
  );
}

/** Live panel: fetches the unified session and renders it.
 *
 * ``onlyWhenRoomLinked`` suppresses the panel until the cowork thread is linked
 * to a Team Room — used where a compact presence bar already covers the
 * cowork-only case, so this panel surfaces *only* the extra linked-room surface. */
export function CollaborationSessionPanel({
  threadId,
  className,
  onlyWhenRoomLinked = false,
}: {
  threadId: string;
  className?: string;
  onlyWhenRoomLinked?: boolean;
}) {
  const { t } = useI18n();
  const { data: session } = useCollabSession(threadId, { live: true });
  if (!session) return null;
  if (onlyWhenRoomLinked && !session.room_id) return null;
  return (
    <div className={cn("w-full", className)}>
      <CollaborationSessionView session={session} t={t} />
    </div>
  );
}
