import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Responsive shell for every non-Echo realtime conversation header.
 *
 * The surrounding ChatPageLayout intentionally has a fixed-height, clipped
 * header. This component makes the prioritisation explicit before that clip:
 * the conversation title and collaboration actions stay visible, while
 * runtime and supporting utilities collapse at narrow chat-column widths.
 */
export function RealtimeGroupHeaderLayout({
  agentIdentity,
  title,
  projectStatus,
  runStatus,
  members,
  invite,
  workbench,
  secondaryActions,
  className,
}: {
  agentIdentity?: ReactNode;
  title: ReactNode;
  projectStatus?: ReactNode;
  runStatus?: ReactNode;
  members?: ReactNode;
  invite?: ReactNode;
  workbench?: ReactNode;
  secondaryActions?: ReactNode;
  className?: string;
}) {
  return (
    <div
      data-testid="realtime-group-header"
      data-header-layout="realtime"
      className={cn(
        "realtime-group-header @container/realtime-header flex min-w-0 flex-1 items-center gap-1",
        className,
      )}
    >
      <div className="realtime-group-header__identity flex min-w-0 flex-1 items-center gap-2 @max-[720px]/realtime-header:gap-1.5">
        {agentIdentity ? (
          <div
            className="realtime-group-header__agent min-w-0 shrink-0 @max-[720px]/realtime-header:hidden"
            data-header-priority="supporting"
          >
            {agentIdentity}
          </div>
        ) : null}
        <div
          className="realtime-group-header__title min-w-12 flex-1 overflow-hidden"
          data-header-priority="primary"
        >
          {title}
        </div>
        {projectStatus ? (
          <div
            className="realtime-group-header__project shrink-0"
            data-header-priority="primary"
          >
            {projectStatus}
          </div>
        ) : null}
        {runStatus ? (
          <div
            className="realtime-group-header__run shrink-0 @max-[880px]/realtime-header:hidden"
            data-header-priority="secondary"
          >
            {runStatus}
          </div>
        ) : null}
      </div>

      <div className="realtime-group-header__actions ml-auto flex shrink-0 items-center gap-1 @max-[420px]/realtime-header:gap-0.5">
        {members ? (
          <div
            className="realtime-group-header__members shrink-0"
            data-header-priority="primary"
          >
            {members}
          </div>
        ) : null}
        {invite ? (
          <div
            className="realtime-group-header__invite shrink-0"
            data-header-priority="primary"
          >
            {invite}
          </div>
        ) : null}
        {workbench ? (
          <div
            className="realtime-group-header__workbench shrink-0"
            data-header-priority="primary"
          >
            {workbench}
          </div>
        ) : null}
        {secondaryActions ? (
          <div
            className="realtime-group-header__secondary flex shrink-0 items-center gap-1 @max-[880px]/realtime-header:hidden"
            data-header-priority="secondary"
          >
            {secondaryActions}
          </div>
        ) : null}
      </div>
    </div>
  );
}
