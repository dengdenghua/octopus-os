import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import { renderWithProviders } from "@/test/harness";

import { AmbientSuggestionsPanel } from "./ambient-suggestions-panel";

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
    {
      id: "def",
      project_root: "/p",
      title: "Old accepted",
      description: "",
      prompt: "x",
      locale: "en-US",
      status: "accepted",
      source_turn_ids: [],
      created_at: "",
      updated_at: "",
      model: null,
      experimental: false,
    },
    {
      id: "ghi",
      project_root: "/p",
      title: "Bygones",
      description: "",
      prompt: "y",
      locale: "en-US",
      status: "dismissed",
      source_turn_ids: [],
      created_at: "",
      updated_at: "",
      model: null,
      experimental: false,
    },
  ],
};

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
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

describe("AmbientSuggestionsPanel", () => {
  it("renders empty state", async () => {
    mockOnce({
      project_root: "/p",
      generated_at: "",
      enabled: true,
      suggestions: [],
    });
    renderWithProviders(<AmbientSuggestionsPanel project="/p" />);
    await waitFor(() => {
      expect(screen.getByText(/No suggestions yet/i)).toBeInTheDocument();
    });
  });

  it("renders disabled hint when bucket.enabled is false", async () => {
    mockOnce({
      project_root: "/p",
      generated_at: "",
      enabled: false,
      suggestions: [],
    });
    renderWithProviders(<AmbientSuggestionsPanel project="/p" />);
    await waitFor(() => {
      expect(screen.getByText(/Feature disabled/i)).toBeInTheDocument();
    });
  });

  it("lists pending suggestions with Accept/Dismiss buttons", async () => {
    mockOnce(SAMPLE_BUCKET);
    renderWithProviders(<AmbientSuggestionsPanel project="/p" />);
    await waitFor(() => {
      expect(screen.getByText("Fix CI")).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: /Accept suggestion: Fix CI/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Dismiss suggestion: Fix CI/i }),
    ).toBeInTheDocument();
  });

  it("Accept click sends PATCH with status=accepted", async () => {
    mockOnce(SAMPLE_BUCKET);
    renderWithProviders(<AmbientSuggestionsPanel project="/p" />);
    await waitFor(() => {
      expect(screen.getByText("Fix CI")).toBeInTheDocument();
    });

    mockOnce({ ok: true });
    mockOnce(SAMPLE_BUCKET);

    fireEvent.click(
      screen.getByRole("button", { name: /Accept suggestion: Fix CI/i }),
    );

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        (c) => (c[1] as RequestInit | undefined)?.method === "PATCH",
      );
      expect(patchCall).toBeDefined();
      expect(patchCall![0]).toContain("/api/ambient-suggestions/abc");
      expect(
        JSON.parse((patchCall![1] as RequestInit).body as string),
      ).toMatchObject({
        status: "accepted",
      });
    });
  });

  it("Dismiss click sends PATCH with status=dismissed", async () => {
    mockOnce(SAMPLE_BUCKET);
    renderWithProviders(<AmbientSuggestionsPanel project="/p" />);
    await waitFor(() => {
      expect(screen.getByText("Fix CI")).toBeInTheDocument();
    });

    mockOnce({ ok: true });
    mockOnce(SAMPLE_BUCKET);

    fireEvent.click(
      screen.getByRole("button", { name: /Dismiss suggestion: Fix CI/i }),
    );

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        (c) => (c[1] as RequestInit | undefined)?.method === "PATCH",
      );
      expect(patchCall).toBeDefined();
      expect(
        JSON.parse((patchCall![1] as RequestInit).body as string),
      ).toMatchObject({
        status: "dismissed",
      });
    });
  });

  it("Generate button calls POST /run when agentId provided", async () => {
    mockOnce(SAMPLE_BUCKET);
    renderWithProviders(
      <AmbientSuggestionsPanel project="/p" agentId="coder" />,
    );
    await waitFor(() => {
      expect(screen.getByText("Fix CI")).toBeInTheDocument();
    });

    mockOnce({ added: 1, generated: 1, error: null });
    mockOnce(SAMPLE_BUCKET);

    fireEvent.click(screen.getByRole("button", { name: /Generate/i }));

    await waitFor(() => {
      const runCall = fetchMock.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).endsWith("/api/ambient-suggestions/run"),
      );
      expect(runCall).toBeDefined();
    });
  });

  it("hides Generate button when no agentId", async () => {
    mockOnce(SAMPLE_BUCKET);
    renderWithProviders(<AmbientSuggestionsPanel project="/p" />);
    await waitFor(() => {
      expect(screen.getByText("Fix CI")).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: /Generate/i }),
    ).not.toBeInTheDocument();
  });

  it("shows Dismissed section count", async () => {
    mockOnce(SAMPLE_BUCKET);
    renderWithProviders(<AmbientSuggestionsPanel project="/p" />);
    await waitFor(() => {
      expect(screen.getByText(/Dismissed \(1\)/)).toBeInTheDocument();
    });
  });

  it("renders fetch error", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => ({}),
      text: async () => "boom",
    });
    renderWithProviders(<AmbientSuggestionsPanel project="/p" />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });
});
