import { swallow } from "@/core/utils/log";
import { jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import type { SubagentRouteDecision } from "@/core/parallel-agents/api";

export type ResearchDepth = "quick" | "standard" | "deep";

export type ResearchSourceProvider =
  | "web_search"
  | "fetch_url"
  | "uploaded_file"
  | "local_file"
  | "manual_material";

export type ResearchSourceKind =
  | "web"
  | "news"
  | "academic"
  | "company_site"
  | "ecommerce"
  | "social"
  | "forum"
  | "uploaded_file"
  | "provided_url"
  | "local_file";

export interface ResearchMaterial {
  id: string;
  kind: "file" | "url" | "text" | "site";
  title: string;
  path?: string | null;
  url?: string | null;
  text?: string | null;
  notes?: string | null;
}

export interface ResearchSource {
  id: string;
  kind: ResearchSourceKind;
  label: string;
  query_hint: string;
  provider: ResearchSourceProvider;
  query_templates: string[];
  site_filters: string[];
  freshness_days?: number | null;
  url?: string | null;
  enabled: boolean;
}

export interface ResearchEvidence {
  id: string;
  job_id?: string | null;
  step_id?: string | null;
  role_id?: string | null;
  title: string;
  url?: string | null;
  source_kind?: ResearchSourceKind | null;
  published_at?: string | null;
  quote_or_summary: string;
  claim: string;
  stance: "support" | "contradict" | "context";
  confidence: number;
}

export interface ResearchPrefetchLog {
  id: string;
  source_id?: string | null;
  source_kind?: ResearchSourceKind | null;
  source_label: string;
  provider: ResearchSourceProvider;
  action: "search" | "fetch" | "material" | "skip";
  query?: string | null;
  url?: string | null;
  status: "completed" | "failed" | "skipped";
  result_count: number;
  evidence_count: number;
  error?: string | null;
  created_at: string;
}

export interface ResearchRole {
  id: string;
  name: string;
  subagent_name: string;
  focus: string;
  deliverable: string;
  search_angles: string[];
}

export interface ResearchStep {
  id: string;
  title: string;
  role_id: string;
  status: "pending" | "running" | "completed" | "failed";
  source_ids: string[];
  expected_searches: number;
  prompt: string;
  route_decision?: SubagentRouteDecision | null;
}

export interface ResearchJob {
  job_id: string;
  thread_id?: string | null;
  lead_agent_name?: string | null;
  topic: string;
  status: "planned" | "running" | "completed" | "failed" | "cancelled";
  depth: ResearchDepth;
  locale: string;
  created_at: string;
  materials: ResearchMaterial[];
  sources: ResearchSource[];
  evidence: ResearchEvidence[];
  prefetch_logs: ResearchPrefetchLog[];
  route_decisions?: SubagentRouteDecision[];
  roles: ResearchRole[];
  steps: ResearchStep[];
  max_searches: number;
  dispatch_batch_id?: string | null;
  final_report_format: string;
  final_report?: string | null;
  completed_at?: string | null;
  memory_entry?: string | null;
  memory_written_at?: string | null;
  memory_path?: string | null;
}

export interface DeepResearchRequest {
  topic: string;
  thread_id?: string | null;
  lead_agent_name?: string | null;
  depth?: ResearchDepth;
  locale?: string;
  materials?: Partial<ResearchMaterial>[];
  urls?: string[];
  source_kinds?: ResearchSourceKind[];
  roles?: ResearchRole[];
  max_subagents?: number;
  max_searches?: number;
  include_thread_uploads?: boolean;
  prefetch_sources?: boolean;
  task_risk_level?: "low" | "medium" | "high" | "critical" | null;
  final_report_format?: "markdown" | "brief" | "slides_outline";
}

async function postResearch(
  path: string,
  body: DeepResearchRequest,
): Promise<ResearchJob> {
  const res = await fetch(`${getBackendBaseURL()}${path}`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(
      err.detail ?? `Deep research request failed: ${res.status}`,
    );
  }
  return (await res.json()) as ResearchJob;
}

export function planDeepResearch(
  body: DeepResearchRequest,
): Promise<ResearchJob> {
  return postResearch("/api/research/deep/plan", body);
}

export function startDeepResearch(
  body: DeepResearchRequest,
): Promise<ResearchJob> {
  return postResearch("/api/research/deep/start", body);
}

export async function fetchDeepResearchJob(
  jobId: string,
): Promise<ResearchJob | null> {
  try {
    const res = await fetch(
      `${getBackendBaseURL()}/api/research/deep/jobs/${jobId}`,
      {
        headers: jsonAuthHeaders(),
      },
    );
    if (!res.ok) return null;
    return (await res.json()) as ResearchJob;
  } catch (e) {
    swallow(e);
    return null;
  }
}

export async function listDeepResearchJobs(): Promise<ResearchJob[]> {
  try {
    const res = await fetch(`${getBackendBaseURL()}/api/research/deep/jobs`, {
      headers: jsonAuthHeaders(),
    });
    if (!res.ok) return [];
    const data = (await res.json()) as { jobs?: ResearchJob[] };
    return data.jobs ?? [];
  } catch (e) {
    swallow(e);
    return [];
  }
}
