export type SystemSettingsSurface = "native" | "echo";

/**
 * Native desktop shells own operating-system settings. Browser and NAS
 * desktops always use Echo's internal settings, regardless of whether the
 * appliance authentication gate is enabled.
 */
export function resolveSystemSettingsSurface(
  nativeAppsAvailable: boolean,
): SystemSettingsSurface {
  return nativeAppsAvailable ? "native" : "echo";
}
