import type { AIMessage } from "@/core/api/types";
import type { BaseStream } from "@/core/api/use-stream-types";
import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

import type { AgentThreadState } from "../threads";

import { urlOfArtifact } from "./utils";

export class ArtifactLoadError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ArtifactLoadError";
    this.status = status;
  }
}

export async function loadArtifactContent({
  filepath,
  threadId,
  isMock,
}: {
  filepath: string;
  threadId: string;
  isMock?: boolean;
}) {
  let enhancedFilepath = filepath;
  if (filepath.endsWith(".skill")) {
    enhancedFilepath = filepath + "/SKILL.md";
  }
  const url = urlOfArtifact({ filepath: enhancedFilepath, threadId, isMock });
  const response = await fetch(url, { headers: authHeaders() });
  const text = await response.text();
  if (!response.ok) {
    throw new ArtifactLoadError(
      response.status,
      `artifact request failed (${response.status})`,
    );
  }
  return { content: text, url };
}

export function loadArtifactContentFromToolCall({
  url: urlString,
  thread,
}: {
  url: string;
  thread: BaseStream<AgentThreadState>;
}) {
  const url = new URL(urlString);
  const toolCallId = url.searchParams.get("tool_call_id");
  const messageId = url.searchParams.get("message_id");
  if (messageId && toolCallId) {
    const message = thread.messages.find((message) => message.id === messageId);
    if (message?.type === "ai" && (message as AIMessage).tool_calls) {
      const toolCall = (message as AIMessage).tool_calls!.find(
        (toolCall) => toolCall.id === toolCallId,
      );
      if (toolCall) {
        return toolCall.args.content as string | undefined;
      }
    }
  }
}

export function loadToolCallInfo({
  url: urlString,
  thread,
}: {
  url: string;
  thread: BaseStream<AgentThreadState>;
}) {
  const url = new URL(urlString);
  const toolCallId = url.searchParams.get("tool_call_id");
  const messageId = url.searchParams.get("message_id");
  if (messageId && toolCallId) {
    const message = thread.messages.find((message) => message.id === messageId);
    if (message?.type === "ai" && (message as AIMessage).tool_calls) {
      const toolCall = (message as AIMessage).tool_calls!.find(
        (toolCall) => toolCall.id === toolCallId,
      );
      if (toolCall) {
        return {
          name: toolCall.name,
          path: toolCall.args.path as string | undefined,
          content: toolCall.args.content as string | undefined,
          oldStr: toolCall.args.old_str as string | undefined,
          newStr: toolCall.args.new_str as string | undefined,
        };
      }
    }
  }
  return null;
}

export async function loadOriginalFileContent(path: string, threadId?: string) {
  const baseURL = getBackendBaseURL();
  const params = new URLSearchParams({
    path,
    max_lines: "5000",
  });
  if (threadId) {
    params.set("thread_id", threadId);
  }
  const response = await fetch(`${baseURL}/api/fs/read?${params.toString()}`);
  if (!response.ok) return null;
  const data = await response.json();
  if (data.binary) return null;
  return data.content as string;
}
