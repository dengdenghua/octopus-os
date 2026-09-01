import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { getAPIClient } from "../api";
import type { LocalSettings } from "../settings";

import type { LiveToolEvent } from "@/components/workspace/live-tool-timeline";

import type { AgentThread, AgentThreadState } from "./types";
import { threadVisibleInPersonaHistory } from "./persona-history";
import { useThreadStreamRealtime } from "./use-thread-stream-realtime";
import { isPrimaryPersonaAgentId } from "@/core/agents/persona-policy";

export type ToolEndEvent = {
  name: string;
  data: unknown;
};

export type ThreadStreamOptions = {
  threadId?: string | null | undefined;
  context: Omit<LocalSettings["context"], "mode"> & {
    mode: LocalSettings["context"]["mode"] | "code" | "team";
  };
  isMock?: boolean;
  onStart?: (threadId: string) => void;
  onFinish?: (state: AgentThreadState) => void;
  onToolEnd?: (event: ToolEndEvent) => void;
};

export function upsertLiveToolEvent(
  events: LiveToolEvent[],
  nextEvent: LiveToolEvent,
): LiveToolEvent[] {
  const existingIndex = events.findIndex((event) => event.id === nextEvent.id);
  if (existingIndex === -1) {
    return [...events, nextEvent];
  }

  const updated = [...events];
  const existing = updated[existingIndex]!;
  const merged: LiveToolEvent = { ...existing };
  for (const [key, value] of Object.entries(nextEvent) as Array<
    [keyof LiveToolEvent, LiveToolEvent[keyof LiveToolEvent]]
  >) {
    if (value !== undefined) {
      (merged as unknown as Record<string, unknown>)[key] = value;
    }
  }
  updated[existingIndex] = {
    ...merged,
  };
  return updated;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function numberValue(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (
    typeof value === "string" &&
    value.trim() &&
    !Number.isNaN(Number(value))
  ) {
    return Number(value);
  }
  return undefined;
}

function recordValue(value: unknown): Record<string, unknown> | undefined {
  if (isRecord(value)) return value;
  if (Array.isArray(value)) return { items: value };
  if (typeof value === "string" && value.trim()) return { input: value };
  return undefined;
}

function parseCapabilityDisabled(
  value: unknown,
): { group: string; config_flag: string } | undefined {
  if (!isRecord(value)) return undefined;
  const group = stringValue(value.group);
  const configFlag =
    stringValue(value.config_flag) ?? stringValue(value.configFlag);
  if (!group || !configFlag) return undefined;
  return { group, config_flag: configFlag };
}

function terminalToolStatus(value: unknown): LiveToolEvent["status"] {
  if (value === true) return "error";
  const normalized = typeof value === "string" ? value.toLowerCase() : "";
  if (
    normalized === "error" ||
    normalized === "failed" ||
    normalized === "failure" ||
    normalized === "rejected" ||
    normalized === "cancelled" ||
    normalized === "canceled" ||
    normalized === "timeout" ||
    normalized === "timed_out"
  ) {
    return "error";
  }
  return "done";
}

export function normalizeCustomToolEvent(
  event: Record<string, unknown>,
): LiveToolEvent | null {
  const type = stringValue(event.type);
  if (
    type !== "tool_start" &&
    type !== "tool_end" &&
    type !== "sub_tool_start" &&
    type !== "sub_tool_end"
  ) {
    return null;
  }

  const name = stringValue(event.tool_name) ?? stringValue(event.name);
  const rawId =
    stringValue(event.tool_call_id) ??
    stringValue(event.toolUseId) ??
    stringValue(event.id);
  if (!name || !rawId) return null;

  const isStart = type === "tool_start" || type === "sub_tool_start";
  const startedAt = Date.now();
  const finishedAt = isStart ? undefined : Date.now();
  const input = recordValue(
    event.input_preview ?? event.input ?? event.args ?? event.arguments,
  );
  const output =
    event.output_preview ?? event.output ?? event.result ?? event.observation;
  const subAgentRole =
    stringValue(event.sub_agent_role) ?? stringValue(event.subAgentRole);
  const subagentCodename =
    stringValue(event.subagent_codename) ??
    stringValue(event.subagentCodename) ??
    stringValue(event.codename);
  const rawAgentId = stringValue(event.agent_id) ?? stringValue(event.agentId);
  const agentId =
    stringValue(event.requested_agent_id) ??
    stringValue(event.requestedAgentId) ??
    (rawAgentId && rawAgentId !== subAgentRole
      ? rawAgentId
      : (subagentCodename ?? rawAgentId));

  return {
    id: rawId,
    name,
    status: isStart
      ? "running"
      : terminalToolStatus(event.status ?? event.is_error),
    startedAt,
    finishedAt,
    durationMs: isStart
      ? undefined
      : numberValue(event.duration_ms ?? event.durationMs),
    iteration: numberValue(event.iteration) ?? 0,
    agentId,
    agentName:
      stringValue(event.agent_name) ??
      stringValue(event.agentName) ??
      stringValue(event.sub_agent_role) ??
      stringValue(event.subAgentRole),
    input,
    output,
    parentToolUseId:
      stringValue(event.parent_tool_use_id) ??
      stringValue(event.parentToolUseId),
    subAgentRole,
    subagentCodename,
    subagentAvatar:
      stringValue(event.subagent_avatar) ??
      stringValue(event.subagentAvatar) ??
      stringValue(event.avatar),
    thought: stringValue(event.thought),
    observation: stringValue(event.observation),
    capabilityDisabled: parseCapabilityDisabled(event.capability_disabled),
  };
}

export function finalizeLiveToolEvents(
  events: LiveToolEvent[],
  nextStatus: Extract<LiveToolEvent["status"], "done" | "error">,
  output: unknown,
): LiveToolEvent[] {
  const finishedAt = Date.now();
  return events.map((event) => {
    if (event.status !== "running" && event.status !== "waiting_approval") {
      return event;
    }
    return {
      ...event,
      status: nextStatus,
      finishedAt,
      durationMs: Math.max(0, finishedAt - event.startedAt),
      output: event.output ?? output,
    };
  });
}

function buildRunLifecycleEvents(
  runStartedAt: number,
  status: Extract<LiveToolEvent["status"], "done" | "error">,
  output: unknown,
): LiveToolEvent[] {
  const finishedAt = Date.now();
  const connectionStartedAt = Math.min(runStartedAt + 300, finishedAt);
  const gatewayStartedAt = Math.min(runStartedAt + 900, finishedAt);
  return [
    {
      id: "live-log:request",
      name: "turn_request",
      status: "done",
      startedAt: runStartedAt,
      finishedAt: connectionStartedAt,
      durationMs: Math.max(0, connectionStartedAt - runStartedAt),
      iteration: 0,
    },
    {
      id: "live-log:stream",
      name: "stream_connection",
      status: "done",
      startedAt: connectionStartedAt,
      finishedAt: gatewayStartedAt,
      durationMs: Math.max(0, gatewayStartedAt - connectionStartedAt),
      iteration: 0,
      input: { transport: "WebSocket" },
    },
    {
      id: "live-log:model-gateway",
      name: "model_gateway",
      status,
      startedAt: gatewayStartedAt,
      finishedAt,
      durationMs: Math.max(0, finishedAt - gatewayStartedAt),
      iteration: 0,
      output,
    },
  ];
}

export function finalizeTurnHistory(
  events: LiveToolEvent[],
  runStartedAt: number,
  nextStatus: Extract<LiveToolEvent["status"], "done" | "error">,
  output: unknown,
): LiveToolEvent[] {
  const lifecycleEvents = buildRunLifecycleEvents(
    runStartedAt,
    nextStatus,
    output,
  );
  // Finalize the tool event lifecycle, but never rewrite checklist item
  // statuses. The checklist is model/runtime-authored task state; receiving a
  // final answer is not evidence that its active or pending rows completed.
  const finalizedEvents = finalizeLiveToolEvents(events, nextStatus, output);
  const lifecycleIds = new Set(lifecycleEvents.map((event) => event.id));
  const lifecycleNames = new Set(lifecycleEvents.map((event) => event.name));
  return [
    ...lifecycleEvents,
    ...finalizedEvents.filter(
      (event) => !lifecycleIds.has(event.id) && !lifecycleNames.has(event.name),
    ),
  ];
}

export function useThreadStream({
  threadId,
  context,
  isMock,
  onStart,
  onFinish,
  onToolEnd,
}: ThreadStreamOptions) {
  // Every caller now goes through the realtime WebSocket. This hook name
  // stays stable because workspace pages share the tuple contract.
  void isMock;

  return useThreadStreamRealtime({
    threadId: typeof threadId === "string" ? threadId : "",
    model:
      typeof (context as { model_name?: unknown })?.model_name === "string"
        ? ((context as { model_name?: string }).model_name as string)
        : undefined,
    onStart,
    onFinish,
    onToolEnd,
    context,
  });
}

export function useThreads(
  params: Record<string, unknown> = {
    limit: 50,
    sortBy: "updated_at",
    sortOrder: "desc",
    select: ["thread_id", "updated_at", "values"],
  },
  mode?: "chat" | "code" | "team",
  agent?: string | null,
) {
  const personaHistoryAgent =
    agent && isPrimaryPersonaAgentId(agent) ? agent : null;
  // Compose metadata filter from the optional mode + agent arguments.
  // The backend's ThreadStateStore.search() ANDs together every metadata
  // key-value pair we send, so `{mode:"chat", agent:"coder"}` yields
  // exactly the threads that this Coder agent handled in chat mode.
  //
  // Skipping the `agent` clause entirely (null/undefined) shows all
  // threads; used by search dialogs and admin views that want the
  // full cross-agent history.
  if (mode || (agent && !personaHistoryAgent)) {
    const metadata = {
      ...((params.metadata as Record<string, unknown>) || {}),
    } as Record<string, unknown>;
    if (mode) metadata.mode = mode;
    if (agent && !personaHistoryAgent) metadata.agent = agent;
    params = { ...params, metadata };
  }
  const apiClient = getAPIClient();
  return useQuery<AgentThread[]>({
    queryKey: ["threads", "search", params, personaHistoryAgent],
    queryFn: async () => {
      const maxResults = params.limit as number | undefined;
      const initialOffset = (params.offset ?? 0) as number;
      const DEFAULT_PAGE_SIZE = 50;

      // Preserve prior semantics: if a non-positive limit is explicitly provided,
      // delegate to a single search call with the original parameters.
      if (maxResults !== undefined && maxResults <= 0) {
        const response = await apiClient.threads.search(params);
        const result = response as AgentThread[];
        return personaHistoryAgent
          ? result.filter((thread) =>
              threadVisibleInPersonaHistory(thread, personaHistoryAgent),
            )
          : result;
      }

      const pageSize =
        typeof maxResults === "number" && maxResults > 0
          ? Math.min(DEFAULT_PAGE_SIZE, maxResults)
          : DEFAULT_PAGE_SIZE;

      const threads: AgentThread[] = [];
      let offset = initialOffset;
      const MAX_PAGES = 100;
      let page = 0;

      while (page < MAX_PAGES) {
        page++;
        if (typeof maxResults === "number" && threads.length >= maxResults) {
          break;
        }

        const currentLimit =
          typeof maxResults === "number"
            ? Math.min(pageSize, maxResults - threads.length)
            : pageSize;

        if (typeof maxResults === "number" && currentLimit <= 0) {
          break;
        }

        const response = (await apiClient.threads.search({
          ...params,
          limit: currentLimit,
          offset,
        })) as AgentThread[];

        threads.push(
          ...(personaHistoryAgent
            ? response.filter((thread) =>
                threadVisibleInPersonaHistory(thread, personaHistoryAgent),
              )
            : response),
        );

        if (response.length < currentLimit) {
          break;
        }

        offset += response.length;
      }

      return threads;
    },
    refetchOnWindowFocus: false,
    staleTime: 5 * 60_000, // 5 min — thread list changes infrequently; avoids
    // re-fetching every time the drawer opens. Mutations (delete, rename)
    // invalidate via onSettled/onSuccess.
  });
}

export function useDeleteThread() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    onMutate: async ({ threadId }: { threadId: string }) => {
      await queryClient.cancelQueries({ queryKey: ["threads", "search"] });
      const previousThreads = queryClient.getQueriesData<AgentThread[]>({
        queryKey: ["threads", "search"],
      });
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread> | undefined) => {
          if (oldData == null) {
            return oldData;
          }
          return oldData.filter((t) => t.thread_id !== threadId);
        },
      );
      return { previousThreads };
    },
    mutationFn: async ({ threadId }: { threadId: string }) => {
      // Single DELETE: there used to be a second fetch right after this
      // that hit the SAME endpoint and 404'd because the first call had
      // already removed the thread. The 404 threw, the mutation failed,
      // ``onSuccess`` never ran, React Query's cache stayed stale, and
      // the UI looked like it couldn't delete the thread. The second
      // call was a leftover from when there was a separate "local
      // thread data" cleanup endpoint. That endpoint no longer exists,
      // so the call is pure double-delete bug.
      await apiClient.threads.delete(threadId);
    },
    onError(_error, _variables, context) {
      for (const [queryKey, data] of context?.previousThreads ?? []) {
        queryClient.setQueryData(queryKey, data);
      }
      toast.error("删除对话失败，已恢复列表");
    },
    onSettled() {
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
    },
  });
}

export function useRenameThread() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      title,
    }: {
      threadId: string;
      title: string;
    }) => {
      // dsh session-title semantics: a user rename pins the title against
      // automatic regeneration (source=user + title_pinned on the record).
      await apiClient.threads.renameTitle(threadId, title);
    },
    onSuccess(_, { threadId, title }) {
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread> | undefined) => {
          if (!oldData) return oldData;
          return oldData.map((t) => {
            if (t.thread_id === threadId) {
              return {
                ...t,
                values: {
                  ...t.values,
                  title,
                },
              };
            }
            return t;
          });
        },
      );
    },
  });
}

export function useForkThread() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: ({
      threadId,
      atMessageIndex,
    }: {
      threadId: string;
      atMessageIndex?: number;
    }) => apiClient.threads.forkThread(threadId, atMessageIndex),
    onSuccess() {
      queryClient.invalidateQueries({
        queryKey: ["threads", "search"],
        exact: false,
      });
    },
  });
}
