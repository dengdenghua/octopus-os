import { useEffect } from "react";

import {
  type CloudInstalledStatus,
  fetchCloudInstalled,
  fetchRuntimePluginStatuses,
  type RuntimePluginStatus,
} from "@/core/agents/agent-world-api";
import { setModuleAvailabilitySnapshot } from "@/core/modules/enabled-modules";
import { swallow } from "@/core/utils/log";

import { WORKBENCH_BUILTIN_APPS } from "./apps";

let inFlight: Promise<Record<string, boolean>> | null = null;
let snapshotInFlight: Promise<WorkbenchAvailabilitySnapshot> | null = null;

interface WorkbenchAvailabilitySnapshot {
  installed: CloudInstalledStatus;
  runtimeStatuses: ReadonlyMap<string, RuntimePluginStatus>;
}

/** Share the boot-time inventory between the workspace shell and app center. */
export function loadWorkbenchAvailabilitySnapshot(): Promise<WorkbenchAvailabilitySnapshot> {
  if (snapshotInFlight) return snapshotInFlight;
  snapshotInFlight = Promise.all([
    fetchCloudInstalled(),
    fetchRuntimePluginStatuses().catch((error) => {
      // Older backends may not expose the PluginHub inventory endpoint.
      // Their durable cloud package state remains a usable fallback.
      swallow(error);
      return new Map<string, RuntimePluginStatus>();
    }),
  ])
    .then(([installed, runtimeStatuses]) => ({ installed, runtimeStatuses }))
    .finally(() => {
      snapshotInFlight = null;
    });
  return snapshotInFlight;
}

/**
 * Reconcile mutable workbench packages with navigation surfaces. This is the
 * single source for direct-route, sidebar, desktop and Dock availability.
 */
export function syncWorkbenchAvailability(
  snapshot?: WorkbenchAvailabilitySnapshot,
): Promise<Record<string, boolean>> {
  if (inFlight) return inFlight;
  inFlight = (async () => {
    const { installed, runtimeStatuses } =
      snapshot ?? (await loadWorkbenchAvailabilitySnapshot());
    const installedSet = new Set(installed.plugins);
    const availability: Record<string, boolean> = {};

    await Promise.all(
      WORKBENCH_BUILTIN_APPS.map(async (app) => {
        if (app.delivery === "core") {
          availability[app.moduleId] = true;
          return;
        }
        const durableState = app.packageId
          ? installed.plugin_states?.[app.packageId]
          : undefined;
        const installedFallback = durableState
          ? Boolean(
              durableState.installed &&
              durableState.enabled &&
              durableState.lifecycle_state !== "broken" &&
              durableState.lifecycle_state !== "incompatible",
            )
          : app.packageId
            ? installedSet.has(app.packageId)
            : false;
        if (app.runtimePlugin) {
          const runtimeStatus = runtimeStatuses.get(app.runtimePlugin);
          availability[app.moduleId] = runtimeStatus
            ? Boolean(runtimeStatus.installed && runtimeStatus.enabled)
            : installedFallback;
          return;
        }
        availability[app.moduleId] = installedFallback;
      }),
    );

    setModuleAvailabilitySnapshot(availability);
    return availability;
  })().finally(() => {
    inFlight = null;
  });
  return inFlight;
}

export function useWorkbenchAvailabilitySync(): void {
  useEffect(() => {
    void syncWorkbenchAvailability().catch(swallow);
  }, []);
}
