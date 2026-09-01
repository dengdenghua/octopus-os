/**
 * Echo message and thread types.
 */

export type MessageContent = string | MessageContentComplex[];
export type MessageContentComplex =
  | { type: "text"; text: string }
  | { type: "image_url"; image_url: string | { url: string; detail?: string } }
  // Extended-thinking blocks. `thinking` is the canonical field; some
  // providers emit `text` instead.
  | { type: "thinking"; thinking?: string; text?: string };

export interface BaseMessage {
  content: MessageContent;
  id?: string;
  name?: string;
  additional_kwargs?: Record<string, unknown>;
  response_metadata?: Record<string, unknown>;
}

export interface HumanMessage extends BaseMessage {
  type: "human";
}

export interface AIMessage extends BaseMessage {
  type: "ai";
  tool_calls?: ToolCall[];
  usage_metadata?: UsageMetadata;
}

export interface ToolMessage extends BaseMessage {
  type: "tool";
  tool_call_id: string;
  status?: "error" | "success";
}

export interface SystemMessage extends BaseMessage {
  type: "system";
}

export type Message =
  | HumanMessage
  | AIMessage
  | ToolMessage
  | SystemMessage
  | (BaseMessage & {
      type: string;
      tool_calls?: ToolCall[];
      tool_call_id?: string;
    });

export interface ToolCall {
  name: string;
  args: Record<string, unknown>;
  id?: string;
  type?: "tool_call";
  timelineSequence?: number | null;
  parentItemId?: string | null;
  phaseId?: string | null;
  effectReceipt?: {
    effectKey: string;
    callId: string;
    state: "indeterminate";
    reason: string;
    fencingToken: number;
  } | null;
}

export interface UsageMetadata {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface Thread<T = Record<string, unknown>> {
  thread_id: string;
  status: string;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
  values: T;
}

export interface ThreadState<T = Record<string, unknown>> {
  values: T;
  next: string[];
  metadata: Record<string, unknown>;
  checkpoint: { id?: string; checkpoint_id?: string; ts?: string };
  checkpoint_id?: string;
  tasks: unknown[];
}

export function isAIMessage(msg: Message): msg is AIMessage {
  return msg.type === "ai";
}

export function hasToolCalls(
  msg: Message,
): msg is AIMessage & { tool_calls: ToolCall[] } {
  return (
    isAIMessage(msg) &&
    Array.isArray(msg.tool_calls) &&
    msg.tool_calls.length > 0
  );
}

export function isHumanMessage(msg: Message): msg is HumanMessage {
  return msg.type === "human";
}

export function isToolMessage(msg: Message): msg is ToolMessage {
  return msg.type === "tool";
}
