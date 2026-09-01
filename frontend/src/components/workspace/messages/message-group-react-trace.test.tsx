import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AIMessage, ToolMessage } from "@/core/api/types";
import { renderWithProviders } from "@/test/harness";

import { MessageGroup } from "./message-group";

vi.mock("../artifacts", () => ({
  useArtifacts: () => ({
    setOpen: vi.fn(),
    autoOpen: false,
    autoSelect: false,
    selectedArtifact: null,
    select: vi.fn(),
  }),
}));

describe("MessageGroup labelled ReAct trace privacy", () => {
  it("does not manufacture public actions from a private labelled trace", () => {
    const hiddenTail = "UNIQUE_OBSERVATION_TAIL_SHOULD_BE_COMPACTED";
    const message: AIMessage = {
      id: "ai-1",
      type: "ai",
      content: "",
      additional_kwargs: {
        reasoning_content: [
          "Thought: I need fresh market evidence before choosing a track.",
          "",
          "Action:",
          '  web_search({"query":"silver economy market"})',
          '  fetch_url({"url":"https://example.com/report"})',
          "",
          "Observation: [1/2 web_search]",
          "(real tool execution succeeded) web_search",
          `{"results":[{"title":"Report","url":"https://example.com/report"}],"tail":"${"x ".repeat(80)}${hiddenTail}"}`,
          "",
          "Thought: Now I can compare the options.",
        ].join("\n"),
      },
    };

    renderWithProviders(<MessageGroup messages={[message]} isLoading />, {
      locale: "en-US",
    });

    expect(
      screen.queryByTestId("process-timeline-event-execution"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Search sources")).not.toBeInTheDocument();
    expect(screen.queryByText(/web_search/)).not.toBeInTheDocument();
    expect(screen.queryByText(/fetch_url/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/real tool execution succeeded/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(new RegExp(hiddenTail))).not.toBeInTheDocument();

    expect(
      screen.queryByText("Search sources: silver economy market"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Read webpage: https://example.com/report"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTitle(/Replay/)).not.toBeInTheDocument();
  });

  it("renders real fetch tools as human activity without leaking the raw tool name", () => {
    const toolCall: AIMessage = {
      id: "ai-fetch",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "fetch-1",
          name: "fetch_url",
          args: { url: "https://example.com/report" },
        },
      ],
    };
    const result: ToolMessage = {
      id: "tool-fetch",
      type: "tool",
      content: '{"success":true,"title":"Market report"}',
      tool_call_id: "fetch-1",
    };

    renderWithProviders(
      <MessageGroup messages={[toolCall, result]} isLoading={false} />,
      { locale: "zh-CN" },
    );

    expect(screen.getByText(/浏览网页/)).toBeInTheDocument();
    expect(screen.queryByText(/fetch_url/i)).not.toBeInTheDocument();
  });
});
