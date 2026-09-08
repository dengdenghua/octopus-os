import { authHeaders } from "@/core/auth/api";
import { databaseFileRoute } from "@/core/storage/file-location";
import { ArtifactLoadError } from "./loader";
import {
  normalizeWorkspaceArtifactRef,
  parseWorkspaceOutputRef,
  urlOfArtifact,
} from "./utils";

export function canLocateArtifact(
  filepath: string,
  threadId?: string,
): boolean {
  const normalized = normalizeWorkspaceArtifactRef(filepath, threadId);
  return Boolean(
    parseWorkspaceOutputRef(normalized) || databaseFileRoute(filepath, ""),
  );
}

export async function locateArtifactRoute(
  filepath: string,
  threadId: string,
): Promise<string> {
  const normalized = normalizeWorkspaceArtifactRef(filepath, threadId);
  if (!parseWorkspaceOutputRef(normalized)) {
    const route = databaseFileRoute(filepath, threadId, filepath);
    if (!route) throw new Error("此文件尚未提供可定位的本地路径。");
    return route;
  }
  const url = new URL(
    urlOfArtifact({ filepath: normalized, threadId }),
    window.location.origin,
  );
  url.searchParams.set("locate", "true");
  const response = await fetch(url.toString(), {
    headers: authHeaders(),
    cache: "no-store",
  });
  if (!response.ok)
    throw new ArtifactLoadError(response.status, "无法定位文件");
  const body = (await response.json()) as {
    path?: unknown;
    thread_id?: unknown;
    resource_id?: unknown;
  };
  const route =
    typeof body.path === "string" && body.thread_id === threadId
      ? databaseFileRoute(
          body.path,
          threadId,
          normalized,
          typeof body.resource_id === "string" ? body.resource_id : null,
        )
      : null;
  if (!route) throw new Error("服务未提供有效的文件位置。");
  return route;
}
