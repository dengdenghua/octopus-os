/** Preserve local path identity; URL encoding happens only at the route boundary. */
export function normalizeLocalPath(path: string): string {
  // On Unix a backslash can be part of a filename; only Windows paths use
  // it as a directory separator. The client OS does not identify the NAS OS.
  return /^(?:[a-z]:[/\\]|\\\\|\/\/)/i.test(path)
    ? path.replaceAll("\\", "/")
    : path;
}

export function localFileLocation(
  path: string,
): { path: string; directory: string } | null {
  const normalized = normalizeLocalPath(path);
  if (!/^(?:[a-z]:\/|\/)/i.test(normalized) || normalized.includes("\0"))
    return null;
  const parts = normalized.split("/");
  if (parts.some((part) => part === "." || part === "..") || !parts.at(-1))
    return null;
  const end = normalized.lastIndexOf("/");
  const directory = normalized.slice(0, end) || "/";
  return {
    path: normalized,
    directory: /^[a-z]:$/i.test(directory) ? `${directory}/` : directory,
  };
}

export function databaseFileRoute(
  path: string,
  threadId: string,
  artifact?: string,
  resourceId?: string | null,
): string | null {
  const location = localFileLocation(path);
  if (!location) return null;
  const normalizedResourceId = resourceId?.trim();
  return `/workspace/storage?${new URLSearchParams({
    library: "computer",
    file: location.path,
    sourceThread: threadId,
    ...(artifact ? { sourceArtifact: artifact } : {}),
    ...(normalizedResourceId ? { resource_id: normalizedResourceId } : {}),
  })}`;
}

/** Build a database location route when only a server-issued resource ID is available. */
export function databaseResourceRoute(
  resourceId: string,
  threadId: string,
  artifact?: string,
): string | null {
  const normalizedResourceId = resourceId.trim();
  if (!normalizedResourceId) return null;
  return `/workspace/storage?${new URLSearchParams({
    library: "computer",
    sourceThread: threadId,
    ...(artifact ? { sourceArtifact: artifact } : {}),
    resource_id: normalizedResourceId,
  })}`;
}

export function sourceArtifactRoute(
  threadId: string,
  artifact: string,
): string {
  return `/workspace/realtime/${encodeURIComponent(threadId)}?${new URLSearchParams({ artifact })}`;
}

export function directoryBreadcrumbs(
  path: string,
): { label: string; path: string }[] {
  const normalized = normalizeLocalPath(path);
  const root = normalized.match(/^(?:[a-z]:\/|\/\/[^/]+\/[^/]+\/?|\/)/i)?.[0];
  if (!root) return [];
  const parts = normalized.slice(root.length).split("/").filter(Boolean);
  let current = root;
  return [
    { label: root, path: root },
    ...parts.map((label) => {
      current = `${current.replace(/\/$/, "")}/${label}`;
      return { label, path: current };
    }),
  ];
}
