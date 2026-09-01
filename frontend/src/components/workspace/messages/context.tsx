import type { BaseStream } from "@/core/api/use-stream-types";
import { createContext, useContext, useMemo, type ReactNode } from "react";

import type { AgentThreadState } from "@/core/threads";

// Context for thread metadata (rarely changes)
export interface ThreadMetaContextType {
  threadId: string | null;
  isMock?: boolean;
}

export const ThreadMetaContext = createContext<
  ThreadMetaContextType | undefined
>(undefined);

// Context for thread messages (changes when new messages arrive, but NOT during streaming)
export interface ThreadMessagesContextType {
  messages: BaseStream<AgentThreadState>["messages"];
}

export const ThreadMessagesContext = createContext<
  ThreadMessagesContextType | undefined
>(undefined);

// Context for the streaming message (changes on every delta chunk — isolated to avoid
// re-rendering components that only need the committed message list)
export interface ThreadStreamingContextType {
  streamingMessage: BaseStream<AgentThreadState>["streamingMessage"];
  subgraphStreams: BaseStream<AgentThreadState>["subgraphStreams"];
}

export const ThreadStreamingContext = createContext<
  ThreadStreamingContextType | undefined
>(undefined);

// Context for thread state values (changes occasionally)
export interface ThreadValuesContextType {
  values: BaseStream<AgentThreadState>["values"];
}

export const ThreadValuesContext = createContext<
  ThreadValuesContextType | undefined
>(undefined);

// Context for thread status (changes during streaming)
export interface ThreadStatusContextType {
  isLoading: boolean;
  error: Error | undefined;
}

export const ThreadStatusContext = createContext<
  ThreadStatusContextType | undefined
>(undefined);

// Context for thread actions (stable references)
export interface ThreadActionsContextType {
  stop: () => void;
  submit: BaseStream<AgentThreadState>["submit"];
}

export const ThreadActionsContext = createContext<
  ThreadActionsContextType | undefined
>(undefined);

// =============================================================================
// Legacy combined context for backward compatibility (deprecated)
// =============================================================================

export interface ThreadContextType {
  thread: BaseStream<AgentThreadState>;
  isMock?: boolean;
}

export const ThreadContext = createContext<ThreadContextType | undefined>(
  undefined,
);

/**
 * @deprecated Use granular hooks instead: useThreadMeta, useThreadMessages, useThreadValues, useThreadStatus, useThreadActions
 */
export function useThread() {
  const context = useContext(ThreadContext);
  if (context === undefined) {
    throw new Error("useThread must be used within a ThreadContext");
  }
  return context;
}

// =============================================================================
// Granular hooks for better performance
// =============================================================================

export function useThreadMeta() {
  const context = useContext(ThreadMetaContext);
  if (context === undefined) {
    throw new Error("useThreadMeta must be used within a ThreadMetaContext");
  }
  return context;
}

export function useThreadMessages() {
  const context = useContext(ThreadMessagesContext);
  if (context === undefined) {
    throw new Error(
      "useThreadMessages must be used within a ThreadMessagesContext",
    );
  }
  return context;
}

export function useThreadStreaming() {
  const context = useContext(ThreadStreamingContext);
  if (context === undefined) {
    throw new Error(
      "useThreadStreaming must be used within a ThreadStreamingContext",
    );
  }
  return context;
}

export function useThreadValues() {
  const context = useContext(ThreadValuesContext);
  if (context === undefined) {
    throw new Error(
      "useThreadValues must be used within a ThreadValuesContext",
    );
  }
  return context;
}

export function useThreadStatus() {
  const context = useContext(ThreadStatusContext);
  if (context === undefined) {
    throw new Error(
      "useThreadStatus must be used within a ThreadStatusContext",
    );
  }
  return context;
}

export function useThreadActions() {
  const context = useContext(ThreadActionsContext);
  if (context === undefined) {
    throw new Error(
      "useThreadActions must be used within a ThreadActionsContext",
    );
  }
  return context;
}

// =============================================================================
// Provider component that splits the thread into granular contexts
// =============================================================================

interface ThreadProvidersProps {
  thread: BaseStream<AgentThreadState>;
  isMock?: boolean;
  children: ReactNode;
}

export function ThreadProviders({
  thread,
  isMock,
  children,
}: ThreadProvidersProps) {
  const metaValue = useMemo(
    () => ({ threadId: thread.threadId, isMock }),
    [thread.threadId, isMock],
  );

  const messagesValue = useMemo(
    () => ({ messages: thread.messages }),
    [thread.messages],
  );

  const streamingValue = useMemo(
    () => ({
      streamingMessage: thread.streamingMessage,
      subgraphStreams: thread.subgraphStreams,
    }),
    [thread.streamingMessage, thread.subgraphStreams],
  );

  const valuesValue = useMemo(
    () => ({ values: thread.values }),
    [thread.values],
  );

  const statusValue = useMemo(
    () => ({ isLoading: thread.isLoading, error: thread.error }),
    [thread.isLoading, thread.error],
  );

  const actionsValue = useMemo(
    () => ({ stop: thread.stop, submit: thread.submit }),
    [thread.stop, thread.submit],
  );

  const legacyValue = useMemo(() => ({ thread, isMock }), [thread, isMock]);

  return (
    <ThreadMetaContext.Provider value={metaValue}>
      <ThreadMessagesContext.Provider value={messagesValue}>
        <ThreadStreamingContext.Provider value={streamingValue}>
          <ThreadValuesContext.Provider value={valuesValue}>
            <ThreadStatusContext.Provider value={statusValue}>
              <ThreadActionsContext.Provider value={actionsValue}>
                <ThreadContext.Provider value={legacyValue}>
                  {children}
                </ThreadContext.Provider>
              </ThreadActionsContext.Provider>
            </ThreadStatusContext.Provider>
          </ThreadValuesContext.Provider>
        </ThreadStreamingContext.Provider>
      </ThreadMessagesContext.Provider>
    </ThreadMetaContext.Provider>
  );
}
