import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { env } from "@/env";
import { getWorkspaceArtifactRefetchInterval } from "@/core/artifacts/polling";
import { useWorkspaceArtifacts } from "@/core/artifacts/use-workspace-artifacts";
import { normalizeWorkspaceArtifactRef } from "@/core/artifacts/utils";
import { isInternalArtifactRef } from "@/core/artifacts/workspace-outputs";
import { cn } from "@/lib/utils";

import { ArtifactPanel, useArtifacts } from "../artifacts";
import { useThread } from "../messages/context";

/**
 * ChatBox – conversation column + slide-in artifact drawer.
 *
 * Previous implementation used react-resizable-panels with an imperative
 * `setLayout({chat, artifacts})` call that silently did nothing (the
 * library's API takes an array, not an object), so the drawer never
 * actually opened. Rewritten as a flex layout with a width-animated
 * drawer: a cleaner Codex-style slide-in from the right with frosted
 * glass, proper enter/exit animation, and a visible close affordance.
 */

const DRAWER_WIDTH = "min(340px, 38vw)";
const EMPTY_ARTIFACTS: string[] = [];

const ChatBox: React.FC<{
  artifactPanelMode?: "drawer" | "external";
  children: React.ReactNode;
  threadId: string;
}> = ({ artifactPanelMode = "drawer", children, threadId }) => {
  const { thread } = useThread();
  const queryClient = useQueryClient();
  const threadIdRef = useRef(threadId);
  const previousRunRef = useRef({
    isLoading: thread.isLoading,
    threadId,
  });
  const { data: workspaceArtifacts = EMPTY_ARTIFACTS } = useWorkspaceArtifacts(
    threadId,
    {
      refetchInterval: getWorkspaceArtifactRefetchInterval(thread.isLoading),
    },
  );

  useEffect(() => {
    const previous = previousRunRef.current;
    const runJustFinished =
      previous.threadId === threadId && previous.isLoading && !thread.isLoading;

    previousRunRef.current = { isLoading: thread.isLoading, threadId };

    if (!runJustFinished || !threadId || threadId === "new") return;
    void queryClient.invalidateQueries({
      exact: true,
      queryKey: ["workspace-artifacts", threadId],
    });
  }, [queryClient, thread.isLoading, threadId]);

  const {
    artifacts,
    open: artifactsOpen,
    autoOpen,
    selectedArtifact,
    setOpen: setArtifactsOpen,
    setArtifacts,
    select: selectArtifact,
    deselect,
  } = useArtifacts();

  const [autoSelectFirstArtifact, setAutoSelectFirstArtifact] = useState(true);
  useEffect(() => {
    if (threadIdRef.current !== threadId) {
      threadIdRef.current = threadId;
      deselect();
    }

    const mergedArtifacts = mergeArtifacts(
      workspaceArtifacts,
      thread.values.artifacts ?? EMPTY_ARTIFACTS,
      threadId,
    );
    setArtifacts((current) =>
      artifactsEqual(current, mergedArtifacts) ? current : mergedArtifacts,
    );

    if (env.STATIC_WEBSITE_ONLY && autoSelectFirstArtifact) {
      if (mergedArtifacts.length > 0) {
        setAutoSelectFirstArtifact(false);
        selectArtifact(mergedArtifacts[0]!);
      }
    }
  }, [
    threadId,
    autoSelectFirstArtifact,
    deselect,
    selectArtifact,
    setArtifacts,
    thread.values.artifacts,
    workspaceArtifacts,
  ]);

  // A receipt opened before its workspace artifact list finished loading can
  // leave an old absolute path selected. Normalize that existing selection as
  // well, otherwise the drawer keeps querying the legacy uploads endpoint.
  useEffect(() => {
    if (!selectedArtifact) return;
    const normalized = normalizeWorkspaceArtifactRef(
      selectedArtifact,
      threadId,
    );
    if (normalized !== selectedArtifact) {
      selectArtifact(normalized, true);
    }
  }, [selectedArtifact, selectArtifact, threadId]);

  useEffect(() => {
    // External consumers (Realtime's unified workbench) own artifact
    // visibility. Keep synchronising the artifact list, but never mutate the
    // legacy drawer-open state behind their back.
    if (artifactPanelMode !== "drawer") return;
    if (thread.isLoading) return;
    if (artifacts.length === 0) return;
    if (!autoOpen) return;
    setArtifactsOpen(true);
  }, [
    artifactPanelMode,
    artifacts.length,
    autoOpen,
    setArtifactsOpen,
    thread.isLoading,
  ]);

  // In static-preview mode, hide the drawer entirely if no artifacts yet.
  const drawerEnabled = artifactPanelMode === "drawer";
  const drawerOpen =
    drawerEnabled &&
    (env.STATIC_WEBSITE_ONLY
      ? artifactsOpen && (artifacts?.length ?? 0) > 0
      : artifactsOpen);

  return (
    <div className="relative flex size-full min-h-0">
      {/* Conversation column shrinks as the drawer opens. Uses a
          timing-function that settles rather than flashes, and the
          inner content keeps its own overflow handling. */}
      <div
        className="min-w-0 flex-1 transition-[margin-right] duration-slow ease-[cubic-bezier(0.22,1,0.36,1)]"
        style={{ marginRight: drawerOpen ? DRAWER_WIDTH : "0px" }}
      >
        {children}
      </div>

      {/* Drawer — absolutely positioned so its transform doesn't affect
          layout. Slides in from the right with a light scrim border and
          backdrop blur that picks up the underlying conversation. */}
      {drawerEnabled && (
        <aside
          aria-hidden={!drawerOpen}
          className={cn(
            "absolute right-0 top-0 bottom-0 z-20 flex flex-col overflow-hidden",
            "border-l border-border-default bg-[color:color-mix(in_oklch,var(--card)_92%,transparent)]",
            "backdrop-blur-[10px]",
            "shadow-[-12px_0_32px_-16px_rgba(0,0,0,0.12)]",
            "transition-[transform,opacity] duration-slow ease-[cubic-bezier(0.22,1,0.36,1)]",
            drawerOpen
              ? "translate-x-0 opacity-100 pointer-events-auto"
              : "translate-x-full opacity-0 pointer-events-none",
          )}
          style={{ width: DRAWER_WIDTH }}
        >
          <ArtifactPanel className="size-full" threadId={threadId} />
        </aside>
      )}
    </div>
  );
};

function mergeArtifacts(
  primary: string[],
  fallback: string[],
  threadId: string,
) {
  const merged: string[] = [];
  const seen = new Set<string>();
  for (const rawArtifact of [...primary, ...fallback]) {
    const artifact = normalizeWorkspaceArtifactRef(rawArtifact, threadId);
    if (isInternalArtifactRef(artifact)) continue;
    if (seen.has(artifact)) continue;
    seen.add(artifact);
    merged.push(artifact);
  }
  return merged;
}

function artifactsEqual(left: string[], right: string[]) {
  return (
    left.length === right.length &&
    left.every((item, index) => item === right[index])
  );
}

export { ChatBox };
