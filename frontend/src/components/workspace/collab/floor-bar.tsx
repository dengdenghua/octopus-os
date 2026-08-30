import { HandIcon, MegaphoneIcon, SkipForwardIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { useOptionalCollab } from "./collab-provider";

const TURN_POLICIES = new Set(["round_robin", "roll_call", "moderated"]);

/**
 * Compact floor indicator for turn-based rooms: shows whose turn it is,
 * lets a member raise their hand / pass, and lets the moderator grant the
 * floor from the raised-hands queue. Renders nothing for free/admin_only.
 */
export function FloorBar() {
  const { t } = useI18n();
  const collab = useOptionalCollab();
  if (!collab) return null;

  const { floor, currentUser, users, raiseHand, yieldFloor, grantFloor } =
    collab;
  if (!TURN_POLICIES.has(floor.speakerPolicy)) return null;

  const myId = currentUser?.id ?? "";
  const isMyTurn = floor.currentSpeakerId === myId;
  const isModerator = !!floor.moderatorId && floor.moderatorId === myId;
  const inQueue = floor.floorRequests.includes(myId);
  const nameFor = (id: string) => users.find((u) => u.id === id)?.name ?? id;

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border-subtle px-3 py-1.5 text-xs">
      <MegaphoneIcon className="size-3.5 text-muted-foreground" />
      <span
        className={cn(
          "font-medium",
          isMyTurn
            ? "text-success"
            : "text-muted-foreground",
        )}
      >
        {isMyTurn
          ? t.teamFloor.yourTurn
          : floor.currentSpeakerId
            ? `${nameFor(floor.currentSpeakerId)} · ${t.teamFloor.speaking}`
            : t.teamFloor.floorOpen}
      </span>

      <div className="ml-auto flex items-center gap-1.5">
        {isMyTurn ? (
          <Button
            size="sm"
            variant="ghost"
            className="h-6 gap-1 px-2 text-xs"
            onClick={() => yieldFloor()}
          >
            <SkipForwardIcon className="size-3" />
            {t.teamFloor.pass}
          </Button>
        ) : (
          floor.speakerPolicy === "moderated" && (
            <Button
              size="sm"
              variant="ghost"
              className="h-6 gap-1 px-2 text-xs"
              disabled={inQueue}
              onClick={() => raiseHand()}
            >
              <HandIcon className="size-3" />
              {inQueue ? t.teamFloor.handRaised : t.teamFloor.raiseHand}
            </Button>
          )
        )}
      </div>

      {isModerator && floor.floorRequests.length > 0 && (
        <div className="flex w-full flex-wrap items-center gap-1.5 pt-1">
          <span className="text-muted-foreground">
            {t.teamFloor.raisedHands}:
          </span>
          {floor.floorRequests.map((id) => (
            <Button
              key={id}
              size="sm"
              variant="outline"
              className="h-6 px-2 text-xs"
              onClick={() => grantFloor(id)}
              title={t.teamFloor.grantFloor}
            >
              {nameFor(id)}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}
