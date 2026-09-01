export function threadCollaborationRoute(
  threadId: string,
  isNewThread: boolean,
) {
  return isNewThread
    ? "/workspace/realtime/new"
    : `/workspace/realtime/${encodeURIComponent(threadId)}`;
}

export function threadCollaborationLink({
  threadId,
  isNewThread,
  origin,
  pathname,
}: {
  threadId: string;
  isNewThread: boolean;
  origin?: string;
  pathname?: string;
}) {
  const route = threadCollaborationRoute(threadId, isNewThread);
  if (!origin) return route;
  return `${origin}${pathname ?? ""}#${route}`;
}
