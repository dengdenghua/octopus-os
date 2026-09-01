export interface WikiStatus {
  exists: boolean;
  status: "current" | "outdated" | "not_generated" | string;
  generated_at?: string | null;
  files_analyzed: number;
  generated_files: string[];
  changes_pending?: number | null;
  root?: string | null;
  consistent?: boolean;
  schema?: string | null;
  plugin_id?: string | null;
  plugin_version?: string | null;
  generator_version?: string | null;
  project_id?: string | null;
}

export interface WikiDocEntry {
  path: string;
  name: string;
  size: number;
}

export interface WikiDocList {
  docs: WikiDocEntry[];
  lang: string;
}

export interface WikiDocument {
  path: string;
  content: string;
  size: number;
  meta?: Record<string, unknown>;
}

export interface WikiUpdateResult {
  status?: string;
  ok?: boolean;
  updated_files?: number;
  error?: string | null;
}
