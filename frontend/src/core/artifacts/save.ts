import { authHeaders } from "@/core/auth/api";

import {
  parseWorkspaceOutputRef,
  urlOfArtifact,
  urlOfArtifactRevision,
} from "./utils";

export type ArtifactSaveResult = {
  success: boolean;
  path: string;
  bytes: number;
  sha256: string;
  revision_id?: string | null;
};

export class ArtifactSaveError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ArtifactSaveError";
    this.status = status;
  }
}

export async function sha256Text(content: string): Promise<string | undefined> {
  if (!globalThis.crypto?.subtle) return undefined;
  const bytes = new TextEncoder().encode(content);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export function canSaveWorkspaceOutput(filepath: string): boolean {
  const parsed = parseWorkspaceOutputRef(filepath);
  return Boolean(parsed && /\.html?$/i.test(parsed.relativePath));
}

export async function saveWorkspaceOutputContent({
  filepath,
  threadId,
  content,
  expectedContent,
}: {
  filepath: string;
  threadId: string;
  content: string;
  expectedContent: string;
}): Promise<ArtifactSaveResult> {
  if (!canSaveWorkspaceOutput(filepath)) {
    throw new ArtifactSaveError(
      "This artifact is not an editable HTML workspace output.",
      415,
    );
  }
  const expectedSha256 = await sha256Text(expectedContent);
  if (!expectedSha256) {
    throw new ArtifactSaveError(
      "Secure content verification is unavailable in this browser.",
      0,
    );
  }
  const response = await fetch(urlOfArtifact({ filepath, threadId }), {
    method: "PUT",
    headers: {
      ...authHeaders(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      content,
      expected_sha256: expectedSha256,
    }),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      detail?: string | { message?: string };
    } | null;
    const detail = payload?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : typeof detail?.message === "string"
          ? detail.message
          : `Failed to save artifact (HTTP ${response.status}).`;
    throw new ArtifactSaveError(message, response.status);
  }
  return response.json();
}

export async function restoreWorkspaceOutputRevision({
  filepath,
  threadId,
  revisionId,
  expectedContent,
}: {
  filepath: string;
  threadId: string;
  revisionId: string;
  expectedContent: string;
}): Promise<ArtifactSaveResult> {
  const url = urlOfArtifactRevision({ filepath, threadId });
  if (!url || !canSaveWorkspaceOutput(filepath)) {
    throw new ArtifactSaveError(
      "This artifact is not an editable HTML workspace output.",
      415,
    );
  }
  const expectedSha256 = await sha256Text(expectedContent);
  if (!expectedSha256) {
    throw new ArtifactSaveError(
      "Secure content verification is unavailable in this browser.",
      0,
    );
  }
  const response = await fetch(url, {
    method: "POST",
    headers: {
      ...authHeaders(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      revision_id: revisionId,
      expected_sha256: expectedSha256,
    }),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      detail?: string | { message?: string };
    } | null;
    const detail = payload?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : typeof detail?.message === "string"
          ? detail.message
          : `Failed to restore artifact (HTTP ${response.status}).`;
    throw new ArtifactSaveError(message, response.status);
  }
  return response.json();
}
