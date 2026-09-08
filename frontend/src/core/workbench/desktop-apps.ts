import { WORKBENCH_BUILTIN_APPS } from "./apps";

/** Launchers use installation availability, never sidebar visibility. */
export function availableDesktopWorkbenchApps(
  availability: ReadonlyMap<string, boolean> | null,
) {
  return WORKBENCH_BUILTIN_APPS.filter(
    (app) =>
      app.delivery === "core" || availability?.get(app.moduleId) === true,
  );
}
