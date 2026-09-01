import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import {
  FollowUpSuggestions,
  resetFollowUpGenerationGuard,
} from "./follow-up-suggestions";

const fetchMock = vi.fn();

const SAMPLE_BUCKET = {
  project_root: "/p",
  generated_at: "2026-05-08T10:00:00Z",
  enabled: true,
  suggestions: [
    {
      id: "abc",
      project_root: "/p",
      title: "Fix CI",
      description: "Last 3 runs failed",
      prompt: "investigate CI failures",
      locale: "en-US",
      status: "pending",
      source_turn_ids: ["t1"],
      created_at: "",
      updated_at: "",
      model: "mock",
      experimental: true,
    },
  ],
};

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  resetFollowUpGenerationGuard();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function mockOnce(
  body: unknown,
  init: Partial<{ ok: boolean; status: number }> = {},
) {
  fetchMock.mockResolvedValueOnce({
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
}

describe("FollowUpSuggestions", () => {
  it("auto-generates on mount while idle (mount IS the turn-finished signal)", async () => {
    mockOnce({ added: 0, generated: 0, error: null }); // /run
    mockOnce({
      project_root: "/p",
      generated_at: "",
      enabled: true,
      suggestions: [],
    }); // refresh after run
    renderWithProviders(
      <FollowUpSuggestions
        project="/p"
        agentId="coder"
        conversationVersion={ "v0" }
        isLoading={false}
        onSelect={vi.fn()}
      />,
    );
    await waitFor(() => {
      const runCall = fetchMock.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).endsWith("/api/ambient-suggestions/run"),
      );
      expect(runCall).toBeDefined();
    });
  });

  it("uses the global locale for generated bubble content", async () => {
    mockOnce({ added: 0, generated: 0, error: null });
    mockOnce({
      project_root: "/p",
      generated_at: "",
      enabled: true,
      suggestions: [],
    });
    renderWithProviders(
      <FollowUpSuggestions
        project="/p"
        agentId="coder"
        conversationVersion={ "v1" }
        isLoading={false}
        onSelect={vi.fn()}
      />,
      { locale: "zh-CN" },
    );

    await waitFor(() => {
      const runCall = fetchMock.mock.calls.find(
        (call) => (call[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(runCall).toBeDefined();
      const body = JSON.parse(String((runCall?.[1] as RequestInit)?.body));
      expect(body.locale).toBe("zh-CN");
    });
  });

  it("does not generate when project or agentId is missing", async () => {
    renderWithProviders(
      <FollowUpSuggestions
        project={null}
        agentId="coder"
        conversationVersion={ "v2" }
        isLoading={false}
        onSelect={vi.fn()}
      />,
    );
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not re-generate when remounted with the same conversation version", async () => {
    mockOnce({ added: 0, generated: 0, error: null }); // /run
    mockOnce({
      project_root: "/p",
      generated_at: "",
      enabled: true,
      suggestions: [],
    }); // refresh after run
    const { unmount } = renderWithProviders(
      <FollowUpSuggestions
        project="/p"
        agentId="coder"
        conversationVersion="v-remount"
        isLoading={false}
        onSelect={vi.fn()}
      />,
    );
    await waitFor(() => {
      const runCall = fetchMock.mock.calls.find(
        (call) => (call[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(runCall).toBeDefined();
    });
    unmount();

    const runCallsBefore = fetchMock.mock.calls.filter(
      (c) => (c[1] as RequestInit | undefined)?.method === "POST",
    ).length;
    renderWithProviders(
      <FollowUpSuggestions
        project="/p"
        agentId="coder"
        conversationVersion="v-remount"
        isLoading={false}
        onSelect={vi.fn()}
      />,
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    const runCallsAfter = fetchMock.mock.calls.filter(
      (c) => (c[1] as RequestInit | undefined)?.method === "POST",
    ).length;
    expect(runCallsAfter).toBe(runCallsBefore);
  });

  it("renders pending suggestion chips after generation", async () => {
    mockOnce({ added: 1, generated: 1, error: null }); // /run
    mockOnce(SAMPLE_BUCKET); // refresh
    renderWithProviders(
      <FollowUpSuggestions
        project="/p"
        agentId="coder"
        conversationVersion={ "v3" }
        isLoading={false}
        onSelect={vi.fn()}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText("Fix CI")).toBeInTheDocument();
    });
  });

  it("clicking a chip sends the prompt and marks it accepted", async () => {
    mockOnce({ added: 1, generated: 1, error: null }); // /run
    mockOnce(SAMPLE_BUCKET); // refresh
    const onSelect = vi.fn();
    renderWithProviders(
      <FollowUpSuggestions
        project="/p"
        agentId="coder"
        conversationVersion={ "v4" }
        isLoading={false}
        onSelect={onSelect}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText("Fix CI")).toBeInTheDocument();
    });

    mockOnce({ ok: true }); // PATCH
    mockOnce(SAMPLE_BUCKET); // refresh after PATCH
    fireEvent.click(screen.getByText("Fix CI"));

    await waitFor(() => {
      expect(onSelect).toHaveBeenCalledWith("investigate CI failures");
      const patchCall = fetchMock.mock.calls.find(
        (c) => (c[1] as RequestInit | undefined)?.method === "PATCH",
      );
      expect(patchCall).toBeDefined();
      expect(patchCall![0]).toContain("/api/ambient-suggestions/abc");
    });
  });
});
