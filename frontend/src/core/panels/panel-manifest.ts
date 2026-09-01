/**
 * PanelManifest — the frontend block contract (composition layer, P3).
 *
 * Design doc: `docs/architecture/blocks.md` §2 (`widget` block).
 *
 * A "panel" is the frontend equivalent of a backend block: a self-contained
 * workbench surface that declares where it mounts (`zone`), what it
 * subscribes to (`subscribes`) and reads (`dataSources`), and who may see it
 * (`permission`). New panels become "register + done" — host pages read the
 * registry instead of hard-coding imports, so adding a panel never edits an
 * existing page.
 *
 * This module is deliberately framework-minimal: a plain registry with a
 * subscription channel. The React hook (`use-panels.ts`) and the default
 * registrations (`default-panels.ts`) live next to it.
 */
import type { ComponentType } from "react";

export type PanelZone = "workspace" | "workbench" | "settings" | "global";
export type PanelPermission = "everyone" | "owner" | "admin";

/** Ambient context a host may provide to a panel (stable, optional). */
export interface PanelContext {
  threadId?: string | null;
  turnIndex?: number | null;
  agentId?: string | null;
}

export interface PanelProps {
  panel: PanelManifest;
  context: PanelContext;
}

export interface PanelManifest {
  /** Stable id, e.g. `workbench.system-status`. */
  id: string;
  title: string;
  zone: PanelZone;
  /** Rendered surface. Prefer a small, self-contained component. */
  component: ComponentType<PanelProps>;
  /** Event types the panel subscribes to (observability contract). */
  subscribes?: string[];
  /** Data sources the panel reads (contract, not enforcement). */
  dataSources?: string[];
  permission?: PanelPermission;
  order?: number;
  description?: string;
}

export function definePanel(manifest: PanelManifest): PanelManifest {
  return manifest;
}

type Listener = () => void;

const registry = new Map<string, PanelManifest>();
const listeners = new Set<Listener>();
let version = 0;

function emitChange(): void {
  version += 1;
  for (const listener of listeners) listener();
}

/** Monotonic registry version — the stable `useSyncExternalStore` snapshot. */
export function getPanelVersion(): number {
  return version;
}

export function subscribePanels(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function registerPanel(manifest: PanelManifest): void {
  if (registry.has(manifest.id)) {
    throw new Error(`duplicate panel id: ${manifest.id}`);
  }
  registry.set(manifest.id, manifest);
  emitChange();
}

export function getPanel(id: string): PanelManifest | undefined {
  return registry.get(id);
}

export function listPanels(filter?: {
  zone?: PanelZone;
  permission?: PanelPermission;
}): PanelManifest[] {
  const panels = [...registry.values()].filter((panel) => {
    if (filter?.zone && panel.zone !== filter.zone) return false;
    if (filter?.permission && panel.permission !== filter.permission)
      return false;
    return true;
  });
  return panels.sort(
    (a, b) => (a.order ?? 0) - (b.order ?? 0) || a.id.localeCompare(b.id),
  );
}

export function resetPanelsForTests(): void {
  registry.clear();
  emitChange();
}
