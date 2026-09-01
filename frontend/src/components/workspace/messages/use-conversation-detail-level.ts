import { useLocalSettings } from "@/core/settings";

export type ConversationDetailLevel = "low" | "medium" | "high";

export interface DetailLevelConfig {
  level: ConversationDetailLevel;
  showToolCalls: boolean;
  showCodeBlocks: boolean;
  showThinkingProcess: boolean;
  showIntermediateSteps: boolean;
  autoCollapseToolCalls: boolean;
}

/**
 * Hook to access conversation detail level settings and derived display flags.
 *
 * Detail levels:
 * - **low**: Minimal view - hide tool details, code blocks, only show final answers
 * - **medium** (default): Balanced - collapse intermediate steps, show final results
 * - **high**: Verbose - show all tool calls, reasoning, code blocks expanded
 */
export function useConversationDetailLevel(): DetailLevelConfig {
  const [settings] = useLocalSettings();
  const level = settings.display.conversation_detail_level ?? "medium";

  switch (level) {
    case "low":
      return {
        level,
        showToolCalls: false,
        showCodeBlocks: false,
        showThinkingProcess: false,
        showIntermediateSteps: false,
        autoCollapseToolCalls: true,
      };
    case "high":
      return {
        level,
        showToolCalls: true,
        showCodeBlocks: true,
        showThinkingProcess: true,
        showIntermediateSteps: true,
        autoCollapseToolCalls: false,
      };
    case "medium":
    default:
      return {
        level,
        showToolCalls: true,
        showCodeBlocks: true,
        showThinkingProcess: true,
        showIntermediateSteps: true,
        autoCollapseToolCalls: true,
      };
  }
}
