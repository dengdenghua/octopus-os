import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { Message } from "@/core/api/types";

import { ThreadStreamingContext, ThreadValuesContext } from "./context";
import { MessageListItem } from "./message-list-item";

const renderTracker = vi.hoisted(() => ({
  markdown: vi.fn(),
}));

vi.mock("./markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => {
    renderTracker.markdown(content);
    return null;
  },
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: {
        fileSizeB: "B",
        fileSizeKB: "KB",
        fileSizeMB: "MB",
      },
      conversation: {
        interruptedMessage: "interrupted",
        pausedMessage: "paused",
        cancelledMessage: "cancelled",
      },
      message: {
        attachmentFallback: "attachment",
      },
    },
  }),
}));

describe("MessageListItem streaming isolation", () => {
  it("does not re-render a historical message when streaming or values contexts change", () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const message = {
      id: "history-1",
      type: "ai",
      content: "settled history",
      additional_kwargs: {},
    } as Message;

    const tree = ({
      streamingMessage,
      values,
    }: {
      streamingMessage: Message | null;
      values: Record<string, unknown>;
    }) => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <ThreadStreamingContext.Provider
            value={{
              streamingMessage: streamingMessage as never,
              subgraphStreams: {} as never,
            }}
          >
            <ThreadValuesContext.Provider value={{ values: values as never }}>
              <MessageListItem
                message={message}
                isLoading={false}
                isLastMessage={false}
              />
            </ThreadValuesContext.Provider>
          </ThreadStreamingContext.Provider>
        </MemoryRouter>
      </QueryClientProvider>
    );

    const view = render(tree({ streamingMessage: null, values: {} }));
    const initialRenderCount = renderTracker.markdown.mock.calls.length;
    expect(initialRenderCount).toBeGreaterThan(0);

    view.rerender(
      tree({
        streamingMessage: {
          id: "active-stream",
          type: "ai",
          content: "next delta",
          additional_kwargs: {},
        } as Message,
        values: {
          execution_plan: {
            plan_id: "unrelated-plan",
            title: "Unrelated",
            steps: [],
          },
        },
      }),
    );

    expect(renderTracker.markdown).toHaveBeenCalledTimes(initialRenderCount);
  });
});
