import { jsonAuthHeaders, authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

export const NARRATIVE_STUDIO_API_BASE =
  "/api/plugins/narrative-studio" as const;

export type CanonStatus = "candidate";

export interface NarrativeProject {
  id: string;
  title: string;
  premise: string;
  language: string;
  default_branch_id?: string;
  canon_policy: string;
  canon_status: CanonStatus;
  governance?: {
    quorum: number;
    approval_ratio: number;
  };
  created_at?: string;
  updated_at?: string;
}

export interface NarrativeCounts {
  world_packs: number;
  branches: number;
  chapters: number;
  scenes: number;
  facts: number;
  state_changes: number;
}

export interface NarrativeWorldPack {
  id: string;
  name: string;
  summary: string;
  resources: unknown[];
  metadata?: Record<string, unknown>;
  canon_status: CanonStatus;
}

export interface NarrativeBranch {
  id: string;
  name: string;
  base_branch_id?: string;
  purpose: string;
  canon_status: CanonStatus;
}

export interface NarrativeChapter {
  id: string;
  branch_id: string;
  ordinal: number;
  title: string;
  summary: string;
  body: string;
  canon_status: CanonStatus;
}

export interface NarrativeScene {
  id: string;
  chapter_id: string;
  branch_id: string;
  ordinal: number;
  title: string;
  goal: string;
  conflict: string;
  outcome: string;
  pov_character_id?: string;
  body: string;
  canon_status: CanonStatus;
}

export interface NarrativeFact {
  id: string;
  branch_id?: string;
  subject: string;
  predicate: string;
  object: string;
  scope: string;
  source_refs: string[];
  canon_status: CanonStatus;
}

export interface NarrativeStateChange {
  id: string;
  branch_id: string;
  chapter_id?: string;
  scene_id?: string;
  entity_id: string;
  field: string;
  before?: unknown;
  after?: unknown;
  reason: string;
  canon_status: CanonStatus;
}

export interface NarrativeArc {
  id: string;
  title: string;
  summary: string;
  status: string;
  chapter_ids: string[];
  metadata?: Record<string, unknown>;
}

export interface NarrativeEntity {
  id: string;
  name: string;
  kind: string;
  description: string;
  attributes: Record<string, unknown>;
  source_refs: string[];
}

export interface NarrativeRelationship {
  id: string;
  source_entity_id: string;
  target_entity_id: string;
  kind: string;
  description: string;
  status: string;
}

export interface NarrativeForeshadow {
  id: string;
  title: string;
  setup: string;
  payoff: string;
  status: string;
  source_refs: string[];
  target_chapter_id?: string;
}

export interface NarrativeContextSource {
  id: string;
  kind: string;
  title: string;
  reference: string;
  excerpt: string;
  tokens: number;
  char_count: number;
  truncated: boolean;
  included: boolean;
}

export interface NarrativeContextPack {
  id: string;
  project_id: string;
  branch_id?: string;
  chapter_id?: string;
  scene_id?: string;
  token_budget: number;
  token_count: number;
  max_chars: number;
  max_items: number;
  total_chars: number;
  sources: NarrativeContextSource[];
  omitted_count: number;
  created_at?: string;
}

export type NarrativePipelineStageStatus =
  | "pending"
  | "submitted"
  | "ready"
  | "running"
  | "completed"
  | "blocked"
  | "failed"
  | "skipped";

export interface NarrativePipelineStage {
  id: string;
  name: string;
  status: NarrativePipelineStageStatus;
  ordinal: number;
  output?: unknown;
  error?: string;
  actor?: string;
  updated_at?: string;
}

export interface NarrativePipelineRun {
  id: string;
  project_id: string;
  branch_id?: string;
  chapter_id?: string;
  scene_id?: string;
  status: string;
  stages: NarrativePipelineStage[];
  created_at?: string;
  updated_at?: string;
}

export interface NarrativeReviewVote {
  id: string;
  actor: string;
  decision: string;
  rationale: string;
  created_at?: string;
}

export interface NarrativeReviewRequest {
  id: string;
  project_id: string;
  target_type: string;
  target_id: string;
  revision: number;
  title: string;
  status: string;
  quorum_required: number;
  quorum_received: number;
  blockers: string[];
  blocking: boolean;
  approval_ratio: number;
  resolution?: unknown;
  votes: NarrativeReviewVote[];
  created_at?: string;
  updated_at?: string;
}

export interface NarrativeCanonCommit {
  id: string;
  review_request_id: string;
  target_type: string;
  target_id: string;
  status: string;
  actor: string;
  rationale: string;
  committed_at?: string;
}

export interface NarrativeExtensions {
  arcs: NarrativeArc[];
  entities: NarrativeEntity[];
  relationships: NarrativeRelationship[];
  foreshadows: NarrativeForeshadow[];
  contextPacks: NarrativeContextPack[];
  pipelineRuns: NarrativePipelineRun[];
  reviewRequests: NarrativeReviewRequest[];
  canonCommits: NarrativeCanonCommit[];
  warnings: string[];
}

export interface NarrativeStudioStatus {
  status: string;
  ready: boolean;
  plugin?: string;
  canon_policy?: string;
  version?: string;
  capabilities?: string[];
  mcp?: {
    enabled: boolean;
    endpoint: string;
    transport: string;
    auth?: string;
    tool_policy?: string;
    tools: string[];
  };
  packaged_skills?: Array<{
    name: string;
    description: string;
  }>;
}

export interface EchoImportResult {
  available: boolean;
  imported: boolean;
  reason?: string;
  source_root: string;
  world_pack?: NarrativeWorldPack;
  inventory: {
    bible: number;
    characters: number;
    factions: number;
    locations: number;
    technologies: number;
    relationships: number;
    timeline: number;
    stories: number;
    total_files: number;
  };
  truncated: boolean;
  skipped_oversize: number;
}

export interface NarrativeWorkspace {
  project: NarrativeProject;
  counts: NarrativeCounts;
  worldPacks: NarrativeWorldPack[];
  branches: NarrativeBranch[];
  chapters: NarrativeChapter[];
  scenes: NarrativeScene[];
  facts: NarrativeFact[];
  stateChanges: NarrativeStateChange[];
}

export interface CreateProjectInput {
  id?: string;
  title: string;
  premise?: string;
  language?: string;
}

export interface CreateBranchInput {
  id?: string;
  name: string;
  base_branch_id?: string;
  purpose?: string;
}

export interface CreateChapterInput {
  id?: string;
  branch_id: string;
  ordinal: number;
  title: string;
  summary?: string;
  body?: string;
}

export interface UpdateChapterInput {
  ordinal?: number;
  title?: string;
  summary?: string;
  body?: string;
}

export interface CreateSceneInput {
  id?: string;
  branch_id: string;
  ordinal: number;
  title: string;
  goal?: string;
  conflict?: string;
  outcome?: string;
  pov_character_id?: string;
  body?: string;
}

export interface UpdateSceneInput {
  ordinal?: number;
  title?: string;
  goal?: string;
  conflict?: string;
  outcome?: string;
  pov_character_id?: string;
  body?: string;
}

export interface CreateContextPackInput {
  branch_id?: string;
  chapter_id?: string;
  scene_id?: string;
  token_budget?: number;
  max_chars?: number;
  max_items?: number;
  query?: string;
}

export interface CreatePipelineRunInput {
  branch_id?: string;
  chapter_id?: string;
  scene_id?: string;
  context_pack_id?: string;
  objective?: string;
}

export interface SubmitPipelineStageInput {
  actor: string;
  output?: unknown;
  notes?: string;
}

export interface CreateReviewRequestInput {
  target_type: string;
  target_id: string;
  revision?: number;
  branch_id?: string;
  chapter_id?: string;
  title?: string;
  summary?: string;
  blocking?: boolean;
  requested_by?: string;
}

export interface ReviewVoteInput {
  actor: string;
  decision: "approve" | "reject" | "abstain";
  rationale?: string;
}

export interface CanonCommitInput {
  actor: string;
  rationale: string;
  confirm: true;
}

export class NarrativeApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "NarrativeApiError";
    this.status = status;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function asRatio(value: unknown, fallback = 0): number {
  const number = asNumber(value, fallback);
  return Math.max(0, Math.min(number > 1 ? number / 100 : number, 1));
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function candidateStatus(_value: unknown): CanonStatus {
  return "candidate";
}

function recordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function unwrapItems(
  value: unknown,
  aliases: string[],
): Record<string, unknown>[] {
  if (Array.isArray(value)) return recordArray(value);
  if (!isRecord(value)) return [];
  const keys = ["items", ...aliases];
  for (const key of keys) {
    if (Array.isArray(value[key])) return recordArray(value[key]);
  }
  return [];
}

function normalizeProject(value: Record<string, unknown>): NarrativeProject {
  const governance = isRecord(value.governance) ? value.governance : undefined;
  return {
    id: asString(value.id),
    title: asString(value.title, "未命名叙事项目"),
    premise: asString(value.premise),
    language: asString(value.language, "zh-CN"),
    default_branch_id: optionalString(value.default_branch_id),
    canon_policy: asString(value.canon_policy, "candidate_only"),
    canon_status: candidateStatus(value.canon_status),
    governance: governance
      ? {
          quorum: asNumber(
            governance.quorum,
            asNumber(
              governance.review_quorum,
              asNumber(governance.required_votes, 1),
            ),
          ),
          approval_ratio: asRatio(
            governance.approval_ratio,
            asRatio(governance.threshold, 1),
          ),
        }
      : undefined,
    created_at: optionalString(value.created_at),
    updated_at: optionalString(value.updated_at),
  };
}

function normalizeWorldPack(
  value: Record<string, unknown>,
): NarrativeWorldPack {
  return {
    id: asString(value.id),
    name: asString(value.name, "未命名世界资料"),
    summary: asString(value.summary),
    resources: Array.isArray(value.resources) ? value.resources : [],
    metadata: isRecord(value.metadata) ? value.metadata : undefined,
    canon_status: candidateStatus(value.canon_status),
  };
}

function normalizeBranch(value: Record<string, unknown>): NarrativeBranch {
  return {
    id: asString(value.id),
    name: asString(value.name, "未命名分支"),
    base_branch_id: optionalString(value.base_branch_id),
    purpose: asString(value.purpose),
    canon_status: candidateStatus(value.canon_status),
  };
}

function normalizeChapter(value: Record<string, unknown>): NarrativeChapter {
  return {
    id: asString(value.id),
    branch_id: asString(value.branch_id),
    ordinal: asNumber(value.ordinal, 1),
    title: asString(value.title, "未命名章节"),
    summary: asString(value.summary),
    body: asString(value.body),
    canon_status: candidateStatus(value.canon_status),
  };
}

function normalizeScene(
  value: Record<string, unknown>,
  chapterId = "",
): NarrativeScene {
  return {
    id: asString(value.id),
    chapter_id: asString(value.chapter_id, chapterId),
    branch_id: asString(value.branch_id),
    ordinal: asNumber(value.ordinal, 1),
    title: asString(value.title, "未命名场景"),
    goal: asString(value.goal),
    conflict: asString(value.conflict),
    outcome: asString(value.outcome),
    pov_character_id: optionalString(value.pov_character_id),
    body: asString(value.body),
    canon_status: candidateStatus(value.canon_status),
  };
}

function normalizeFact(value: Record<string, unknown>): NarrativeFact {
  return {
    id: asString(value.id),
    branch_id: optionalString(value.branch_id),
    subject: asString(value.subject),
    predicate: asString(value.predicate),
    object: asString(value.object),
    scope: asString(value.scope, "universe"),
    source_refs: Array.isArray(value.source_refs)
      ? value.source_refs.filter(
          (item): item is string => typeof item === "string",
        )
      : [],
    canon_status: candidateStatus(value.canon_status),
  };
}

function normalizeStateChange(
  value: Record<string, unknown>,
): NarrativeStateChange {
  return {
    id: asString(value.id),
    branch_id: asString(value.branch_id),
    chapter_id: optionalString(value.chapter_id),
    scene_id: optionalString(value.scene_id),
    entity_id: asString(value.entity_id),
    field: asString(value.field),
    before: value.before,
    after: value.after,
    reason: asString(value.reason),
    canon_status: candidateStatus(value.canon_status),
  };
}

function normalizeArc(value: Record<string, unknown>): NarrativeArc {
  return {
    id: asString(value.id),
    title: asString(value.title, asString(value.name, "未命名故事弧")),
    summary: asString(value.summary, asString(value.description)),
    status: asString(value.status, "candidate"),
    chapter_ids: stringArray(value.chapter_ids),
    metadata: isRecord(value.metadata) ? value.metadata : undefined,
  };
}

function normalizeEntity(value: Record<string, unknown>): NarrativeEntity {
  return {
    id: asString(value.id),
    name: asString(value.name, asString(value.title, "未命名实体")),
    kind: asString(
      value.kind,
      asString(value.type, asString(value.entity_type, "entity")),
    ),
    description: asString(value.description, asString(value.summary)),
    attributes: isRecord(value.attributes)
      ? value.attributes
      : isRecord(value.metadata)
        ? value.metadata
        : {},
    source_refs: stringArray(value.source_refs),
  };
}

function normalizeRelationship(
  value: Record<string, unknown>,
): NarrativeRelationship {
  return {
    id: asString(value.id),
    source_entity_id: asString(
      value.source_entity_id,
      asString(
        value.from_entity_id,
        asString(value.source_id, asString(value.source)),
      ),
    ),
    target_entity_id: asString(
      value.target_entity_id,
      asString(
        value.to_entity_id,
        asString(value.target_id, asString(value.target)),
      ),
    ),
    kind: asString(
      value.kind,
      asString(value.type, asString(value.relationship_type, "related")),
    ),
    description: asString(value.description, asString(value.summary)),
    status: asString(value.status, "active"),
  };
}

function normalizeForeshadow(
  value: Record<string, unknown>,
): NarrativeForeshadow {
  return {
    id: asString(value.id),
    title: asString(value.title, asString(value.name, "未命名伏笔")),
    setup: asString(value.setup, asString(value.description)),
    payoff: asString(
      value.payoff,
      asString(value.intended_payoff, asString(value.resolution)),
    ),
    status: asString(value.status, "open"),
    source_refs: stringArray(value.source_refs),
    target_chapter_id: optionalString(
      value.target_chapter_id ?? value.payoff_chapter_id,
    ),
  };
}

function normalizeContextSource(
  value: Record<string, unknown>,
): NarrativeContextSource {
  const charCount = asNumber(
    value.char_count,
    asString(value.content, asString(value.text)).length,
  );
  return {
    id: asString(value.id, asString(value.reference, asString(value.ref))),
    kind: asString(value.kind, asString(value.type, "source")),
    title: asString(
      value.title,
      asString(value.name, asString(value.reference, "未命名来源")),
    ),
    reference: asString(
      value.reference,
      asString(value.ref, asString(value.source_ref)),
    ),
    excerpt: asString(
      value.excerpt,
      asString(value.content, asString(value.text)),
    ),
    tokens: asNumber(
      value.tokens,
      asNumber(value.token_count, Math.ceil(charCount / 4)),
    ),
    char_count: charCount,
    truncated: value.truncated === true,
    included: value.included !== false && value.omitted !== true,
  };
}

function normalizeContextPack(
  value: Record<string, unknown>,
): NarrativeContextPack {
  const maxChars = asNumber(value.max_chars);
  const totalChars = asNumber(value.total_chars);
  const budget = asNumber(
    value.token_budget,
    asNumber(value.budget, asNumber(value.max_tokens, Math.ceil(maxChars / 4))),
  );
  const sources = unwrapItems(value.sources, ["references", "citations"]).map(
    normalizeContextSource,
  );
  return {
    id: asString(value.id),
    project_id: asString(value.project_id),
    branch_id: optionalString(value.branch_id),
    chapter_id: optionalString(value.chapter_id ?? value.target_chapter_id),
    scene_id: optionalString(value.scene_id),
    token_budget: budget,
    token_count: asNumber(
      value.token_count,
      asNumber(
        value.used_tokens,
        asNumber(
          value.estimated_tokens,
          asNumber(value.total_tokens, Math.ceil(totalChars / 4)),
        ),
      ),
    ),
    max_chars: maxChars,
    max_items: asNumber(value.max_items),
    total_chars: totalChars,
    sources,
    omitted_count: asNumber(
      value.omitted_count,
      sources.filter((source) => !source.included).length,
    ),
    created_at: optionalString(value.created_at),
  };
}

const PIPELINE_STATUSES = new Set<NarrativePipelineStageStatus>([
  "pending",
  "submitted",
  "ready",
  "running",
  "completed",
  "blocked",
  "failed",
  "skipped",
]);

function normalizePipelineStage(
  value: Record<string, unknown>,
): NarrativePipelineStage {
  const rawStatus = asString(value.status, "pending");
  const stageKey = asString(
    value.id,
    asString(value.stage_id, asString(value.key, asString(value.name))),
  );
  return {
    id: stageKey,
    name: asString(
      value.name,
      asString(value.label, asString(value.id, "stage")),
    ),
    status: PIPELINE_STATUSES.has(rawStatus as NarrativePipelineStageStatus)
      ? (rawStatus as NarrativePipelineStageStatus)
      : "pending",
    ordinal: asNumber(value.ordinal),
    output: value.output ?? value.result,
    error: optionalString(value.error),
    actor: optionalString(value.actor ?? value.submitted_by),
    updated_at: optionalString(value.updated_at),
  };
}

function normalizePipelineRun(
  value: Record<string, unknown>,
): NarrativePipelineRun {
  const stages = unwrapItems(value.stages, ["steps", "pipeline_stages"]).map(
    normalizePipelineStage,
  );
  return {
    id: asString(value.id),
    project_id: asString(value.project_id),
    branch_id: optionalString(value.branch_id),
    chapter_id: optionalString(value.chapter_id),
    scene_id: optionalString(value.scene_id),
    status: asString(value.status, "pending"),
    stages,
    created_at: optionalString(value.created_at),
    updated_at: optionalString(value.updated_at),
  };
}

function normalizeReviewVote(
  value: Record<string, unknown>,
): NarrativeReviewVote {
  return {
    id: asString(value.id),
    actor: asString(
      value.actor,
      asString(value.voter_id, asString(value.voter, "未知审核者")),
    ),
    decision: asString(value.decision, asString(value.vote, "abstain")),
    rationale: asString(value.rationale, asString(value.reason)),
    created_at: optionalString(value.created_at),
  };
}

function normalizeReviewRequest(
  value: Record<string, unknown>,
): NarrativeReviewRequest {
  const quorum = isRecord(value.quorum) ? value.quorum : {};
  const blockers = Array.isArray(value.blockers)
    ? value.blockers.map((item) =>
        typeof item === "string"
          ? item
          : isRecord(item)
            ? asString(
                item.message,
                asString(
                  item.reason,
                  asString(item.title, asString(item.code, asString(item.id))),
                ),
              )
            : String(item),
      )
    : Array.isArray(value.blocking)
      ? value.blocking.map(String)
      : [];
  const votes = unwrapItems(value.votes, ["review_votes"]).map(
    normalizeReviewVote,
  );
  const decidingVotes = votes.filter((vote) =>
    ["approve", "reject"].includes(vote.decision),
  );
  const inferredApprovalRatio = decidingVotes.length
    ? decidingVotes.filter((vote) => vote.decision === "approve").length /
      decidingVotes.length
    : 0;
  return {
    id: asString(value.id),
    project_id: asString(value.project_id),
    target_type: asString(value.target_type, "chapter"),
    target_id: asString(
      value.target_id,
      asString(value.target, asString(value.chapter_id)),
    ),
    revision: asNumber(value.revision, asNumber(value.target_revision, 1)),
    title: asString(value.title, "正典审核"),
    status: asString(value.status, "pending"),
    quorum_required: asNumber(
      value.quorum_required,
      asNumber(quorum.required, asNumber(value.required_votes, 1)),
    ),
    quorum_received: asNumber(
      value.quorum_received,
      asNumber(quorum.received, asNumber(value.approval_count, votes.length)),
    ),
    blockers:
      value.blocking === true && !blockers.filter(Boolean).length
        ? ["存在尚未解决的阻塞项"]
        : blockers.filter(Boolean),
    blocking: value.blocking === true || blockers.filter(Boolean).length > 0,
    approval_ratio: asRatio(
      value.approval_ratio,
      asRatio(quorum.approval_ratio, inferredApprovalRatio),
    ),
    resolution: value.resolution,
    votes,
    created_at: optionalString(value.created_at),
    updated_at: optionalString(value.updated_at),
  };
}

function normalizeCanonCommit(
  value: Record<string, unknown>,
): NarrativeCanonCommit {
  return {
    id: asString(value.id),
    review_request_id: asString(
      value.review_request_id,
      asString(value.review_id),
    ),
    target_type: asString(value.target_type),
    target_id: asString(value.target_id),
    status: asString(value.status, "committed"),
    actor: asString(value.actor, asString(value.committed_by)),
    rationale: asString(value.rationale, asString(value.message)),
    committed_at: optionalString(value.committed_at ?? value.created_at),
  };
}

function normalizeCounts(value: unknown): NarrativeCounts {
  const source = isRecord(value) ? value : {};
  return {
    world_packs: asNumber(source.world_packs),
    branches: asNumber(source.branches),
    chapters: asNumber(source.chapters),
    scenes: asNumber(source.scenes),
    facts: asNumber(source.facts),
    state_changes: asNumber(source.state_changes),
  };
}

async function responseError(response: Response): Promise<NarrativeApiError> {
  let detail = "";
  try {
    const payload: unknown = await response.json();
    if (isRecord(payload)) {
      detail = asString(payload.detail) || asString(payload.message);
    }
  } catch {
    // An empty/non-JSON error response still carries a useful HTTP status.
  }
  return new NarrativeApiError(
    detail || `叙事服务请求失败（${response.status}）`,
    response.status,
  );
}

async function narrativeRequest(
  path: string,
  init?: RequestInit,
): Promise<unknown> {
  const response = await fetch(
    `${getBackendBaseURL()}${NARRATIVE_STUDIO_API_BASE}${path}`,
    {
      ...init,
      headers: init?.body
        ? { ...jsonAuthHeaders(), ...init.headers }
        : { ...authHeaders(), ...init?.headers },
    },
  );
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<unknown>;
}

function entityFromPayload(
  payload: unknown,
  aliases: string[],
): Record<string, unknown> {
  if (!isRecord(payload)) return {};
  for (const alias of aliases) {
    const nested = payload[alias];
    if (isRecord(nested)) return nested;
  }
  return payload;
}

export async function getNarrativeStatus(
  signal?: AbortSignal,
): Promise<NarrativeStudioStatus> {
  const payload = await narrativeRequest("/status", { signal });
  const value = isRecord(payload) ? payload : {};
  const status = asString(value.status, "ready");
  return {
    status,
    ready:
      typeof value.ready === "boolean"
        ? value.ready
        : status === "ok" || status === "ready",
    plugin: optionalString(value.plugin),
    canon_policy: optionalString(value.canon_policy),
    version: optionalString(value.version),
    capabilities: stringArray(value.capabilities),
    mcp: isRecord(value.mcp)
      ? {
          enabled: value.mcp.enabled === true,
          endpoint: asString(value.mcp.endpoint),
          transport: asString(value.mcp.transport),
          auth: optionalString(value.mcp.auth),
          tool_policy: optionalString(value.mcp.tool_policy),
          tools: stringArray(value.mcp.tools),
        }
      : undefined,
    packaged_skills: Array.isArray(value.packaged_skills)
      ? value.packaged_skills
          .filter(isRecord)
          .map((skill) => ({
            name: asString(skill.name),
            description: asString(skill.description),
          }))
          .filter((skill) => skill.name)
      : undefined,
  };
}

export async function listNarrativeProjects(
  signal?: AbortSignal,
): Promise<NarrativeProject[]> {
  const payload = await narrativeRequest("/projects", { signal });
  return unwrapItems(payload, ["projects"]).map(normalizeProject);
}

export async function getNarrativeProject(
  projectId: string,
  signal?: AbortSignal,
): Promise<{ project: NarrativeProject; counts: NarrativeCounts }> {
  const payload = await narrativeRequest(
    `/projects/${encodeURIComponent(projectId)}`,
    { signal },
  );
  const root = isRecord(payload) ? payload : {};
  return {
    project: normalizeProject(entityFromPayload(payload, ["project"])),
    counts: normalizeCounts(root.counts),
  };
}

async function listProjectEntities<T>(
  projectId: string,
  collectionPath: string,
  aliases: string[],
  normalize: (value: Record<string, unknown>) => T,
  signal?: AbortSignal,
): Promise<T[]> {
  const payload = await narrativeRequest(
    `/projects/${encodeURIComponent(projectId)}/${collectionPath}`,
    { signal },
  );
  return unwrapItems(payload, aliases).map(normalize);
}

export function listWorldPacks(projectId: string, signal?: AbortSignal) {
  return listProjectEntities(
    projectId,
    "world-packs",
    ["world_packs"],
    normalizeWorldPack,
    signal,
  );
}

export function listBranches(projectId: string, signal?: AbortSignal) {
  return listProjectEntities(
    projectId,
    "branches",
    ["branches"],
    normalizeBranch,
    signal,
  );
}

export function listChapters(projectId: string, signal?: AbortSignal) {
  return listProjectEntities(
    projectId,
    "chapters",
    ["chapters"],
    normalizeChapter,
    signal,
  );
}

export async function listScenes(
  projectId: string,
  chapterId: string,
  signal?: AbortSignal,
): Promise<NarrativeScene[]> {
  const payload = await narrativeRequest(
    `/projects/${encodeURIComponent(projectId)}/chapters/${encodeURIComponent(chapterId)}/scenes`,
    { signal },
  );
  return unwrapItems(payload, ["scenes"]).map((value) =>
    normalizeScene(value, chapterId),
  );
}

export function listFacts(projectId: string, signal?: AbortSignal) {
  return listProjectEntities(
    projectId,
    "facts",
    ["facts"],
    normalizeFact,
    signal,
  );
}

export function listStateChanges(projectId: string, signal?: AbortSignal) {
  return listProjectEntities(
    projectId,
    "state-changes",
    ["state_changes"],
    normalizeStateChange,
    signal,
  );
}

export async function loadNarrativeWorkspace(
  projectId: string,
  signal?: AbortSignal,
): Promise<NarrativeWorkspace> {
  const [detail, worldPacks, branches, chapters, facts, stateChanges] =
    await Promise.all([
      getNarrativeProject(projectId, signal),
      listWorldPacks(projectId, signal),
      listBranches(projectId, signal),
      listChapters(projectId, signal),
      listFacts(projectId, signal),
      listStateChanges(projectId, signal),
    ]);
  const sceneGroups = await Promise.all(
    chapters.map((chapter) => listScenes(projectId, chapter.id, signal)),
  );
  return {
    ...detail,
    worldPacks,
    branches,
    chapters,
    scenes: sceneGroups.flat(),
    facts,
    stateChanges,
  };
}

async function createEntity<T>(
  projectId: string,
  path: string,
  body: object,
  aliases: string[],
  normalize: (value: Record<string, unknown>) => T,
): Promise<T> {
  const payload = await narrativeRequest(
    `/projects/${encodeURIComponent(projectId)}/${path}`,
    { method: "POST", body: JSON.stringify(body) },
  );
  return normalize(entityFromPayload(payload, aliases));
}

export async function createNarrativeProject(
  input: CreateProjectInput,
): Promise<NarrativeProject> {
  const payload = await narrativeRequest("/projects", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return normalizeProject(entityFromPayload(payload, ["project"]));
}

export function createBranch(projectId: string, input: CreateBranchInput) {
  return createEntity(
    projectId,
    "branches",
    input,
    ["branch"],
    normalizeBranch,
  );
}

export function createChapter(projectId: string, input: CreateChapterInput) {
  return createEntity(
    projectId,
    "chapters",
    input,
    ["chapter"],
    normalizeChapter,
  );
}

export async function updateChapter(
  projectId: string,
  chapterId: string,
  input: UpdateChapterInput,
): Promise<NarrativeChapter> {
  const payload = await narrativeRequest(
    `/projects/${encodeURIComponent(projectId)}/chapters/${encodeURIComponent(chapterId)}`,
    { method: "PUT", body: JSON.stringify(input) },
  );
  return normalizeChapter(entityFromPayload(payload, ["chapter"]));
}

export function createScene(
  projectId: string,
  chapterId: string,
  input: CreateSceneInput,
) {
  return createEntity(
    projectId,
    `chapters/${encodeURIComponent(chapterId)}/scenes`,
    input,
    ["scene"],
    (value) => normalizeScene(value, chapterId),
  );
}

export async function updateScene(
  projectId: string,
  chapterId: string,
  sceneId: string,
  input: UpdateSceneInput,
): Promise<NarrativeScene> {
  const payload = await narrativeRequest(
    `/projects/${encodeURIComponent(projectId)}/chapters/${encodeURIComponent(chapterId)}/scenes/${encodeURIComponent(sceneId)}`,
    { method: "PUT", body: JSON.stringify(input) },
  );
  return normalizeScene(entityFromPayload(payload, ["scene"]), chapterId);
}

export async function importEchoUniverse(
  projectId: string,
  input: { pack_name?: string; include_content?: boolean } = {},
): Promise<EchoImportResult> {
  const payload = await narrativeRequest(
    `/projects/${encodeURIComponent(projectId)}/imports/echo`,
    { method: "POST", body: JSON.stringify(input) },
  );
  const value = isRecord(payload) ? payload : {};
  const inventoryValue = isRecord(value.inventory) ? value.inventory : {};
  const packValue = isRecord(value.world_pack)
    ? normalizeWorldPack(value.world_pack)
    : undefined;
  return {
    available: value.available === true,
    imported: value.imported === true,
    reason: optionalString(value.reason),
    source_root: asString(value.source_root),
    world_pack: packValue,
    inventory: {
      bible: asNumber(inventoryValue.bible),
      characters: asNumber(inventoryValue.characters),
      factions: asNumber(inventoryValue.factions),
      locations: asNumber(inventoryValue.locations),
      technologies: asNumber(inventoryValue.technologies),
      relationships: asNumber(inventoryValue.relationships),
      timeline: asNumber(inventoryValue.timeline),
      stories: asNumber(inventoryValue.stories),
      total_files: asNumber(inventoryValue.total_files),
    },
    truncated: value.truncated === true,
    skipped_oversize: asNumber(value.skipped_oversize),
  };
}

export function listArcs(projectId: string, signal?: AbortSignal) {
  return listProjectEntities(projectId, "arcs", ["arcs"], normalizeArc, signal);
}

export function listEntities(projectId: string, signal?: AbortSignal) {
  return listProjectEntities(
    projectId,
    "entities",
    ["entities"],
    normalizeEntity,
    signal,
  );
}

export function listRelationships(projectId: string, signal?: AbortSignal) {
  return listProjectEntities(
    projectId,
    "relationships",
    ["relationships"],
    normalizeRelationship,
    signal,
  );
}

export function listForeshadows(projectId: string, signal?: AbortSignal) {
  return listProjectEntities(
    projectId,
    "foreshadows",
    ["foreshadows"],
    normalizeForeshadow,
    signal,
  );
}

export function listContextPacks(projectId: string, signal?: AbortSignal) {
  return listProjectEntities(
    projectId,
    "context-packs",
    ["context_packs", "packs"],
    normalizeContextPack,
    signal,
  );
}

export function createContextPack(
  projectId: string,
  input: CreateContextPackInput,
) {
  const body = {
    branch_id: input.branch_id,
    target_chapter_id: input.chapter_id,
    label: input.query || "章节创作上下文",
    max_chars: input.max_chars ?? (input.token_budget ?? 12_000) * 4,
    max_items: input.max_items ?? 48,
  };
  return createEntity(
    projectId,
    "context-packs",
    body,
    ["context_pack", "pack"],
    normalizeContextPack,
  );
}

export function listPipelineRuns(projectId: string, signal?: AbortSignal) {
  return listProjectEntities(
    projectId,
    "pipeline-runs",
    ["pipeline_runs", "runs"],
    normalizePipelineRun,
    signal,
  );
}

export function createPipelineRun(
  projectId: string,
  input: CreatePipelineRunInput,
) {
  const body = {
    branch_id: input.branch_id,
    chapter_id: input.chapter_id,
    context_pack_id: input.context_pack_id,
    goal: input.objective || "",
  };
  return createEntity(
    projectId,
    "pipeline-runs",
    body,
    ["pipeline_run", "run"],
    normalizePipelineRun,
  );
}

export async function submitPipelineStage(
  projectId: string,
  runId: string,
  stageId: string,
  input: SubmitPipelineStageInput,
): Promise<NarrativePipelineRun> {
  const payload = await narrativeRequest(
    `/projects/${encodeURIComponent(projectId)}/pipeline-runs/${encodeURIComponent(runId)}/stages/${encodeURIComponent(stageId)}/submit`,
    {
      method: "POST",
      body: JSON.stringify({
        output:
          typeof input.output === "string"
            ? input.output
            : JSON.stringify(input.output ?? { notes: input.notes || "" }),
        source_refs: [],
        submitted_by: input.actor,
      }),
    },
  );
  const root = entityFromPayload(payload, ["pipeline_run", "run"]);
  if (Array.isArray(root.stages)) return normalizePipelineRun(root);
  const responseRoot = isRecord(payload) ? payload : {};
  const nestedRun = responseRoot.pipeline_run ?? responseRoot.run;
  if (isRecord(nestedRun)) return normalizePipelineRun(nestedRun);
  return normalizePipelineRun(root);
}

export function listReviewRequests(projectId: string, signal?: AbortSignal) {
  return listProjectEntities(
    projectId,
    "review-requests",
    ["review_requests", "reviews"],
    normalizeReviewRequest,
    signal,
  );
}

export function createReviewRequest(
  projectId: string,
  input: CreateReviewRequestInput,
) {
  const body = {
    target_type: input.target_type,
    target_id: input.target_id,
    title: input.title || "候选内容正典审核",
    summary: input.summary || input.title || "请审核当前候选修订。",
    blocking: input.blocking ?? false,
    requested_by: input.requested_by || "human-editor",
  };
  return createEntity(
    projectId,
    "review-requests",
    body,
    ["review_request", "review"],
    normalizeReviewRequest,
  );
}

export async function voteReviewRequest(
  projectId: string,
  reviewId: string,
  input: ReviewVoteInput,
): Promise<NarrativeReviewRequest> {
  const payload = await narrativeRequest(
    `/projects/${encodeURIComponent(projectId)}/review-requests/${encodeURIComponent(reviewId)}/votes`,
    {
      method: "POST",
      body: JSON.stringify({
        voter_id: input.actor,
        decision: input.decision,
        rationale: input.rationale || "",
      }),
    },
  );
  return normalizeReviewRequest(
    entityFromPayload(payload, ["review_request", "review"]),
  );
}

export function listCanonCommits(projectId: string, signal?: AbortSignal) {
  return listProjectEntities(
    projectId,
    "canon-commits",
    ["canon_commits", "commits"],
    normalizeCanonCommit,
    signal,
  );
}

export async function commitCanonReview(
  projectId: string,
  reviewId: string,
  input: CanonCommitInput,
): Promise<NarrativeCanonCommit> {
  if (input.confirm !== true) {
    throw new NarrativeApiError("正典提交必须经过人工确认。", 400);
  }
  const payload = await narrativeRequest(
    `/projects/${encodeURIComponent(projectId)}/review-requests/${encodeURIComponent(reviewId)}/commit`,
    { method: "POST", body: JSON.stringify(input) },
  );
  return normalizeCanonCommit(
    entityFromPayload(payload, ["canon_commit", "commit"]),
  );
}

type ExtensionCollectionKey = Exclude<keyof NarrativeExtensions, "warnings">;

const EMPTY_EXTENSIONS: Omit<NarrativeExtensions, "warnings"> = {
  arcs: [],
  entities: [],
  relationships: [],
  foreshadows: [],
  contextPacks: [],
  pipelineRuns: [],
  reviewRequests: [],
  canonCommits: [],
};

export async function loadNarrativeExtensions(
  projectId: string,
  signal?: AbortSignal,
): Promise<NarrativeExtensions> {
  const requests: Array<readonly [ExtensionCollectionKey, Promise<unknown[]>]> =
    [
      ["arcs", listArcs(projectId, signal)],
      ["entities", listEntities(projectId, signal)],
      ["relationships", listRelationships(projectId, signal)],
      ["foreshadows", listForeshadows(projectId, signal)],
      ["contextPacks", listContextPacks(projectId, signal)],
      ["pipelineRuns", listPipelineRuns(projectId, signal)],
      ["reviewRequests", listReviewRequests(projectId, signal)],
      ["canonCommits", listCanonCommits(projectId, signal)],
    ];
  const settled = await Promise.allSettled(
    requests.map(([, request]) => request),
  );
  const output: NarrativeExtensions = {
    ...EMPTY_EXTENSIONS,
    warnings: [],
  };
  settled.forEach((result, index) => {
    const entry = requests[index];
    if (!entry) return;
    const key = entry[0];
    if (result.status === "fulfilled") {
      // Every collection is normalized before it reaches this aggregation layer.
      (output[key] as unknown[]) = result.value;
    } else if (
      result.reason instanceof DOMException &&
      result.reason.name === "AbortError"
    ) {
      return;
    } else {
      output.warnings.push(
        result.reason instanceof Error
          ? `${key}: ${result.reason.message}`
          : `${key}: 加载失败`,
      );
    }
  });
  return output;
}
