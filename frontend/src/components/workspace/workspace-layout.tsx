import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface WorkspaceLayoutProps {
  /* Implementation note. */
  workspaceIdentity?: ReactNode;
  /* Implementation note. */
  modeSwitcher?: ReactNode;
  /* Implementation note. */
  headerActions?: ReactNode;
  /* Implementation note. */
  mainContent: ReactNode;
  /* Implementation note. */
  rightPanel?: ReactNode;
  /* Implementation note. */
  inputArea: ReactNode;
  /* Implementation note. */
  showRightPanel?: boolean;
  /* Implementation note. */
  rightPanelWidth?: string;
  /* Implementation note. */
  isEmptyState?: boolean;
  /* Implementation note. */
  className?: string;
}

/* Implementation note. */
export function WorkspaceLayout({
  workspaceIdentity,
  modeSwitcher,
  headerActions,
  mainContent,
  rightPanel,
  inputArea,
  showRightPanel = false,
  rightPanelWidth = "18rem",
  isEmptyState = false,
  className,
}: WorkspaceLayoutProps) {
  return (
    <div className={cn("relative flex size-full min-h-0 flex-col", className)}>
      {/* Implementation note. */}
      <header className="workspace-panel-subtle flex h-14 shrink-0 items-center justify-between px-4">
        {/* Implementation note. */}
        <div className="flex items-center gap-3">{workspaceIdentity}</div>

        {/* Implementation note. */}
        {modeSwitcher && (
          <div className="absolute left-1/2 -translate-x-1/2 hidden lg:block">
            {modeSwitcher}
          </div>
        )}

        {/* Implementation note. */}
        <div className="flex items-center gap-2">{headerActions}</div>
      </header>

      {/* Implementation note. */}
      <div className="relative flex min-h-0 flex-1">
        {/* Implementation note. */}
        <main className="relative flex min-h-0 flex-1 flex-col">
          {/* Implementation note. */}
          <div
            className={cn(
              "flex-1 overflow-y-auto",
              isEmptyState && "flex items-center justify-center",
            )}
          >
            {mainContent}
          </div>

          {/* Implementation note. */}
          <div className="flex justify-center px-4 py-3">{inputArea}</div>
        </main>

        {/* Implementation note. */}
        {showRightPanel && rightPanel && (
          <aside
            className="workspace-panel-subtle ml-3 hidden min-h-0 shrink-0 flex-col overflow-hidden lg:flex"
            style={{ width: rightPanelWidth }}
          >
            {rightPanel}
          </aside>
        )}
      </div>
    </div>
  );
}
