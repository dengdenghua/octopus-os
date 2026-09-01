export interface Skill {
  name: string;
  description: string;
  category?: string;
  license?: string | null;
  enabled: boolean;
  /** Registration group from `_GROUP_REGISTRARS` · null for file-backed domain skills. */
  group?: string | null;
  /* Implementation note. */
  kind?: "system" | "automation" | "domain";
}
