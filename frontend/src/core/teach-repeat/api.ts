/** API client for the Teach & Repeat system. */

import { getBackendBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";

import type {
  AdaptiveReplayRequest,
  AppendRecordingEventsResponse,
  RecordingEvent,
  RecordingStatus,
  ReplayRequest,
  ReplayResult,
  StartRecordingRequest,
  StartRecordingResponse,
  StopRecordingRequest,
  StopRecordingResponse,
  TemplateListResponse,
  TemplateUpdateRequest,
  WorkflowTemplate,
} from "./types";

const BASE = () => `${getBackendBaseURL()}/api/teach-repeat`;

// ---------------------------------------------------------------------------
// Recording
// ---------------------------------------------------------------------------

export async function startRecording(
  request: StartRecordingRequest,
): Promise<StartRecordingResponse> {
  const res = await fetch(`${BASE()}/record/start`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(
      err.detail ?? `Failed to start recording: ${res.statusText}`,
    );
  }
  return (await res.json()) as StartRecordingResponse;
}

export async function stopRecording(
  request: StopRecordingRequest,
): Promise<StopRecordingResponse> {
  const res = await fetch(`${BASE()}/record/stop`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(
      err.detail ?? `Failed to stop recording: ${res.statusText}`,
    );
  }
  return (await res.json()) as StopRecordingResponse;
}

export async function appendRecordingEvents(
  threadId: string,
  events: RecordingEvent[],
): Promise<AppendRecordingEventsResponse> {
  const res = await fetch(`${BASE()}/record/events`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ thread_id: threadId, events }),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(
      err.detail ?? `Failed to append recording events: ${res.statusText}`,
    );
  }
  return (await res.json()) as AppendRecordingEventsResponse;
}

export async function getRecordingStatus(
  threadId: string,
): Promise<RecordingStatus> {
  const res = await fetch(
    `${BASE()}/record/status?thread_id=${encodeURIComponent(threadId)}`,
    { headers: authHeaders() },
  );
  if (!res.ok)
    throw new Error(`Failed to get recording status: ${res.statusText}`);
  return (await res.json()) as RecordingStatus;
}

// ---------------------------------------------------------------------------
// Templates CRUD
// ---------------------------------------------------------------------------

export async function listTemplates(opts?: {
  skip?: number;
  limit?: number;
  search?: string;
  tag?: string;
}): Promise<TemplateListResponse> {
  const params = new URLSearchParams();
  if (opts?.skip !== undefined) params.set("skip", String(opts.skip));
  if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts?.search) params.set("search", opts.search);
  if (opts?.tag) params.set("tag", opts.tag);

  const qs = params.toString();
  const url = `${BASE()}/templates${qs ? `?${qs}` : ""}`;
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Failed to list templates: ${res.statusText}`);
  return (await res.json()) as TemplateListResponse;
}

export async function getTemplate(id: string): Promise<WorkflowTemplate> {
  const res = await fetch(`${BASE()}/templates/${encodeURIComponent(id)}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to get template: ${res.statusText}`);
  return (await res.json()) as WorkflowTemplate;
}

export async function updateTemplate(
  id: string,
  request: TemplateUpdateRequest,
): Promise<WorkflowTemplate> {
  const res = await fetch(`${BASE()}/templates/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(request),
  });
  if (!res.ok) throw new Error(`Failed to update template: ${res.statusText}`);
  return (await res.json()) as WorkflowTemplate;
}

export async function deleteTemplate(id: string): Promise<void> {
  const res = await fetch(`${BASE()}/templates/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to delete template: ${res.statusText}`);
}

export async function duplicateTemplate(id: string): Promise<WorkflowTemplate> {
  const res = await fetch(
    `${BASE()}/templates/${encodeURIComponent(id)}/duplicate`,
    { method: "POST", headers: authHeaders() },
  );
  if (!res.ok)
    throw new Error(`Failed to duplicate template: ${res.statusText}`);
  return (await res.json()) as WorkflowTemplate;
}

// ---------------------------------------------------------------------------
// Replay
// ---------------------------------------------------------------------------

export async function replayTemplate(
  id: string,
  request: ReplayRequest,
): Promise<ReplayResult> {
  const res = await fetch(
    `${BASE()}/templates/${encodeURIComponent(id)}/replay`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(request),
    },
  );
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(
      err.detail ?? `Failed to replay template: ${res.statusText}`,
    );
  }
  return (await res.json()) as ReplayResult;
}

export async function replayAdaptive(
  id: string,
  request: AdaptiveReplayRequest,
): Promise<ReplayResult> {
  const res = await fetch(
    `${BASE()}/templates/${encodeURIComponent(id)}/replay-adaptive`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(request),
    },
  );
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(
      err.detail ?? `Failed to run adaptive replay: ${res.statusText}`,
    );
  }
  return (await res.json()) as ReplayResult;
}
