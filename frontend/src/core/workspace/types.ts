/**
 * Remote workspace collaboration types.
 *
 * Mirrors the backend contract exposed by
 * ``runtime/sensing/gateway/workspace_api_router.py`` under the
 * ``ui.remote_workspace`` feature flag.
 */

export type MountType = "local" | "smb" | "nfs" | "webdav" | "sftp" | "s3";

export type MemberRole = "owner" | "editor" | "reviewer" | "viewer";

export type LeaseKind = "write" | "read" | "exclusive";

export interface Workspace {
  id: string;
  name: string;
  mount_type: MountType;
  mount_target: string;
  mount_options: Record<string, string> | null;
  owner_id: string;
  created_at: string;
}

export interface WorkspaceMember {
  workspace_id: string;
  member_id: string;
  role: MemberRole;
  added_at: string;
}

export interface FileLease {
  lease_id: string;
  workspace_id: string;
  file_path: string;
  holder_id: string;
  acquired_at: string;
  expires_at: string;
  kind: LeaseKind;
}

export interface CreateWorkspaceParams {
  name: string;
  mount_type: MountType;
  mount_target: string;
  mount_options: Record<string, string>;
  owner_id: string;
}

export interface AcquireLeaseParams {
  file_path: string;
  holder_id: string;
  ttl_seconds?: number;
  kind?: LeaseKind;
}

export interface WorkspaceHealth {
  healthy: boolean;
  detail?: string;
}
