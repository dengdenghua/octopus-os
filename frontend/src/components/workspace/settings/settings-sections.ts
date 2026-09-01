export const SETTINGS_SECTIONS = [
  "account",
  "subscription",
  "appearance",
  "conversation",
  "models",
  "memory",
  "notification",
  "tools",
  "browserAutomation",
  "desktopAutomation",
  "automationSecurity",
  "privacy",
  "observability",
  "about",
] as const;

export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];

const LEGACY_SETTINGS_SECTIONS: Record<string, SettingsSection> = {
  mcp: "tools",
  session: "privacy",
  conversation: "conversation",
  personalSpace: "privacy",
  automation: "browserAutomation",
  sandbox: "automationSecurity",
};

/** Resolve public and legacy destinations without loading the dialog bundle. */
export function normalizeSettingsSection(section?: string): SettingsSection {
  if (section && SETTINGS_SECTIONS.includes(section as SettingsSection)) {
    return section as SettingsSection;
  }
  return (section && LEGACY_SETTINGS_SECTIONS[section]) || "appearance";
}
