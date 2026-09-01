// Public surface for the realtime protocol client.
//
// Import from ``@/core/realtime`` in React components and stores. The
// lower-level modules (client, reducer, envelope) remain reachable for
// test harnesses and non-React consumers.

export type {
  Envelope,
  JsonRpcRequest,
  JsonRpcResponse,
  JsonRpcError,
  JsonRpcId,
  Notification,
} from "./envelope";
export {
  JsonRpcErrorCode,
  isNotification,
  isRequest,
  isResponse,
} from "./envelope";

export type {
  AgentPhaseSnapshot,
  AgentMessageItem,
  ApprovalItem,
  ArtifactItem,
  CommandExecutionItem,
  Conversation,
  ErrorItem,
  FileChange,
  FileChangeItem,
  FileHunk,
  Item,
  ItemBase,
  ItemStatus,
  ItemType,
  McpToolProgress,
  McpToolCallItem,
  PendingApproval,
  PlanItem,
  ReasoningItem,
  SubagentItem,
  ThreadSummary,
  TodoListItem,
  Turn,
  TurnStatus,
  UserMessageItem,
  VerificationItem,
  WorkspaceFocus,
  WorkspaceFocusView,
} from "./items";
export { emptyConversation } from "./items";

export type {
  ConversationEvent,
  ReducerDiagnostic,
  ReducerDiagnosticHandler,
  ReducerOutput,
} from "./reducer";
export { itemStreamText, reduce } from "./reducer";

export type { RealtimeClientOptions } from "./client";
export { RealtimeClient, createDefaultClient } from "./client";

export type {
  UseRealtimeThreadArgs,
  UseRealtimeThreadValue,
} from "./use-realtime-thread";
export { useRealtimeThread } from "./use-realtime-thread";

export type {
  StreamPhase,
  StreamVitals,
  VitalsMarks,
  VitalsThresholds,
} from "./stream-vitals";
export {
  applyVitalNotification,
  classifyVitals,
  DEFAULT_VITALS_THRESHOLDS,
  emptyVitals,
  emptyVitalsMarks,
  FIRST_RESPONSE_DELAY_NOTICE_MS,
  formatStreamElapsed,
  seedVitalsFromResumedTurn,
} from "./stream-vitals";
export { useStreamVitals } from "./use-stream-vitals";
export type {
  StreamTelemetrySummary,
  StreamTurnOutcome,
  StreamTurnTelemetry,
} from "./stream-telemetry";
export {
  appendStreamTelemetry,
  clearStreamTelemetry,
  createStreamTurnTelemetry,
  readStreamTelemetry,
  STREAM_TELEMETRY_UPDATED_EVENT,
  summarizeStreamTelemetry,
} from "./stream-telemetry";
