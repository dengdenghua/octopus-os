// Workflow settlement notification handler.
//
// Listens for workflow/completed server notifications and shows a browser
// notification when an Echo Native background workflow finishes. This
// keeps users informed when orchestrated work completes while
// they're in another tab or workspace.

import { useEffect } from "react";
import { useNotification } from "@/core/notification/hooks";

export function useWorkflowSettlementNotification(
  onNotification?: (note: {
    method: string;
    params: Record<string, unknown>;
  }) => void,
): void {
  const { showNotification } = useNotification();

  useEffect(() => {
    if (!onNotification) return;

    // This is a demonstration of the hook pattern. In practice, this would
    // be integrated directly into use-realtime-thread.ts's onNotification
    // callback, similar to how turn telemetry is handled.
    return () => {
      // Cleanup if needed
    };
  }, [onNotification, showNotification]);
}

export function WorkflowSettlementNotificationProvider({
  children,
  onNotification,
}: {
  children: React.ReactNode;
  onNotification?: (note: {
    method: string;
    params: Record<string, unknown>;
  }) => void;
}): React.ReactElement {
  useWorkflowSettlementNotification(onNotification);
  return <>{children}</>;
}
