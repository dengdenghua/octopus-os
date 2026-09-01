/**
 * Remote workspace collaboration API client.
 *
 * Wraps the ``/api/workspaces`` endpoints exposed by
 * ``runtime/sensing/gateway/workspace_api_router.py``. The route is
 * gated by the ``ui.remote_workspace`` feature flag on the backend —
 * callers should also defensively handle 404 / 501 responses.
 */

import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

import type {
  AcquireLeaseParams,
  CreateWorkspaceParams,
  FileLease,
  Workspace,
  WorkspaceHealth,
  WorkspaceMember,
} from "./types";

const BASE = () => `${getBackendBaseURL()}/api/workspaces`;

async function assertOk(response: Response, label: string): Promise<void> {
  if (response.ok) return;
  const text = await response.text().catch(() => "");
  let detail = "";
  try {
    const parsed = JSON.parse(text) as { detail?: string };
    detail = parsed.detail ?? "";
  } catch {
    detail = text || response.statusText;
  }
  throw new Error(detail || `${label}: ${response.status}`);
}

function parseJson<T>(response: Response): Promise<T> {
  return response.json() as Promise<T>;
}

export async function listWorkspaces(): Promise<Workspace[]> {
  // Identity comes from the bearer token / HttpOnly cookie. A separately
  // persisted user id can drift from the JWT subject and cause a false 403.
  const res = await fetch(BASE(), {
    headers: authHeaders(),
  });
  await assertOk(res, "Failed to load workspaces");
  const data = await parseJson<Workspace[] | { workspaces: Workspace[] }>(res);
  return Array.isArray(data) ? data : (data.workspaces ?? []);
}

export async function getWorkspace(id: string): Promise<Workspace> {
  const res = await fetch(`${BASE()}/${encodeURIComponent(id)}`, {
    headers: authHeaders(),
  });
  await assertOk(res, "Failed to load workspace");
  return parseJson<Workspace>(res);
}

export async function createWorkspace(
  params: CreateWorkspaceParams,
): Promise<Workspace> {
  const res = await fetch(BASE(), {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(params),
  });
  await assertOk(res, "Failed to create workspace");
  return parseJson<Workspace>(res);
}

export async function deleteWorkspace(id: string): Promise<void> {
  const res = await fetch(`${BASE()}/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  await assertOk(res, "Failed to delete workspace");
}

export async function listMembers(
  workspaceId: string,
): Promise<WorkspaceMember[]> {
  const res = await fetch(
    `${BASE()}/${encodeURIComponent(workspaceId)}/members`,
    {
      headers: authHeaders(),
    },
  );
  await assertOk(res, "Failed to load workspace members");
  const data = await parseJson<
    WorkspaceMember[] | { members: WorkspaceMember[] }
  >(res);
  return Array.isArray(data) ? data : (data.members ?? []);
}

export async function addMember(
  workspaceId: string,
  memberId: string,
  role: string,
): Promise<void> {
  const res = await fetch(
    `${BASE()}/${encodeURIComponent(workspaceId)}/members`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ member_id: memberId, role }),
    },
  );
  await assertOk(res, "Failed to add workspace member");
}

export async function removeMember(
  workspaceId: string,
  memberId: string,
): Promise<void> {
  const res = await fetch(
    `${BASE()}/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(memberId)}`,
    { method: "DELETE", headers: authHeaders() },
  );
  await assertOk(res, "Failed to remove workspace member");
}

export async function acquireLease(
  workspaceId: string,
  params: AcquireLeaseParams,
): Promise<FileLease> {
  const res = await fetch(
    `${BASE()}/${encodeURIComponent(workspaceId)}/lease`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(params),
    },
  );
  await assertOk(res, "Failed to acquire file lease");
  return parseJson<FileLease>(res);
}

export async function releaseLease(
  workspaceId: string,
  leaseId: string,
): Promise<void> {
  const res = await fetch(
    `${BASE()}/${encodeURIComponent(workspaceId)}/lease/${encodeURIComponent(leaseId)}`,
    { method: "DELETE", headers: authHeaders() },
  );
  await assertOk(res, "Failed to release file lease");
}

export async function renewLease(
  workspaceId: string,
  leaseId: string,
  ttl?: number,
): Promise<FileLease> {
  const body: Record<string, unknown> = {};
  if (typeof ttl === "number") body.ttl_seconds = ttl;
  const res = await fetch(
    `${BASE()}/${encodeURIComponent(workspaceId)}/lease/${encodeURIComponent(leaseId)}/renew`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(body),
    },
  );
  await assertOk(res, "Failed to renew file lease");
  return parseJson<FileLease>(res);
}

export async function listLeases(workspaceId: string): Promise<FileLease[]> {
  const res = await fetch(
    `${BASE()}/${encodeURIComponent(workspaceId)}/leases`,
    { headers: authHeaders() },
  );
  await assertOk(res, "Failed to load file leases");
  const data = await parseJson<FileLease[] | { leases: FileLease[] }>(res);
  return Array.isArray(data) ? data : (data.leases ?? []);
}

export async function checkHealth(
  workspaceId: string,
): Promise<WorkspaceHealth> {
  const res = await fetch(
    `${BASE()}/${encodeURIComponent(workspaceId)}/health`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({}),
    },
  );
  await assertOk(res, "Workspace health check failed");
  return parseJson<WorkspaceHealth>(res);
}

// Re-export for callers that want a single import site.
export type {
  AcquireLeaseParams,
  CreateWorkspaceParams,
  FileLease,
  LeaseKind,
  MemberRole,
  MountType,
  Workspace,
  WorkspaceHealth,
  WorkspaceMember,
} from "./types";
