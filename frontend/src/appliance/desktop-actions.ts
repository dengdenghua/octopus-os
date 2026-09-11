/** Read-only desktop intents. A URL never grants access to NAS data. */
export type DesktopAction =
  | { type: "photos.search"; query: string }
  | { type: "photos.reveal"; path: string; query: string }
  | { type: "files.open"; path: string }
  | { type: "files.reveal"; path: string };

export function parseDesktopAction(search: string): DesktopAction | null {
  const params = new URLSearchParams(search);
  const type = params.get("desktopAction");
  if (type === "photos.search") {
    const query = (params.get("query") ?? "").trim();
    return query && query.length <= 120 && !/[\x00-\x1f]/.test(query)
      ? { type, query }
      : null;
  }
  if (
    type === "files.open" ||
    type === "files.reveal" ||
    type === "photos.reveal"
  ) {
    const path = params.get("path") ?? "";
    if (
      path.length > 2048 ||
      /[\\:\x00-\x1f]/.test(path) ||
      path.startsWith("/") ||
      path.split("/").some((part) => part === ".." || part === ".")
    )
      return null;
    if (type !== "files.open" && (!path || path.endsWith("/"))) return null;
    if (type === "photos.reveal") {
      const query = (params.get("query") ?? "").trim();
      if (query.length > 120 || /[\x00-\x1f]/.test(query)) return null;
      return { type, path, query };
    }
    return { type, path };
  }
  return null;
}

export function revealFileRequest(
  path: string,
): { path: string; selectedPath: string } | null {
  const action = parseDesktopAction(
    new URLSearchParams({ desktopAction: "files.reveal", path }).toString(),
  );
  if (!action || action.type !== "files.reveal") return null;
  const slash = path.lastIndexOf("/");
  return { path: slash < 0 ? "" : path.slice(0, slash), selectedPath: path };
}

export function desktopActionHref(action: DesktopAction): string {
  const params = new URLSearchParams({ desktopAction: action.type });
  if (action.type === "photos.search") params.set("query", action.query);
  else params.set("path", action.path);
  if (action.type === "photos.reveal") params.set("query", action.query);
  return `/#/desktop?${params}`;
}
