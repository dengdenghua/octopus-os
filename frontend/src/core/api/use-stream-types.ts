/**
 * Thread-stream result types.
 *
 * The realtime WebSocket is the streaming transport; this shape remains the shared state contract for workspace pages and artifact helpers.
 *
 * Kept as types only (no runtime). See ``@/core/realtime`` for the transport.
 */

import type { Message } from "@/core/api/types";

export interface UseStreamResult<TState extends Record<string, unknown>> {
  messages: Message[];
  streamingMessage: Message | null;
  /**
   * Map of subgraph agent IDs to their currently streaming message.
   * When subgraph streaming is enabled, messages from child graphs are
   * routed here by their ``subgraph`` / ``agent_id`` / ``agent_name``
   * metadata so that ``streamingMessage`` stays reserved for the lead
   * agent. Key = agent identifier, value = accumulated message.
   */
  subgraphStreams: Record<string, Message>;
  values: TState;
  isLoading: boolean;
  isThreadLoading?: boolean;
  error: Error | undefined;
  stop: () => void;
  refresh: () => Promise<void>;
  submit: (
    input: Record<string, unknown>,
    options?: {
      threadId?: string;
      config?: Record<string, unknown>;
      context?: Record<string, unknown>;
      streamSubgraphs?: boolean;
      optimisticValues?: Partial<TState>;
      streamMode?: string[];
    },
  ) => void;
  threadId: string | null;
}

/** Historical alias. Prefer ``UseStreamResult`` directly in new code. */
export type BaseStream<
  TState extends Record<string, unknown> = Record<string, unknown>,
> = UseStreamResult<TState>;
