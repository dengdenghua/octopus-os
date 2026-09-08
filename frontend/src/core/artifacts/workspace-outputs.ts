import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

import {
  parseWorkspaceResourceId,
  parseWorkspaceOutputRef,
  type WorkspaceOutputArea,
  workspaceOutputRef,
} from "./utils";

export interface WorkspaceOutputEntry {
  name: string;
  area: WorkspaceOutputArea;
  relative_path: string;
  path: string;
  size: number;
  modified: number;
  download_url: string;
  /** Stable cross-surface identity; absolute host paths remain compatibility data. */
  resource_id?: string;
}

interface WorkspaceOutputsResponse {
  thread_id: string;
  area: WorkspaceOutputArea;
  files: WorkspaceOutputEntry[];
  count: number;
}

const ARTIFACT_AREAS: WorkspaceOutputArea[] = [
  "final",
  "deploy",
  "stages",
  "output",
];

export function isInternalWorkspaceOutput(relativePath: string): boolean {
  const normalized = relativePath.replaceAll("\\", "/").replace(/^\/+/, "");
  const basename = normalized
    .slice(normalized.lastIndexOf("/") + 1)
    .toLowerCase();
  if (
    ["plan.md", "todo.md", "todos.md", "note.md", "notes.md"].includes(basename)
  ) {
    return true;
  }
  if (basename.endsWith(".lock")) return true;
  const lowerPath = normalized.toLowerCase();
  if (
    lowerPath.includes("output/final/") ||
    lowerPath.startsWith("final/") ||
    lowerPath.startsWith("stages/")
  ) {
    return true;
  }
  return /-full\.jsonl$/i.test(basename);
}

export function isInternalArtifactRef(filepath: string): boolean {
  const workspaceOutput = parseWorkspaceOutputRef(filepath);
  return isInternalWorkspaceOutput(workspaceOutput?.relativePath ?? filepath);
}

async function listWorkspaceOutputArea(
  threadId: string,
  area: WorkspaceOutputArea,
  signal?: AbortSignal,
): Promise<WorkspaceOutputEntry[]> {
  const params = new URLSearchParams({ area });
  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/outputs?${params.toString()}`,
    { headers: authHeaders(), signal },
  );
  if (!response.ok) return [];
  const data = (await response.json()) as WorkspaceOutputsResponse;
  return data.files ?? [];
}

export async function listWorkspaceArtifactRefs(
  threadId: string,
  signal?: AbortSignal,
): Promise<string[]> {
  const results = await Promise.allSettled(
    ARTIFACT_AREAS.map((area) =>
      listWorkspaceOutputArea(threadId, area, signal),
    ),
  );
  const refs: string[] = [];
  const seen = new Set<string>();

  for (const result of results) {
    if (result.status !== "fulfilled") continue;
    for (const file of result.value) {
      if (isInternalWorkspaceOutput(file.relative_path)) continue;
      // The gateway owns the canonical identity.  Keep it when it is valid
      // for this thread; only older gateways need the compatibility ref.
      const suppliedResource =
        typeof file.resource_id === "string"
          ? parseWorkspaceResourceId(file.resource_id)
          : null;
      const normalizedRelativePath = file.relative_path
        .replaceAll("\\", "/")
        .replace(/^\/+/, "");
      const ref =
        suppliedResource?.threadId === threadId &&
        suppliedResource.area === file.area &&
        suppliedResource.relativePath === normalizedRelativePath
          ? file.resource_id!
          : workspaceOutputRef({
              area: file.area,
              relativePath: file.relative_path,
            });
      const key = ref;
      if (seen.has(key)) continue;
      seen.add(key);
      refs.push(ref);
    }
  }

  return refs;
}
