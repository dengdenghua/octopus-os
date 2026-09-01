/**
 * React bindings over the PanelManifest registry.
 *
 * Follows the house store pattern (`timeline-linkage.ts`): the store keeps a
 * monotonic version, the hook subscribes via `useSyncExternalStore`, and the
 * derived list/panel is memoized on that version — so a plugin-like dynamic
 * registration lights up live without tearing.
 */
import { useMemo, useSyncExternalStore } from "react";

import { ensureDefaultPanels } from "./default-panels";
import {
  getPanel,
  getPanelVersion,
  listPanels,
  subscribePanels,
  type PanelManifest,
  type PanelPermission,
  type PanelZone,
} from "./panel-manifest";

ensureDefaultPanels();

export function usePanels(filter?: {
  zone?: PanelZone;
  permission?: PanelPermission;
}): PanelManifest[] {
  const version = useSyncExternalStore(
    subscribePanels,
    getPanelVersion,
    getPanelVersion,
  );
  return useMemo(
    () => listPanels(filter),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version, filter?.zone, filter?.permission],
  );
}

export function usePanel(id: string): PanelManifest | undefined {
  const version = useSyncExternalStore(
    subscribePanels,
    getPanelVersion,
    getPanelVersion,
  );
  return useMemo(
    () => getPanel(id),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version, id],
  );
}
