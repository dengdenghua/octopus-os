// Workflow settlement notification component.
//
// Monitors the realtime thread for workflow/completed notifications and
// displays browser notifications when Echo Native background workflows
// finish, keeping users informed while they're in another tab or workspace.

import { useEffect, useRef } from "react";
import { useNotification } from "@/core/notification/hooks";

interface WorkflowCompletedPayload {
  threadId: string;
  workflowName: string;
  workflowDescription: string;
  runId: string;
  stopReason: string;
  success: boolean;
  agentsStarted: number;
  error?: string | null;
}

interface WorkflowSettlementProps {
  /** Thread ID to monitor for workflow completion notifications */
  threadId: string;
  /** Callback to inject into the notification stream */
  onWorkflowCompleted?: (payload: WorkflowCompletedPayload) => void;
}

/**
 * Hook to handle workflow completion notifications.
 *
 * Usage:
 *   const handleWorkflowNotification = useWorkflowSettlement({ threadId });
 *
 * Then in the realtime client's onNotification callback:
 *   if (note.method === "workflow/completed") {
 *     handleWorkflowNotification(note.params);
 *   }
 */
export function useWorkflowSettlement(props: WorkflowSettlementProps) {
  const { showNotification } = useNotification();
  const seenRunIds = useRef(new Set<string>());

  // Cleanup old run IDs to prevent memory leak
  useEffect(() => {
    const cleanup = setInterval(() => {
      if (seenRunIds.current.size > 100) {
        seenRunIds.current.clear();
      }
    }, 60_000);
    return () => clearInterval(cleanup);
  }, []);

  return (payload: WorkflowCompletedPayload) => {
    // Only show notifications for the current thread
    if (payload.threadId !== props.threadId) return;

    // Dedupe: don't show the same workflow completion twice
    if (seenRunIds.current.has(payload.runId)) return;
    seenRunIds.current.add(payload.runId);

    const title = payload.success
      ? `✅ Workflow Complete`
      : `❌ Workflow Failed`;

    const bodyParts: string[] = [];
    if (payload.workflowName) {
      bodyParts.push(payload.workflowName);
    }
    if (payload.workflowDescription) {
      bodyParts.push(payload.workflowDescription);
    }
    if (payload.success) {
      bodyParts.push(`${payload.agentsStarted} agents completed`);
    } else if (payload.error) {
      bodyParts.push(`Error: ${payload.error}`);
    }

    const body = bodyParts.join("\n");

    showNotification(title, {
      body,
      tag: `workflow-${payload.runId}`,
      requireInteraction: false,
      icon: payload.success ? "/icon-success.png" : "/icon-error.png",
    });

    // Optional callback for additional handling
    props.onWorkflowCompleted?.(payload);
  };
}

/**
 * Component wrapper that provides workflow settlement notifications.
 * Mount this once at the workspace level to enable workflow notifications.
 */
export function WorkflowSettlementProvider({
  children,
  threadId,
  onWorkflowCompleted,
}: {
  children: React.ReactNode;
  threadId: string;
  onWorkflowCompleted?: (payload: WorkflowCompletedPayload) => void;
}) {
  useWorkflowSettlement({ threadId, onWorkflowCompleted });
  return <>{children}</>;
}
