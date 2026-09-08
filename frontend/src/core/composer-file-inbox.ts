import { currentActorId } from "@/core/auth/api";
import { preserveWorkbenchPresentation } from "@/core/router/desktop-workspace-route";

export type ComposerFileReference = {
  path: string;
  workDir?: string | null;
  sourceLabel?: string | null;
  resourceId?: string | null;
};
type Batch = {
  actor: string;
  threadId: string;
  files: ComposerFileReference[];
  createdAt: number;
};
const queue: Batch[] = [];
export const COMPOSER_FILES_READY = "echo:composer-files-ready";

/** Drop all in-memory references at an authentication session boundary. */
export function clearComposerFiles(): void {
  queue.splice(0, queue.length);
}

/** In-memory references only: no file upload and no path persisted to browser storage. */
export function queueComposerFiles(
  threadId: string,
  files: ComposerFileReference[],
): void {
  const actor = currentActorId();
  const clean = files
    .filter((file) => file.path.trim())
    .map((file) => ({ ...file }));
  if (!threadId || !clean.length) return;
  queue.push({ actor, threadId, files: clean, createdAt: Date.now() });
  window.dispatchEvent(new Event(COMPOSER_FILES_READY));
}

export function consumeComposerFiles(
  threadId?: string | null,
): ComposerFileReference[] {
  const actor = currentActorId();
  const consumed: ComposerFileReference[] = [];
  for (let index = 0; index < queue.length; ) {
    const batch = queue[index]!;
    if (batch.actor !== actor || Date.now() - batch.createdAt > 5 * 60_000) {
      queue.splice(index, 1);
    } else if (threadId && batch.threadId === threadId) {
      consumed.push(...batch.files);
      queue.splice(index, 1);
    } else index++;
  }
  return consumed;
}

export function quoteDatabaseFiles(
  paths: string[],
  targetThreadId?: string | null,
  resourceIds?: Array<string | null | undefined>,
): void {
  const threadId = targetThreadId || "echo-assistant";
  queueComposerFiles(
    threadId,
    paths.map((path, index) => {
      const resourceId = resourceIds?.[index]?.trim();
      return resourceId
        ? { path, sourceLabel: "本地数据库", resourceId }
        : { path, sourceLabel: "本地数据库" };
    }),
  );
  const route = targetThreadId
    ? `/workspace/realtime/${encodeURIComponent(threadId)}`
    : "/workspace/realtime/echo-assistant?agent=echo";
  const currentRoute = window.location.hash.replace(/^#/, "");
  const queryStart = currentRoute.indexOf("?");
  const currentSearch = queryStart >= 0 ? currentRoute.slice(queryStart) : "";
  window.location.hash = `#${preserveWorkbenchPresentation(route, currentSearch)}`;
}
