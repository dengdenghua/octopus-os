import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

import type {
  WikiDocList,
  WikiDocument,
  WikiStatus,
  WikiUpdateResult,
} from "./types";

async function wikiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getBackendBaseURL()}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...init?.headers },
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as {
      detail?: string;
    };
    throw new Error(
      detail.detail ?? `Wiki request failed (${response.status})`,
    );
  }
  return (await response.json()) as T;
}

function withRoot(path: string, root?: string | null): string {
  if (!root) return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}root=${encodeURIComponent(root)}`;
}

export function getWikiStatus(root?: string | null): Promise<WikiStatus> {
  return wikiRequest(withRoot("/api/wiki/status", root));
}

export function listWikiDocs(root?: string | null): Promise<WikiDocList> {
  return wikiRequest(withRoot("/api/wiki/docs?lang=zh", root));
}

export function getWikiDocument(
  path: string,
  root?: string | null,
): Promise<WikiDocument> {
  const safePath = path.split("/").map(encodeURIComponent).join("/");
  return wikiRequest(withRoot(`/api/wiki/docs/${safePath}`, root));
}

export function generateWiki(root?: string | null): Promise<WikiUpdateResult> {
  return wikiRequest(withRoot("/api/wiki/generate", root), { method: "POST" });
}

export function updateWiki(root?: string | null): Promise<WikiUpdateResult> {
  return wikiRequest(withRoot("/api/wiki/update", root), { method: "POST" });
}
