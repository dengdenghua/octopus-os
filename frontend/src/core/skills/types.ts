export interface SkillInfo {
  name: string;
  description: string;
  license?: string | null;
  enabled: boolean;
  category: string;
  affinity?: string[];
  group?: string | null;
  trusted_source?: string | null;
  has_tests?: boolean;
  kind?: "system" | "automation" | "domain";
  market_visibility?:
    | "market"
    | "internal"
    | "provider"
    | "duplicate"
    | "deprecated"
    | string;
  market_reason?: string | null;
  canonical_skill?: string | null;
}

export interface CustomSkillContent extends SkillInfo {
  content: string;
  history?: SkillHistoryEntry[];
}

export interface SkillHistoryEntry {
  version: number;
  content: string;
  timestamp: string;
}

export interface SkillInstallRequest {
  url?: string;
  name?: string;
  force?: boolean;
  thread_id?: string;
  path?: string;
}

export interface SkillInstallResponse {
  success: boolean;
  name: string;
  message: string;
  security_scan?: {
    passed: boolean;
    warnings: string[];
  };
}

export interface SkillUpdateRequest {
  enabled: boolean;
}

export interface CustomSkillUpdateRequest {
  content: string;
  description?: string;
  commit_message?: string;
}

export interface SkillRollbackRequest {
  version: number;
}

export interface SkillPerformance {
  name: string;
  usage_count: number;
  success_count: number;
  fail_count: number;
  avg_duration_ms: number;
  last_used_at: string;
}

// ---------------------------------------------------------------------------
// Skills Market types
// ---------------------------------------------------------------------------

export interface SkillParam {
  name: string;
  type: "string" | "number" | "boolean" | "select";
  description: string;
  default?: unknown;
  options?: string[];
}

export interface SkillExample {
  input: string;
  expected_behavior: string;
}

export interface SkillPackage {
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  category: string;
  tags: string[];
  system_prompt: string;
  tools_required: string[];
  parameters: SkillParam[];
  examples: SkillExample[];
  icon: string;
  downloads: number;
  rating: number;
  rating_count: number;
  is_builtin: boolean;
  is_installed: boolean;
  is_enabled: boolean;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface SkillMarketListResponse {
  skills: SkillPackage[];
  total: number;
  counts: {
    total: number;
    installed: number;
    builtin: number;
    enabled: number;
  };
}

export interface SkillMarketInstallRequest {
  source?: string;
  skill_data?: Record<string, unknown>;
}

export interface SkillMarketInstallResponse {
  success: boolean;
  skill: SkillPackage;
}

export interface SkillMarketRateRequest {
  rating: number;
  review_text?: string;
}

export interface SkillMarketCreateRequest {
  name: string;
  version?: string;
  description?: string;
  author?: string;
  category?: string;
  tags?: string[];
  system_prompt?: string;
  tools_required?: string[];
  parameters?: Record<string, unknown>[];
  examples?: Record<string, unknown>[];
  icon?: string;
}
