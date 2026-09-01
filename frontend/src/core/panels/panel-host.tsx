/**
 * PanelHost — the consumption primitive of the PanelManifest contract.
 *
 * A host renders every registered panel in a zone. Pages stop hard-coding
 * imports: they drop `<PanelHost zone="workbench" context={...} />` and the
 * registry decides what appears — so a new panel is "register + done".
 *
 * Empty zones render nothing (no placeholder), so hosts can be added to a
 * page before any panel exists without visual churn.
 */
import type { ReactNode } from "react";

import type { PanelContext, PanelZone } from "./panel-manifest";
import { usePanels } from "./use-panels";

export interface PanelHostProps {
  zone: PanelZone;
  context?: PanelContext;
  className?: string;
  /** Optional header renderer; default shows the panel title. */
  renderHeader?: (panelTitle: string) => ReactNode;
}

export function PanelHost({
  zone,
  context = {},
  className,
  renderHeader,
}: PanelHostProps) {
  const panels = usePanels({ zone });
  if (panels.length === 0) return null;

  return (
    <div className={className} data-testid={`panel-host-${zone}`}>
      {panels.map((panel) => {
        const Component = panel.component;
        return (
          <section key={panel.id} data-testid={`panel-${panel.id}`}>
            {renderHeader ? (
              renderHeader(panel.title)
            ) : (
              <h3 className="text-sm font-semibold">{panel.title}</h3>
            )}
            <Component panel={panel} context={context} />
          </section>
        );
      })}
    </div>
  );
}
