import { BotIcon, MonitorIcon } from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import type { WorkbenchRosterSeat } from "./helpers";
import { rosterSeatRoleLabel } from "./helpers";

export function RosterComputerPlaceholder({
  seat,
  onOpenMain,
}: {
  seat: WorkbenchRosterSeat;
  onOpenMain: () => void;
}) {
  const { t } = useI18n();
  const roleLabel = rosterSeatRoleLabel(seat, t);
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-auto w-full max-w-2xl px-5 py-5">
        <section className="border-b border-border-subtle pb-4">
          <div className="flex items-center gap-3">
            <div className="flex size-11 shrink-0 items-center justify-center overflow-hidden rounded-full border border-border bg-muted/30 text-xl">
              {seat.avatarUrl ? (
                <img
                  src={seat.avatarUrl}
                  alt={seat.name}
                  className="size-full object-cover"
                />
              ) : seat.icon?.trim() ? (
                <span aria-hidden="true">{seat.icon}</span>
              ) : (
                <BotIcon className="size-7 text-muted-foreground" />
              )}
            </div>
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-foreground">
                {seat.name}
              </div>
              <div className="mt-0.5 text-xs text-muted-foreground">
                {roleLabel}
              </div>
              <span className="mt-1.5 inline-flex items-center gap-1 rounded-full bg-success/10 px-2 py-0.5 text-xs font-medium text-success">
                <span className="size-1 rounded-full bg-success" />
                {t.agentWorkbenchPanel.dockStatusPresent}
              </span>
            </div>
          </div>
          <div className="mt-4 flex items-start gap-2.5 rounded-lg border border-dashed border-border-default bg-muted/10 px-3 py-3">
            <MonitorIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground/55" />
            <div>
              <div className="text-xs font-medium text-foreground">
                {t.agentWorkbenchPanel.noIndependentProcessActivity}
              </div>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {t.agentWorkbenchPanel.noIndependentProcessActivityDescription}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onOpenMain}
            className="mt-3 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            {t.agentWorkbenchPanel.switchToMainComputer}
          </button>
        </section>
      </div>
    </div>
  );
}
