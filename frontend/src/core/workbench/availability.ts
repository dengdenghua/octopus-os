import { useEffect } from "react";
import { currentActorId } from "@/core/auth/api";

import {
  type CloudInstalledStatus,
  fetchCloudInstalled,
  fetchRuntimePluginStatuses,
  type RuntimePluginStatus,
} from "@/core/agents/agent-world-api";
import { setModuleAvailabilitySnapshot } from "@/core/modules/enabled-modules";
import { swallow } from "@/core/utils/log";

import { WORKBENCH_BUILTIN_APPS } from "./apps";

let revision = 0;
let inFlight: Promise<Record<string, boolean>> | null = null;
let snapshotInFlight: Promise<WorkbenchAvailabilitySnapshot> | null = null;
let inFlightActor: string | null = null;
let snapshotActor: string | null = null;

interface WorkbenchAvailabilitySnapshot {
  actor?: string;
  installed: CloudInstalledStatus;
  runtimeStatuses: ReadonlyMap<string, RuntimePluginStatus>;
}

/** Share the boot-time inventory between the workspace shell and app center. */
export function loadWorkbenchAvailabilitySnapshot(): Promise<WorkbenchAvailabilitySnapshot> {
  const actor = currentActorId();
  if (snapshotInFlight && snapshotActor === actor) return snapshotInFlight;
  snapshotActor = actor;
  const request = Promise.all([
    fetchCloudInstalled(),
    fetchRuntimePluginStatuses().catch((error) => {
      // Older backends may not expose the PluginHub inventory endpoint.
      // Their durable cloud package state remains a usable fallback.
      swallow(error);
      return new Map<string, RuntimePluginStatus>();
    }),
  ])
    .then(([installed, runtimeStatuses]) => ({
      installed,
      runtimeStatuses,
      actor,
    }))
    .finally(() => {
      if (snapshotInFlight === request) snapshotInFlight = null;
    });
  snapshotInFlight = request;
  return request;
}

/**
 * Reconcile mutable workbench packages with navigation surfaces. This is the
 * single source for direct-route, sidebar, desktop and Dock availability.
 */
export function syncWorkbenchAvailability(
  snapshot?: WorkbenchAvailabilitySnapshot,
): Promise<Record<string, boolean>> {
  const actor = currentActorId();
  if (snapshot?.actor && snapshot.actor !== actor) return Promise.resolve({});
  if (!snapshot && inFlight && inFlightActor === actor) return inFlight;
  const requestRevision = ++revision;
  const request = (async () => {
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
        // A stale runtime process must not resurrect an uninstalled or broken package.
        if (
          durableState &&
          (!durableState.installed ||
            !durableState.enabled ||
            ["broken", "incompatible"].includes(
              durableState.lifecycle_state ?? "",
            ))
        ) {
          availability[app.moduleId] = false;
          return;
        }
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

    if (requestRevision === revision && actor === currentActorId())
      setModuleAvailabilitySnapshot(availability);
    return availability;
  })()
    .catch((error) => {
      if (requestRevision === revision && actor === currentActorId()) {
        // Failed verification cannot leave previously available remote apps active.
        setModuleAvailabilitySnapshot(
          Object.fromEntries(
            WORKBENCH_BUILTIN_APPS.map((app) => [
              app.moduleId,
              app.delivery === "core",
            ]),
          ),
        );
      }
      throw error;
    })
    .finally(() => {
      if (inFlight === request) inFlight = null;
    });
  if (!snapshot) {
    inFlight = request;
    inFlightActor = actor;
  }
  return request;
}

export function useWorkbenchAvailabilitySync(): void {
  const actor = currentActorId();
  useEffect(() => {
    void syncWorkbenchAvailability().catch(swallow);
  }, [actor]);
}
