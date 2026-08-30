import { ArrowUpRightIcon, Layers3Icon } from "lucide-react";
import { Link } from "react-router-dom";

import { workspacePresetForAgent } from "@/core/workspace/workspace-presets";

export function PersonaWorkbenchHome({
  personaId,
}: {
  personaId?: string | null;
}) {
  const preset = workspacePresetForAgent(personaId);

  return (
    <div className="persona-workbench-home flex min-h-0 flex-1 items-center justify-center overflow-y-auto bg-background p-6 text-center">
      <section
        aria-label={preset.workbenchLabel}
        className="flex w-full max-w-xs flex-col items-center"
      >
        <div className="flex size-11 items-center justify-center rounded-lg border border-border bg-muted/25 text-muted-foreground">
          <Layers3Icon className="size-5" strokeWidth={1.5} />
        </div>
        <h2 className="mt-3 text-sm font-medium text-foreground/90">
          {preset.workbenchLabel}
        </h2>
        <p className="mt-1 max-w-64 text-xs leading-5 text-muted-foreground/75">
          {preset.workbenchSummary}
        </p>

        <div
          className="mt-3 flex flex-wrap items-center justify-center gap-x-2 text-xs text-muted-foreground/65"
          aria-label="工作台能力"
        >
          {preset.workbenchLanes.map((lane, index) => (
            <span key={lane} className="inline-flex items-center gap-2">
              {index > 0 ? (
                <span aria-hidden="true" className="text-border-strong">
                  ·
                </span>
              ) : null}
              <span>{lane}</span>
            </span>
          ))}
        </div>

        {preset.primaryAction ? (
          <Link
            to={preset.primaryAction.to}
            className="mt-5 inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-background px-3 text-xs font-medium text-foreground transition-colors hover:bg-muted/45"
          >
            {preset.primaryAction.label}
            <ArrowUpRightIcon className="size-3.5 text-muted-foreground" />
          </Link>
        ) : null}
      </section>
    </div>
  );
}
