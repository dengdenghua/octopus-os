export interface ContextSection {
  summary: string;
  updatedAt: string;
}

export interface UserContext {
  workContext: ContextSection;
  personalContext: ContextSection;
  topOfMind: ContextSection;
}

export interface HistoryContext {
  recentMonths: ContextSection;
  earlierContext: ContextSection;
  longTermBackground: ContextSection;
}

export interface MemoryFact {
  id: string;
  content: string;
  category: string;
  confidence: number;
  createdAt: string;
  source: string;
  scope?: "global" | "agent" | "project" | string;
  agent_id?: string;
  project?: string;
  sourceError?: string | null;
}

export type MemoryAssetType =
  | "conversation"
  | "atom"
  | "scenario"
  | "persona"
  | "skill"
  | "wiki"
  | "code_graph"
  | "media";

export type MemoryLayer = "L0" | "L1" | "L2" | "L3";
export type MemoryVisibility = "private" | "team" | "restricted" | "agent";
export type MemoryAssetStatus = "draft" | "active" | "archived" | "rejected";

export interface MemoryProvenance {
  source_type: string;
  source_id: string;
  source_uri: string;
  captured_at: string;
  parent_ids: string[];
  evidence: string;
}

export interface MemoryAsset {
  id: string;
  asset_type: MemoryAssetType;
  layer: MemoryLayer;
  title: string;
  content: string;
  owner: string;
  visibility: MemoryVisibility;
  status: MemoryAssetStatus;
  version: number;
  scope: string;
  confidence: number;
  created_at: string;
  updated_at: string;
  team_id: string;
  agent_id: string;
  project: string;
  allowed_users: string[];
  allowed_roles: string[];
  allowed_agents: string[];
  tags: string[];
  provenance: MemoryProvenance;
}

export interface MemoryAssetList {
  items: MemoryAsset[];
  count: number;
}

export interface MemoryAssetTrace {
  asset_id: string;
  layer: MemoryLayer;
  source: MemoryProvenance;
  parent_ids: string[];
  trace_complete: boolean;
}

export interface MemoryAssetQuery {
  q?: string;
  asset_type?: MemoryAssetType | "";
  layer?: MemoryLayer | "";
  status?: MemoryAssetStatus | "";
  visibility?: MemoryVisibility | "";
  team_id?: string;
  agent_id?: string;
  roles?: string;
  limit?: number;
}

export interface MemoryData {
  version: string;
  lastUpdated: string;
  user: UserContext;
  history: HistoryContext;
  facts: MemoryFact[];
}

export interface MemoryConfig {
  enabled: boolean;
  storage_path: string;
  auto_capture_enabled: boolean;
  debounce_seconds: number;
  max_facts: number;
  fact_confidence_threshold: number;
  injection_enabled: boolean;
  max_injection_tokens: number;
}

export type MemoryConfigPatch = Partial<
  Pick<
    MemoryConfig,
    | "enabled"
    | "auto_capture_enabled"
    | "injection_enabled"
    | "debounce_seconds"
    | "max_facts"
    | "fact_confidence_threshold"
    | "max_injection_tokens"
  >
>;

export interface MemorySearchResult {
  id: string;
  content: string;
  category: string;
  confidence: number;
  createdAt: string;
  source: string;
  scope?: "global" | "agent" | "project" | string;
  agent_id?: string;
  project?: string;
  relevance: number;
}

export interface FactCreateRequest {
  content: string;
  category?: string;
  confidence?: number;
  scope?: "global" | "agent" | "project" | string;
  agent_id?: string;
  project?: string;
  title?: string;
  tags?: string[];
  visibility?: MemoryVisibility;
  team_id?: string;
  allowed_users?: string[];
  allowed_roles?: string[];
  allowed_agents?: string[];
  provenance?: Partial<MemoryProvenance>;
}

export type MemoryFactInput = FactCreateRequest;

export interface FactPatchRequest {
  content?: string;
  category?: string;
  confidence?: number;
  scope?: "global" | "agent" | "project" | string;
  agent_id?: string;
  project?: string;
  title?: string;
  tags?: string[];
  asset_type?: MemoryAssetType;
  layer?: MemoryLayer;
  visibility?: MemoryVisibility;
  status?: MemoryAssetStatus;
  team_id?: string;
  allowed_users?: string[];
  allowed_roles?: string[];
  allowed_agents?: string[];
  provenance?: Partial<MemoryProvenance>;
}

export type MemoryFactPatchInput = FactPatchRequest;

export type UserMemory = MemoryData;
