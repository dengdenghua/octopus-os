/**
 * Default panel registrations — the "register + done" demonstration.
 *
 * Registers one self-contained reference panel (`workbench.system-status`)
 * through the PanelManifest contract. A host renders it by looking up
 * `getPanel("workbench.system-status")` — no page import needed.
 */
import type { PanelProps } from "./panel-manifest";
import { definePanel, getPanel, registerPanel } from "./panel-manifest";

function SystemStatusPanel({ panel, context }: PanelProps) {
  return (
    <div
      data-testid="system-status-panel"
      className="rounded-md border p-3 text-sm"
    >
      <div className="font-medium">{panel.title}</div>
      <ul className="mt-2 space-y-1 text-muted-foreground">
        <li>panel: {panel.id}</li>
        <li>zone: {panel.zone}</li>
        <li>thread: {context.threadId ?? "—"}</li>
        <li>agent: {context.agentId ?? "—"}</li>
      </ul>
    </div>
  );
}

export function ensureDefaultPanels(): void {
  if (getPanel("workbench.system-status")) return;
  registerPanel(
    definePanel({
      id: "workbench.system-status",
      title: "System Status",
      zone: "workspace",
      description:
        "Reference PanelManifest template — self-contained and renderable.",
      subscribes: ["turn.started", "turn.completed"],
      dataSources: ["thread", "agent"],
      permission: "everyone",
      order: 0,
      component: SystemStatusPanel,
    }),
  );
}
