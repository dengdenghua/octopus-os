export interface Agent {
  name: string;
  display_name?: string | null;
  description: string;
  icon?: string | null;
  avatar_url?: string | null;
  visual_urls?: Record<string, string> | null;
  model: string | null;
  tool_groups: string[] | null;
  soul?: string | null;
  /** Backend-provided capability flags · see runtime/platform/scope.py
   *  and runtime/execution/agents/base.py for the authoritative list.
   *  Kept loose-typed so adding a flag server-side doesn't require a
   *  matching type migration on the client. */
  capabilities?: Record<string, unknown>;
  /** Immutable identity code; display name and profession may change. */
  identity_code?: string | null;
  identity_profile?: {
    code?: string;
    code_version?: number;
    immutable?: boolean;
    derived_for_legacy_profile?: boolean;
    personality_anchors?: {
      mode?: "fictional_archetype" | "creator_defined" | string;
      western_zodiac?: string | null;
      chinese_zodiac?: string | null;
      five_elements?: string[];
      bazi_archetype?: string | null;
      traits?: string[];
      note?: string;
    };
  };
  /* Implementation note. */
  budget?: {
    max_tokens?: number;
    max_usd?: number;
    max_iterations?: number;
  };
}

// ---------------------------------------------------------------------------
// Agent Market types
// ---------------------------------------------------------------------------

export type AgentWorldCategory =
  | "assistant"
  | "coder"
  | "researcher"
  | "creative"
  | "automation"
  | "specialist"
  | "financial";

export interface AgentWorldAgent {
  id: string;
  name: string;
  display_name: string;
  description: string;
  author: string;
  category: AgentWorldCategory;
  tags: string[];
  icon: string;
  avatar_url?: string;
  visual_urls?: Record<string, string> | null;
  version: string;
  downloads: number;
  rating: number;
  rating_count: number;
  is_featured: boolean;
  is_official: boolean;
  is_installed: boolean;
  /** Origin of the local binding, e.g. registry or a built-in persona. */
  source_kind?: string;
  created_at: string;
  model?: string | null;
  soul?: string | null;
  tool_groups?: string[] | null;
  extra_affinity?: string[];
  private_skills?: string[];
  capabilities?: Record<string, unknown>;
  character_profile?: {
    id?: string;
    name?: string;
    zh_name?: string;
    codename?: string;
    gender?: string;
    apparent_age?: string;
    epithet?: string;
    quote?: string;
    intro?: string;
    background?: string;
    personality?: string;
    temperament?: string;
    likes?: string[];
    dislikes?: string[];
    quirks?: string[];
    key_phrases?: string[];
    tone?: string[];
    appearance?: string[];
    interaction?: string[];
    current_state?: string[];
    visual_keywords?: string[];
    visual_assets?: Record<string, string>;
    emotion_list?: string[];
    emotion_videos?: Record<string, string[]>;
  } | null;
  key_skills?: string[];
  available_skills?: string[];
}

export interface AgentProfile {
  agent_name: string;
  display_name: string;
  avatar_url?: string;
  bio: string;
  category: string;
  tags: string[];
  stats: {
    total_conversations: number;
    total_messages: number;
    satisfaction_rate: number;
    avg_response_time_ms: number;
    tasks_completed: number;
  };
  capabilities: string[];
  last_active?: string;
}

export interface AgentMemory {
  id: string;
  memory_type: "fact" | "preference" | "learned_skill" | "relationship";
  content: string;
  confidence: number;
  created_at: string;
  access_count: number;
}

export interface AgentRating {
  user_id: string;
  rating: number;
  review_text?: string;
  created_at: string;
}

export interface AgentRelationship {
  agent_name: string;
  related_agent_name: string;
  relationship_type: string;
  strength: number;
  context?: string;
}

export interface AgentWorldListParams {
  category?: AgentWorldCategory;
  search?: string;
  featured?: boolean;
  sort_by?: "downloads" | "rating" | "created_at" | "name";
  sort_order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

export interface AgentWorldListResponse {
  agents: AgentWorldAgent[];
  total: number;
  page: number;
  page_size: number;
}

// ---------------------------------------------------------------------------
// Original types
// ---------------------------------------------------------------------------

export interface CreateAgentRequest {
  name: string;
  description?: string;
  model?: string | null;
  tool_groups?: string[] | null;
  soul?: string;
}

export interface UpdateAgentRequest {
  description?: string | null;
  model?: string | null;
  tool_groups?: string[] | null;
  soul?: string | null;
  capabilities?: Record<string, unknown> | null;
}
