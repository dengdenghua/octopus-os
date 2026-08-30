import { useParams, useLocation, useSearchParams } from "react-router-dom";
import { useRef, useState } from "react";

import { env } from "@/env";
import { uuid } from "@/core/utils/uuid";

type ThreadChatState = {
  threadId: string;
  isNewThread: boolean;
};

function resolveStateFromPath(
  threadIdFromPath: string | undefined,
  isNewPath: boolean,
): ThreadChatState {
  if (isNewPath) {
    return { threadId: uuid(), isNewThread: true };
  }
  return {
    threadId: threadIdFromPath ?? uuid(),
    isNewThread: false,
  };
}

export function useThreadChat() {
  const params = useParams();
  const threadIdFromPath = params.threadId ?? params.thread_id;
  const location = useLocation();
  const { pathname } = location;
  const isNewPath = threadIdFromPath === "new" || pathname.endsWith("/new");

  const [searchParams] = useSearchParams();

  // Build a stable identity for the current route so we only reset state
  // when the user actually navigates (new → existing, existing → different
  // existing, existing → new). We combine pathname + threadIdFromPath so
  // that both hash changes and param changes trigger a reset.
  // `taskNonce` (sidebar 新建任务) must join the identity: clicking it while
  // already on `/realtime/new` keeps the same pathname, and without the
  // nonce the identical pathId skips the reset - the button looks dead and
  // the old draft/thread id survives a "new task" click.
  const taskNonce =
    (location.state as { taskNonce?: string } | null)?.taskNonce ?? "";
  // `/realtime/new?agent=…` is a persona-scoped draft. Switching persona on
  // the same `/new` pathname must allocate a clean thread identity; otherwise
  // the previous role's messages, failures and model selection leak into the
  // new role even though the URL changed.
  const routeAgentId = isNewPath
    ? (searchParams.get("agent")?.trim() ?? "")
    : "";
  const pathId = `${pathname}|${threadIdFromPath ?? ""}|${taskNonce}|${routeAgentId}`;

  const [state, setState] = useState<ThreadChatState>(() =>
    resolveStateFromPath(threadIdFromPath, isNewPath),
  );

  // Track the last pathId we resolved state for. When it changes, we
  // synchronously compute the correct state during render (before child
  // components run) to avoid a one-frame flash of stale messages. This
  // is the getDerivedStateFromProps pattern for hooks: calling setState
  // during render is safe when guarded by a condition — React will
  // immediately re-render with the new state without showing intermediate UI.
  const lastPathIdRef = useRef<string>(pathId);
  let current: ThreadChatState = state;

  if (lastPathIdRef.current !== pathId) {
    lastPathIdRef.current = pathId;
    current = resolveStateFromPath(threadIdFromPath, isNewPath);
    setState(current);
  }

  // setIsNewThread allows callers to mark a thread as "no longer new"
  // after the first message is sent (before the deferred route commit
  // navigates to the real thread id).
  const setIsNewThread = (v: boolean) => {
    setState((s) => ({ ...s, isNewThread: v }));
  };

  const isMock = env.STATIC_WEBSITE_ONLY && searchParams.get("mock") === "true";
  return {
    threadId: current.threadId,
    isNewThread: current.isNewThread,
    setIsNewThread,
    isMock,
  };
}
