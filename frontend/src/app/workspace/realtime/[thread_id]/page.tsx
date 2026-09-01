import { Settings2Icon, XIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { FinalArtifactCompletionNotice } from "@/components/workspace/realtime/final-artifact-completion-notice";
import {
  RightPanelMenu,
  type RightPanelPage,
} from "@/components/workspace/realtime/right-panel-menu";

import { ChatHeaderRecButton } from "@/components/workspace/realtime/chat-header-rec-button";

import { ChatHeaderAgentBadge } from "@/components/workspace/realtime/chat-header-agent-badge";
import { ConversationEmptyState } from "@/components/workspace/realtime/conversation-empty-state";
import { ProjectGroupHeaderBadge } from "@/components/workspace/realtime/project-group-header-badge";
import { RealtimeGroupHeaderLayout } from "@/components/workspace/realtime/realtime-group-header-layout";
import {
  RealtimeChatHeaderActions,
  RealtimeChatHeaderMemberSurface,
  type RealtimeChatHeaderShareOptions,
} from "@/components/workspace/realtime/realtime-chat-header-controls";
import { PromoteGroupToProjectDialog } from "@/components/workspace/realtime/promote-group-to-project-dialog";
import {
  detachGroupProjectCapability,
  resolveGroupProjectCapabilityAction,
} from "@/components/workspace/realtime/group-project-capability";

import {
  TaskCollaboratorControl,
  type ChatCollaborationRosterEntry,
} from "@/components/workspace/realtime/task-collaborator-control";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { useDetachProjectFromGroup } from "@/core/projects/hooks";

import {
  ArtifactsProvider,
  useArtifacts,
} from "@/components/workspace/artifacts";
import {
  AgentWorkbenchPanel,
  hasAgentWorkbenchContent,
  type AgentWorkbenchTabId,
  type WorkbenchRosterSeat,
  workspaceFocusTabFromEvents,
} from "@/components/workspace/agent-workbench-panel";
import {
  useBoundProjectState,
  type ProjectFullState,
} from "@/components/workspace/agent-workbench-panel/project-os-tab";
import {
  CoworkRoomTimelineEntry,
  dedupeCoworkRoomMessages,
  GroupHumanInviteButton,
} from "@/components/workspace/collab";
import {
  AGENT_WORKBENCH_FOCUS_EVENT,
  AGENT_WORKBENCH_OPEN_EVENT,
  type AgentWorkbenchEventView,
  type AgentWorkbenchFocusAgentSnapshot,
  type AgentWorkbenchFocusDetail,
  type AgentWorkbenchFocusView,
  type AgentWorkbenchOpenDetail,
  type AgentWorkbenchProcessEventKind,
  type AgentWorkbenchProcessEventSnapshot,
} from "@/components/workspace/agent-workbench-events";
import { ChatBox, useThreadChat } from "@/components/workspace/chats";
import { ChatsDrawer } from "@/components/workspace/chats-drawer";
import { ChatHeaderMenuButton } from "@/components/workspace/chat-header-menu-button";
import {
  ChatInputBox,
  type DeepResearchComposerOptions,
} from "@/components/workspace/chat-input-box";
import { ConversationRosterStrip } from "@/components/workspace/conversation-roster-strip";
import type { GroupTaskStrategy } from "@/components/workspace/group-task-strategy";
import { ComposerStepProgress } from "@/components/workspace/composer-step-progress";
import type {
  AgentModeName,
  AuditIntensity,
  DetectResponse,
  DetectionSignals,
} from "@/components/workspace/mode-selector";
import type { ReasoningMode } from "@/components/workspace/reasoning-mode";
import type { PersonalMode } from "@/components/workspace/personal-mode-selector";
import { RecRecorderOverlay } from "@/components/workspace/rec-recorder-overlay";
import { useCapabilitySurface } from "@/core/plugins/use-capability-surface";
import type { PromptInputFilePart, UploadedFileInfo } from "@/core/uploads";
import { normalizeWorkspaceArtifactRef } from "@/core/artifacts/utils";
import {
  OPEN_ARTIFACT_EVENT,
  type OpenArtifactDetail,
} from "@/core/artifacts/open-artifact";
import {
  preferredWorkbenchTab,
  rememberWorkbenchTab,
} from "@/core/workspace/workbench-preferences";
import type { AutomationTarget } from "@/core/computer/api";
import {
  loadAutomationTarget,
  saveAutomationTarget,
} from "@/core/automation/target";
import { ChatPageLayout } from "@/components/workspace/chat-page-layout";
import { AutomationControlDock } from "@/components/workspace/automation-control-dock";
import { TeachRepeatPanel } from "@/components/workspace/teach-repeat-panel";
import { RunDurationBadge } from "@/components/workspace/run-duration-badge";
import { RealtimeApprovalPrompt } from "@/components/workspace/realtime-approval-toasts";
import { DeepResearchHistoryPanel } from "@/components/workspace/deep-research-history-panel";
import { DeepResearchPanel } from "@/components/workspace/deep-research-panel";
import {
  FINAL_DELIVERABLE_PATTERN,
  finalOutputArtifactEntries,
} from "@/components/workspace/agent-workbench-utils";
import {
  MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
  MessageList,
  type MessageListTimelineEntry,
} from "@/components/workspace/messages";
import { ModelSwitchTimelineEntry } from "@/components/workspace/messages/model-switch-timeline-entry";
import { convertToSteps } from "@/components/workspace/messages/message-group";
import { extractResultUrl } from "@/components/workspace/messages/message-output-summary";
import { LoadOlderTurnsBanner } from "@/components/workspace/messages/load-older-turns-banner";
import { ThreadProviders } from "@/components/workspace/messages/context";
import { liveEventIsReportLike } from "@/core/threads/report-deliverable";
import { ThreadTitle } from "@/components/workspace/thread-title";
import {
  loadModelSwitchEvents,
  recordModelSwitchEvent,
  type ModelSwitchEvent,
} from "@/core/threads/model-switch-events";
import { ShareMenu } from "@/components/workspace/share-menu";
import {
  TeamModePicker,
  normalizeTeamResponseMode,
  serveMeshForMode,
  type TeamMode,
} from "@/components/workspace/team-mode-picker";
import {
  toWorkBlocks,
  workBlockLabelsFromShape,
} from "@/components/workspace/work-blocks";
import { screenBlocksForAgent } from "@/components/workspace/agent-workbench-snapshot";
import { buildReplayFromBlocks } from "@/components/workspace/replay-from-blocks";
import { buildReplayHtml } from "@/core/sharing/replay-html";
import { downloadTextFile, shareSlug } from "@/core/sharing/download";
import {
  modePresetForAgentMode,
  workflowPresetForMode,
} from "@/core/agent-modes/presets";
import { PlanPanel } from "@/components/workspace/plan-panel";
import { AutomationSubscriptionPanel } from "@/components/workspace/automation/automation-subscription-panel";
import { AssistantSettingsMenu } from "@/components/workspace/assistant-settings-menu";
import { StreamingDebugger } from "@/components/workspace/streaming-debugger";
import { ContextCompressionIndicator } from "@/components/workspace/context-compression-indicator";
import { Welcome } from "@/components/workspace/welcome";
import {
  latestPersistedTodoEventsFromMessages,
  restoredTodoEventsForDisplay,
} from "@/components/workspace/persisted-tool-events";
import {
  usePlanActionHandler,
  useRegenerateHandler,
} from "@/components/workspace/use-thread-page";
import { swallow } from "@/core/utils/log";
import { getRecordingStatus } from "@/core/teach-repeat/api";
import { SubtasksProvider } from "@/core/tasks/context";
import { getAPIClient } from "@/core/api";
import { authHeaders } from "@/core/auth/api";
import { getControlPlaneBaseURL } from "@/core/config";
import { toHashRouterShellUrl } from "@/core/router/hash-shell-url";
import { taskWorkspaceRoute } from "@/core/router/task-workspace-route";
import { useDeferredRouteCommit } from "@/core/router/use-deferred-route-commit";
import { useThreadSettings } from "@/core/settings";
import { applyCoderModelProfileBoundary } from "@/core/coder/api";
import {
  useThreadStream,
  type ThreadStreamOptions,
} from "@/core/threads/hooks";
import { buildProgressOutline } from "@/core/threads/progress-outline";
import { deriveThreadTitle } from "@/core/threads/sidebar";
import {
  consumePendingNewSession,
  isThreadStale,
  writePendingNewSession,
} from "@/core/threads/pending-new-session";
import { useIsMobile } from "@/hooks/use-mobile";
import type { ReasoningEffort } from "@/core/threads";
import {
  normalizePermissionMode,
  type PermissionMode,
} from "@/core/permissions";
import { startDeepResearch, type ResearchJob } from "@/core/research/api";
import { ACTIVE_AGENT_EVENT, useActiveAgentId } from "@/core/agents/active";
import {
  isPrimaryPersonaAgentId,
  primaryPersonaAgentIdOrDefault,
} from "@/core/agents/persona-policy";
import {
  dedupeAgentsByName,
  dedupePersonaAgentsByDisplayName,
  useAgent,
  useAgents,
  useMobileDevices,
} from "@/core/agents";
import { emitAgentChanged, eventBus, useEvent } from "@/core/events";
import {
  consumeTaskCollaboratorPreset,
  TASK_COLLABORATOR_PRESET_EVENT,
  type TaskCollaboratorPreset,
  writeTaskCollaboratorPreset,
} from "@/core/collaboration/task-collaborator-preset";
import {
  collaborationRosterFromThread,
  hydrateCollaborationRoster,
} from "@/core/collaboration/thread-collaboration";
import {
  groupTaskStrategyAfterSubmit,
  groupTaskStrategyContext,
} from "@/core/collaboration/group-task-strategy-context";
import {
  buildCoworkSelectionSyncPlan,
  coworkGroupToCollaborationRoster,
  coworkSessionToCollaborationRoster,
  coworkSessionToMentionMembers,
  useApplyCollabRoomMessageProjectAction,
  useCollabSession,
  useCoworkGroup,
  useEnsureCollabRoom,
  usePostCollabRoomMessage,
  useReplaceCoworkRoster,
  type CoworkMessageProjectActionInput,
  type CoworkRoomEntityRef,
  type CoworkRoomMessage,
} from "@/core/cowork";
import { currentActorId } from "@/core/auth/api";
import { canAccessGlobalControlPlane } from "@/core/auth/control-plane-access";
import { useAuth } from "@/providers/AuthProvider";
import { usePauseTask, useTasks } from "@/core/tasks/hooks";
import { isAIMessage, isHumanMessage, type Message } from "@/core/api/types";
import {
  type FileInMessage,
  parseUploadedFiles,
  stripUploadedFilesTag,
} from "@/core/messages/utils";
import {
  QUICK_REPLY_EVENT,
  quickReplyTextForThread,
  type QuickReplyDetail,
} from "@/core/messages/quick-reply";
import { useI18n } from "@/core/i18n/hooks";
import { ToolEffectsProvider } from "@/core/observability/tool-effects-context";
import {
  extractContentFromMessage,
  extractTextFromMessage,
  isAssistantStopTerminalState,
  isSettledAssistantAnswer,
  latestAssistantTerminalState,
  assistantAnswerRequestsUserInput,
} from "@/core/messages/utils";
import { useModels } from "@/core/models/hooks";
import { resolveModelContextWindow } from "@/core/models/context-window";
import { classifyModeIntent } from "@/core/modes/intent-classifier";
import type { StreamVitals } from "@/core/realtime";
import { getChannelsStatus, type ChannelName } from "@/core/channels/api";
import { usePetAgentEvents } from "@/core/pet/use-pet-agent-events";
import {
  extractCodeBlocks,
  hasPreviewableBlocks,
} from "@/lib/extract-code-blocks";
import { isAbsolutePath, joinPath } from "@/lib/path-utils";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

function normalizeReasoningEffortForUi(
  effort: ReasoningEffort | undefined,
): ReasoningEffort | undefined {
  return effort === "max" ? "xhigh" : effort;
}

// Collect the most recent human message texts (newest first, capped at 5) for
// intent-based mode auto-switching. Index 0 is the latest message so the
// intent classifier's time weights apply correctly.
function recentHumanMessageTexts(messages: Message[]): string[] {
  const texts: string[] = [];
  for (let i = messages.length - 1; i >= 0 && texts.length < 5; i -= 1) {
    const message = messages[i];
    if (!message || !isHumanMessage(message)) continue;
    const text = extractTextFromMessage(message).trim();
    if (text) texts.push(text);
  }
  return texts;
}

function modeLabelFor(
  mode: AgentModeName,
  t: ReturnType<typeof useI18n>["t"],
): string {
  if (mode === "audit") return t.modes.audit;
  if (mode === "uxui") return t.modes.uxui;
  return t.modes.develop;
}

const CHAT_WORKDIR_KEY = "chat:workdir:lastUsed";
const CODE_WORKDIR_KEY = "code:workdir:lastUsed";
const RECENT_WORKDIRS_KEY = "echo:recentWorkdirs";
const AGENT_WORKBENCH_OPEN_KEY = "echo:agent-workbench-open";
const MAX_RECENT_WORKDIRS = 6;

type ThreadRouteState = {
  threadOwnerAgentId?: string;
  workspacePath?: string;
  /** Navigation from a project entry requests the contextual project tab,
   * while ordinary thread navigation keeps the user's workbench preference. */
  openProjectWorkbench?: boolean;
  /** A project was just created with the explicit "invite people next"
   * choice. The destination consumes this once after its canonical room is
   * ready, then removes it from history state. */
  openHumanInviteAfterCreate?: boolean;
};

function normalizeWorkDirKey(path: string): string {
  return path.trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

/** Keep role folders readable while preventing display names from escaping the root. */
function personalRoleFolderName(
  agent: { name?: string; display_name?: string | null } | null,
  fallback: string,
): string {
  const raw =
    agent?.display_name?.trim() ||
    agent?.name?.trim() ||
    fallback.trim() ||
    "角色";
  const safe = raw
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
    .replace(/[. ]+$/g, "")
    .trim();
  return safe || "角色";
}

function rememberChatWorkDir(dir: string) {
  if (typeof window === "undefined") return;
  try {
    if (!dir || !isAbsolutePath(dir)) {
      window.localStorage.removeItem(CHAT_WORKDIR_KEY);
      return;
    }
    window.localStorage.setItem(CHAT_WORKDIR_KEY, dir);
    window.localStorage.setItem(CODE_WORKDIR_KEY, dir);

    const raw = window.localStorage.getItem(RECENT_WORKDIRS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    const current = Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string")
      : [];
    const next = [
      dir,
      ...current.filter(
        (item) => normalizeWorkDirKey(item) !== normalizeWorkDirKey(dir),
      ),
    ].slice(0, MAX_RECENT_WORKDIRS);
    window.localStorage.setItem(RECENT_WORKDIRS_KEY, JSON.stringify(next));
  } catch (e) {
    swallow(e, "storage");
  }
}

function readRememberedChatWorkDir(): string {
  if (typeof window === "undefined") return "";
  try {
    const remembered =
      window.localStorage.getItem(CHAT_WORKDIR_KEY)?.trim() ?? "";
    return isAbsolutePath(remembered) ? remembered : "";
  } catch (e) {
    swallow(e, "storage");
    return "";
  }
}

function readAgentWorkbenchOpenPreference(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(AGENT_WORKBENCH_OPEN_KEY) === "1";
  } catch (e) {
    swallow(e, "storage");
    return false;
  }
}

type CompactResult = {
  compacted: boolean;
  reason?: string;
  turnCount?: number;
  keepRecent?: number;
};

type CompactableThread = {
  compact?: () => Promise<CompactResult>;
};

const URL_PATTERN = /https?:\/\/[^\s，,]+/gi;

function extractResearchUrls(text: string): { topic: string; urls: string[] } {
  const urls = Array.from(new Set(text.match(URL_PATTERN) ?? []));
  const topic = text.replace(URL_PATTERN, " ").replace(/\s+/g, " ").trim();
  return { topic: topic || text.trim(), urls };
}

function latestModelContextTokens(messages: Message[]): number | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    if (!message || !isAIMessage(message)) continue;
    const usage = message.usage_metadata;
    if (!usage) continue;
    const input = Number.isFinite(usage.input_tokens) ? usage.input_tokens : 0;
    const output = Number.isFinite(usage.output_tokens)
      ? usage.output_tokens
      : 0;
    const total = Number.isFinite(usage.total_tokens)
      ? usage.total_tokens
      : input + output;
    return Math.max(0, total);
  }
  return null;
}

// Text extraction is the expensive part of the estimate and the realtime
// adapter keeps Message identity stable for unchanged items, so cache the
// per-message text length by reference: during streaming only the message
// objects a delta actually rebuilt get re-extracted.
const messageTextLengthCache = new WeakMap<Message, number>();

function retainedMessageTextLength(message: Message): number {
  const cached = messageTextLengthCache.get(message);
  if (cached !== undefined) return cached;
  const length = extractTextFromMessage(message).length;
  messageTextLengthCache.set(message, length);
  return length;
}

function estimateRetainedContextTokens(messages: Message[]): number {
  const chars = messages.reduce(
    (total, message) => total + retainedMessageTextLength(message),
    0,
  );
  return Math.ceil(chars / 4);
}

function estimateCurrentContextTokens(messages: Message[]): number {
  const latestUsage = latestModelContextTokens(messages);
  const retainedEstimate = estimateRetainedContextTokens(messages);
  return Math.max(latestUsage ?? 0, retainedEstimate);
}

function recordFromUnknown(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function firstString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function threadOwnerAgentFromMetadata(
  metadata?: Record<string, unknown> | null,
  values?: Record<string, unknown> | null,
): string {
  return firstString(
    metadata?.agent,
    metadata?.agent_name,
    metadata?.agent_id,
    metadata?.lead_agent_name,
    metadata?.current_agent,
    values?.current_speaker,
    values?.agent_name,
  );
}

function latestArtifactFocusPathFromEvents(
  events: Array<{ input?: unknown }>,
): string | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const input = recordFromUnknown(events[index]?.input);
    const focus = recordFromUnknown(input?.workspaceFocus);
    const view = focus?.view;
    if (view !== "artifact" && view !== "image") continue;
    const path = input?.path;
    if (typeof path === "string" && path.trim().length > 0) return path;
  }
  return null;
}

/**
 * Plain chat workspace. Mirrors the team / code page architecture
 * (`ThreadProviders → ChatBox → ChatPageLayout`) so headers, message
 * list, composer, and Welcome state all share the same Echo-style
 * design. No file tree, no team-mode picker — just the conversation.
 */
export default function RealtimePage() {
  const chatState = useThreadChat();

  return (
    <ArtifactsProvider threadId={chatState.threadId}>
      <RealtimePageContent chatState={chatState} />
    </ArtifactsProvider>
  );
}

function RealtimePageContent({
  chatState,
}: {
  chatState: ReturnType<typeof useThreadChat>;
}) {
  const { t } = useI18n();
  const { authStatus, user } = useAuth();
  const { threadId, isNewThread, setIsNewThread } = chatState;
  const isMobile = useIsMobile();
  const {
    artifacts,
    open: artifactsOpen,
    select: selectArtifact,
    setOpen: setArtifactsOpen,
    setArtifacts,
  } = useArtifacts();
  const [settings, setSettings] = useThreadSettings(threadId);
  const [mounted, setMounted] = useState(false);
  const [, setShowPreview] = useState(false);
  const [researchJob, setResearchJob] = useState<ResearchJob | null>(null);
  const [researchLoading, setResearchLoading] = useState(false);
  const [researchError, setResearchError] = useState<string | null>(null);
  const [showResearch, setShowResearch] = useState(false);
  const [showResearchHistory, setShowResearchHistory] = useState(false);
  const [showAgentPlan, setShowAgentPlan] = useState(false);
  const [agentWorkbenchTab, setAgentWorkbenchTab] =
    useState<AgentWorkbenchTabId>("agent");
  const [agentWorkbenchTabTouched, setAgentWorkbenchTabTouched] =
    useState(false);
  const [agentWorkbenchDismissed, setAgentWorkbenchDismissed] = useState(false);
  const [agentWorkbenchManuallyOpened, setAgentWorkbenchManuallyOpened] =
    useState(() => (isNewThread ? false : readAgentWorkbenchOpenPreference()));
  const [focusedWorkbenchAgentId, setFocusedWorkbenchAgentId] = useState<
    string | null
  >(null);
  // Which sub-view the focus event asked for; lives and dies with
  // focusedWorkbenchAgentId (set together, cleared together).
  const [focusedWorkbenchAgentView, setFocusedWorkbenchAgentView] =
    useState<AgentWorkbenchFocusView | null>(null);
  const [focusedWorkbenchAgentSnapshot, setFocusedWorkbenchAgentSnapshot] =
    useState<AgentWorkbenchFocusAgentSnapshot | null>(null);
  const [focusedWorkbenchTurnIndex, setFocusedWorkbenchTurnIndex] = useState<
    number | null
  >(null);
  // Bumped on every focus emission so the panel treats a repeat focus of the
  // same agent (e.g. a view switch) as a fresh intent.
  const [focusedWorkbenchAgentNonce, setFocusedWorkbenchAgentNonce] =
    useState(0);
  const [focusedWorkbenchEventId, setFocusedWorkbenchEventId] = useState<
    string | null
  >(null);
  const [focusedWorkbenchEventKind, setFocusedWorkbenchEventKind] =
    useState<AgentWorkbenchProcessEventKind | null>(null);
  const [focusedWorkbenchEventView, setFocusedWorkbenchEventView] =
    useState<AgentWorkbenchEventView | null>(null);
  const [focusedWorkbenchEventNonce, setFocusedWorkbenchEventNonce] =
    useState(0);
  const [focusedWorkbenchProcessEvent, setFocusedWorkbenchProcessEvent] =
    useState<AgentWorkbenchProcessEventSnapshot | null>(null);
  const [focusedWorkbenchEffectKey, setFocusedWorkbenchEffectKey] = useState<
    string | null
  >(null);
  const settledWorkbenchAutoDismissedRef = useRef<string | null>(null);
  const emptyWorkbenchAutoDismissedRef = useRef<string | null>(null);
  const [discussionOnly, setDiscussionOnly] = useState(false);
  const [chatsDrawerOpen, setChatsDrawerOpen] = useState(false);
  // 助理专属：右侧内嵌「自动化 / 订阅」管理面板开关。
  const [showAutomationPanel, setShowAutomationPanel] = useState(false);
  const recorderPluginEnabled = useCapabilitySurface("chat.recorder");
  const [showTeachRepeatPanel, setShowTeachRepeatPanel] = useState(false);
  const closeSpecialUtilityPanels = useCallback(() => {
    setShowTeachRepeatPanel(false);
    setShowAutomationPanel(false);
  }, []);
  const openTeachRepeatPanel = useCallback(() => {
    if (!recorderPluginEnabled) return;
    setShowAutomationPanel(false);
    setShowTeachRepeatPanel(true);
  }, [recorderPluginEnabled]);
  const toggleAutomationPanel = useCallback(() => {
    setShowTeachRepeatPanel(false);
    setShowAutomationPanel((open) => !open);
  }, []);
  const [projectAgentMode, setProjectAgentMode] =
    useState<AgentModeName>("develop");
  const [auditIntensity, setAuditIntensity] =
    useState<AuditIntensity>("standard");
  const [projectDetection, setProjectDetection] =
    useState<DetectResponse | null>(null);
  // Whether the user manually overrode the auto-detected work mode. When true,
  // intent-based auto-switching only suggests (never silently switches).
  const [modeManualOverride, setModeManualOverride] = useState(false);
  // A pending intent-based mode suggestion surfaced above the composer.
  const [modeIntentSuggestion, setModeIntentSuggestion] = useState<{
    mode: AgentModeName;
    label: string;
  } | null>(null);
  // Personal-space work mode (general/build/research) — only meaningful when no
  // project dir is bound; threaded into the turn context as personal_mode. It no
  // longer downgrades capability: personal space still runs against an isolated
  // coding workspace, while a selected folder binds a user project workspace.
  const [personalMode, setPersonalMode] = useState<PersonalMode>(
    () => settings.personal_space.default_mode,
  );
  const lastPersonalDefaultRef = useRef(settings.personal_space.default_mode);
  useEffect(() => {
    const nextDefault = settings.personal_space.default_mode;
    if (lastPersonalDefaultRef.current === nextDefault) return;
    lastPersonalDefaultRef.current = nextDefault;
    setPersonalMode(nextDefault);
  }, [settings.personal_space.default_mode]);
  const handlePersonalModeChange = useCallback(
    (nextMode: PersonalMode) => {
      setPersonalMode(nextMode);
      if (settings.personal_space.remember_last_mode) {
        lastPersonalDefaultRef.current = nextMode;
        setSettings("personal_space", { default_mode: nextMode });
      }
    },
    [setSettings, settings.personal_space.remember_last_mode],
  );
  // REC floating recorder overlay (replaces the old confirm() start/stop flow).
  const [recOverlayOpen, setRecOverlayOpen] = useState(false);
  const [recIsRecording, setRecIsRecording] = useState(false);
  useEffect(() => {
    if (!recorderPluginEnabled || isNewThread || !threadId) {
      setRecIsRecording(false);
      setRecOverlayOpen(false);
      setShowTeachRepeatPanel(false);
      return;
    }

    let cancelled = false;
    void getRecordingStatus(threadId)
      .then((status) => {
        if (!cancelled) setRecIsRecording(status.recording);
      })
      .catch((error) => swallow(error, "teach-repeat-header-status"));

    return () => {
      cancelled = true;
    };
  }, [isNewThread, recorderPluginEnabled, threadId]);
  // Work directory for Agent project/code state. Empty means the thread uses its
  // isolated personal coding workspace; selecting a local folder binds a user
  // project directory without mixing it with the separate Team workspace.
  const [workDir, setWorkDir] = useState<string>(() =>
    isNewThread ? "" : readRememberedChatWorkDir(),
  );
  const [automationTarget, setAutomationTarget] =
    useState<AutomationTarget | null>(() => loadAutomationTarget(threadId));
  const localStartedThreadIdRef = useRef<string | null>(null);
  const handleWorkDirChange = useCallback((dir: string) => {
    setWorkDir(dir);
    rememberChatWorkDir(dir);
  }, []);
  const handleAutomationTargetChange = useCallback(
    (target: AutomationTarget | null) => {
      setAutomationTarget(target);
      saveAutomationTarget(threadId, target);
    },
    [threadId],
  );
  useEffect(() => {
    setAutomationTarget(loadAutomationTarget(threadId));
  }, [threadId]);
  const threadWorkspaceQuery = useQuery({
    queryKey: ["thread", "workspace-path", threadId],
    enabled:
      !isNewThread &&
      Boolean(threadId) &&
      localStartedThreadIdRef.current !== threadId,
    queryFn: async () => {
      // Realtime/SSE streams share localhost's HTTP/1.1 connection pool. A
      // separate loopback alias keeps this binding lookup from sitting behind
      // those streams and flashing "个人空间" for several seconds.
      const response = await fetch(
        `${getControlPlaneBaseURL()}/api/threads/${encodeURIComponent(threadId)}/state`,
        { headers: authHeaders() },
      );
      if (!response.ok) {
        throw new Error(`Thread workspace unavailable (${response.status})`);
      }
      const state = (await response.json()) as {
        metadata?: Record<string, unknown>;
      };
      const workspacePath = state.metadata?.["workspace_path"];
      return typeof workspacePath === "string" && isAbsolutePath(workspacePath)
        ? workspacePath
        : "";
    },
    refetchOnWindowFocus: false,
  });
  const threadIdentityQuery = useQuery({
    queryKey: ["thread", "identity", threadId],
    enabled:
      !isNewThread &&
      Boolean(threadId) &&
      localStartedThreadIdRef.current !== threadId,
    queryFn: async () => getAPIClient().threads.get(threadId),
    refetchOnWindowFocus: false,
    retry: false,
  });
  // The live stream state has no ``title`` (the realtime adapter maps only the
  // turn stream), so resolve the header/browser-tab title from the persisted
  // thread record — same derivation the sidebar uses.
  const headerThreadTitle = useMemo(
    () =>
      threadIdentityQuery.data
        ? deriveThreadTitle(threadIdentityQuery.data)
        : undefined,
    [threadIdentityQuery.data],
  );
  const [collaboratorPickerOpen, setCollaboratorPickerOpen] = useState(false);
  const coworkGroupQuery = useCoworkGroup(isNewThread ? null : threadId);
  const collabSessionQuery = useCollabSession(isNewThread ? null : threadId);
  const boundProjectQuery = useBoundProjectState(isNewThread ? null : threadId);
  const detachProjectFromGroupMutation = useDetachProjectFromGroup();
  const { confirm: confirmProjectDetach, confirmDialog: projectDetachDialog } =
    useConfirmDialog();
  const replaceCoworkRosterMutation = useReplaceCoworkRoster();
  const ensureCollabRoomMutation = useEnsureCollabRoom();
  const postCollabRoomMessageMutation = usePostCollabRoomMessage();
  const applyRoomMessageProjectActionMutation =
    useApplyCollabRoomMessageProjectAction();
  const persistedThreadWorkspacePath = threadWorkspaceQuery.data ?? "";

  useEffect(() => {
    if (
      isNewThread ||
      threadWorkspaceQuery.isPending ||
      localStartedThreadIdRef.current === threadId
    ) {
      return;
    }
    if (!persistedThreadWorkspacePath) {
      // A transient query failure (the thread is not persisted yet — e.g.
      // the throwaway uuid /new still holds while the first turn is
      // streaming) must NOT be read as "no bound workspace". Treating that
      // 404 as empty wiped the user's bound folder and remembered workdir
      // mid-conversation, which is the "bound but still lost" bug.
      if (threadWorkspaceQuery.isSuccess) {
        setWorkDir("");
        rememberChatWorkDir("");
      }
      return;
    }
    setWorkDir((current) => {
      if (
        normalizeWorkDirKey(current) ===
        normalizeWorkDirKey(persistedThreadWorkspacePath)
      ) {
        return current;
      }
      rememberChatWorkDir(persistedThreadWorkspacePath);
      return persistedThreadWorkspacePath;
    });
  }, [
    isNewThread,
    persistedThreadWorkspacePath,
    threadId,
    threadWorkspaceQuery.isPending,
    threadWorkspaceQuery.isSuccess,
    threadWorkspaceQuery.isError,
  ]);

  const { models } = useModels();
  const { agents: builtinAgents } = useAgents();
  const hasPersistedCollaboration = Boolean(
    collabSessionQuery.data?.room_id ||
    (collabSessionQuery.data &&
      (collabSessionQuery.data.roster.length > 1 ||
        collabSessionQuery.data.mode !== "chat")) ||
    (coworkGroupQuery.data &&
      (coworkGroupQuery.data.state.roster.length > 1 ||
        coworkGroupQuery.data.state.mode !== "chat")),
  );
  const { mobileAgents } = useMobileDevices({
    enabled: collaboratorPickerOpen || hasPersistedCollaboration,
  });
  const allTaskCollaboratorAgents = useMemo(
    () =>
      dedupePersonaAgentsByDisplayName(
        dedupeAgentsByName([...mobileAgents, ...builtinAgents]),
      ),
    [builtinAgents, mobileAgents],
  );
  const collaborationMentionMembers = useMemo(
    () =>
      coworkSessionToMentionMembers(
        collabSessionQuery.data,
        allTaskCollaboratorAgents.map((agent) => ({
          name: agent.name,
          display_name: agent.display_name,
          icon: agent.icon,
          description: agent.description,
          avatar_url: agent.avatar_url,
        })),
      ),
    [allTaskCollaboratorAgents, collabSessionQuery.data],
  );
  const [selectedCollaboratorIds, setSelectedCollaboratorIds] = useState<
    string[]
  >([]);
  const [teamModeIntent, setTeamModeIntent] = useState<TeamMode>("chat");
  const [groupTaskStrategy, setGroupTaskStrategy] =
    useState<GroupTaskStrategy>("auto");
  const [humanInviteDialogOpen, setHumanInviteDialogOpen] = useState(false);
  const [humanInviteRoomId, setHumanInviteRoomId] = useState("");
  const [promoteGroupDialogOpen, setPromoteGroupDialogOpen] = useState(false);
  const collaboratorSelectionTouchedRef = useRef(false);
  const responseModeIntentTouchedRef = useRef(false);
  const pendingRosterModeRef = useRef<TeamMode | null>(null);
  const lastCoworkSyncSignatureRef = useRef<string | null>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        AGENT_WORKBENCH_OPEN_KEY,
        agentWorkbenchManuallyOpened ? "1" : "0",
      );
    } catch (e) {
      swallow(e, "storage");
    }
  }, [agentWorkbenchManuallyOpened]);

  useEffect(() => {
    collaboratorSelectionTouchedRef.current = false;
    responseModeIntentTouchedRef.current = false;
    pendingRosterModeRef.current = null;
    lastCoworkSyncSignatureRef.current = null;
    setSelectedCollaboratorIds([]);
    setTeamModeIntent("chat");
    setGroupTaskStrategy("auto");
    setHumanInviteDialogOpen(false);
    setHumanInviteRoomId("");
    setPromoteGroupDialogOpen(false);
  }, [threadId]);

  useEffect(() => {
    setAgentWorkbenchTabTouched(false);
    setFocusedWorkbenchAgentId(null);
    setFocusedWorkbenchAgentView(null);
    setFocusedWorkbenchAgentSnapshot(null);
    setFocusedWorkbenchTurnIndex(null);
    setFocusedWorkbenchEventId(null);
    setFocusedWorkbenchEventKind(null);
    setFocusedWorkbenchEventView(null);
    setFocusedWorkbenchEffectKey(null);
  }, [threadId]);

  useEffect(() => {
    if (!isNewThread) return;
    setAgentWorkbenchManuallyOpened(false);
    setAgentWorkbenchTabTouched(false);
  }, [isNewThread, threadId]);

  const navigate = useNavigate();
  const location = useLocation();
  const routeState = (location.state as ThreadRouteState | null) ?? null;
  const projectWorkbenchRouteOpenedRef = useRef<string | null>(null);
  const humanInviteRouteOpenedRef = useRef<string | null>(null);
  const isProjectHomeThread = Boolean(
    threadIdentityQuery.data?.metadata?.["project_home"] ||
    threadIdentityQuery.data?.values?.["project_home"],
  );
  useEffect(() => {
    const shouldOpen =
      routeState?.openProjectWorkbench ||
      isProjectHomeThread ||
      Boolean(boundProjectQuery.data);
    if (
      isNewThread ||
      !shouldOpen ||
      projectWorkbenchRouteOpenedRef.current === threadId
    ) {
      return;
    }
    projectWorkbenchRouteOpenedRef.current = threadId;
    closeSpecialUtilityPanels();
    setArtifactsOpen(false);
    setShowAgentPlan(false);
    setShowResearchHistory(false);
    setShowResearch(false);
    setShowPreview(false);
    setAgentWorkbenchDismissed(false);
    setAgentWorkbenchManuallyOpened(true);
    setAgentWorkbenchTab("project");
    setAgentWorkbenchTabTouched(true);
  }, [
    isNewThread,
    isProjectHomeThread,
    boundProjectQuery.data,
    closeSpecialUtilityPanels,
    routeState?.openProjectWorkbench,
    setArtifactsOpen,
    threadId,
  ]);
  const params = useParams<{ agentName?: string }>();
  const qc = useQueryClient();
  const searchParams = useMemo(
    () => new URLSearchParams(location.search),
    [location.search],
  );
  const embeddedDesignChat =
    searchParams.get("embedded") === "design" ||
    (typeof window !== "undefined" &&
      window.parent !== window &&
      window.frameElement?.getAttribute("data-echo-design-chat") === "true");
  const embeddedDesignProject = searchParams.get("project")?.trim() || "";
  const embeddedCreationSpace =
    searchParams.get("creation_space")?.trim() || "";
  const embeddedCreativeProject =
    searchParams.get("creative_project")?.trim() || "";
  const initialPrompt = useMemo(() => {
    return searchParams.get("prompt") ?? "";
  }, [searchParams]);
  const queryAgentName = (searchParams.get("agent") ?? "").trim();
  const routeAgentName = useMemo(() => {
    const raw = params.agentName?.trim();
    if (!raw) return "";
    try {
      return decodeURIComponent(raw);
    } catch (e) {
      swallow(e);
      return raw;
    }
  }, [params.agentName]);
  const isAgentRoute = !!routeAgentName;
  const isRealtimeRoute = location.pathname.startsWith("/workspace/realtime");
  const memoryMode = searchParams.get("memory") ?? "";
  const queryWorkspacePath = searchParams.get("workspace_path") ?? "";
  const storedActiveAgentId = useActiveAgentId();

  // Unified task routes carry the selected persona in ?agent= while every chat
  // thread stays on the /workspace/realtime/* surface.
  const requestedTaskAgentId =
    routeAgentName || (queryAgentName === "echo" ? "" : queryAgentName);
  const activeAgentId = isNewThread
    ? isPrimaryPersonaAgentId(requestedTaskAgentId)
      ? requestedTaskAgentId
      : primaryPersonaAgentIdOrDefault(storedActiveAgentId)
    : requestedTaskAgentId || storedActiveAgentId || "general";
  const { agent: activeAgent } = useAgent(activeAgentId);
  const hintedThreadOwnerAgentId = routeState?.threadOwnerAgentId?.trim() || "";
  const hintedWorkspacePath =
    typeof routeState?.workspacePath === "string" &&
    isAbsolutePath(routeState.workspacePath)
      ? routeState.workspacePath
      : isAbsolutePath(queryWorkspacePath)
        ? queryWorkspacePath
        : "";
  const threadOwnerAgentId = useMemo(
    () =>
      threadOwnerAgentFromMetadata(
        threadIdentityQuery.data?.metadata,
        threadIdentityQuery.data?.values,
      ),
    [threadIdentityQuery.data],
  );
  const resolvedThreadOwnerAgentId =
    threadOwnerAgentId || hintedThreadOwnerAgentId;
  const legacyOnDemandThreadOwnerId =
    !isNewThread &&
    resolvedThreadOwnerAgentId &&
    resolvedThreadOwnerAgentId !== "echo" &&
    !isPrimaryPersonaAgentId(resolvedThreadOwnerAgentId)
      ? resolvedThreadOwnerAgentId
      : "";
  const allowThreadFork = !legacyOnDemandThreadOwnerId;
  // 「助手」侧栏入口使用固定的 echo-assistant 持久会话（见
  // workspace-sidebar 的 ECHO_THREAD_ID）。历史上该线程可能因
  // 创建时选中的 agent 而写入 general 等身份，这里在渲染层强制归位
  // 为 echo，避免助手页面被解析成别的 agent；发送层 agent_name
  // 同源修正存量数据。
  const effectiveAgentId = isNewThread
    ? activeAgentId
    : (threadId === "echo-assistant" ? "echo" : resolvedThreadOwnerAgentId) ||
      activeAgentId;

  // A bound project owns the right-hand surface. Otherwise the persona's
  // preset is the default, with the user's last manual tab remembered per
  // persona. Runtime focus events still take precedence by marking the tab as
  // touched before this passive defaulting effect can run.
  useEffect(() => {
    if (boundProjectQuery.isPending || agentWorkbenchTabTouched) return;
    setAgentWorkbenchTab(
      preferredWorkbenchTab(effectiveAgentId, Boolean(boundProjectQuery.data)),
    );
  }, [
    agentWorkbenchTabTouched,
    boundProjectQuery.data,
    boundProjectQuery.isPending,
    effectiveAgentId,
    threadId,
  ]);
  // 助理（echo）是私人助手本体：不走编码/工作空间工作台，固定进入
  // 纯对话长对话，隐藏工作空间选择器。
  const isEchoAssistant = effectiveAgentId === "echo";
  const { agent: effectiveAgent } = useAgent(
    isEchoAssistant ? effectiveAgentId : null,
  );

  const channelsStatusQuery = useQuery({
    queryKey: ["channels-status"],
    queryFn: getChannelsStatus,
    enabled: isEchoAssistant,
    refetchInterval: 30000,
    staleTime: 10000,
  });

  const connectedChannels = useMemo(() => {
    if (!channelsStatusQuery.data?.channels) return [];
    return Object.entries(channelsStatusQuery.data.channels)
      .filter(([, status]) => status.enabled && status.running)
      .map(([name]) => name as ChannelName);
  }, [channelsStatusQuery.data]);

  const channelDisplayNames: Record<string, string> = {
    wechat: "微信",
    dingtalk: "钉钉",
    feishu: "飞书",
    wecom: "企业微信",
    telegram: "Telegram",
    slack: "Slack",
    discord: "Discord",
  };
  const { agent: threadOwnerAgent } = useAgent(
    resolvedThreadOwnerAgentId && resolvedThreadOwnerAgentId !== activeAgentId
      ? resolvedThreadOwnerAgentId
      : null,
  );
  const displayAgent = isEchoAssistant
    ? effectiveAgent
    : resolvedThreadOwnerAgentId && resolvedThreadOwnerAgentId !== activeAgentId
      ? threadOwnerAgent
      : activeAgent;
  const selectedExecutionEngine =
    displayAgent?.capabilities?.execution_backend === "codex_app_server"
      ? ("codex" as const)
      : ("echo" as const);
  const currentTaskAgentName = displayAgent?.name ?? effectiveAgentId;
  const composerDisplayAgent = useMemo(
    () =>
      displayAgent ?? {
        name: effectiveAgentId,
        display_name: effectiveAgentId,
        avatar_url: null,
        icon: null,
      },
    [displayAgent, effectiveAgentId],
  );
  const selectedCollaborators = useMemo(() => {
    const selected = new Set(selectedCollaboratorIds);
    return allTaskCollaboratorAgents.filter((agent) =>
      selected.has(agent.name),
    );
  }, [allTaskCollaboratorAgents, selectedCollaboratorIds]);
  const persistedCollaborationRoster = useMemo(
    () =>
      collaborationRosterFromThread(
        threadIdentityQuery.data?.metadata,
        threadIdentityQuery.data?.values,
        currentTaskAgentName,
      ),
    [
      currentTaskAgentName,
      threadIdentityQuery.data?.metadata,
      threadIdentityQuery.data?.values,
    ],
  );
  const coworkCollaborationProfiles = useMemo(
    () => [composerDisplayAgent, ...allTaskCollaboratorAgents],
    [allTaskCollaboratorAgents, composerDisplayAgent],
  );
  const coworkCollaborationRoster = useMemo(() => {
    const sessionRoster = coworkSessionToCollaborationRoster(
      collabSessionQuery.data,
      currentTaskAgentName,
      coworkCollaborationProfiles,
    );
    const groupRoster = coworkGroupToCollaborationRoster(
      coworkGroupQuery.data,
      currentTaskAgentName,
      coworkCollaborationProfiles,
    );
    if (sessionRoster.length === 0) return groupRoster;
    if (groupRoster.length === 0) return sessionRoster;
    const seen = new Map<string, ChatCollaborationRosterEntry>();
    for (const entry of sessionRoster) seen.set(entry.agent_id, entry);
    for (const entry of groupRoster) {
      if (!seen.has(entry.agent_id)) seen.set(entry.agent_id, entry);
    }
    return Array.from(seen.values());
  }, [
    collabSessionQuery.data,
    coworkCollaborationProfiles,
    coworkGroupQuery.data,
    currentTaskAgentName,
  ]);
  const savedCollaborationRoster = useMemo(() => {
    if (coworkCollaborationRoster.length > 0) return coworkCollaborationRoster;
    return persistedCollaborationRoster;
  }, [coworkCollaborationRoster, persistedCollaborationRoster]);
  const persistedCollaboratorIds = useMemo(
    () =>
      savedCollaborationRoster
        .filter(
          (agent) =>
            agent.role !== "tl" && agent.agent_id !== currentTaskAgentName,
        )
        .map((agent) => agent.agent_id),
    [currentTaskAgentName, savedCollaborationRoster],
  );
  const persistedCollaboratorKey = persistedCollaboratorIds.join("\u0000");
  const savedCollaborationMode =
    collabSessionQuery.data?.mode ?? coworkGroupQuery.data?.state.mode;
  const applyTaskCollaboratorPreset = useCallback(
    (preset: TaskCollaboratorPreset) => {
      const nextIds = Array.from(
        new Set(
          (preset.collaboratorIds ?? [])
            .map((id) => id.trim())
            .filter((id) => id && id !== currentTaskAgentName),
        ),
      );
      collaboratorSelectionTouchedRef.current = true;
      setSelectedCollaboratorIds(nextIds);
      setTeamModeIntent(
        nextIds.length > 0
          ? normalizeTeamResponseMode(preset.mode ?? "cluster")
          : "chat",
      );
      if (preset.openPicker) {
        setCollaboratorPickerOpen(true);
      }
    },
    [currentTaskAgentName],
  );
  useEffect(() => {
    const storedPreset = consumeTaskCollaboratorPreset();
    if (storedPreset) {
      applyTaskCollaboratorPreset(storedPreset);
    }
    const handler = (event: Event) => {
      const preset = (event as CustomEvent<TaskCollaboratorPreset>).detail;
      if (preset) {
        applyTaskCollaboratorPreset(preset);
      }
    };
    window.addEventListener(TASK_COLLABORATOR_PRESET_EVENT, handler);
    return () =>
      window.removeEventListener(TASK_COLLABORATOR_PRESET_EVENT, handler);
  }, [applyTaskCollaboratorPreset]);
  useEffect(() => {
    if (
      embeddedDesignChat ||
      isNewThread ||
      threadIdentityQuery.isPending ||
      localStartedThreadIdRef.current === threadId
    ) {
      return;
    }
    if (collaboratorSelectionTouchedRef.current) {
      return;
    }
    setSelectedCollaboratorIds((current) =>
      current.join("\u0000") === persistedCollaboratorKey
        ? current
        : persistedCollaboratorIds,
    );
    if (
      !responseModeIntentTouchedRef.current &&
      persistedCollaboratorIds.length > 0
    ) {
      setTeamModeIntent(normalizeTeamResponseMode(savedCollaborationMode));
    }
  }, [
    embeddedDesignChat,
    isNewThread,
    persistedCollaboratorKey,
    persistedCollaboratorIds,
    savedCollaborationMode,
    threadId,
    threadIdentityQuery.isPending,
  ]);
  const selectedCollaboratorKey = selectedCollaboratorIds.join("\u0000");
  useEffect(() => {
    if (isNewThread || !threadId || threadId === "new") return;
    // Project membership decides whether the lead agent must remain in the
    // durable roster. Never reconcile against the query's transient empty
    // state during a hard refresh.
    if (boundProjectQuery.isPending) return;

    const startedLocally = localStartedThreadIdRef.current === threadId;
    const userTouched = collaboratorSelectionTouchedRef.current;
    const matchesSavedRoster =
      selectedCollaboratorKey === persistedCollaboratorKey;
    if (!startedLocally && !userTouched && !matchesSavedRoster) return;
    const sessionState = collabSessionQuery.data
      ? {
          roster: collabSessionQuery.data.roster,
          mode: collabSessionQuery.data.mode,
          event_count: coworkGroupQuery.data?.state.event_count ?? 0,
          is_one_to_one:
            collabSessionQuery.data.roster.filter(
              (member) => member.kind === "agent",
            ).length <= 1 &&
            collabSessionQuery.data.roster.filter(
              (member) => member.kind === "human",
            ).length <= 1,
          room_id: collabSessionQuery.data.room_id,
        }
      : null;
    const currentCoworkState =
      sessionState ?? coworkGroupQuery.data?.state ?? null;
    if (
      currentCoworkState === null &&
      (collabSessionQuery.isPending || coworkGroupQuery.isPending)
    ) {
      return;
    }

    const plan = buildCoworkSelectionSyncPlan({
      leaderId: currentTaskAgentName,
      collaboratorIds: selectedCollaboratorIds,
      mode:
        pendingRosterModeRef.current ??
        normalizeTeamResponseMode(savedCollaborationMode),
      current: currentCoworkState,
      keepLeader: Boolean(boundProjectQuery.data),
    });
    if (!plan.hasWork) return;

    const signature = `${threadId}|${plan.signature}`;
    if (lastCoworkSyncSignatureRef.current === signature) return;
    lastCoworkSyncSignatureRef.current = signature;

    replaceCoworkRosterMutation.mutate(
      {
        threadId,
        input: { agent_ids: plan.desiredAgentIds, mode: plan.mode },
      },
      {
        onSuccess: () => {
          collaboratorSelectionTouchedRef.current = false;
          pendingRosterModeRef.current = null;
        },
        onError: () => {
          // The picker is a draft until the one atomic server write succeeds.
          // Roll back visibly on failure instead of showing members that will
          // disappear on refresh.
          collaboratorSelectionTouchedRef.current = false;
          pendingRosterModeRef.current = null;
          setSelectedCollaboratorIds(persistedCollaboratorIds);
          setTeamModeIntent(
            persistedCollaboratorIds.length > 0
              ? normalizeTeamResponseMode(savedCollaborationMode)
              : "chat",
          );
          toast.error("AI 成员保存失败，请重试");
        },
      },
    );
  }, [
    collabSessionQuery.data,
    collabSessionQuery.isPending,
    coworkGroupQuery.data?.state,
    coworkGroupQuery.data,
    coworkGroupQuery.isPending,
    boundProjectQuery.data,
    boundProjectQuery.isPending,
    currentTaskAgentName,
    isNewThread,
    persistedCollaboratorKey,
    persistedCollaboratorIds,
    replaceCoworkRosterMutation,
    savedCollaborationMode,
    selectedCollaboratorIds,
    selectedCollaboratorKey,
    teamModeIntent,
    threadId,
  ]);
  const handleSelectedCollaboratorIdsChange = useCallback(
    (ids: string[]) => {
      const leader = currentTaskAgentName.trim();
      const nextIds = Array.from(
        new Set(ids.map((id) => id.trim()).filter((id) => id && id !== leader)),
      );
      collaboratorSelectionTouchedRef.current = true;
      pendingRosterModeRef.current =
        nextIds.length === 0
          ? "chat"
          : persistedCollaboratorIds.length === 0
            ? "cluster"
            : normalizeTeamResponseMode(savedCollaborationMode);
      setSelectedCollaboratorIds(nextIds);
      if (nextIds.length === 0) {
        setTeamModeIntent("chat");
      }
    },
    [
      currentTaskAgentName,
      persistedCollaboratorIds.length,
      savedCollaborationMode,
    ],
  );
  const handleTeamModeIntentChange = useCallback((mode: TeamMode) => {
    responseModeIntentTouchedRef.current = true;
    setTeamModeIntent(mode);
  }, []);
  const collaborationRoster = useMemo<ChatCollaborationRosterEntry[]>(() => {
    const leaderName = composerDisplayAgent.name?.trim() || effectiveAgentId;
    const roster: ChatCollaborationRosterEntry[] = [
      {
        agent_id: leaderName,
        name: leaderName,
        display_name:
          composerDisplayAgent.display_name?.trim() ||
          composerDisplayAgent.name?.trim() ||
          leaderName,
        avatar_url: composerDisplayAgent.avatar_url ?? null,
        icon: composerDisplayAgent.icon ?? null,
        role: "tl",
      },
    ];
    for (const agent of selectedCollaborators) {
      if (!agent.name || agent.name === leaderName) continue;
      roster.push({
        agent_id: agent.name,
        name: agent.name,
        display_name: agent.display_name?.trim() || agent.name,
        avatar_url: agent.avatar_url ?? null,
        icon: agent.icon ?? null,
        role: "member",
      });
    }
    return roster;
  }, [composerDisplayAgent, effectiveAgentId, selectedCollaborators]);
  const collaborationEnabled =
    !embeddedDesignChat && selectedCollaborators.length > 0;
  const visibleCollaborationRoster = useMemo(() => {
    const primary =
      collaborationEnabled || savedCollaborationRoster.length === 0
        ? collaborationRoster
        : savedCollaborationRoster;
    const secondary =
      primary === collaborationRoster
        ? savedCollaborationRoster
        : collaborationRoster;
    if (secondary.length === 0) {
      return hydrateCollaborationRoster(primary, coworkCollaborationProfiles);
    }
    const seen = new Map<string, ChatCollaborationRosterEntry>();
    for (const entry of primary) seen.set(entry.agent_id, entry);
    for (const entry of secondary) {
      if (!seen.has(entry.agent_id)) seen.set(entry.agent_id, entry);
    }
    return hydrateCollaborationRoster(
      Array.from(seen.values()),
      coworkCollaborationProfiles,
    );
  }, [
    collaborationEnabled,
    collaborationRoster,
    coworkCollaborationProfiles,
    savedCollaborationRoster,
  ]);
  const visibleCollaborationEnabled =
    !embeddedDesignChat && visibleCollaborationRoster.length > 1;
  const isGroupConversation =
    !embeddedDesignChat &&
    !isEchoAssistant &&
    (visibleCollaborationEnabled ||
      Boolean(collabSessionQuery.data?.room_id) ||
      Boolean(boundProjectQuery.data));
  const collaborationRosterSeats = useMemo<WorkbenchRosterSeat[]>(() => {
    const seats = new Map<string, WorkbenchRosterSeat>();
    for (const agent of visibleCollaborationRoster) {
      seats.set(`agent:${agent.agent_id}`, {
        id: agent.agent_id,
        name: agent.display_name,
        avatarUrl: agent.avatar_url ?? null,
        icon: agent.icon ?? null,
        role: agent.role,
        kind: "agent",
      });
    }
    for (const participant of collabSessionQuery.data?.room_participants ??
      []) {
      const rawId =
        participant.id ?? participant.participant_id ?? participant.name;
      const id = typeof rawId === "string" ? rawId.trim() : "";
      if (!id) continue;
      const rawName = participant.display_name ?? participant.name;
      const name =
        typeof rawName === "string" && rawName.trim() ? rawName.trim() : id;
      const rawRole = participant.role;
      const role =
        rawRole === "owner"
          ? "群主"
          : rawRole === "viewer"
            ? "访客"
            : rawRole === "member"
              ? "群成员"
              : typeof rawRole === "string"
                ? rawRole
                : "群成员";
      const rawAvatar = participant.avatar_url;
      seats.set(`human:${id}`, {
        id,
        name,
        avatarUrl: typeof rawAvatar === "string" ? rawAvatar : null,
        role,
        kind: participant.kind === "agent" ? "agent" : "human",
      });
    }
    return Array.from(seats.values());
  }, [collabSessionQuery.data?.room_participants, visibleCollaborationRoster]);
  const collaborationTeamName =
    boundProjectQuery.data?.project.name ||
    firstString(threadIdentityQuery.data?.values?.title, initialPrompt) ||
    t.collab.defaultTeamName;
  const currentInviteActor = currentActorId();
  const currentRoomParticipant = useMemo(
    () =>
      (collabSessionQuery.data?.room_participants ?? []).find((participant) => {
        const actorId =
          typeof participant.actor_id === "string"
            ? participant.actor_id.trim()
            : "";
        const participantId =
          typeof participant.id === "string" ? participant.id.trim() : "";
        return (
          actorId === currentInviteActor ||
          participantId === currentInviteActor ||
          participantId === `actor-${currentInviteActor}`
        );
      }),
    [collabSessionQuery.data?.room_participants, currentInviteActor],
  );
  const canManageHumanInvites = useMemo(() => {
    const participantRole =
      typeof currentRoomParticipant?.role === "string"
        ? currentRoomParticipant.role.trim().toLowerCase()
        : "";
    if (participantRole) return participantRole === "owner";
    const metadata = threadIdentityQuery.data?.metadata;
    const ownerActorId =
      metadata && typeof metadata["owner_actor_id"] === "string"
        ? metadata["owner_actor_id"].trim()
        : "";
    return (
      !ownerActorId ||
      currentInviteActor === "anonymous" ||
      ownerActorId === currentInviteActor
    );
  }, [currentInviteActor, currentRoomParticipant, threadIdentityQuery.data]);
  const projectCapabilityAction = resolveGroupProjectCapabilityAction({
    isNewThread,
    isGroupConversation,
    hasBoundProject: Boolean(boundProjectQuery.data),
    canManageGroup: canManageHumanInvites,
  });
  const canPromoteGroupToProject = projectCapabilityAction === "create";
  const collaborationContext = useMemo(() => {
    if (!collaborationEnabled) return {};
    const isCoworkMode = teamModeIntent !== "chat";
    return {
      agent_name: effectiveAgentId,
      subagent_enabled: isCoworkMode,
      is_plan_mode: isCoworkMode,
      team_mode: isCoworkMode ? "cowork" : "chat",
      response_mode_override: teamModeIntent,
      serve_mesh: serveMeshForMode(teamModeIntent),
      topology_id: teamModeIntent === "cluster" ? "cowork" : undefined,
      agent_roster: collaborationRoster,
      team_members: collaborationRoster.map((agent) => agent.display_name),
      team_leader: collaborationRoster[0]?.display_name ?? effectiveAgentId,
      team_id: `thread:${threadId}`,
      team_name: collaborationTeamName,
      project: t.collab.projectPrefix(collaborationTeamName),
      task_agent_refs: selectedCollaborators.map((agent) => agent.name),
      task_agent_names: selectedCollaborators.map(
        (agent) => agent.display_name ?? agent.name,
      ),
    };
  }, [
    collaborationEnabled,
    collaborationRoster,
    collaborationTeamName,
    effectiveAgentId,
    selectedCollaborators,
    t,
    teamModeIntent,
    threadId,
  ]);
  const collaborationRoomMemberPayload = useMemo(
    () =>
      visibleCollaborationRoster.map((agent) => ({
        name: agent.agent_id,
        display_name: agent.display_name,
        description:
          agent.role === "tl"
            ? t.collab.common.leader
            : t.collab.common.aiMember,
        avatar_url: agent.avatar_url ?? undefined,
        icon: agent.icon ?? undefined,
      })),
    [t, visibleCollaborationRoster],
  );
  const collaborationRoomSignature = useMemo(
    () =>
      [
        threadId,
        collaborationTeamName,
        teamModeIntent,
        ...collaborationRoomMemberPayload.map((member) => member.name),
      ].join("\u0000"),
    [
      collaborationRoomMemberPayload,
      collaborationTeamName,
      teamModeIntent,
      threadId,
    ],
  );
  const resolvedHumanInviteRoomId =
    humanInviteRoomId || collabSessionQuery.data?.room_id || "";
  const ensureHumanInviteRoom = useCallback(async () => {
    if (isNewThread || !threadId || threadId === "new") {
      throw new Error("请先发送一条消息，再邀请真人加入群聊");
    }
    const response = await ensureCollabRoomMutation.mutateAsync({
      threadId,
      input: {
        id: `collab-${threadId}`,
        name: collaborationTeamName,
        members: collaborationRoomMemberPayload,
        leaderId: collaborationRoomMemberPayload[0]?.name ?? effectiveAgentId,
        mode: teamModeIntent,
      },
    });
    const roomId =
      response.session.room_id ||
      (typeof response.room.id === "string" ? response.room.id : "");
    if (!roomId) throw new Error("群聊房间创建失败，请重试");
    setHumanInviteRoomId(roomId);
    return roomId;
  }, [
    collaborationRoomMemberPayload,
    collaborationTeamName,
    effectiveAgentId,
    ensureCollabRoomMutation,
    isNewThread,
    teamModeIntent,
    threadId,
  ]);
  const handleOpenHumanInvite = useCallback(async () => {
    try {
      await ensureHumanInviteRoom();
      setHumanInviteDialogOpen(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "无法创建邀请链接");
    }
  }, [ensureHumanInviteRoom]);
  useEffect(() => {
    if (
      isNewThread ||
      !routeState?.openHumanInviteAfterCreate ||
      humanInviteRouteOpenedRef.current === threadId
    ) {
      return;
    }
    humanInviteRouteOpenedRef.current = threadId;
    const nextState = { ...routeState };
    delete nextState.openHumanInviteAfterCreate;
    void handleOpenHumanInvite().finally(() => {
      navigate(`${location.pathname}${location.search}`, {
        replace: true,
        state: nextState,
      });
    });
  }, [
    handleOpenHumanInvite,
    isNewThread,
    location.pathname,
    location.search,
    navigate,
    routeState,
    threadId,
  ]);
  const lastEnsuredCollabRoomRef = useRef<string | null>(null);
  useEffect(() => {
    if (
      embeddedDesignChat ||
      isNewThread ||
      !threadId ||
      threadId === "new" ||
      (!visibleCollaborationEnabled && !boundProjectQuery.data) ||
      collabSessionQuery.isPending ||
      collabSessionQuery.data?.room_id ||
      ensureCollabRoomMutation.isPending
    ) {
      return;
    }
    if (lastEnsuredCollabRoomRef.current === collaborationRoomSignature) {
      return;
    }
    lastEnsuredCollabRoomRef.current = collaborationRoomSignature;
    ensureCollabRoomMutation.mutate(
      {
        threadId,
        input: {
          id: `collab-${threadId}`,
          name: collaborationTeamName,
          members: collaborationRoomMemberPayload,
          leaderId: collaborationRoomMemberPayload[0]?.name ?? effectiveAgentId,
          mode: teamModeIntent,
        },
      },
      {
        onError: () => {
          if (lastEnsuredCollabRoomRef.current === collaborationRoomSignature) {
            lastEnsuredCollabRoomRef.current = null;
          }
        },
      },
    );
  }, [
    collabSessionQuery.data?.room_id,
    collabSessionQuery.isPending,
    boundProjectQuery.data,
    collaborationRoomMemberPayload,
    collaborationRoomSignature,
    collaborationTeamName,
    embeddedDesignChat,
    effectiveAgentId,
    ensureCollabRoomMutation,
    isNewThread,
    teamModeIntent,
    threadId,
    visibleCollaborationEnabled,
  ]);
  useEffect(() => {
    setSelectedCollaboratorIds((current) =>
      current.filter((id) => id !== currentTaskAgentName),
    );
  }, [currentTaskAgentName]);
  const effectiveReasoningEffort = normalizeReasoningEffortForUi(
    settings.context.reasoning_effort,
  );
  const routeMode = settings.context.mode;
  const effectiveWorkDir =
    !isNewThread &&
    threadWorkspaceQuery.isPending &&
    localStartedThreadIdRef.current !== threadId &&
    hintedWorkspacePath
      ? hintedWorkspacePath
      : workDir;
  const projectWorkspacePath = effectiveWorkDir.trim();
  const personalWorkspaceRoot = !projectWorkspacePath
    ? settings.personal_space.default_folder.trim()
    : "";
  const personalWorkspacePath = personalWorkspaceRoot
    ? embeddedDesignChat && embeddedCreationSpace
      ? joinPath(
          joinPath(personalWorkspaceRoot, "创作空间"),
          personalRoleFolderName(displayAgent, embeddedCreationSpace),
        )
      : joinPath(
          personalWorkspaceRoot,
          personalRoleFolderName(displayAgent, effectiveAgentId),
        )
    : "";
  const isProjectCodeMode = !!projectWorkspacePath;
  // When user has explicitly selected a named agent (not default "general", not echo)
  // via the footer selector, treat it as conversation mode rather than defaulting to code.
  const isExplicitAgentSelected =
    !!effectiveAgentId &&
    effectiveAgentId !== "general" &&
    effectiveAgentId !== "echo";
  const isExplicitConversationMode =
    isEchoAssistant ||
    isExplicitAgentSelected ||
    routeMode === "chat" ||
    routeMode === "flash" ||
    discussionOnly;
  const isCodingWorkspaceMode =
    isProjectCodeMode ||
    ((isAgentRoute || isRealtimeRoute) && !isExplicitConversationMode);
  // Code mode is available to every agent by default · per-agent unlock
  // flag removed. Tool/permission scoping lives in the skills &
  // permissions system, not a global gate.
  const codeModeUnlocked = true;
  const projectSignals = useMemo(() => {
    if (!isProjectCodeMode || !projectDetection) return undefined;
    const signals = projectDetection.signals;
    const compact: DetectionSignals = {
      workspace_path: projectWorkspacePath,
      exists: signals.exists,
      file_count: signals.file_count,
      manifests: signals.manifests?.slice(0, 8),
      structure_dirs: signals.structure_dirs?.slice(0, 12),
      git_commits: signals.git_commits,
      has_readme: signals.has_readme,
      lock_files: signals.lock_files?.slice(0, 8),
      commands: signals.commands?.slice(0, 8),
    };
    return {
      recommended_mode: projectDetection.recommended_mode,
      confidence: projectDetection.confidence,
      reason: projectDetection.reason,
      signals: compact,
    };
  }, [isProjectCodeMode, projectDetection, projectWorkspacePath]);
  const projectModePreset = useMemo(
    () => modePresetForAgentMode(projectAgentMode),
    [projectAgentMode],
  );
  const activeGroupTaskContext = useMemo(
    () => groupTaskStrategyContext(groupTaskStrategy),
    [groupTaskStrategy],
  );
  const effectiveMode: ReasoningMode = isEchoAssistant
    ? "chat"
    : isCodingWorkspaceMode
      ? "code"
      : isAgentRoute && routeMode === "deep"
        ? routeMode
        : isAgentRoute
          ? "react"
          : discussionOnly
            ? "chat"
            : "react";
  const streamMode: ReasoningMode | "team" = collaborationEnabled
    ? "team"
    : effectiveMode;
  const threadRouteFor = useCallback(
    (id: string) => {
      const path = `/workspace/realtime/${encodeURIComponent(id)}`;
      if (!embeddedDesignChat) return path;
      const query = new URLSearchParams({ embedded: "design" });
      if (embeddedDesignProject) query.set("project", embeddedDesignProject);
      if (embeddedCreationSpace)
        query.set("creation_space", embeddedCreationSpace);
      if (embeddedCreativeProject)
        query.set("creative_project", embeddedCreativeProject);
      return `${path}?${query.toString()}`;
    },
    [
      embeddedCreativeProject,
      embeddedCreationSpace,
      embeddedDesignChat,
      embeddedDesignProject,
    ],
  );
  const markSidebarThreadRunning = useCallback(
    (id: string) => {
      const targetThreadId = id.trim();
      if (!targetThreadId) return;
      eventBus.emit("thread:run-status", {
        href: threadRouteFor(targetThreadId),
        state: "running",
        threadId: targetThreadId,
      });
    },
    [threadRouteFor],
  );
  const clearSidebarThreadStatus = useCallback(
    (id: string) => {
      const targetThreadId = id.trim();
      if (!targetThreadId) return;
      eventBus.emit("thread:run-status", {
        href: threadRouteFor(targetThreadId),
        state: null,
        threadId: targetThreadId,
      });
    },
    [threadRouteFor],
  );
  const newThreadRouteForMode = useCallback(
    (mode: string, prompt?: string) => {
      const agentId =
        mode === "react" || mode === "deep" ? activeAgentId : "general";
      return taskWorkspaceRoute({ agentId, prompt });
    },
    [activeAgentId],
  );
  const openWorkDirInNewTask = useCallback(
    (dir: string) => {
      const next = dir.trim();
      if (!isAbsolutePath(next)) return;
      const route = taskWorkspaceRoute({
        agentId: effectiveAgentId,
        workspacePath: next,
      });
      const opened = window.open(
        new URL(toHashRouterShellUrl(route), window.location.origin).toString(),
        "_blank",
        "noopener,noreferrer",
      );
      if (opened) opened.opener = null;
    },
    [effectiveAgentId],
  );
  useEffect(() => {
    const handler = (event: Event) => {
      const path = (event as CustomEvent<{ path?: string }>).detail?.path;
      if (!path || !isAbsolutePath(path)) return;
      if (isNewThread) {
        handleWorkDirChange(path);
        return;
      }
      if (normalizeWorkDirKey(path) !== normalizeWorkDirKey(effectiveWorkDir)) {
        openWorkDirInNewTask(path);
      }
    };
    window.addEventListener("echo:workdir-selected", handler);
    return () => window.removeEventListener("echo:workdir-selected", handler);
  }, [
    effectiveWorkDir,
    handleWorkDirChange,
    isNewThread,
    openWorkDirInNewTask,
  ]);
  const routeWorkspaceHintKeyRef = useRef<string | null>(null);
  useEffect(() => {
    if (!isNewThread || !hintedWorkspacePath) return;
    const key = normalizeWorkDirKey(hintedWorkspacePath);
    if (routeWorkspaceHintKeyRef.current === key) return;
    routeWorkspaceHintKeyRef.current = key;
    handleWorkDirChange(hintedWorkspacePath);
  }, [handleWorkDirChange, hintedWorkspacePath, isNewThread]);

  // ── Agent-switch refresh ───────────────────────────────────
  // When the user picks a different agent in the footer dropdown while
  // looking at an existing thread, we need to:
  //   1. Leave the stale conversation window (it belonged to the old
  //      agent · showing its messages while the new agent answers the
  //      next turn is confusing and mixes personas).
  //   2. Invalidate the thread-list query so the sidebar re-fetches the
  //      new agent's threads (metadata.agent filter changed).
  // Skip the navigate+invalidate on the FIRST observed value (page
  // mount) — only react to actual changes.
  const [composerSeed, setComposerSeed] = useState(initialPrompt);
  const boundProjectState: ProjectFullState | null | undefined =
    boundProjectQuery.data;
  const projectMilestoneOptions = useMemo(
    () =>
      (boundProjectState?.milestones ?? []).map((milestone) => ({
        id: milestone.id,
        name: milestone.name,
        status: milestone.status,
      })),
    [boundProjectState?.milestones],
  );
  const defaultProjectMilestoneId = useMemo(() => {
    if (!boundProjectState) return undefined;
    return (
      boundProjectState.project.current_ms ??
      boundProjectState.milestones.find(
        (milestone) => milestone.status !== "done",
      )?.id ??
      boundProjectState.milestones[0]?.id
    );
  }, [boundProjectState]);
  const visibleRoomMessages = useMemo(
    () =>
      dedupeCoworkRoomMessages(collabSessionQuery.data?.room_messages ?? []),
    [collabSessionQuery.data?.room_messages],
  );
  const roomMessageMetadataBySourceId = useMemo(() => {
    const metadataById: Record<string, CoworkRoomMessage["metadata"]> = {};
    for (const message of collabSessionQuery.data?.room_messages ?? []) {
      const sourceId = message.metadata?.source_message_id;
      if (sourceId) metadataById[sourceId] = message.metadata;
    }
    return metadataById;
  }, [collabSessionQuery.data?.room_messages]);
  const openProjectWorkbenchForEntity = useCallback(
    (entity?: CoworkRoomEntityRef) => {
      closeSpecialUtilityPanels();
      setArtifactsOpen(false);
      setShowAgentPlan(false);
      setShowResearchHistory(false);
      setShowResearch(false);
      setShowPreview(false);
      setAgentWorkbenchDismissed(false);
      setAgentWorkbenchManuallyOpened(true);
      setAgentWorkbenchTab("project");
      setAgentWorkbenchTabTouched(true);
      if (entity && typeof window !== "undefined") {
        window.setTimeout(() => {
          window.dispatchEvent(
            new CustomEvent("echo:project-entity-focus", {
              detail: {
                ...entity,
                project_id: entity.project_id ?? boundProjectState?.project.id,
              },
            }),
          );
        }, 0);
      }
    },
    [
      boundProjectState?.project.id,
      closeSpecialUtilityPanels,
      setArtifactsOpen,
    ],
  );
  const handleDetachProjectCapability = useCallback(async () => {
    if (!boundProjectState || !canManageHumanInvites || isNewThread) return;
    const expectedProjectId = boundProjectState.project.id;
    const confirmed = await confirmProjectDetach({
      title: t.projectCapability.detachConfirmTitle,
      description: t.projectCapability.detachConfirmDescription,
      confirmLabel: t.projectCapability.detachConfirmAction,
      destructive: true,
    });
    if (!confirmed) return;

    const outcome = await detachGroupProjectCapability({
      expectedProjectId,
      requestDetach: ({ force, expectedProjectId: guardedProjectId }) =>
        detachProjectFromGroupMutation.mutateAsync({
          threadId,
          expectedProjectId: guardedProjectId,
          force,
        }),
      confirmForce: () =>
        confirmProjectDetach({
          title: t.projectCapability.forceDetachConfirmTitle,
          description: t.projectCapability.forceDetachConfirmDescription,
          confirmLabel: t.projectCapability.forceDetachConfirmAction,
          destructive: true,
        }),
    });
    if (outcome === "cancelled") {
      toast.info(t.projectCapability.detachCancelled);
      return;
    }
    if (outcome === "binding-changed") {
      toast.error(t.projectCapability.detachBindingChanged);
      return;
    }
    if (outcome === "failed") {
      toast.error(t.projectCapability.detachFailed);
      return;
    }

    await boundProjectQuery.refetch().catch(() => undefined);
    setAgentWorkbenchTab("agent");
    setAgentWorkbenchManuallyOpened(false);
    setAgentWorkbenchDismissed(true);
    toast.success(t.projectCapability.detached);
  }, [
    boundProjectQuery,
    boundProjectState,
    canManageHumanInvites,
    confirmProjectDetach,
    detachProjectFromGroupMutation,
    isNewThread,
    t.projectCapability,
    threadId,
  ]);
  const handleThreadMessageProjectAction = useCallback(
    async (
      input: CoworkMessageProjectActionInput,
      message: CoworkRoomMessage,
    ) => {
      const project = boundProjectState?.project;
      if (!project) throw new Error("当前群聊尚未绑定项目");
      if (isNewThread || !threadId || threadId === "new") {
        throw new Error("请先创建项目群再执行此操作");
      }

      if (!collabSessionQuery.data?.room_id) {
        await ensureCollabRoomMutation.mutateAsync({
          threadId,
          input: {
            id: `collab-${threadId}`,
            name: collaborationTeamName,
            members: collaborationRoomMemberPayload,
            leaderId:
              collaborationRoomMemberPayload[0]?.name ?? effectiveAgentId,
            mode: teamModeIntent,
          },
        });
      }

      const posted = await postCollabRoomMessageMutation.mutateAsync({
        threadId,
        input: {
          text: message.text.trim() || "群聊消息",
          participant_id: currentActorId(),
          display_name: "我",
          source_message_id:
            message.metadata?.source_message_id ??
            `thread:${threadId}:${message.seq}`,
        },
      });
      const messageSeq = posted.message?.seq ?? posted.seq;
      const normalizedInput: CoworkMessageProjectActionInput = {
        ...input,
        project_id: project.id,
        ...(input.action === "publish_artifact"
          ? {
              artifact: {
                ...(input.artifact ?? {}),
                source_message_seq: messageSeq,
              },
            }
          : {}),
      };
      const response = await applyRoomMessageProjectActionMutation.mutateAsync({
        threadId,
        messageSeq,
        input: normalizedInput,
      });
      await boundProjectQuery.refetch();
      openProjectWorkbenchForEntity(response.target);
      const successLabel: Record<
        CoworkMessageProjectActionInput["action"],
        string
      > = {
        link_milestone: "已关联到里程碑",
        create_item: "已创建项目事项",
        record_decision: "已记录项目决策",
        publish_artifact: "已发布到项目资料",
      };
      toast.success(
        response.replayed ? "该项目记录已存在" : successLabel[input.action],
      );
    },
    [
      applyRoomMessageProjectActionMutation,
      boundProjectQuery,
      boundProjectState?.project,
      collabSessionQuery.data?.room_id,
      collaborationRoomMemberPayload,
      collaborationTeamName,
      effectiveAgentId,
      ensureCollabRoomMutation,
      isNewThread,
      openProjectWorkbenchForEntity,
      postCollabRoomMessageMutation,
      teamModeIntent,
      threadId,
    ],
  );
  const projectMessageActions = useMemo(
    () =>
      boundProjectState
        ? {
            threadId,
            projectId: boundProjectState.project.id,
            milestones: projectMilestoneOptions,
            defaultMilestoneId: defaultProjectMilestoneId,
            messageMetadataBySourceId: roomMessageMetadataBySourceId,
            onActionRequest: handleThreadMessageProjectAction,
            onActionError: (error: Error) => {
              toast.error(error.message || "项目操作失败");
            },
          }
        : undefined,
    [
      boundProjectState,
      defaultProjectMilestoneId,
      handleThreadMessageProjectAction,
      projectMilestoneOptions,
      roomMessageMetadataBySourceId,
      threadId,
    ],
  );
  const roomTimelineMessageActions = useMemo(
    () =>
      boundProjectState
        ? {
            threadId,
            projectId: boundProjectState.project.id,
            milestones: projectMilestoneOptions,
            defaultMilestoneId: defaultProjectMilestoneId,
            onReply: (message: CoworkRoomMessage) => {
              const quoted = message.text
                .replace(/\s+/g, " ")
                .trim()
                .slice(0, 160);
              setComposerSeed(`> ${quoted}\n\n`);
            },
            onMentionAuthor: (message: CoworkRoomMessage) => {
              const member = collabSessionQuery.data?.roster.find(
                (candidate) => candidate.id === message.participant_id,
              );
              const mention =
                member?.kind === "agent" && message.participant_id
                  ? `@agent:${message.participant_id}`
                  : `@${message.display_name || message.participant_id || "成员"}`;
              setComposerSeed(`${mention} `);
            },
            onActionApplied: (
              response: { target?: CoworkRoomEntityRef } | undefined,
              input: CoworkMessageProjectActionInput,
            ) => {
              void boundProjectQuery.refetch();
              if (response?.target) {
                openProjectWorkbenchForEntity(response.target);
              }
              const labels: Record<
                CoworkMessageProjectActionInput["action"],
                string
              > = {
                link_milestone: "已关联到里程碑",
                create_item: "已创建项目事项",
                record_decision: "已记录项目决策",
                publish_artifact: "已发布到项目资料",
              };
              toast.success(labels[input.action]);
            },
            onActionError: (error: Error) => {
              toast.error(error.message || "项目操作失败");
            },
          }
        : false,
    [
      boundProjectQuery,
      boundProjectState,
      collabSessionQuery.data?.roster,
      defaultProjectMilestoneId,
      openProjectWorkbenchForEntity,
      projectMilestoneOptions,
      threadId,
    ],
  );
  const roomTimelineEntries = useMemo(
    () =>
      visibleRoomMessages.map((message) => ({
        id: `${message.room_id ?? collabSessionQuery.data?.room_id ?? "room"}:${message.seq}`,
        createdAt: message.ts,
        content: (
          <CoworkRoomTimelineEntry
            message={message}
            participants={collabSessionQuery.data?.room_participants ?? []}
            currentParticipantId={currentInviteActor}
            messageActions={roomTimelineMessageActions}
            onEntityClick={openProjectWorkbenchForEntity}
            className="my-1"
          />
        ),
      })),
    [
      collabSessionQuery.data?.room_id,
      collabSessionQuery.data?.room_participants,
      currentInviteActor,
      openProjectWorkbenchForEntity,
      roomTimelineMessageActions,
      visibleRoomMessages,
    ],
  );
  const prevAgentRef = useRef<string | null>(null);
  const { stageRoute: stageThreadRoute, commitRoute: commitThreadRoute } =
    useDeferredRouteCommit();
  useEffect(() => {
    if (initialPrompt) setComposerSeed(initialPrompt);
  }, [initialPrompt]);
  useEffect(() => {
    // Only a fresh-task route may select a persona. Historical thread URLs
    // intentionally carry no agent query: their persisted owner is the source
    // of truth and must not be overwritten by a localStorage/default fallback.
    if (!isNewThread) return;
    const selectedAgent = routeAgentName || queryAgentName;
    if (!selectedAgent) return;
    // Echo is the global assistant entry point — it sits ABOVE the
    // persona picker, not as a selectable role. Navigating to the assistant
    // thread MUST NOT mutate the footer's active persona, otherwise the
    // footer drifts to a random task collaborator
    // because "echo" is filtered out of switcherAgents.
    if (selectedAgent === "echo") return;
    if (!isPrimaryPersonaAgentId(selectedAgent)) {
      const leaderId = primaryPersonaAgentIdOrDefault(activeAgentId);
      const preset: TaskCollaboratorPreset = {
        leaderId,
        collaboratorIds: [selectedAgent],
        mode: "cluster",
        label: selectedAgent,
        openPicker: true,
      };
      // Preserve old/deep links, but reinterpret the requested expert as a
      // current-task member instead of reviving a standalone identity lane.
      writeTaskCollaboratorPreset(preset);
      applyTaskCollaboratorPreset(preset);
      consumeTaskCollaboratorPreset();
      navigate(taskWorkspaceRoute({ agentId: leaderId }), { replace: true });
      return;
    }
    // 统一走 emitAgentChanged：同时写 localStorage + 派发 eventBus 事件，
    // 保证左下角 AgentFooter（只订阅 eventBus agent:changed）能立即同步，
    // 不再出现仅写 localStorage/发 window CustomEvent 导致两边角色不一致。
    // source: "system" 表示这是路由/URL 驱动的同步，不触发 navigate 循环。
    emitAgentChanged(selectedAgent, "system");
    try {
      window.dispatchEvent(
        new CustomEvent(ACTIVE_AGENT_EVENT, {
          detail: { name: selectedAgent },
        }),
      );
    } catch (e) {
      swallow(e, "storage");
    }
  }, [
    activeAgentId,
    applyTaskCollaboratorPreset,
    isNewThread,
    navigate,
    queryAgentName,
    routeAgentName,
  ]);
  useEffect(() => {
    // 使用 effectiveAgentId 而非 resolvedThreadOwnerAgentId：echo-assistant
    // 的存量 metadata 可能写的是 general，若据此派发会把 footer 的 active
    // persona 漂移到别的 agent（「助手跳转别人agent」的另一个来源）。归位后
    // echo 与 activeAgentId 相等，自然命中首条守卫而跳过派发。
    if (
      !effectiveAgentId ||
      effectiveAgentId === activeAgentId ||
      effectiveAgentId === "echo"
    ) {
      return;
    }
    try {
      window.dispatchEvent(
        new CustomEvent(ACTIVE_AGENT_EVENT, {
          detail: { name: effectiveAgentId, source: "thread" },
        }),
      );
    } catch (e) {
      swallow(e, "event");
    }
    emitAgentChanged(effectiveAgentId, "thread");
  }, [activeAgentId, effectiveAgentId]);
  useEffect(() => {
    const context = settings.context as typeof settings.context & {
      page_agent_memory_mode?: string;
    };
    if (!memoryMode || context.page_agent_memory_mode === memoryMode) {
      return;
    }
    setSettings("context", {
      ...settings.context,
      page_agent_memory_mode: memoryMode,
    } as Partial<typeof settings.context>);
  }, [memoryMode, setSettings, settings, settings.context]);
  useEffect(() => {
    const prev = prevAgentRef.current;
    prevAgentRef.current = activeAgentId;
    if (prev === null || prev === activeAgentId) return;
    // Agent actually changed mid-session → flush both views.
    qc.invalidateQueries({ queryKey: ["threads", "search"] });
    // The visible route stays on the unified realtime surface; the selected
    // agent is carried by ?agent= for fresh tasks and by thread metadata for
    // history.
  }, [activeAgentId, qc]);

  useEvent(
    "agent:changed",
    ({ name, source }) => {
      // thread: 由当前 thread owner 驱动的同步，不导航
      // system: 由 URL/路由驱动的同步（页面首次加载、query 变化），不导航
      if (source === "thread" || source === "system") return;
      if (!name || name === activeAgentId) return;
      qc.invalidateQueries({ queryKey: ["threads", "search"] });
      navigate(taskWorkspaceRoute({ agentId: name }), { replace: false });
    },
    [activeAgentId, navigate, qc],
  );

  const streamOptions = useMemo<ThreadStreamOptions>(
    () => ({
      threadId,
      // Spread settings.context FIRST so our agent_name wins. Otherwise any
      // stale `agent_name` in the shared settings store (shared across
      // threads) clobbers the current page's pick — which is how turn 2+
      // started sending the wrong id before this fix.
      context: applyCoderModelProfileBoundary(
        effectiveAgentId,
        {
          ...settings.context,
          reasoning_effort: effectiveReasoningEffort,
          // Opt-in guardian independent review for high-risk actions. Only
          // sent when the user enabled it; the backend gate reads these and
          // degrades to the rule engine on review failure. The review model
          // is left to the backend (conversation's own model) unless the
          // user explicitly picked one.
          guardian_review_enabled: settings.context.guardian_review_enabled
            ? true
            : undefined,
          guardian_review_model:
            settings.context.guardian_review_enabled &&
            settings.context.guardian_review_model
              ? settings.context.guardian_review_model
              : undefined,
          mode: streamMode,
          workspace_path: isProjectCodeMode ? projectWorkspacePath : undefined,
          workspace_scope: isProjectCodeMode
            ? "project"
            : isCodingWorkspaceMode
              ? "personal"
              : undefined,
          personal_workspace_enabled:
            !isProjectCodeMode && isCodingWorkspaceMode ? true : undefined,
          // Personal space keeps one user-selected root while each role gets a
          // readable, isolated child folder. The UI still presents this as
          // personal space; only an explicitly picked folder is a project.
          personal_workspace_path:
            !isProjectCodeMode && isCodingWorkspaceMode
              ? personalWorkspacePath || undefined
              : undefined,
          capability_mode: isCodingWorkspaceMode ? "code" : undefined,
          code_mode: isCodingWorkspaceMode ? "solo" : undefined,
          // Project presets describe how to operate on a bound user project.
          // Personal space has its own general/build/research contract; sending
          // the default project "develop" bundle here made all three personal
          // modes behave like development mode.
          agent_mode: isProjectCodeMode ? projectAgentMode : undefined,
          mode_preset: isProjectCodeMode ? projectModePreset.id : undefined,
          workflow_preset: isProjectCodeMode
            ? workflowPresetForMode(projectAgentMode, auditIntensity)
            : undefined,
          // UX/UI is not just a prompt label: enable the runtime's browser
          // regression contract so visual work must be inspected after changes.
          browser_regression_enabled:
            isProjectCodeMode && projectAgentMode === "uxui" ? true : undefined,
          // Personal-space work mode. Backend keeps this as scope steering while the
          // same code capability/tool chain remains available in personal workspace.
          personal_mode: !isProjectCodeMode ? personalMode : undefined,
          personal_instructions: !isProjectCodeMode
            ? settings.personal_space.custom_instructions.trim() || undefined
            : undefined,
          skill_pack_profile: isProjectCodeMode
            ? projectModePreset.skillPackProfile
            : undefined,
          verification_policy: isProjectCodeMode
            ? projectModePreset.verificationPolicy
            : undefined,
          default_skill_packs: isProjectCodeMode
            ? projectModePreset.defaultSkillPacks
            : undefined,
          default_plugins: isProjectCodeMode
            ? projectModePreset.defaultPlugins
            : undefined,
          mode_contract: isProjectCodeMode
            ? projectModePreset.promptContract
            : undefined,
          project_signals: projectSignals,
          agent_name: effectiveAgentId,
          // The outer Realtime layer owns transport and lifecycle only for a
          // Codex role. It must not smart-route a second, purely decorative
          // system model over the model that the Codex account profile will
          // actually execute.
          execution_engine: selectedExecutionEngine,
          // A stable, user-visible browser tab / desktop window reference. The
          // runtime receives structured identity instead of guessing from prose.
          automation_target:
            !embeddedDesignChat && automationTarget
              ? automationTarget
              : undefined,
          interaction_mode:
            effectiveMode === "react" ||
            effectiveMode === "deep" ||
            effectiveMode === "code"
              ? "office"
              : undefined,
          ...collaborationContext,
          // Group strategy owns the work contract for this turn. Spread it last
          // so hidden personal/project selectors cannot leak stale constraints
          // into a project group (including Project OS groups without workDir).
          ...(isGroupConversation ? activeGroupTaskContext : {}),
        },
        selectedExecutionEngine,
      ),
      onStart: (startedThreadId) => {
        if (startedThreadId !== threadId) {
          clearSidebarThreadStatus(threadId);
        }
        markSidebarThreadRunning(startedThreadId);
        localStartedThreadIdRef.current = startedThreadId;
        setIsNewThread(false);
        const targetPath = threadRouteFor(startedThreadId);
        // The live page deliberately stays mounted until the turn settles, so
        // React Router cannot own this transition yet. Keep sidebar selection
        // and its thread list in sync with the server-issued id immediately.
        eventBus.emit("thread:route-sync", {
          href: targetPath,
          threadId: startedThreadId,
        });
        void qc.invalidateQueries({ queryKey: ["threads", "search"] });
        // Keep the /new route mounted for the lifetime of the first turn.
        // Changing the hash here still notifies the desktop HashRouter and
        // tears down its WebSocket, even when history.replaceState is used.
        // The sidebar already follows thread:route-sync; commit the actual URL
        // once onFinish confirms that the server-owned turn is terminal.
        stageThreadRoute(targetPath);
      },
      onFinish: () => {
        // Drop the locally-started marker once the turn is terminal. It exists
        // only to keep identity/workspace queries paused during the first turn
        // (the server-issued id may not be queryable yet); leaving it set would
        // permanently disable threadIdentityQuery, pinning the header/browser
        // title to "未命名" on every thread the user has messaged this session.
        localStartedThreadIdRef.current = null;
        void qc.invalidateQueries({ queryKey: ["threads", "search"] });
        commitThreadRoute();
      },
    }),
    [
      auditIntensity,
      activeGroupTaskContext,
      automationTarget,
      clearSidebarThreadStatus,
      collaborationContext,
      commitThreadRoute,
      embeddedDesignChat,
      effectiveAgentId,
      effectiveMode,
      effectiveReasoningEffort,
      isCodingWorkspaceMode,
      isGroupConversation,
      isProjectCodeMode,
      markSidebarThreadRunning,
      personalMode,
      personalWorkspacePath,
      projectAgentMode,
      projectModePreset,
      projectSignals,
      projectWorkspacePath,
      qc,
      selectedExecutionEngine,
      setIsNewThread,
      settings.context,
      settings.personal_space.custom_instructions,
      stageThreadRoute,
      streamMode,
      threadId,
      threadRouteFor,
    ],
  );
  const [
    thread,
    sendMessage,
    isUploading,
    allToolEvents,
    lastTurnToolEvents,
    realtimeApprovals,
  ] = useThreadStream(streamOptions);
  const [isCompressingContext, setIsCompressingContext] = useState(false);
  const selectedModel = useMemo(() => {
    const modelName = settings.context.model_name;
    return (
      models.find(
        (model) =>
          model.selection_id === modelName ||
          model.name === modelName ||
          model.id === modelName ||
          model.model === modelName,
      ) ?? models[0]
    );
  }, [models, settings.context.model_name]);
  const [modelSwitchTimeline, setModelSwitchTimeline] = useState<{
    threadId: string;
    events: ModelSwitchEvent[];
  }>(() => ({
    threadId,
    events: loadModelSwitchEvents(threadId),
  }));
  useEffect(() => {
    setModelSwitchTimeline({
      threadId,
      events: loadModelSwitchEvents(threadId),
    });
  }, [threadId]);
  const modelSwitchTimelineEntries = useMemo<MessageListTimelineEntry[]>(() => {
    const visibleEvents =
      modelSwitchTimeline.threadId === threadId
        ? modelSwitchTimeline.events
        : [];
    return visibleEvents.map((event) => ({
      id: event.id,
      createdAt: event.createdAt,
      content: <ModelSwitchTimelineEntry modelName={event.modelName} />,
    }));
  }, [modelSwitchTimeline, threadId]);
  const conversationTimelineEntries = useMemo<MessageListTimelineEntry[]>(
    () => [...roomTimelineEntries, ...modelSwitchTimelineEntries],
    [modelSwitchTimelineEntries, roomTimelineEntries],
  );
  const maxContextTokens = useMemo(
    () => resolveModelContextWindow(selectedModel),
    [selectedModel],
  );
  const contextTokens = useMemo(
    () => estimateCurrentContextTokens(thread.messages),
    [thread.messages],
  );
  const compactThread = (thread as typeof thread & CompactableThread).compact;
  const handleCompressContext = useCallback(async () => {
    if (!compactThread || isCompressingContext) {
      if (!compactThread) {
        toast.error("Context compression is not available for this thread");
      }
      return;
    }
    setIsCompressingContext(true);
    try {
      const result = await compactThread();
      if (result.compacted) {
        toast.success(
          t.contextCompressor?.autoCompressed ?? "Context compressed",
        );
        return;
      }
      const kept =
        result.keepRecent != null && result.turnCount != null
          ? `Only ${result.turnCount} turns; keeping the latest ${result.keepRecent}.`
          : "Nothing to compress yet.";
      toast.message(
        t.contextCompressor?.compressContext ?? "Compress context",
        {
          description:
            result.reason === "below_keep_recent"
              ? kept
              : (result.reason ?? kept),
        },
      );
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to compress context",
      );
    } finally {
      setIsCompressingContext(false);
    }
  }, [compactThread, isCompressingContext, t.contextCompressor]);

  // If the first stream fails before onStart fires, isNewThread stays
  // true while messages already rendered, producing a Welcome overlay
  // on top of the live conversation. Second source of truth: once any
  // messages arrive, treat the thread as established.
  useEffect(() => {
    if (isNewThread && thread.messages.length > 0) {
      setIsNewThread(false);
      const targetPath = threadRouteFor(threadId);
      // Same deferred route commit as onStart. This fallback can run before
      // the loading-edge callback on a fast first item; mutating the hash here
      // used to remount the page and interrupt the turn before any answer.
      stageThreadRoute(targetPath);
      eventBus.emit("thread:route-sync", {
        href: targetPath,
        threadId,
      });
      // A fast terminal failure can be reduced in one React batch, so the
      // usual loading edge never invokes onFinish. Commit immediately once
      // the first message is already terminal; otherwise `/new` remounts can
      // discard the only visible failure receipt and leave a ghost draft.
      if (!thread.isLoading) {
        commitThreadRoute();
      }
    }
  }, [
    commitThreadRoute,
    isNewThread,
    thread.messages.length,
    thread.isLoading,
    setIsNewThread,
    stageThreadRoute,
    threadId,
    threadRouteFor,
  ]);

  useRegenerateHandler(thread, sendMessage, threadId);
  usePlanActionHandler(sendMessage, threadId);

  // 「环境受限」横幅授权：点「授权并重试」先写线程级 network_access，等它落到
  // settings.context 后再触发既有 regenerate —— 否则 sendMessage 的闭包仍拿着旧档。
  const [pendingNetworkRegen, setPendingNetworkRegen] = useState<
    "common" | "full" | null
  >(null);
  const handleAuthorizeNetwork = useCallback(
    (tier: "common" | "full") => {
      setPendingNetworkRegen(tier);
      setSettings("context", {
        ...settings.context,
        network_access: tier,
      });
    },
    [setSettings, settings.context],
  );
  useEffect(() => {
    if (!pendingNetworkRegen) return;
    if (settings.context.network_access !== pendingNetworkRegen) return;
    setPendingNetworkRegen(null);
    window.dispatchEvent(
      new CustomEvent("echo:regenerate", { detail: { threadId } }),
    );
  }, [pendingNetworkRegen, settings.context.network_access, threadId]);

  const previewBlocks = useMemo(() => {
    for (let i = thread.messages.length - 1; i >= 0; i--) {
      const msg = thread.messages[i];
      // Current turn only: an inline-preview block from an earlier turn
      // must not hijack every later completion (same scoping as
      // resultPreviewUrl below).
      if (msg && isHumanMessage(msg)) break;
      if (!msg || !isAIMessage(msg)) continue;
      const text =
        typeof msg.content === "string"
          ? msg.content
          : msg.content
              .filter(
                (c): c is { type: "text"; text: string } => c.type === "text",
              )
              .map((c) => c.text)
              .join("\n");
      const blocks = extractCodeBlocks(text);
      if (hasPreviewableBlocks(blocks)) return blocks;
    }
    return null;
  }, [thread.messages]);

  // Deployed preview URL (vercel/netlify/localhost/etc.) — when present and
  // no inline html blocks exist, we still treat the task as a "frontend
  // task" and auto-switch the workbench to the browser preview tab on
  // completion. The URL is also forwarded to LivePreviewPanel so it can
  // render the deployed site instead of falling back to srcDoc.
  // Only the current turn is scanned (messages after the last human message).
  const lastTurnMessages = useMemo(() => {
    let turnStart = 0;
    for (let i = thread.messages.length - 1; i >= 0; i--) {
      const message = thread.messages[i];
      if (message && isHumanMessage(message)) {
        turnStart = i + 1;
        break;
      }
    }
    return thread.messages.slice(turnStart);
  }, [thread.messages]);
  const lastTurnUserInput = useMemo(() => {
    // 概要页「上下文」统计需要覆盖整段对话喂入的上下文文件，而不只是最后一轮：
    // 文件通常在对话开头喂入，后续轮次只发文字追问。因此跨所有 human 消息聚合
    // 上传文件与附件（按文件名去重），文本仍取最后一条 human 消息。
    const humanMessages = thread.messages.filter(isHumanMessage);
    if (humanMessages.length === 0) return null;

    const last = humanMessages[humanMessages.length - 1]!;
    const rawOf = (m: (typeof humanMessages)[number]) =>
      typeof m.content === "string"
        ? m.content
        : m.content
            .filter(
              (c): c is { type: "text"; text: string } => c.type === "text",
            )
            .map((c) => c.text)
            .join("\n");
    const text = stripUploadedFilesTag(rawOf(last));

    const seenFilenames = new Set<string>();
    const uploaded: Array<{ filename: string; path: string }> = [];
    const attachments: Array<{ filename: string }> = [];
    for (const human of humanMessages) {
      const raw = rawOf(human);
      // Files ride the structured metadata channel (additional_kwargs.files) as
      // the primary source; the <uploaded_files> content tag is only a backward
      // compat fallback. Merge both, de-duplicated by filename.
      const structuredFiles = (
        Array.isArray(human.additional_kwargs?.files)
          ? (human.additional_kwargs.files as FileInMessage[])
          : []
      )
        .map((f) => ({ filename: f.filename, path: f.path ?? "" }))
        .filter((f) => f.filename);
      const contentFiles = parseUploadedFiles(raw)
        .filter((f): f is typeof f & { path: string } => Boolean(f.path))
        .map((f) => ({
          filename: f.filename,
          path: f.path,
        }));
      for (const f of [...structuredFiles, ...contentFiles]) {
        if (seenFilenames.has(f.filename)) continue;
        seenFilenames.add(f.filename);
        uploaded.push(f);
      }
      const rawAttachments = Array.isArray(human.additional_kwargs?.attachments)
        ? (human.additional_kwargs.attachments as Array<{ filename?: string }>)
        : [];
      for (const a of rawAttachments) {
        if (!a.filename || seenFilenames.has(a.filename)) continue;
        seenFilenames.add(a.filename);
        attachments.push({ filename: a.filename });
      }
    }
    if (!text && uploaded.length === 0 && attachments.length === 0) return null;
    return { text, uploadedFiles: uploaded, attachments };
  }, [thread.messages]);

  // A deploy URL from an earlier turn must not hijack every later completion.
  const resultPreviewUrl = useMemo(() => {
    return extractResultUrl(lastTurnMessages);
  }, [lastTurnMessages]);
  // 侧边栏「进展」面板的叙事大纲：按 iteration 分组（意图/执行计数/事实）。
  const progressOutline = useMemo(
    () => buildProgressOutline(convertToSteps(lastTurnMessages)),
    [lastTurnMessages],
  );

  const latestPersistedTodoEvents = useMemo(
    () => latestPersistedTodoEventsFromMessages(lastTurnMessages),
    [lastTurnMessages],
  );
  const restoredTodoEvents = useMemo(
    () =>
      restoredTodoEventsForDisplay({
        isLoading: thread.isLoading,
        lastTurnToolEvents,
        latestPersistedTodoEvents,
      }),
    [lastTurnToolEvents, latestPersistedTodoEvents, thread.isLoading],
  );
  const agentDisplayEvents = useMemo(
    () => [...lastTurnToolEvents, ...restoredTodoEvents],
    [lastTurnToolEvents, restoredTodoEvents],
  );
  const workbenchDisplayEvents = useMemo(() => {
    if (focusedWorkbenchTurnIndex === null) return agentDisplayEvents;
    return allToolEvents.filter(
      (event) => event.turnIndex === focusedWorkbenchTurnIndex,
    );
  }, [agentDisplayEvents, allToolEvents, focusedWorkbenchTurnIndex]);
  const latestWorkspaceFocusTab = useMemo(
    () => workspaceFocusTabFromEvents(agentDisplayEvents),
    [agentDisplayEvents],
  );
  // Self-contained replay export, surfaced from the unified share menu.
  const replayBlocks = useMemo(
    () => screenBlocksForAgent(toWorkBlocks(agentDisplayEvents), null),
    [agentDisplayEvents],
  );
  const handleExportReplay = useCallback(() => {
    if (replayBlocks.length === 0) return;
    const title =
      thread?.values?.title || initialPrompt || t.realtime.replay.titleDefault;
    const html = buildReplayHtml(
      buildReplayFromBlocks(
        replayBlocks,
        {
          title,
          brand: "EchoAI · EchoOS",
          footer: `${new Date().toLocaleDateString()} · ${t.realtime.replay.footer}`,
        },
        workBlockLabelsFromShape(
          (t as unknown as { workBlocks?: unknown }).workBlocks,
        ),
      ),
    );
    downloadTextFile(html, `echo-replay-${shareSlug(title)}.html`);
  }, [replayBlocks, thread, initialPrompt, t]);
  const latestArtifactFocusPath = useMemo(
    () => latestArtifactFocusPathFromEvents(agentDisplayEvents),
    [agentDisplayEvents],
  );
  const isAgentWorkflowMode =
    effectiveMode === "deep" ||
    effectiveMode === "react" ||
    effectiveMode === "code";
  const tasks = useTasks("all");
  const hasRunningAgentEvents = lastTurnToolEvents.some(
    (event) =>
      event.status === "running" || event.status === "waiting_approval",
  );
  const hasActiveBackgroundTask = (tasks.data?.active ?? []).some(
    (task) => task.thread_id === threadId,
  );
  const hasPausedBackgroundTask = (tasks.data?.paused ?? []).some(
    (task) => task.thread_id === threadId,
  );
  const hasPendingBackgroundTask = (tasks.data?.pending ?? []).some(
    (task) => task.thread_id === threadId,
  );
  const hasPausedOrPendingBackgroundTask =
    hasPausedBackgroundTask || hasPendingBackgroundTask;
  const requiresReportDeliverable = useMemo(
    () =>
      agentDisplayEvents.some((event) => {
        // The stream mapping layer precomputes this flag once per event —
        // consuming it avoids re-stringifying payloads on every render.
        // undefined means the event bypassed that layer (e.g. restored
        // todo events), so fall back to matching here.
        if (event.isReportLike !== undefined) return event.isReportLike;
        return liveEventIsReportLike(event);
      }),
    [agentDisplayEvents],
  );
  const hasReportArtifact = useMemo(
    () =>
      lastTurnMessages.some(
        (message) =>
          isSettledAssistantAnswer(message, { allowToolCalls: true }) &&
          FINAL_DELIVERABLE_PATTERN.test(
            extractTextFromMessage(message) ||
              extractContentFromMessage(message),
          ),
      ),
    [lastTurnMessages],
  );
  const finalArtifactEntries = useMemo(
    () => finalOutputArtifactEntries(agentDisplayEvents),
    [agentDisplayEvents],
  );
  const hasFinalArtifact = finalArtifactEntries.length > 0;
  const lastTurnTerminalState = useMemo(
    () => latestAssistantTerminalState(lastTurnMessages),
    [lastTurnMessages],
  );
  const agentRunInterrupted = isAssistantStopTerminalState(
    lastTurnTerminalState,
  );
  const agentRunPaused = lastTurnTerminalState === "paused";
  const legacyBlockedOnUser = useMemo(
    () =>
      lastTurnTerminalState === null &&
      assistantAnswerRequestsUserInput(lastTurnMessages),
    [lastTurnMessages, lastTurnTerminalState],
  );
  const agentRunBlocked =
    lastTurnTerminalState === "blocked" || legacyBlockedOnUser;
  const hasAgentAnswer = useMemo(
    () =>
      lastTurnTerminalState === null &&
      !agentRunBlocked &&
      (hasFinalArtifact ||
        lastTurnMessages.some((message) =>
          // Realtime history folds a completed tool call and the concise
          // final answer into the same AI message. Tool presence therefore
          // cannot mean "still running" once the message is explicitly an
          // answer; commentary/streaming metadata is already rejected by the
          // helper. A short two-line answer is still a valid terminal answer.
          isSettledAssistantAnswer(message, { allowToolCalls: true }),
        )),
    [
      agentRunBlocked,
      hasFinalArtifact,
      lastTurnMessages,
      lastTurnTerminalState,
    ],
  );
  const canSettleStaleLiveEvents =
    !thread.isLoading &&
    (!thread.error || hasFinalArtifact) &&
    hasAgentAnswer &&
    (!requiresReportDeliverable || hasReportArtifact || hasFinalArtifact);
  const agentRunSettled =
    !thread.isLoading &&
    (!hasRunningAgentEvents ||
      canSettleStaleLiveEvents ||
      lastTurnTerminalState !== null ||
      agentRunBlocked) &&
    !hasActiveBackgroundTask &&
    (!hasPausedOrPendingBackgroundTask || agentRunPaused);
  const hasCompletedAgentOutput =
    lastTurnTerminalState === null &&
    !agentRunBlocked &&
    (!thread.error || hasFinalArtifact) &&
    agentRunSettled &&
    (!requiresReportDeliverable || hasReportArtifact || hasFinalArtifact);
  const agentRunFailed =
    agentRunSettled &&
    !agentRunInterrupted &&
    !agentRunBlocked &&
    !hasCompletedAgentOutput &&
    !hasPausedOrPendingBackgroundTask;
  const sidebarRunState = useMemo<
    "running" | "waiting" | "error" | null
  >(() => {
    if (hasPausedOrPendingBackgroundTask) return "waiting";
    if (agentRunInterrupted) return null;
    if (agentRunBlocked) return "waiting";
    if (agentRunFailed || (thread.error && !thread.isLoading)) return "error";
    if (agentRunSettled) return null;
    if (
      agentDisplayEvents.some((event) => event.status === "waiting_approval")
    ) {
      return "waiting";
    }
    if (
      hasActiveBackgroundTask ||
      thread.isLoading ||
      Boolean(thread.streamingMessage) ||
      agentDisplayEvents.some((event) => event.status === "running")
    ) {
      return "running";
    }
    return null;
  }, [
    agentDisplayEvents,
    agentRunInterrupted,
    agentRunBlocked,
    agentRunFailed,
    agentRunSettled,
    hasActiveBackgroundTask,
    hasPausedOrPendingBackgroundTask,
    thread.error,
    thread.isLoading,
    thread.streamingMessage,
  ]);
  const sidebarThreadId =
    thread.threadId ?? localStartedThreadIdRef.current ?? threadId;
  // Forward the derived run state to the Godot desktop pet (no-op in browser).
  // The in-page sprite pet was removed — the desktop sidecar is the only pet
  // now, so the returned mood is unused and the call is kept for its effect.
  usePetAgentEvents({
    runState: sidebarRunState,
    settled: agentRunSettled,
    failed: agentRunFailed,
    streaming: Boolean(thread.streamingMessage),
  });
  useEffect(() => {
    const href = threadRouteFor(sidebarThreadId);
    eventBus.emit("thread:run-status", {
      href,
      state: sidebarRunState,
      threadId: sidebarThreadId,
    });
    return () => {
      eventBus.emit("thread:run-status", {
        href,
        state: null,
        threadId: sidebarThreadId,
      });
    };
  }, [sidebarRunState, sidebarThreadId, threadRouteFor]);
  const shouldHideSettledProcessChrome =
    agentRunSettled && hasCompletedAgentOutput;
  const hasRenderableAgentWorkbench = useMemo(
    () =>
      isAgentWorkflowMode &&
      hasAgentWorkbenchContent(agentDisplayEvents, {
        hasAnswer: hasCompletedAgentOutput,
        runSettled: agentRunSettled,
        runFailed: agentRunFailed,
        paused: hasPausedOrPendingBackgroundTask,
      }),
    [
      agentDisplayEvents,
      agentRunFailed,
      agentRunSettled,
      hasCompletedAgentOutput,
      hasPausedOrPendingBackgroundTask,
      isAgentWorkflowMode,
    ],
  );
  const canOpenAgentWorkbench =
    !embeddedDesignChat &&
    (!isNewThread ||
      collaborationEnabled ||
      hasRenderableAgentWorkbench ||
      !!previewBlocks ||
      // Realtime keeps the right workbench available from the first turn. The
      // actual file tree still lives in the left project pane; this panel is the
      // live agent workstation and replay surface.
      isCodingWorkspaceMode ||
      isRealtimeRoute);
  const durableCollaborationEnabled =
    !embeddedDesignChat &&
    (collaborationEnabled || Boolean(boundProjectQuery.data));
  const showAgentWorkbench =
    canOpenAgentWorkbench &&
    (agentWorkbenchManuallyOpened ||
      (durableCollaborationEnabled &&
        !agentWorkbenchDismissed &&
        (!isNewThread || thread.isLoading || hasRenderableAgentWorkbench)) ||
      (!agentWorkbenchDismissed &&
        hasRenderableAgentWorkbench &&
        showAgentPlan)) &&
    !showResearchHistory &&
    !(showResearch && (!!researchJob || !!researchError));
  const artifactCount = artifacts?.length ?? 0;
  const settledWorkbenchTurnKey = useMemo(() => {
    const latestMessage = thread.messages[thread.messages.length - 1];
    return `${threadId}:${latestMessage?.id ?? thread.messages.length}`;
  }, [thread.messages, threadId]);
  const hasCurrentTurnAgentResponse = useMemo(
    () => lastTurnMessages.some((message) => isAIMessage(message)),
    [lastTurnMessages],
  );

  useEffect(() => {
    if (!canOpenAgentWorkbench) {
      setAgentWorkbenchManuallyOpened(false);
    }
    if (!hasRenderableAgentWorkbench) {
      if (!isNewThread) {
        setAgentWorkbenchDismissed(false);
      }
      setAgentWorkbenchTabTouched(false);
    }
  }, [canOpenAgentWorkbench, hasRenderableAgentWorkbench, isNewThread]);

  useEffect(() => {
    if (
      durableCollaborationEnabled ||
      !hasRenderableAgentWorkbench ||
      !shouldHideSettledProcessChrome ||
      artifactsOpen ||
      showAgentPlan
    ) {
      return;
    }
    if (settledWorkbenchAutoDismissedRef.current === settledWorkbenchTurnKey) {
      return;
    }
    settledWorkbenchAutoDismissedRef.current = settledWorkbenchTurnKey;
    setAgentWorkbenchDismissed(true);
    setAgentWorkbenchTabTouched(false);
  }, [
    artifactsOpen,
    durableCollaborationEnabled,
    hasRenderableAgentWorkbench,
    settledWorkbenchTurnKey,
    shouldHideSettledProcessChrome,
    showAgentPlan,
  ]);

  useEffect(() => {
    if (
      durableCollaborationEnabled ||
      !agentWorkbenchManuallyOpened ||
      // Never undo an explicit user action. This flag is set by the header
      // menu and artifact-link handoff; auto-dismiss is only for untouched,
      // system-opened empty workbenches.
      agentWorkbenchTabTouched ||
      thread.isLoading ||
      !agentRunSettled ||
      !hasCurrentTurnAgentResponse ||
      hasRenderableAgentWorkbench ||
      // A user-opened artifact is valid workbench content even when this
      // historical turn has no replayable agent events. Without this guard,
      // the empty-workbench cleanup closes the panel in the same render batch
      // that a markdown Office/PDF link opens it.
      (agentWorkbenchTab === "artifacts" && artifacts.length > 0) ||
      artifactsOpen ||
      showAgentPlan ||
      previewBlocks ||
      resultPreviewUrl
    ) {
      return;
    }
    if (emptyWorkbenchAutoDismissedRef.current === settledWorkbenchTurnKey) {
      return;
    }
    emptyWorkbenchAutoDismissedRef.current = settledWorkbenchTurnKey;
    setAgentWorkbenchManuallyOpened(false);
    setAgentWorkbenchDismissed(true);
    setAgentWorkbenchTabTouched(false);
  }, [
    agentRunSettled,
    agentWorkbenchManuallyOpened,
    agentWorkbenchTabTouched,
    artifactsOpen,
    durableCollaborationEnabled,
    hasCurrentTurnAgentResponse,
    hasRenderableAgentWorkbench,
    agentWorkbenchTab,
    artifacts.length,
    previewBlocks,
    resultPreviewUrl,
    settledWorkbenchTurnKey,
    showAgentPlan,
    thread.isLoading,
  ]);

  useEffect(() => {
    if (thread.isLoading) {
      setAgentWorkbenchTabTouched(false);
      setAgentWorkbenchDismissed(false);
    }
  }, [thread.isLoading]);

  useEffect(() => {
    if (
      !showAgentWorkbench ||
      !thread.isLoading ||
      agentWorkbenchTabTouched ||
      !latestWorkspaceFocusTab
    ) {
      return;
    }
    if (latestWorkspaceFocusTab === "artifacts") {
      if (artifactCount <= 0) return;
      if (
        latestArtifactFocusPath &&
        artifacts.includes(latestArtifactFocusPath)
      ) {
        selectArtifact(latestArtifactFocusPath, true);
      }
      // Artifacts now live inside the unified workbench surface. Keeping the
      // legacy standalone flag open would reserve a second (empty) sidebar.
      setArtifactsOpen(false);
      setShowAgentPlan(false);
      setShowResearchHistory(false);
      setShowResearch(false);
      setShowPreview(false);
    }
    setAgentWorkbenchTab(latestWorkspaceFocusTab);
  }, [
    agentWorkbenchTabTouched,
    artifactCount,
    artifacts,
    latestArtifactFocusPath,
    latestWorkspaceFocusTab,
    selectArtifact,
    setArtifactsOpen,
    showAgentWorkbench,
    thread.isLoading,
  ]);

  useEffect(() => {
    if (
      // Mirrors the isNewThread auto-expand path: on mobile the panel takes
      // over the whole chat column, so never auto-open it there.
      isMobile ||
      (!previewBlocks && !resultPreviewUrl) ||
      agentWorkbenchDismissed ||
      agentWorkbenchTabTouched ||
      thread.isLoading ||
      !hasCompletedAgentOutput
    ) {
      return;
    }
    setAgentWorkbenchTab("browser");
    setAgentWorkbenchDismissed(false);
    setAgentWorkbenchManuallyOpened(true);
    setArtifactsOpen(false);
    setShowAgentPlan(false);
    setShowResearchHistory(false);
    setShowResearch(false);
    setShowPreview(false);
  }, [
    isMobile,
    previewBlocks,
    resultPreviewUrl,
    agentWorkbenchDismissed,
    agentWorkbenchTabTouched,
    thread.isLoading,
    hasCompletedAgentOutput,
    setArtifactsOpen,
  ]);

  useEffect(() => {
    const handleAgentFocus = (event: Event) => {
      const detail = (event as CustomEvent<AgentWorkbenchFocusDetail>).detail;
      const agentId =
        typeof detail?.agentId === "string" ? detail.agentId.trim() : "";
      if (!agentId) return;
      closeSpecialUtilityPanels();
      setFocusedWorkbenchAgentId(agentId);
      setFocusedWorkbenchAgentView(detail?.view ?? null);
      setFocusedWorkbenchAgentSnapshot(detail?.agent ?? null);
      setFocusedWorkbenchTurnIndex(
        typeof detail?.turnIndex === "number" ? detail.turnIndex : null,
      );
      setFocusedWorkbenchAgentNonce((n) => n + 1);
      setFocusedWorkbenchEventId(null);
      setFocusedWorkbenchEventKind(null);
      setFocusedWorkbenchEventView(null);
      setFocusedWorkbenchProcessEvent(null);
      setFocusedWorkbenchEffectKey(null);
      setArtifactsOpen(false);
      setShowAgentPlan(false);
      setAgentWorkbenchDismissed(false);
      setAgentWorkbenchManuallyOpened(true);
      setShowResearchHistory(false);
      setShowResearch(false);
      setShowPreview(false);
      setAgentWorkbenchTab(detail?.tab ?? "agent");
      setAgentWorkbenchTabTouched(true);
    };
    window.addEventListener(AGENT_WORKBENCH_FOCUS_EVENT, handleAgentFocus);
    return () =>
      window.removeEventListener(AGENT_WORKBENCH_FOCUS_EVENT, handleAgentFocus);
  }, [closeSpecialUtilityPanels, setArtifactsOpen]);

  useEffect(() => {
    const handleOpenWorkbench = (event: Event) => {
      const detail = (event as CustomEvent<AgentWorkbenchOpenDetail>).detail;
      closeSpecialUtilityPanels();
      setFocusedWorkbenchAgentId(null);
      setFocusedWorkbenchAgentView(null);
      setFocusedWorkbenchAgentSnapshot(null);
      setFocusedWorkbenchTurnIndex(null);
      setFocusedWorkbenchEventId(detail?.eventId?.trim() || null);
      setFocusedWorkbenchEventKind(detail?.eventKind ?? null);
      setFocusedWorkbenchEventView(detail?.view ?? null);
      setFocusedWorkbenchProcessEvent(detail?.processEvent ?? null);
      setFocusedWorkbenchEffectKey(detail?.effectKey?.trim() || null);
      setFocusedWorkbenchEventNonce((n) => n + 1);
      setArtifactsOpen(false);
      setShowAgentPlan(false);
      setAgentWorkbenchDismissed(false);
      setAgentWorkbenchManuallyOpened(true);
      setShowResearchHistory(false);
      setShowResearch(false);
      setShowPreview(false);
      if (detail?.tab) {
        setAgentWorkbenchTab(detail.tab);
      }
      setAgentWorkbenchTabTouched(true);
    };
    window.addEventListener(AGENT_WORKBENCH_OPEN_EVENT, handleOpenWorkbench);
    return () =>
      window.removeEventListener(
        AGENT_WORKBENCH_OPEN_EVENT,
        handleOpenWorkbench,
      );
  }, [closeSpecialUtilityPanels, setArtifactsOpen]);

  const handleAcceptModeIntent = useCallback(
    (mode: AgentModeName) => {
      setProjectAgentMode(mode);
      setModeIntentSuggestion(null);
      toast.success(t.modeIntent.autoSwitched(modeLabelFor(mode, t)));
    },
    [t],
  );

  const handleDismissModeIntent = useCallback(() => {
    setModeIntentSuggestion(null);
  }, []);

  const handleSubmit = useCallback(
    (message: {
      text: string;
      images?: File[];
      files?: File[];
      uploaded?: UploadedFileInfo[];
    }) => {
      const images = message.images ?? [];
      const attachedFiles = message.files ?? [];
      const browserFiles = [...attachedFiles, ...images];
      if (legacyOnDemandThreadOwnerId) {
        const leaderId = primaryPersonaAgentIdOrDefault(activeAgentId);
        writeTaskCollaboratorPreset({
          leaderId,
          collaboratorIds: [legacyOnDemandThreadOwnerId],
          mode: "cluster",
          label: legacyOnDemandThreadOwnerId,
          openPicker: true,
        });
        if (browserFiles.length === 0 && message.text.trim()) {
          writePendingNewSession(message.text);
          toast.info(t.realtime.composer.legacyOnDemandContinued);
          navigate(taskWorkspaceRoute({ agentId: leaderId }));
        } else {
          toast.info(t.realtime.composer.legacyOnDemandAttachments);
          navigate(
            taskWorkspaceRoute({ agentId: leaderId, prompt: message.text }),
          );
        }
        return;
      }
      // Intent-based mode auto-switch: only in project/code mode, and never
      // for the echo assistant (fixed chat persona). Manual override wins —
      // when the user has hand-picked a mode we only suggest, never silently
      // switch. High-confidence verdicts auto-switch + toast; medium ones
      // surface the lightweight suggestion bar above the composer.
      if (isProjectCodeMode && !isEchoAssistant && !isGroupConversation) {
        const verdict = classifyModeIntent(
          recentHumanMessageTexts(thread.messages),
        );
        if (
          verdict.handle !== "none" &&
          verdict.mode &&
          verdict.mode !== projectAgentMode
        ) {
          const label = modeLabelFor(verdict.mode, t);
          if (modeManualOverride) {
            setModeIntentSuggestion({ mode: verdict.mode, label });
          } else if (verdict.handle === "auto") {
            setProjectAgentMode(verdict.mode);
            toast.success(t.modeIntent.autoSwitched(label));
          } else if (verdict.handle === "suggest") {
            setModeIntentSuggestion({ mode: verdict.mode, label });
          }
        }
      }
      // The auto-new-session preference belongs only to the fixed Assistant
      // window. Project threads and role/personal-space threads keep their
      // own continuity regardless of this setting.
      // Attachments can't travel through the hand-off, so we only auto-start
      // for text-only messages; everything else stays in the current thread.
      const autoNewSessionHours = settings.session?.auto_new_session_hours ?? 0;
      if (
        isEchoAssistant &&
        autoNewSessionHours > 0 &&
        message.text.trim().length > 0 &&
        browserFiles.length === 0 &&
        isThreadStale(threadIdentityQuery.data?.updated_at, autoNewSessionHours)
      ) {
        writePendingNewSession(message.text);
        toast.info(
          `已为你开启新会话（距上次对话已超过 ${autoNewSessionHours} 小时）`,
        );
        navigate(
          taskWorkspaceRoute({ agentId: activeAgentId, prompt: message.text }),
          { replace: false },
        );
        return;
      }

      markSidebarThreadRunning(threadId);
      if (browserFiles.length === 0) {
        void sendMessage(threadId, { text: message.text, files: [] });
        if (isGroupConversation) {
          // Task strategy is a one-turn intent. Returning to auto avoids a
          // later conversational follow-up silently running a heavy workflow.
          setGroupTaskStrategy(groupTaskStrategyAfterSubmit());
        }
        return;
      }
      // Composer-side uploads already happened on attach; align them back onto
      // the parts by filename so the send path can skip the network.
      const uploadedByName = new Map(
        (message.uploaded ?? []).map((info) => [info.filename, info]),
      );
      // Read each image into a data URL so PromptInputFilePart has the
      // `url` field FileUIPart requires; the original File is also
      // attached so the upload path can re-use the bytes without
      // re-decoding.
      void Promise.all(
        browserFiles.map(
          (file) =>
            new Promise<PromptInputFilePart>((resolve, reject) => {
              const mediaType = file.type || "application/octet-stream";
              const uploaded = uploadedByName.get(file.name);
              if (!mediaType.toLowerCase().startsWith("image/")) {
                resolve({
                  type: "file",
                  mediaType,
                  filename: file.name,
                  url: "",
                  file,
                  uploaded,
                });
                return;
              }
              const reader = new FileReader();
              reader.onload = () => {
                const url =
                  typeof reader.result === "string" ? reader.result : "";
                resolve({
                  type: "file",
                  mediaType,
                  filename: file.name,
                  url,
                  file,
                  uploaded,
                });
              };
              reader.onerror = () =>
                reject(reader.error ?? new Error("FileReader failed"));
              reader.readAsDataURL(file);
            }),
        ),
      )
        .then((files) => {
          void sendMessage(threadId, { text: message.text, files });
          if (isGroupConversation) {
            setGroupTaskStrategy(groupTaskStrategyAfterSubmit());
          }
        })
        .catch((err) => {
          swallow(err);
          toast.error(t.chatInputBox.attachmentReadFailed);
        });
    },
    [
      isEchoAssistant,
      isGroupConversation,
      isProjectCodeMode,
      legacyOnDemandThreadOwnerId,
      markSidebarThreadRunning,
      modeManualOverride,
      projectAgentMode,
      sendMessage,
      t,
      thread.messages,
      threadId,
      activeAgentId,
      navigate,
      settings,
      threadIdentityQuery,
    ],
  );
  // Auto-send a one-shot hand-off when a fresh thread is opened by the
  // Assistant timeout or a legacy on-demand-owned conversation migration.
  // Session storage is consumed once, so refresh cannot duplicate the send.
  const pendingNewSessionSentRef = useRef(false);
  useEffect(() => {
    if (!isNewThread) {
      // The page component survives hash-route transitions. Reset the
      // one-shot latch when returning to an existing thread so a later Retry
      // can hand off and auto-send another fresh task.
      pendingNewSessionSentRef.current = false;
      return;
    }
    if (pendingNewSessionSentRef.current) return;
    const pendingText = consumePendingNewSession();
    if (!pendingText) return;
    pendingNewSessionSentRef.current = true;
    const timer = window.setTimeout(() => {
      markSidebarThreadRunning(threadId);
      // sendMessage returns void (fire-and-forget). If the connection isn't
      // ready yet the composer still shows the prompt, so the user can retry
      // by pressing Enter.
      void sendMessage(threadId, { text: pendingText, files: [] });
    }, 200);
    return () => window.clearTimeout(timer);
  }, [isNewThread, threadId, sendMessage, markSidebarThreadRunning]);

  useEffect(() => {
    const handleQuickReply = (event: Event) => {
      const detail = (event as CustomEvent<QuickReplyDetail>).detail;
      const text = quickReplyTextForThread(detail, threadId);
      if (!text || thread.isLoading) return;
      event.preventDefault();
      markSidebarThreadRunning(threadId);
      void sendMessage(threadId, { text, files: [] });
    };
    window.addEventListener(QUICK_REPLY_EVENT, handleQuickReply);
    return () => {
      window.removeEventListener(QUICK_REPLY_EVENT, handleQuickReply);
    };
  }, [markSidebarThreadRunning, sendMessage, thread.isLoading, threadId]);

  // Follow-up suggestion chips: send the picked prompt as if the user typed it.
  const handleSendFollowUp = useCallback(
    (prompt: string) => {
      const text = prompt.trim();
      if (!text || thread.isLoading) return;
      markSidebarThreadRunning(threadId);
      void sendMessage(threadId, { text, files: [] });
    },
    [markSidebarThreadRunning, sendMessage, thread.isLoading, threadId],
  );
  const handleRetryTask = useCallback(
    (prompt: string) => {
      const text = prompt.trim();
      if (!text || thread.isLoading) return;
      // A retry should actually resume the failed conversation. Sending the
      // recovered objective as a new turn preserves the gathered evidence
      // and avoids leaving the user on a pre-filled, unsent "new task" page.
      markSidebarThreadRunning(threadId);
      void sendMessage(threadId, { text, files: [] });
    },
    [markSidebarThreadRunning, sendMessage, thread.isLoading, threadId],
  );
  const handleModeChange = useCallback(
    (mode: ReasoningMode, draft?: string) => {
      if (mode === effectiveMode) return;
      if (mode === "code" && !isCodingWorkspaceMode) return;
      if (!isAgentRoute) {
        setDiscussionOnly(mode === "chat");
        return;
      }
      setSettings("context", {
        ...settings.context,
        mode,
      });
      if (
        mode === "react" ||
        mode === "deep" ||
        (isAgentRoute && mode === "chat")
      ) {
        navigate(newThreadRouteForMode(mode, draft), { replace: false });
      }
    },
    [
      effectiveMode,
      isAgentRoute,
      isCodingWorkspaceMode,
      navigate,
      newThreadRouteForMode,
      setSettings,
      settings.context,
    ],
  );

  const handleDeepResearch = useCallback(
    async (topic: string, options?: DeepResearchComposerOptions) => {
      const extracted = extractResearchUrls(topic);
      const clean = extracted.topic.trim();
      if (!clean || researchLoading) return false;
      const urls = Array.from(
        new Set([
          ...extracted.urls,
          ...(options?.urls ?? []),
          ...(options?.materials ?? [])
            .map((material) => material.url)
            .filter((url): url is string => !!url),
        ]),
      );
      closeSpecialUtilityPanels();
      setResearchLoading(true);
      setResearchError(null);
      setShowAgentPlan(false);
      setShowResearch(true);
      setShowResearchHistory(false);
      setShowPreview(false);
      try {
        const job = await startDeepResearch({
          topic: clean,
          thread_id: threadId,
          lead_agent_name: effectiveAgentId,
          depth: "deep",
          max_subagents: options?.maxSubagents,
          max_searches: options?.maxSearches ?? 274,
          include_thread_uploads: true,
          prefetch_sources: true,
          materials: options?.materials ?? [],
          urls,
          roles: options?.roles,
          source_kinds: options?.sourceKinds ?? [
            "web",
            "news",
            "academic",
            "company_site",
            "ecommerce",
            "social",
            "forum",
            "provided_url",
            "uploaded_file",
          ],
        });
        setResearchJob(job);
        return true;
      } catch (err) {
        swallow(err);
        setResearchError(
          err instanceof Error ? err.message : "Failed to start agent run",
        );
        return false;
      } finally {
        setResearchLoading(false);
      }
    },
    [closeSpecialUtilityPanels, effectiveAgentId, researchLoading, threadId],
  );

  // Implementation note.
  // Implementation note.
  // Implementation note.
  // Implementation note.
  const pauseTask = usePauseTask();
  const handleStop = useCallback(async () => {
    const activeForThread = (tasks.data?.active ?? []).find(
      (t) => t.thread_id === threadId,
    );
    await thread.stop();
    if (activeForThread) {
      try {
        await pauseTask.mutateAsync({
          taskId: activeForThread.task_id,
          reason: "user_request",
          note: t.chatPage.stopNote,
        });
      } catch (e) {
        swallow(e);
      }
    }
  }, [thread, threadId, tasks.data, pauseTask, t.chatPage.stopNote]);

  const hasResearchPanel = showResearch && (!!researchJob || !!researchError);
  const hasSpecialUtilityPanel =
    showTeachRepeatPanel || (isEchoAssistant && showAutomationPanel);
  const activeRightPanel: RightPanelPage | null = hasSpecialUtilityPanel
    ? // RightPanelMenu only needs a non-null value to make its shared button
      // close the currently visible surface. Special utilities have no menu
      // page of their own, so use the generic workbench marker.
      "agent"
    : showResearchHistory
      ? "history"
      : hasResearchPanel
        ? "research"
        : artifactsOpen
          ? "artifacts"
          : showAgentPlan
            ? "plan"
            : showAgentWorkbench
              ? agentWorkbenchTab === "artifacts"
                ? "artifacts"
                : "agent"
              : null;

  const openAgentPanel = useCallback(() => {
    closeSpecialUtilityPanels();
    setFocusedWorkbenchEffectKey(null);
    setArtifactsOpen(false);
    setShowAgentPlan(false);
    setAgentWorkbenchDismissed(false);
    setAgentWorkbenchManuallyOpened(true);
    setShowResearchHistory(false);
    setShowResearch(false);
    setShowPreview(false);
    setAgentWorkbenchTab("agent");
    setAgentWorkbenchTabTouched(true);
  }, [closeSpecialUtilityPanels, setArtifactsOpen]);

  const openArtifactsPanel = useCallback(() => {
    closeSpecialUtilityPanels();
    // Artifacts render inside the workbench's "产物" tab (same surface as
    // terminal / browser). Open the workbench and switch to that tab.
    setArtifactsOpen(false);
    setShowAgentPlan(false);
    setAgentWorkbenchDismissed(false);
    setAgentWorkbenchManuallyOpened(true);
    setShowResearchHistory(false);
    setShowResearch(false);
    setShowPreview(false);
    setAgentWorkbenchTab("artifacts");
    setAgentWorkbenchTabTouched(true);
  }, [closeSpecialUtilityPanels, setArtifactsOpen]);

  const openWorkbenchArtifact = useCallback(
    (path: string) => {
      closeSpecialUtilityPanels();
      const normalizedPath = normalizeWorkspaceArtifactRef(path, threadId);
      if (path) {
        if (!artifacts.includes(normalizedPath)) {
          setArtifacts((prev) => [...prev, normalizedPath]);
        }
        selectArtifact(normalizedPath, true);
      }
      // Route to the embedded artifacts tab inside the workbench
      // (same surface as terminal / browser). Auto-open the workbench
      // if it's not visible.
      setArtifactsOpen(false);
      setShowAgentPlan(false);
      setAgentWorkbenchDismissed(false);
      setAgentWorkbenchManuallyOpened(true);
      setShowResearchHistory(false);
      setShowResearch(false);
      setShowPreview(false);
      setAgentWorkbenchTab("artifacts");
      setAgentWorkbenchTabTouched(true);
    },
    [
      artifacts,
      closeSpecialUtilityPanels,
      selectArtifact,
      setArtifactsOpen,
      setArtifacts,
      threadId,
    ],
  );

  useEffect(() => {
    const handleOpenArtifact = (event: Event) => {
      const detail = (event as CustomEvent<OpenArtifactDetail>).detail;
      const path = typeof detail?.path === "string" ? detail.path.trim() : "";
      if (!path) return;
      event.preventDefault();
      openWorkbenchArtifact(path);
    };
    window.addEventListener(OPEN_ARTIFACT_EVENT, handleOpenArtifact);
    return () =>
      window.removeEventListener(OPEN_ARTIFACT_EVENT, handleOpenArtifact);
  }, [openWorkbenchArtifact]);

  const openFinalArtifactPanel = useCallback(() => {
    const firstEntry = finalArtifactEntries[0];
    if (firstEntry?.path) openWorkbenchArtifact(firstEntry.path);
  }, [finalArtifactEntries, openWorkbenchArtifact]);

  const openAgentPlanPanel = useCallback(() => {
    closeSpecialUtilityPanels();
    setArtifactsOpen(false);
    setShowAgentPlan(true);
    setShowResearchHistory(false);
    setShowResearch(false);
    setShowPreview(false);
  }, [closeSpecialUtilityPanels, setArtifactsOpen]);

  const openPreviewPanel = useCallback(() => {
    closeSpecialUtilityPanels();
    setArtifactsOpen(false);
    setShowAgentPlan(false);
    setAgentWorkbenchDismissed(false);
    setAgentWorkbenchManuallyOpened(true);
    setShowResearchHistory(false);
    setShowResearch(false);
    setShowPreview(false);
    setAgentWorkbenchTab("browser");
    setAgentWorkbenchTabTouched(true);
  }, [closeSpecialUtilityPanels, setArtifactsOpen]);

  const openResearchPanel = useCallback(() => {
    closeSpecialUtilityPanels();
    setArtifactsOpen(false);
    setShowAgentPlan(false);
    setShowResearchHistory(false);
    setShowResearch(true);
    setShowPreview(false);
  }, [closeSpecialUtilityPanels, setArtifactsOpen]);

  const openResearchHistoryPanel = useCallback(() => {
    closeSpecialUtilityPanels();
    setArtifactsOpen(false);
    setShowAgentPlan(false);
    setShowResearchHistory(true);
    setShowResearch(false);
    setShowPreview(false);
  }, [closeSpecialUtilityPanels, setArtifactsOpen]);

  const closeAgentWorkbenchPanel = useCallback(() => {
    setArtifactsOpen(false);
    setAgentWorkbenchManuallyOpened(false);
    setAgentWorkbenchDismissed(true);
  }, [setArtifactsOpen]);

  const closeUnifiedRightPanel = useCallback(() => {
    // Utility views temporarily take over the one right-side surface. Closing
    // the top view only dismisses that view, allowing the previously open
    // workbench (and its selected tab) to reappear underneath.
    if (showTeachRepeatPanel) {
      setShowTeachRepeatPanel(false);
      return;
    }
    if (isEchoAssistant && showAutomationPanel) {
      setShowAutomationPanel(false);
      return;
    }
    if (showResearchHistory) {
      setShowResearchHistory(false);
      return;
    }
    if (hasResearchPanel) {
      setShowResearch(false);
      return;
    }
    if (showAgentPlan) {
      setShowAgentPlan(false);
      return;
    }
    closeAgentWorkbenchPanel();
  }, [
    closeAgentWorkbenchPanel,
    hasResearchPanel,
    isEchoAssistant,
    showAgentPlan,
    showAutomationPanel,
    showResearchHistory,
    showTeachRepeatPanel,
  ]);

  const closeRightPanel = closeUnifiedRightPanel;
  const selectAgentWorkbenchTab = useCallback(
    (tab: AgentWorkbenchTabId) => {
      if (tab === "plan") {
        openAgentPlanPanel();
        return;
      }
      closeSpecialUtilityPanels();
      // "artifacts" now renders inline inside the workbench (same surface as
      // terminal / browser) — no need to open the legacy standalone sidebar.
      setArtifactsOpen(false);
      setShowAgentPlan(false);
      setAgentWorkbenchDismissed(false);
      setAgentWorkbenchManuallyOpened(true);
      setShowResearchHistory(false);
      setShowResearch(false);
      setShowPreview(false);
      setAgentWorkbenchTab(tab);
      setAgentWorkbenchTabTouched(true);
      rememberWorkbenchTab(effectiveAgentId, tab);
    },
    [
      closeSpecialUtilityPanels,
      effectiveAgentId,
      openAgentPlanPanel,
      setArtifactsOpen,
    ],
  );

  const currentAgent = useMemo(
    () => ({
      name: effectiveAgentId,
      display_name: displayAgent?.display_name || effectiveAgentId,
      avatar_url:
        displayAgent?.avatar_url ||
        `/api/agents/${encodeURIComponent(effectiveAgentId)}/avatar`,
      icon: displayAgent?.icon || null,
      execution_engine: selectedExecutionEngine,
    }),
    [displayAgent, effectiveAgentId, selectedExecutionEngine],
  );

  const handleModelChange = useCallback(
    (modelName: string) => {
      setSettings("context", {
        ...settings.context,
        model_name: modelName,
      });
    },
    [setSettings, settings.context],
  );

  const handleModelSwitchNotice = useCallback(
    (modelName: string) => {
      if (
        isNewThread ||
        !threadId ||
        threadId === "new" ||
        thread.messages.length === 0
      ) {
        return;
      }
      setModelSwitchTimeline((current) => {
        const currentEvents =
          current.threadId === threadId
            ? current.events
            : loadModelSwitchEvents(threadId);
        return {
          threadId,
          events: recordModelSwitchEvent(threadId, currentEvents, {
            modelName,
            afterMessageCount: thread.messages.length,
          }),
        };
      });
    },
    [isNewThread, thread.messages.length, threadId],
  );

  const handleReasoningEffortChange = useCallback(
    (reasoningEffort: ReasoningEffort) => {
      setSettings("context", {
        ...settings.context,
        reasoning_effort: normalizeReasoningEffortForUi(reasoningEffort),
      });
    },
    [setSettings, settings.context],
  );

  const handlePermissionModeChange = useCallback(
    (permissionMode: PermissionMode) => {
      // The composer shortcut changes ONLY the permission axis; the execution
      // environment stays independent (controlled in Settings → Sandbox). A
      // bypass mode implies auto-approval, anything else asks on request.
      setSettings("context", {
        ...settings.context,
        permission_mode: permissionMode,
        approval_policy:
          permissionMode === "bypassPermissions" ? "never" : "on-request",
      });
    },
    [setSettings, settings.context],
  );

  const headerAgentIdentity = (
    <ChatHeaderAgentBadge
      agent={displayAgent}
      agentId={effectiveAgentId}
      collaborators={
        visibleCollaborationEnabled ? visibleCollaborationRoster : undefined
      }
    />
  );
  const headerTitle = !isEchoAssistant ? (
    <ThreadTitle
      threadId={threadId}
      thread={thread}
      title={headerThreadTitle}
      className={cn(
        "border-0 bg-transparent px-0 py-0 text-sm",
        isGroupConversation && "w-full max-w-full",
      )}
    />
  ) : null;
  const headerRunStatus = (
    <RunDurationBadge
      isLoading={thread.isLoading}
      vitals={(thread as typeof thread & { vitals?: StreamVitals }).vitals}
    />
  );
  const headerHumanInvite =
    !isEchoAssistant && canManageHumanInvites ? (
      <GroupHumanInviteButton
        roomId={resolvedHumanInviteRoomId}
        threadId={threadId}
        onEnsureRoom={ensureHumanInviteRoom}
        onRoomResolved={setHumanInviteRoomId}
        open={humanInviteDialogOpen}
        onOpenChange={(open) => {
          setHumanInviteDialogOpen(open);
          if (open) setCollaboratorPickerOpen(false);
        }}
        size="sm"
        variant="ghost"
        className="w-full justify-start gap-2 px-2"
        disabled={isNewThread || ensureCollabRoomMutation.isPending}
      />
    ) : null;
  const onlineCollaboratorCount =
    collabSessionQuery.data?.presence.reduce(
      (count, member) => count + (member.online ? 1 : 0),
      0,
    ) ?? 0;
  const headerMemberControl =
    !isEchoAssistant && canManageHumanInvites ? (
      <TaskCollaboratorControl
        agents={allTaskCollaboratorAgents}
        selectedAgents={selectedCollaborators}
        selectedAgentIds={selectedCollaboratorIds}
        currentAgentName={currentTaskAgentName}
        teamMode={teamModeIntent}
        open={collaboratorPickerOpen}
        onOpenChange={setCollaboratorPickerOpen}
        onSelectedAgentIdsChange={handleSelectedCollaboratorIdsChange}
        onTeamModeChange={handleTeamModeIntentChange}
        roster={visibleCollaborationRoster}
        onlineCount={onlineCollaboratorCount}
        humanInviteAction={headerHumanInvite}
        labelPrefix="AI"
        disabled={replaceCoworkRosterMutation.isPending}
      />
    ) : undefined;
  const headerRecorder =
    !isEchoAssistant && recorderPluginEnabled ? (
      <ChatHeaderRecButton
        threadId={threadId}
        onOpen={() => setRecOverlayOpen(true)}
        isRecording={recIsRecording}
      />
    ) : null;
  const headerShareTitle =
    boundProjectState?.project.name ||
    headerThreadTitle ||
    thread?.values?.title ||
    initialPrompt;
  const headerShareOptions: RealtimeChatHeaderShareOptions | undefined =
    headerShareTitle
      ? {
          title: headerShareTitle,
          prompt: initialPrompt || undefined,
          onExportReplay:
            replayBlocks.length > 0 ? handleExportReplay : undefined,
        }
      : undefined;
  const headerWorkbench = (
    <RightPanelMenu
      activePage={activeRightPanel}
      artifactCount={artifactCount}
      hasAgentWorkbench={canOpenAgentWorkbench}
      hasPlan={hasRenderableAgentWorkbench}
      hasPreview={!!previewBlocks}
      hasResearch={!!researchJob || !!researchError}
      hasResearchHistory={!!researchJob || !!researchError}
      onClosePanel={closeRightPanel}
      onOpenAgent={openAgentPanel}
      onOpenArtifacts={openArtifactsPanel}
      onOpenPlan={openAgentPlanPanel}
      onOpenPreview={openPreviewPanel}
      onOpenResearch={openResearchPanel}
      onOpenResearchHistory={openResearchHistoryPanel}
    />
  );
  const headerEchoShare =
    isEchoAssistant && headerShareOptions ? (
      <ShareMenu
        iconOnly
        threadId={threadId}
        title={headerShareOptions.title}
        prompt={headerShareOptions.prompt}
        summary={headerShareOptions.summary}
        footer={headerShareOptions.footer}
        onExportReplay={headerShareOptions.onExportReplay}
      />
    ) : null;
  const headerMemberSurface = !isEchoAssistant ? (
    <RealtimeChatHeaderMemberSurface aiMembers={headerMemberControl} />
  ) : null;
  const headerActions = !isEchoAssistant ? (
    <RealtimeChatHeaderActions
      recording={recorderPluginEnabled ? headerRecorder : null}
      workbench={headerWorkbench}
      share={
        headerShareOptions ? (
          <ShareMenu
            iconOnly
            threadId={threadId}
            title={headerShareOptions.title}
            prompt={headerShareOptions.prompt}
            summary={headerShareOptions.summary}
            footer={headerShareOptions.footer}
            onExportReplay={headerShareOptions.onExportReplay}
          />
        ) : null
      }
    />
  ) : null;

  return (
    <SubtasksProvider>
      <ThreadProviders thread={thread} isMock={false}>
        <ToolEffectsProvider
          enabled={
            !isNewThread && canAccessGlobalControlPlane(authStatus, user)
          }
          active={thread.isLoading}
        >
          <ChatBox artifactPanelMode="external" threadId={threadId}>
            <ChatPageLayout
              isNewThread={isNewThread}
              pageTitle={
                headerThreadTitle ||
                thread?.values?.title ||
                boundProjectState?.project.name ||
                initialPrompt ||
                (isNewThread ? t.sidebar.actionNewTask : "EchoAI")
              }
              header={
                <>
                  {!embeddedDesignChat && !isEchoAssistant && (
                    <ChatHeaderMenuButton
                      onClick={() => setChatsDrawerOpen(true)}
                      className="absolute left-3 top-1/2 -translate-y-1/2 md:hidden"
                    />
                  )}
                  {!isEchoAssistant ? (
                    <RealtimeGroupHeaderLayout
                      title={headerTitle}
                      projectStatus={
                        !embeddedDesignChat && boundProjectState ? (
                          <ProjectGroupHeaderBadge
                            name={boundProjectState.project.name}
                            status={boundProjectState.project.status}
                            onOpenWorkbench={() =>
                              openProjectWorkbenchForEntity()
                            }
                            canDetach={canManageHumanInvites}
                            onDetach={() =>
                              void handleDetachProjectCapability()
                            }
                            isDetaching={
                              detachProjectFromGroupMutation.isPending
                            }
                          />
                        ) : null
                      }
                      runStatus={headerRunStatus}
                      members={embeddedDesignChat ? null : headerMemberSurface}
                      workbench={embeddedDesignChat ? null : headerActions}
                    />
                  ) : (
                    <>
                      {headerAgentIdentity}
                      <div className="flex min-w-0 flex-1 items-center gap-2">
                        {connectedChannels.length > 0 && (
                          <div className="flex shrink-0 items-center gap-1">
                            <span className="size-1.5 rounded-full bg-emerald-500" />
                            <span className="text-mini text-muted-foreground/70">
                              已连接:{" "}
                              {connectedChannels
                                .map((c) => channelDisplayNames[c] || c)
                                .join("、")}
                            </span>
                          </div>
                        )}
                        {headerRunStatus}
                      </div>
                      <div className="ml-auto flex shrink-0 items-center gap-1">
                        {/* 助理是单聊：不提供加人/协作，也不录制，头部保持极简 */}
                        {headerEchoShare}
                        <Button
                          type="button"
                          variant="ghost"
                          aria-label="自动化与订阅"
                          title="自动化与订阅"
                          onClick={toggleAutomationPanel}
                          className={cn(
                            "flex size-[42px] items-center justify-center rounded-lg border shadow-none transition-all duration-base focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 sm:size-8",
                            showAutomationPanel
                              ? "border-transparent bg-transparent text-foreground/82 hover:border-border-default hover:bg-muted/55 hover:text-foreground"
                              : "border-transparent bg-transparent text-muted-foreground hover:border-border-default hover:bg-muted/55 hover:text-foreground",
                          )}
                        >
                          <Settings2Icon className="size-4" />
                        </Button>
                        <AssistantSettingsMenu />
                        {headerWorkbench}
                      </div>
                    </>
                  )}
                  {canPromoteGroupToProject ? (
                    <PromoteGroupToProjectDialog
                      open={promoteGroupDialogOpen}
                      onOpenChange={setPromoteGroupDialogOpen}
                      threadId={threadId}
                      defaultName={headerThreadTitle || collaborationTeamName}
                      onPromoted={async () => {
                        await boundProjectQuery.refetch();
                        openProjectWorkbenchForEntity();
                      }}
                    />
                  ) : null}
                  {projectDetachDialog}
                </>
              }
              headerClassName={
                embeddedDesignChat
                  ? "px-3"
                  : !isEchoAssistant
                    ? "md:pl-3"
                    : undefined
              }
              messageList={
                <MessageList
                  className="size-full"
                  threadId={threadId}
                  thread={thread}
                  onOpenArtifact={openWorkbenchArtifact}
                  project={projectWorkspacePath || null}
                  onSendFollowUp={handleSendFollowUp}
                  onRetryTask={handleRetryTask}
                  onAuthorizeNetwork={handleAuthorizeNetwork}
                  header={
                    realtimeApprovals.hasMoreTurns ? (
                      <LoadOlderTurnsBanner
                        onLoad={realtimeApprovals.loadOlderTurns}
                      />
                    ) : null
                  }
                  emptyState={
                    !realtimeApprovals.hasMoreTurns &&
                    !isNewThread &&
                    !thread.isThreadLoading &&
                    !thread.isLoading ? (
                      <ConversationEmptyState
                        isGroupConversation={isGroupConversation}
                        hasError={Boolean(thread.error)}
                        onRetry={() => {
                          void thread.refresh();
                        }}
                      />
                    ) : null
                  }
                  paddingBottom={MESSAGE_LIST_DEFAULT_PADDING_BOTTOM}
                  mode={effectiveMode}
                  liveToolEvents={embeddedDesignChat ? [] : lastTurnToolEvents}
                  lastTurnToolEvents={
                    embeddedDesignChat ? [] : lastTurnToolEvents
                  }
                  allToolEvents={embeddedDesignChat ? [] : allToolEvents}
                  completedAgentOutput={hasCompletedAgentOutput}
                  currentAgent={currentAgent}
                  agentRoster={
                    visibleCollaborationEnabled
                      ? visibleCollaborationRoster
                      : undefined
                  }
                  showSenderName={
                    !embeddedDesignChat &&
                    (visibleCollaborationEnabled ||
                      Boolean(collabSessionQuery.data?.room_id) ||
                      isProjectHomeThread)
                  }
                  projectMessageActions={projectMessageActions}
                  allowThreadFork={allowThreadFork}
                  timelineEntries={conversationTimelineEntries}
                  footer={
                    <>
                      {hasCompletedAgentOutput &&
                      hasFinalArtifact &&
                      !hasReportArtifact ? (
                        <FinalArtifactCompletionNotice
                          entries={finalArtifactEntries}
                          onOpen={openFinalArtifactPanel}
                        />
                      ) : null}
                    </>
                  }
                />
              }
              inputArea={
                <div
                  className={cn(
                    "relative w-full transition-[max-width,transform] duration-slow",
                    isNewThread &&
                      "-translate-y-[clamp(3rem,12dvh,7rem)] md:-translate-y-[calc(50vh-168px)]",
                    isNewThread ? "max-w-3xl" : "max-w-(--container-width-md)",
                  )}
                >
                  {mounted ? (
                    <div className="flex flex-col gap-2">
                      {isNewThread ? (
                        <Welcome
                          agent={displayAgent}
                          agentName={effectiveAgentId}
                        />
                      ) : null}
                      {!isNewThread ? (
                        <ComposerStepProgress
                          events={agentDisplayEvents}
                          hasAnswer={hasCompletedAgentOutput}
                          isLoading={thread.isLoading}
                          runSettled={agentRunSettled}
                          runFailed={agentRunFailed}
                          paused={hasPausedOrPendingBackgroundTask}
                          className="mt-2"
                        />
                      ) : null}
                      <RealtimeApprovalPrompt
                        approvals={realtimeApprovals.pendingApprovals}
                        resolveApproval={realtimeApprovals.resolveApproval}
                        className="-mb-1"
                      />
                      <div className="pt-3">
                        {automationTarget ? (
                          <AutomationControlDock
                            threadId={threadId}
                            target={automationTarget}
                          />
                        ) : null}
                        <ChatInputBox
                          key={composerSeed || "empty-composer"}
                          status={
                            thread.error && !hasCompletedAgentOutput
                              ? "error"
                              : thread.isLoading
                                ? "streaming"
                                : "ready"
                          }
                          modelName={settings.context.model_name}
                          // Keep one selector, but project model ownership by
                          // engine: Codex roles use the server-owned profile;
                          // native roles serialize the thread's model source.
                          modelProfileControl={!embeddedDesignChat}
                          executionEngine={selectedExecutionEngine}
                          mode={effectiveMode}
                          reasoningEffort={effectiveReasoningEffort}
                          threadId={threadId}
                          mentionMembers={collaborationMentionMembers}
                          isGroupConversation={isGroupConversation}
                          groupTaskStrategy={groupTaskStrategy}
                          onGroupTaskStrategyChange={setGroupTaskStrategy}
                          projectCapabilityEnabled={Boolean(boundProjectState)}
                          onProjectCapabilityAction={
                            projectCapabilityAction === "open"
                              ? () => openProjectWorkbenchForEntity()
                              : projectCapabilityAction === "create"
                                ? () => setPromoteGroupDialogOpen(true)
                                : undefined
                          }
                          onSwitchPanel={
                            recorderPluginEnabled
                              ? (panel) => {
                                  if (panel === "teach-repeat") {
                                    openTeachRepeatPanel();
                                  }
                                }
                              : undefined
                          }
                          responseModeControl={
                            !embeddedDesignChat && isGroupConversation ? (
                              <TeamModePicker
                                value={teamModeIntent}
                                onChange={handleTeamModeIntentChange}
                                ariaLabel={t.chatInputBox.responseMode}
                                compact
                                disabled={
                                  thread.isLoading ||
                                  replaceCoworkRosterMutation.isPending
                                }
                                disabledModes={
                                  visibleCollaborationRoster.length <= 1
                                    ? ["cluster", "swarm"]
                                    : []
                                }
                                disabledReason={
                                  t.chatInputBox.responseModeTeamRequired
                                }
                              />
                            ) : undefined
                          }
                          statusTrailing={
                            !embeddedDesignChat && isGroupConversation ? (
                              <ConversationRosterStrip
                                seats={collaborationRosterSeats}
                                onMemberClick={openAgentPanel}
                              />
                            ) : undefined
                          }
                          automationTarget={
                            embeddedDesignChat ? null : automationTarget
                          }
                          onAutomationTargetChange={
                            embeddedDesignChat
                              ? undefined
                              : handleAutomationTargetChange
                          }
                          disabled={researchLoading}
                          workDir={effectiveWorkDir}
                          displayAgent={composerDisplayAgent}
                          showWorkDirSelector={!embeddedDesignChat}
                          onWorkDirChange={handleWorkDirChange}
                          lockWorkDirToThread={!isNewThread}
                          onOpenWorkDirInNewTask={openWorkDirInNewTask}
                          codeModeUnlocked={codeModeUnlocked}
                          projectAgentMode={projectAgentMode}
                          auditIntensity={auditIntensity}
                          personalMode={personalMode}
                          projectDetection={projectDetection}
                          onProjectAgentModeChange={setProjectAgentMode}
                          onAuditIntensityChange={setAuditIntensity}
                          onPersonalModeChange={handlePersonalModeChange}
                          onProjectDetectionChange={setProjectDetection}
                          onManualOverrideChange={setModeManualOverride}
                          modeIntentSuggestion={modeIntentSuggestion}
                          onAcceptModeIntent={handleAcceptModeIntent}
                          onDismissModeIntent={handleDismissModeIntent}
                          contextTokens={contextTokens}
                          maxContextTokens={maxContextTokens}
                          isCompressingContext={isCompressingContext}
                          onCompressContext={handleCompressContext}
                          onModelChange={handleModelChange}
                          onModelSwitchNotice={handleModelSwitchNotice}
                          onReasoningEffortChange={handleReasoningEffortChange}
                          onModeChange={handleModeChange}
                          permissionMode={normalizePermissionMode(
                            settings.context.permission_mode,
                          )}
                          onPermissionModeChange={handlePermissionModeChange}
                          onSubmit={handleSubmit}
                          onDeepResearch={handleDeepResearch}
                          showInspirationToggle={!embeddedDesignChat}
                          allowAgentModes={!embeddedDesignChat}
                          onStop={handleStop}
                          isUploading={isUploading}
                          autoFocus={isNewThread}
                          defaultValue={composerSeed}
                          placeholder={
                            isEchoAssistant
                              ? t.realtime.composer.placeholderEcho
                              : isProjectCodeMode
                                ? t.realtime.composer.placeholderCode
                                : isNewThread
                                  ? t.realtime.composer.placeholderNew
                                  : undefined
                          }
                          className={
                            isNewThread
                              ? "border-border-subtle bg-card/90 shadow-none"
                              : undefined
                          }
                        />
                      </div>
                    </div>
                  ) : (
                    <div
                      aria-hidden="true"
                      className="workspace-panel h-32 w-full rounded-lg"
                    />
                  )}
                </div>
              }
              secondaryPanel={
                recorderPluginEnabled && showTeachRepeatPanel ? (
                  <div className="flex size-full min-h-0 flex-col overflow-hidden">
                    <div className="flex h-11 shrink-0 items-center justify-between border-b border-border-default px-3">
                      <span className="text-sm font-semibold">
                        {t.teachRepeat.title}
                      </span>
                      <button
                        type="button"
                        onClick={() => setShowTeachRepeatPanel(false)}
                        className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                        aria-label={t.common.close}
                      >
                        <XIcon className="size-3.5" />
                      </button>
                    </div>
                    <TeachRepeatPanel
                      threadId={threadId}
                      className="min-h-0 flex-1 overflow-auto"
                    />
                  </div>
                ) : isEchoAssistant && showAutomationPanel ? (
                  <AutomationSubscriptionPanel
                    className="size-full"
                    onClose={() => setShowAutomationPanel(false)}
                  />
                ) : showResearchHistory ? (
                  <DeepResearchHistoryPanel
                    activeJobId={researchJob?.job_id}
                    onSelect={(job) => {
                      setResearchJob(job);
                      setResearchError(null);
                      setShowResearch(true);
                      setShowResearchHistory(false);
                      setShowPreview(false);
                    }}
                    onClose={() => setShowResearchHistory(false)}
                  />
                ) : showResearch && researchJob ? (
                  <DeepResearchPanel
                    job={researchJob}
                    loading={researchLoading}
                    error={researchError}
                    onClose={() => setShowResearch(false)}
                  />
                ) : showResearch && researchError ? (
                  <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                    <div className="flex items-center justify-between border-b border-border-default px-3 py-2">
                      <span className="text-sm font-medium">Agent</span>
                      <button
                        type="button"
                        onClick={() => setShowResearch(false)}
                        className="rounded-lg p-1 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
                        aria-label={t.common.close}
                      >
                        <XIcon className="size-3.5" />
                      </button>
                    </div>
                    <div className="p-3 text-xs text-destructive">
                      {researchError}
                    </div>
                  </div>
                ) : showAgentPlan ? (
                  <PlanPanel
                    className="size-full rounded-none border-0 shadow-none"
                    messages={thread.messages}
                    open
                    onClose={() => setShowAgentPlan(false)}
                  />
                ) : showAgentWorkbench ? (
                  <AgentWorkbenchPanel
                    activeTab={agentWorkbenchTab}
                    personaId={effectiveAgentId}
                    events={workbenchDisplayEvents}
                    progressOutline={progressOutline}
                    userInput={
                      focusedWorkbenchTurnIndex === null
                        ? lastTurnUserInput
                        : {
                            text: focusedWorkbenchAgentSnapshot?.task ?? "",
                            uploadedFiles: [],
                            attachments: [],
                          }
                    }
                    groundingSources={thread.values.latest_grounding ?? []}
                    focusedAgentId={focusedWorkbenchAgentId}
                    focusedAgentView={focusedWorkbenchAgentView}
                    focusedAgentSnapshot={focusedWorkbenchAgentSnapshot}
                    focusedAgentNonce={focusedWorkbenchAgentNonce}
                    focusedEventId={focusedWorkbenchEventId}
                    focusedEventKind={focusedWorkbenchEventKind}
                    focusedEventView={focusedWorkbenchEventView}
                    focusedEventNonce={focusedWorkbenchEventNonce}
                    focusedProcessEvent={focusedWorkbenchProcessEvent}
                    focusedEffectKey={focusedWorkbenchEffectKey}
                    hasAnswer={
                      focusedWorkbenchTurnIndex === null
                        ? hasCompletedAgentOutput
                        : true
                    }
                    isLoading={
                      focusedWorkbenchTurnIndex === null
                        ? thread.isLoading
                        : false
                    }
                    runSettled={
                      focusedWorkbenchTurnIndex === null
                        ? agentRunSettled
                        : true
                    }
                    runFailed={
                      focusedWorkbenchTurnIndex === null
                        ? agentRunFailed
                        : focusedWorkbenchAgentSnapshot?.status === "error"
                    }
                    runInterrupted={agentRunInterrupted}
                    runBlocked={agentRunBlocked}
                    paused={hasPausedOrPendingBackgroundTask}
                    threadId={threadId}
                    workDir={workDir}
                    browserPreviewBlocks={previewBlocks}
                    resultPreviewUrl={resultPreviewUrl}
                    mainAgentName={
                      displayAgent?.display_name || effectiveAgentId
                    }
                    contextTokens={contextTokens}
                    maxContextTokens={maxContextTokens}
                    isCompressingContext={isCompressingContext}
                    onCompressContext={handleCompressContext}
                    rosterSeats={collaborationRosterSeats}
                    showMachineRosterRail={false}
                    groupTitle={
                      isGroupConversation ? collaborationTeamName : null
                    }
                    currentThreadTitle={headerThreadTitle || null}
                    onInvitePeople={
                      canManageHumanInvites ? handleOpenHumanInvite : undefined
                    }
                    onClose={closeAgentWorkbenchPanel}
                    onSelectTab={selectAgentWorkbenchTab}
                    onOpenArtifact={openWorkbenchArtifact}
                  />
                ) : undefined
              }
              onSecondaryClose={closeUnifiedRightPanel}
              secondaryPanelWidth="min(500px, 38vw)"
            />
          </ChatBox>
          <ChatsDrawer
            open={chatsDrawerOpen}
            onOpenChange={setChatsDrawerOpen}
          />
          {recorderPluginEnabled ? (
            <RecRecorderOverlay
              open={recOverlayOpen}
              threadId={threadId}
              defaultName={
                thread?.values?.title ||
                initialPrompt ||
                t.realtime.recorder.defaultName
              }
              initiallyRecording={recIsRecording}
              onClose={() => setRecOverlayOpen(false)}
              onRecordingChange={setRecIsRecording}
              onOpenLibrary={() => {
                setRecOverlayOpen(false);
                openTeachRepeatPanel();
              }}
            />
          ) : null}
        </ToolEffectsProvider>

        {/* 流式调试面板 */}
        <StreamingDebugger events={allToolEvents} />

        {/* 上下文压缩进度指示器 */}
        <ContextCompressionIndicator
          isCompressing={isCompressingContext}
          contextTokens={contextTokens}
          maxContextTokens={maxContextTokens}
        />
      </ThreadProviders>
    </SubtasksProvider>
  );
}
