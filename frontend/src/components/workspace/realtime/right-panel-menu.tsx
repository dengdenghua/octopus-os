/**
 * RightPanelMenu — extracted from `workspace/realtime/[thread_id]/page.tsx`
 * (P3 decomposition). Behavior-preserving move.
 */
import { PanelRightIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export type RightPanelPage =
  | "agent"
  | "artifacts"
  | "plan"
  | "preview"
  | "research"
  | "history";

export function RightPanelMenu({
  activePage,
  onClosePanel,
  onOpenAgent,
  onOpenArtifacts,
  onOpenPlan,
  onOpenPreview,
  onOpenResearch,
  onOpenResearchHistory,
  hasAgentWorkbench,
  hasPlan,
  hasPreview,
  hasResearch,
  hasResearchHistory,
  artifactCount,
}: {
  activePage: RightPanelPage | null;
  artifactCount: number;
  hasAgentWorkbench: boolean;
  hasPlan: boolean;
  hasPreview: boolean;
  hasResearch: boolean;
  hasResearchHistory: boolean;
  onClosePanel: () => void;
  onOpenAgent: () => void;
  onOpenArtifacts: () => void;
  onOpenPlan: () => void;
  onOpenPreview: () => void;
  onOpenResearch: () => void;
  onOpenResearchHistory: () => void;
}) {
  const { t } = useI18n();
  const hasAnyPanel =
    hasAgentWorkbench ||
    hasPlan ||
    artifactCount > 0 ||
    hasPreview ||
    hasResearch ||
    hasResearchHistory;

  if (!hasAnyPanel) return null;

  const openDefaultPanel = () => {
    if (hasAgentWorkbench) {
      onOpenAgent();
    } else if (artifactCount > 0) {
      onOpenArtifacts();
    } else if (hasPlan) {
      onOpenPlan();
    } else if (hasPreview) {
      onOpenPreview();
    } else if (hasResearch) {
      onOpenResearch();
    } else if (hasResearchHistory) {
      onOpenResearchHistory();
    }
  };
  const handleTogglePanel = () => {
    if (activePage) {
      onClosePanel();
      return;
    }
    openDefaultPanel();
  };

  const panelToggleLabel = activePage
    ? t.realtime.panelToggle.close
    : t.realtime.panelToggle.open;

  return (
    <Button
      type="button"
      aria-label={panelToggleLabel}
      aria-pressed={Boolean(activePage)}
      data-state={activePage ? "open" : "closed"}
      title={panelToggleLabel}
      onClick={handleTogglePanel}
      className={cn(
        "flex size-[42px] items-center justify-center rounded-lg border shadow-none transition-all duration-base focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 sm:size-8",
        activePage
          ? "border-border-default bg-muted/60 text-foreground hover:bg-muted/75 hover:text-foreground"
          : "border-transparent bg-transparent text-muted-foreground hover:border-border-default hover:bg-muted/55 hover:text-foreground",
      )}
    >
      <PanelRightIcon className="size-4" />
    </Button>
  );
}
