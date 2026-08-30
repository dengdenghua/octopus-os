import { memo, useMemo, useState } from "react";

import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import type { OutlineRound } from "@/core/threads/progress-outline";
import type { GroundingSource, VisibilityStep } from "@/core/realtime/items";
import type { WorkBlock } from "../work-blocks";
import type {
  AgentTile,
  AgentWorkbenchTabId,
  DiffEntry,
} from "../agent-workbench-utils";
import type { AgentPhase } from "../agent-phases";
import type { AgentWorkbenchProcessEventSnapshot } from "../agent-workbench-events";
import { AgentCreationCard, AgentSummaryPage } from "../agent-workbench-pages";
import { useAgentWorkbenchI18n } from "../use-agent-workbench-i18n";
import type { LiveToolEvent } from "../live-tool-timeline";
import type { WorkbenchRosterSeat } from "./helpers";
import { SubagentProcessView } from "./subagent-process-view";

type AgentKanbanUserInput = {
  text: string;
  uploadedFiles: Array<{ filename: string; path: string }>;
  attachments: Array<{ filename: string }>;
} | null;

function visibilityStepNeedsAttention(step: VisibilityStep): boolean {
  const details = step.details ?? {};
  const requiresAction = details["requires_user_action"];
  if (requiresAction === true) return true;

  const status = String(details["status"] ?? "").toLowerCase();
  if (
    [
      "pending",
      "blocked",
      "error",
      "failed",
      "needs_input",
      "needs_approval",
      "permission_required",
      "conflict",
    ].includes(status)
  )
    return true;

  const severity = String(details["severity"] ?? "").toLowerCase();
  return severity === "error" || severity === "critical";
}

function AgentKanbanViewImpl({
  effectiveActivityView,
  selectedRosterSeat,
  selectedAgent,
  screenBlocks,
  currentScreenBlockId,
  phases,
  blocks,
  agentTiles,
  visibleDiffEntries,
  focusedProcessEvent,
  focusedEventId,
  progressOutline,
  userInput,
  groundingSources,
  preferStructuredReferences,
  mainAgentName,
  resultPreviewUrl,
  terminalState,
  contextTokens,
  maxContextTokens,
  isCompressingContext,
  onCompressContext,
  visibilityEvents,
  setActivityView,
  onSelectTab,
  onOpenArtifact,
  openMainProcess,
  setSelectedBlockId,
  setManualBlockSelection,
}: {
  effectiveActivityView: "summary" | "screen" | "role";
  selectedRosterSeat: WorkbenchRosterSeat | null;
  selectedAgent: AgentTile | null;
  screenBlocks: WorkBlock[];
  currentScreenBlockId: string | null;
  phases: AgentPhase[];
  blocks: WorkBlock[];
  agentTiles: AgentTile[];
  visibleDiffEntries: DiffEntry[];
  focusedProcessEvent: AgentWorkbenchProcessEventSnapshot | null;
  focusedEventId: string | null | undefined;
  progressOutline: OutlineRound[] | undefined;
  userInput: AgentKanbanUserInput;
  groundingSources: GroundingSource[];
  preferStructuredReferences: boolean;
  mainAgentName: string | null | undefined;
  resultPreviewUrl?: string | null;
  terminalState: "interrupted" | "failed" | "blocked" | null;
  contextTokens?: number;
  maxContextTokens?: number;
  isCompressingContext?: boolean;
  onCompressContext?: () => void | Promise<void>;
  visibilityEvents: LiveToolEvent[];
  setActivityView: (view: "summary" | "trace" | "screen" | "role") => void;
  onSelectTab: ((tab: AgentWorkbenchTabId) => void) | undefined;
  onOpenArtifact: ((path: string) => void) | undefined;
  openMainProcess: () => void;
  setSelectedBlockId: (id: string | null) => void;
  setManualBlockSelection: (selected: boolean) => void;
}) {
  const { t } = useI18n();
  const { agentStatusLabel, agentStatusClass } = useAgentWorkbenchI18n();

  // Visibility (capability routing / delegation / skill-catalog) decisions.
  // Deliberately de-emphasised: collapsed by default, small text,
  // transparent background. Latest visibility item wins.
  const lastVisibilityEvent =
    visibilityEvents[visibilityEvents.length - 1] ?? null;
  const visibilitySteps = useMemo<VisibilityStep[]>(() => {
    const raw = lastVisibilityEvent?.input?.steps;
    if (!Array.isArray(raw)) return [];
    return raw.filter(
      (step): step is VisibilityStep =>
        !!step &&
        typeof step === "object" &&
        typeof (step as VisibilityStep).decision_point === "string" &&
        typeof (step as VisibilityStep).conclusion === "string" &&
        typeof (step as VisibilityStep).basis === "string",
    );
  }, [lastVisibilityEvent]);
  const actionableVisibilitySteps = useMemo(
    () => visibilitySteps.filter(visibilityStepNeedsAttention),
    [visibilitySteps],
  );
  const [visibilityOpen, setVisibilityOpen] = useState(false);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Summary remains the default for the main process. Once an Agent is
          selected, its computer and role card stay explicitly reachable. */}
      <div className="flex items-center gap-4 border-b border-border-subtle px-5 py-2">
        {[
          { id: "summary" as const, label: t.agentWorkbenchPanel.summaryLabel },
          ...(selectedAgent
            ? [
                {
                  id: "screen" as const,
                  label: t.agentWorkbench.executionView,
                },
                {
                  id: "role" as const,
                  label: t.agentWorkbenchPages.roleCard,
                },
              ]
            : []),
        ].map((view) => (
          <button
            key={view.id}
            type="button"
            onClick={() => setActivityView(view.id)}
            className={cn(
              "border-b border-transparent pb-1 text-xs font-medium transition-colors",
              effectiveActivityView === view.id
                ? "border-foreground/70 text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {view.label}
          </button>
        ))}
        <span className="ml-auto font-mono text-xs text-muted-foreground">
          {selectedRosterSeat
            ? selectedRosterSeat.name
            : (selectedAgent?.label ??
              mainAgentName ??
              t.agentWorkbenchPanel.mainComputer)}
        </span>
      </div>

      {effectiveActivityView === "summary" ? (
        <AgentSummaryPage
          phases={phases}
          diffEntries={visibleDiffEntries}
          agentTiles={agentTiles}
          blocks={blocks}
          focusedProcessEvent={focusedProcessEvent}
          focusedEventId={focusedEventId}
          progressOutline={progressOutline}
          userInput={userInput}
          groundingSources={groundingSources}
          preferStructuredReferences={preferStructuredReferences}
          terminalState={terminalState}
          contextTokens={contextTokens}
          maxContextTokens={maxContextTokens}
          isCompressingContext={isCompressingContext}
          onCompressContext={onCompressContext}
          onSelectTab={onSelectTab}
          onOpenArtifact={onOpenArtifact}
          resultPreviewUrl={resultPreviewUrl}
        />
      ) : effectiveActivityView === "screen" && selectedAgent ? (
        <SubagentProcessView
          agent={selectedAgent}
          blocks={screenBlocks}
          currentBlockId={currentScreenBlockId}
          onOpenMain={openMainProcess}
          onSelectBlock={(blockId) => {
            setSelectedBlockId(blockId);
            setManualBlockSelection(true);
          }}
        />
      ) : selectedAgent ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-background/70 p-3">
          <div className="mx-auto w-full max-w-xl">
            <AgentCreationCard
              agent={selectedAgent}
              agentStatusClass={agentStatusClass}
              agentStatusLabel={agentStatusLabel}
            />
          </div>
        </div>
      ) : (
        <AgentSummaryPage
          phases={phases}
          diffEntries={visibleDiffEntries}
          agentTiles={agentTiles}
          blocks={blocks}
          focusedProcessEvent={focusedProcessEvent}
          focusedEventId={focusedEventId}
          progressOutline={progressOutline}
          userInput={userInput}
          groundingSources={groundingSources}
          preferStructuredReferences={preferStructuredReferences}
          terminalState={terminalState}
          contextTokens={contextTokens}
          maxContextTokens={maxContextTokens}
          isCompressingContext={isCompressingContext}
          onCompressContext={onCompressContext}
          onSelectTab={onSelectTab}
          onOpenArtifact={onOpenArtifact}
          resultPreviewUrl={resultPreviewUrl}
        />
      )}

      {/* Explanatory capability traces stay in the event history. This surface
          appears only when the latest trace explicitly needs user attention. */}
      {lastVisibilityEvent && actionableVisibilitySteps.length > 0 ? (
        <div className="relative shrink-0 border-t border-amber-200/70 bg-amber-50/55 py-1 dark:border-amber-900/50 dark:bg-amber-950/15">
          <button
            type="button"
            onClick={() => setVisibilityOpen((open) => !open)}
            className="relative z-10 flex w-full items-center gap-1.5 px-5 py-1.5 text-left transition-colors hover:bg-amber-100/55 dark:hover:bg-amber-900/20"
            aria-expanded={visibilityOpen}
          >
            {visibilityOpen ? (
              <ChevronDownIcon className="size-3 shrink-0 text-muted-foreground/60" />
            ) : (
              <ChevronRightIcon className="size-3 shrink-0 text-muted-foreground/60" />
            )}
            <span className="text-[11px] font-medium text-amber-800 dark:text-amber-300">
              {t.agentWorkbenchPanel.visibilityPanelTitle} ·{" "}
              {t.agentWorkbenchPanel.visibilityPanelAttention}
            </span>
            <span className="rounded-full bg-amber-100 px-1.5 text-[10px] tabular-nums text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
              {actionableVisibilitySteps.length}
            </span>
          </button>
          {visibilityOpen && (
            <div className="relative z-10 space-y-1.5 px-5 pb-2.5">
              {actionableVisibilitySteps.map((step, index) => (
                <div
                  key={`${step.decision_point}:${index}`}
                  className="rounded-md border border-border-subtle bg-background/40 px-2 py-1.5"
                >
                  <span className="text-[10px] font-medium text-muted-foreground/60">
                    {t.agentWorkbenchPanel.visibilityStep} {index + 1}
                  </span>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-foreground/75">
                    {step.conclusion}
                  </p>
                  <p className="mt-0.5 text-[10px] leading-relaxed text-muted-foreground/60">
                    {step.basis}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

export const AgentKanbanView = memo(AgentKanbanViewImpl);
