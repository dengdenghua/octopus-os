import {
  CheckCircle2Icon,
  FileCheck2Icon,
  Link2Icon,
  ListPlusIcon,
  MilestoneIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type {
  CoworkRoomEntityRef,
  CoworkRoomMessage,
  CoworkRoomSystemCard as CoworkRoomSystemCardData,
} from "@/core/cowork";
import { cn } from "@/lib/utils";

const ACTION_META: Record<
  string,
  { label: string; Icon: typeof CheckCircle2Icon }
> = {
  link_milestone: { label: "里程碑已关联", Icon: MilestoneIcon },
  create_item: { label: "项目事项已创建", Icon: ListPlusIcon },
  record_decision: { label: "项目决策已记录", Icon: CheckCircle2Icon },
  publish_artifact: { label: "项目资料已发布", Icon: FileCheck2Icon },
};

export function getCoworkRoomSystemCard(
  message: CoworkRoomMessage,
): CoworkRoomSystemCardData | null {
  const card = message.metadata?.system_card;
  return card && typeof card === "object" ? card : null;
}

export function isCoworkRoomSystemMessage(message: CoworkRoomMessage): boolean {
  return (
    message.metadata?.message_type === "system_card" ||
    getCoworkRoomSystemCard(message) != null
  );
}

export interface CoworkRoomSystemCardProps {
  card: CoworkRoomSystemCardData;
  entityRefs?: CoworkRoomEntityRef[];
  onEntityClick?: (entity: CoworkRoomEntityRef) => void;
  className?: string;
}

export function CoworkRoomSystemCard({
  card,
  entityRefs = [],
  onEntityClick,
  className,
}: CoworkRoomSystemCardProps) {
  const meta = ACTION_META[card.type] ?? {
    label: "项目动态",
    Icon: Link2Icon,
  };
  const target = card.target ?? entityRefs.at(-1);
  const { Icon } = meta;

  return (
    <article
      data-testid="cowork-system-card"
      data-density="compact"
      className={cn(
        "mx-auto w-full max-w-xl rounded-lg border border-border-subtle bg-muted/25 px-2.5 py-2 transition-colors hover:border-primary/20 hover:bg-primary/[0.035]",
        className,
      )}
    >
      <div className="flex min-w-0 items-center gap-2.5">
        <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon className="size-3.5" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="shrink-0 text-[10px] font-medium text-primary">
              {meta.label}
            </span>
            <span className="text-muted-foreground/45" aria-hidden="true">
              ·
            </span>
            <h4 className="min-w-0 flex-1 truncate text-xs font-semibold text-foreground">
              {card.title}
            </h4>
            {card.status ? (
              <Badge
                variant="outline"
                className="h-4 shrink-0 bg-background/80 px-1 text-[9px]"
              >
                {card.status}
              </Badge>
            ) : null}
          </div>
          {card.summary ? (
            <p className="mt-0.5 line-clamp-1 whitespace-pre-wrap text-[11px] leading-4 text-muted-foreground">
              {card.summary}
            </p>
          ) : null}
        </div>
        {target ? (
          onEntityClick ? (
            <button
              type="button"
              className="inline-flex h-7 max-w-32 shrink-0 items-center gap-1 rounded-md px-1.5 text-[10px] font-medium text-primary outline-none transition-colors hover:bg-primary/10 focus-visible:ring-2 focus-visible:ring-ring"
              onClick={() => onEntityClick(target)}
            >
              <Link2Icon className="size-3 shrink-0" aria-hidden="true" />
              <span className="truncate">{target.label || target.id}</span>
            </button>
          ) : (
            <span className="inline-flex max-w-32 shrink-0 items-center gap-1 truncate text-[10px] text-muted-foreground">
              <Link2Icon className="size-3 shrink-0" aria-hidden="true" />
              <span className="truncate">{target.label || target.id}</span>
            </span>
          )
        ) : null}
      </div>
    </article>
  );
}
