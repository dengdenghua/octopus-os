import { getBackendBaseURL } from "../config";
import type { AgentThread } from "../threads";

export type WorkspaceOutputArea =
  | "output"
  | "stages"
  | "final"
  | "deploy"
  | "upload";

const WORKSPACE_OUTPUT_PREFIX = "workspace-output:";
const WORKSPACE_RESOURCE_PREFIX = "workspace-file:v1:";

function encodeWorkspaceResourceSegment(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function workspaceResourceId({
  threadId,
  area,
  relativePath,
}: {
  threadId: string;
  area: WorkspaceOutputArea;
  relativePath: string;
}): string {
  return `${WORKSPACE_RESOURCE_PREFIX}${encodeWorkspaceResourceSegment(threadId)}:${encodeWorkspaceResourceSegment(area)}:${encodeWorkspaceResourceSegment(relativePath.replace(/^\/+/, ""))}`;
}

export function parseWorkspaceResourceId(value: string): {
  threadId: string;
  area: WorkspaceOutputArea;
  relativePath: string;
} | null {
  if (!value.startsWith(WORKSPACE_RESOURCE_PREFIX)) return null;
  const parts = value.split(":");
  if (parts.length !== 5 || parts[0] !== "workspace-file" || parts[1] !== "v1")
    return null;
  const decode = (segment: string) => {
    if (!/^[A-Za-z0-9_-]+$/.test(segment)) return null;
    try {
      const padded =
        segment.replace(/-/g, "+").replace(/_/g, "/") +
        "=".repeat((4 - (segment.length % 4)) % 4);
      const binary = atob(padded);
      return new TextDecoder().decode(
        Uint8Array.from(binary, (char) => char.charCodeAt(0)),
      );
    } catch {
      return null;
    }
  };
  const threadId = decode(parts[2]!);
  const area = decode(parts[3]!);
  const relativePath = decode(parts[4]!);
  if (
    !threadId ||
    !area ||
    !relativePath ||
    !["output", "stages", "final", "deploy", "upload"].includes(area)
  )
    return null;
  if (
    relativePath
      .split("/")
      .some((part) => !part || part === "." || part === "..")
  )
    return null;
  return { threadId, area: area as WorkspaceOutputArea, relativePath };
}

export function workspaceOutputRef({
  area,
  relativePath,
}: {
  area: WorkspaceOutputArea;
  relativePath: string;
}) {
  return `${WORKSPACE_OUTPUT_PREFIX}${area}:${relativePath.replace(/^\/+/, "")}`;
}

export function parseWorkspaceOutputRef(
  filepath: string,
): { area: WorkspaceOutputArea; relativePath: string } | null {
  if (!filepath.startsWith(WORKSPACE_OUTPUT_PREFIX)) return null;
  const rest = filepath.slice(WORKSPACE_OUTPUT_PREFIX.length);
  const separator = rest.indexOf(":");
  if (separator <= 0) return null;
  const area = rest.slice(0, separator) as WorkspaceOutputArea;
  if (!["output", "stages", "final", "deploy", "upload"].includes(area)) {
    return null;
  }
  const relativePath = rest.slice(separator + 1).replace(/^\/+/, "");
  if (!relativePath) return null;
  return { area, relativePath };
}

export function artifactDisplayPath(filepath: string) {
  return (
    parseWorkspaceOutputRef(filepath)?.relativePath ??
    parseWorkspaceResourceId(filepath)?.relativePath ??
    filepath
  );
}

/**
 * Convert a runtime-emitted absolute workspace path into the stable artifact
 * reference understood by the per-thread outputs API.  Tool traces carry
 * absolute paths for auditability, while the preview endpoint intentionally
 * accepts only a scoped relative path.
 */
export function normalizeWorkspaceArtifactRef(
  filepath: string,
  threadId?: string,
) {
  const resource = parseWorkspaceResourceId(filepath);
  if (resource && (!threadId || resource.threadId === threadId)) {
    return workspaceOutputRef(resource);
  }
  if (!threadId || parseWorkspaceOutputRef(filepath)) return filepath;
  const normalizedPath = filepath.replaceAll("\\", "/");
  const relativeOutputMatch = normalizedPath.match(
    /^output\/(final|stages|deploy|upload)\/(.+)$/,
  );
  if (relativeOutputMatch) {
    return workspaceOutputRef({
      area: relativeOutputMatch[1]! as WorkspaceOutputArea,
      relativePath: relativeOutputMatch[2]!,
    });
  }
  const relativeOutputFileMatch = normalizedPath.match(/^output\/([^/]+)$/);
  if (relativeOutputFileMatch) {
    return workspaceOutputRef({
      area: "output",
      relativePath: relativeOutputFileMatch[1]!,
    });
  }
  const parts = normalizedPath.split("/").filter(Boolean);
  const workspaceIndex = parts.lastIndexOf("workspaces");
  if (workspaceIndex < 0 || parts[workspaceIndex + 1] !== threadId) {
    return filepath;
  }
  const root = workspaceIndex + 2;
  const area = parts[root];
  if (!area) return filepath;

  if (area === "output") {
    const nestedArea = parts[root + 1];
    if (nestedArea === "final" || nestedArea === "stages") {
      const relativePath = parts.slice(root + 2).join("/");
      return relativePath
        ? workspaceOutputRef({ area: nestedArea, relativePath })
        : filepath;
    }
  }
  if (["output", "deploy", "upload"].includes(area)) {
    const relativePath = parts.slice(root + 1).join("/");
    return relativePath
      ? workspaceOutputRef({ area: area as WorkspaceOutputArea, relativePath })
      : filepath;
  }
  return filepath;
}

export function isSourceFileArtifact(filepath: string, threadId: string) {
  return (
    /[/\\]/.test(filepath) &&
    !parseWorkspaceOutputRef(normalizeWorkspaceArtifactRef(filepath, threadId))
  );
}

export function urlOfArtifact({
  filepath,
  threadId,
  download = false,
  officePreview = false,
  officeFidelityPreview = false,
  isMock = false,
}: {
  filepath: string;
  threadId: string;
  download?: boolean;
  officePreview?: boolean;
  officeFidelityPreview?: boolean;
  isMock?: boolean;
}) {
  const workspaceOutput = parseWorkspaceOutputRef(
    normalizeWorkspaceArtifactRef(filepath, threadId),
  );
  if (workspaceOutput) {
    const params = new URLSearchParams();
    if (download) params.set("download", "true");
    if (officePreview) params.set("office_preview", "true");
    if (officeFidelityPreview) params.set("office_fidelity_preview", "true");
    return `${getBackendBaseURL()}/api/workspace-resources/${encodeURIComponent(workspaceResourceId({ threadId, ...workspaceOutput }))}${params.size ? `?${params.toString()}` : ""}`;
  }
  if (isMock) {
    const params = new URLSearchParams();
    if (download) params.set("download", "true");
    if (officePreview) params.set("office_preview", "true");
    if (officeFidelityPreview) params.set("office_fidelity_preview", "true");
    const query = params.size ? `?${params.toString()}` : "";
    return `${getBackendBaseURL()}/mock/api/threads/${threadId}/artifacts${filepath}${query}`;
  }
  const params = new URLSearchParams();
  if (download) params.set("download", "true");
  if (officePreview) params.set("office_preview", "true");
  if (officeFidelityPreview) params.set("office_fidelity_preview", "true");
  // Source documents retain their full identity and the current task's scope.
  // The uploads endpoint only owns attachments; reducing a source path to an
  // attachment basename can open an unrelated, older document.
  if (/[/\\]/.test(filepath)) {
    params.set("path", filepath);
    params.set("thread_id", threadId);
    return `${getBackendBaseURL()}/api/fs/content?${params.toString()}`;
  }
  const query = params.size ? `?${params.toString()}` : "";
  return `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/artifacts/${encodeURIComponent(filepath)}${query}`;
}

export function urlOfArtifactRevision({
  filepath,
  threadId,
}: {
  filepath: string;
  threadId: string;
}): string | null {
  const workspaceOutput = parseWorkspaceOutputRef(filepath);
  if (!workspaceOutput) return null;
  const resource = workspaceResourceId({ threadId, ...workspaceOutput });
  return `${getBackendBaseURL()}/api/workspace-resources/${encodeURIComponent(resource)}`;
}

export function extractArtifactsFromThread(thread: AgentThread) {
  return thread.values.artifacts ?? [];
}

export function resolveArtifactURL(absolutePath: string, threadId: string) {
  return urlOfArtifact({ filepath: absolutePath, threadId });
}
