import { useMemo, useRef, useEffect, useState } from "react";
import { ArrowDownIcon } from "lucide-react";

import type { AIMessage, Message, ToolMessage } from "@/core/api/types";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import {
  STREAMING_TYPE_PRESETS,
  useStreamingTextBuffer,
} from "@/hooks/use-streaming-text-buffer";

import type { AgentTile } from "../agent-workbench-utils";
import { repairMojibakeText } from "../agent-workbench-utils";
import type { LiveToolEvent } from "../live-tool-timeline";
import type { WorkBlock } from "../work-blocks";
import { MessageGroup } from "../messages/message-group";
import { MessageListItem } from "../messages/message-list-item";
import { ComputerScopeSwitch } from "./computer-scope-switch";

function publicBlockOutput(block: WorkBlock): string {
  const observation = block.event.observation?.trim();
  if (observation) return repairMojibakeText(readableResultText(observation));
  if (block.event.error?.trim()) return repairMojibakeText(block.event.error);
  // Prefer a readable text channel inside an object payload over a raw
  // JSON.stringify dump — same contract as subagentResultText. Fall back to
  // the stringified snapshot only when the payload carries no text field.
  const rawOutput = block.event.output;
  if (rawOutput && typeof rawOutput === "object" && !Array.isArray(rawOutput)) {
    const readable = readableResultText(rawOutput);
    if (readable) return repairMojibakeText(readable);
  }
  const output = block.outputText.trim();
  if (output) return repairMojibakeText(readableResultText(output));
  return "";
}

const RESULT_TEXT_KEYS = [
  "output",
  "summary",
  "result",
  "reason",
  "message",
  "text",
  "content",
  "output_preview",
  "stdout",
  "content_text",
];

/** Turn a structured result — including legacy JSON-in-a-string envelopes —
 * into the sentence a person is meant to read. Metadata-only envelopes stay
 * blank instead of leaking implementation details into the conversation. */
function readableResultText(value: unknown, depth = 0): string {
  if (depth > 2 || value === null || value === undefined) return "";
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return "";
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      try {
        const parsed = JSON.parse(trimmed) as unknown;
        const readable = readableResultText(parsed, depth + 1);
        if (readable) return readable;
      } catch {
        // It only looked like JSON; preserve the original public text.
      }
    }
    return trimmed;
  }
  if (typeof value !== "object" || Array.isArray(value)) return "";
  const record = value as Record<string, unknown>;
  for (const key of RESULT_TEXT_KEYS) {
    const readable = readableResultText(record[key], depth + 1);
    if (readable) return readable;
  }
  return "";
}

/** Readable final-answer text for a finished sub-agent event.
 *
 * Prefer explicit text channels (``observation``, ``result.output`` /
 * ``summary`` / ``output_preview`` / ``result``). Never JSON-stringify the
 * whole result envelope as a stand-in answer — that used to render the
 * lifecycle metadata ({codename, role, agent_id, duration_s, ...}) as if it
 * were the agent's verdict. */
function subagentResultText(event: LiveToolEvent): string {
  const observation = event.observation?.trim();
  if (observation) return readableResultText(observation);
  for (const bag of [event.input, event.output]) {
    const text = readableResultText(bag);
    if (text) return text;
  }
  return event.error?.trim() ?? "";
}

/**
 * Project one selected sub-agent's event stream onto the exact message model
 * used by the main conversation. MessageGroup then owns thinking/execution
 * streaming, aggregation and disclosure state; this view owns only isolation.
 */
function subagentMessages(
  agent: AgentTile,
  blocks: WorkBlock[],
): {
  task: Message | null;
  process: Message[];
  answer: Message | null;
} {
  const taskText = repairMojibakeText(
    agent.prompt ?? agent.task ?? agent.lastThought ?? "",
  );
  const task: Message | null = taskText
    ? {
        id: `subagent-${agent.id}-task`,
        type: "human",
        content: taskText,
      }
    : null;
  const process: Message[] = [];
  // Once the agent settles, a trailing block that never received a done event
  // must not keep rendering as "running": fold its output into a real result
  // row instead of leaving a half-open step.
  const settled = agent.status !== "running";
  let answerText = "";
  let answerId = `subagent-${agent.id}-answer`;

  for (const block of blocks) {
    const event = block.event;
    if (event.lifecycle === "spawned") continue;
    if (event.lifecycle === "finished") {
      const text = repairMojibakeText(
        subagentResultText(event) || agent.resultSummary || agent.error || "",
      );
      if (text) {
        answerText = text;
        answerId = `${block.id}-answer`;
      }
      continue;
    }

    const callId = event.id || block.id;
    const output = publicBlockOutput(block);
    const thought = repairMojibakeText(
      event.thought?.trim() || event.observation?.trim() || "",
    );
    const ai: AIMessage = {
      id: `${block.id}-assistant`,
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: callId,
          name: event.name,
          args: {
            ...(event.input ?? {}),
            // Match the main conversation's realtime adapter contract: live
            // execution output belongs to the active tool call until it ends.
            ...(event.status === "running" && !settled && output
              ? { output }
              : {}),
          },
          parentItemId: event.parentToolUseId ?? null,
        },
      ],
      additional_kwargs: {
        ...(thought ? { reasoning_content: thought } : {}),
        agent_id: agent.id,
        agent_display_name: agent.codename ?? agent.name,
      },
    };
    process.push(ai);

    if (
      event.status === "error" ||
      (output && (event.status !== "running" || settled))
    ) {
      const tool: ToolMessage = {
        id: `${block.id}-result`,
        type: "tool",
        tool_call_id: callId,
        content: output || event.error || "",
        status: event.status === "error" ? "error" : "success",
      };
      process.push(tool);
    }
  }

  if (!answerText) {
    answerText = repairMojibakeText(
      readableResultText(agent.resultSummary ?? agent.error ?? ""),
    );
  }
  const answer: Message | null = answerText
    ? {
        id: answerId,
        type: "ai",
        content: answerText,
        additional_kwargs: {
          agent_id: agent.id,
          agent_display_name: agent.codename ?? agent.name,
          ...(agent.status === "error" ? { response_state: "failed" } : {}),
        },
      }
    : null;

  return { task, process, answer };
}

export function SubagentProcessView({
  agent,
  blocks,
  onOpenMain,
}: {
  agent: AgentTile;
  blocks: WorkBlock[];
  currentBlockId: string | null;
  onOpenMain: () => void;
  onSelectBlock: (blockId: string) => void;
}) {
  const { t } = useI18n();
  const messages = useMemo(
    () => subagentMessages(agent, blocks),
    [agent, blocks],
  );

  // 智能滚动锚点：自动滚动到底部，除非用户主动向上滚动
  const containerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [showScrollFab, setShowScrollFab] = useState(false);

  // 检测用户是否主动滚动离开底部
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container;
      const isNearBottom = scrollHeight - scrollTop - clientHeight < 100;
      setAutoScroll(isNearBottom);
      setShowScrollFab(!isNearBottom && agent.status === "running");
    };

    container.addEventListener("scroll", handleScroll, { passive: true });
    return () => container.removeEventListener("scroll", handleScroll);
  }, [agent.status]);

  // 当有新消息且处于自动滚动模式时，滚动到底部
  useEffect(() => {
    if (autoScroll && agent.status === "running") {
      messagesEndRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "end",
      });
    }
  }, [messages, autoScroll, agent.status]);

  const handleScrollToBottom = () => {
    setAutoScroll(true);
    messagesEndRef.current?.scrollIntoView({
      behavior: "smooth",
      block: "end",
    });
  };
  const isRunning = agent.status === "running";
  const answerText =
    typeof messages.answer?.content === "string" ? messages.answer.content : "";
  // Reveal the final answer with the same typewriter buffer the main
  // conversation uses. The sub-agent's verdict only materialises at the
  // terminal finished marker (it isn't streamed token-by-token), so once the
  // run settles we drain the burst smoothly instead of a hard flash-in.
  // ``resetKey`` stays pinned to the agent so a live settle animates while a
  // replay/history switch shows the full text instantly.
  const answerDisplay = useStreamingTextBuffer({
    targetText: answerText,
    enabled: isRunning,
    resetKey: agent.id,
    ...STREAMING_TYPE_PRESETS.burstDrain,
  });
  const hasConversation = Boolean(
    messages.task || messages.process.length > 0 || messages.answer,
  );

  return (
    <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden bg-background">
      <div
        ref={containerRef}
        className="flex min-h-0 flex-1 flex-col overflow-y-auto"
      >
        <div className="mx-auto flex w-full max-w-2xl flex-col">
          <ComputerScopeSwitch
            subLabel={`${agent.codename ?? agent.name} · ${t.agentWorkbench.kindAgent} ${agent.label}`}
            onOpenMain={onOpenMain}
          />
          {!hasConversation ? (
            <div className="flex min-h-48 items-center justify-center px-5 text-sm text-muted-foreground">
              {t.agentWorkbenchPanel.waitingForSubagentOutput}
            </div>
          ) : (
            <div
              className="space-y-3 px-5 py-4"
              data-testid="subagent-main-conversation"
            >
              {messages.task ? (
                <MessageListItem
                  message={messages.task}
                  isLastMessage={false}
                />
              ) : null}
              {messages.process.length > 0 ? (
                <MessageGroup
                  messages={messages.process}
                  isLoading={isRunning}
                  keepOpen={isRunning}
                  codeMode
                />
              ) : null}
              {messages.answer ? (
                <MessageListItem
                  message={{ ...messages.answer, content: answerDisplay }}
                  isLoading={isRunning}
                  isLastMessage
                />
              ) : null}
              <div ref={messagesEndRef} className="h-4" />
            </div>
          )}
        </div>
      </div>

      {/* 滚动到底部的悬浮按钮 */}
      {showScrollFab && (
        <button
          type="button"
          onClick={handleScrollToBottom}
          className={cn(
            "absolute bottom-6 right-6 z-10 flex items-center gap-2 rounded-full border border-border-default bg-background px-4 py-2.5 text-sm font-medium shadow-lg transition-all hover:scale-105 hover:shadow-xl",
            "animate-in fade-in slide-in-from-bottom-4 duration-300",
          )}
          aria-label={t.agentWorkbenchPanel?.scrollToBottom ?? "滚动到底部"}
        >
          <ArrowDownIcon className="size-4" />
          <span>
            {t.agentWorkbenchPanel?.viewLatestProgress ?? "查看最新进展"}
          </span>
        </button>
      )}
    </div>
  );
}
