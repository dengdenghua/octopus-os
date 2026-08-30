import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import { renderWithProviders } from "@/test/harness";

import { RemoteBackendsPanel } from "./remote-backends-panel";

const fetchMock = vi.fn();

const ENABLED_LIST = {
  enabled: true,
  backends: [
    {
      id: "b1",
      name: "prod",
      url: "https://api.example.com",
      ssh: null,
      added_at: "2026-05-08T10:00:00Z",
      last_health: "ok" as const,
      last_health_at: "2026-05-08T10:00:00Z",
      health_detail: null,
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

describe("RemoteBackendsPanel", () => {
  it("renders disabled badge when feature off", async () => {
    mockOnce({ enabled: false, backends: [] });
    renderWithProviders(<RemoteBackendsPanel />);
    await waitFor(() => {
      expect(screen.getByLabelText("feature disabled")).toBeInTheDocument();
    });
  });

  it("renders backends list with health badge", async () => {
    mockOnce(ENABLED_LIST);
    renderWithProviders(<RemoteBackendsPanel />);
    await waitFor(() => {
      expect(screen.getByText("prod")).toBeInTheDocument();
    });
    expect(screen.getByText("reachable")).toBeInTheDocument();
    expect(screen.getByText("https://api.example.com")).toBeInTheDocument();
  });

  it("Add form submits POST /api/remote-backends", async () => {
    mockOnce(ENABLED_LIST);
    renderWithProviders(<RemoteBackendsPanel />);
    await waitFor(() => {
      expect(screen.getByText("prod")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText("Backend name"), {
      target: { value: "stage" },
    });
    fireEvent.change(screen.getByLabelText("Backend URL"), {
      target: { value: "https://stage.example.com" },
    });

    mockOnce({
      backend: { ...ENABLED_LIST.backends[0], id: "b2", name: "stage" },
    });
    mockOnce({
      enabled: true,
      backends: [
        ENABLED_LIST.backends[0],
        { ...ENABLED_LIST.backends[0], id: "b2", name: "stage" },
      ],
    });

    fireEvent.submit(screen.getByLabelText("Add remote backend"));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        (c) => (c[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(post).toBeDefined();
      expect(post![0]).toContain("/api/remote-backends");
    });
  });

  it("Ping button posts to /health", async () => {
    mockOnce(ENABLED_LIST);
    renderWithProviders(<RemoteBackendsPanel />);
    await waitFor(() => {
      expect(screen.getByText("prod")).toBeInTheDocument();
    });

    mockOnce({ status: "ok", detail: null });
    mockOnce(ENABLED_LIST);

    fireEvent.click(screen.getByRole("button", { name: /Ping prod/i }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).endsWith("/api/remote-backends/b1/health"),
      );
      expect(call).toBeDefined();
    });
  });

  it("Remove button DELETEs", async () => {
    mockOnce(ENABLED_LIST);
    renderWithProviders(<RemoteBackendsPanel />);
    await waitFor(() => {
      expect(screen.getByText("prod")).toBeInTheDocument();
    });

    mockOnce({ removed: "b1" });
    mockOnce({ enabled: true, backends: [] });

    fireEvent.click(screen.getByRole("button", { name: /Remove prod/i }));

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /Remove remote backend/i }),
      ).toBeInTheDocument();
    });
    fireEvent.click(
      screen.getByRole("button", { name: /^Remove$/i }),
    );

    await waitFor(() => {
      const del = fetchMock.mock.calls.find(
        (c) => (c[1] as RequestInit | undefined)?.method === "DELETE",
      );
      expect(del).toBeDefined();
      expect(del![0]).toContain("/api/remote-backends/b1");
    });
  });

  it("renders fetch error", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => ({}),
      text: async () => "boom",
    });
    renderWithProviders(<RemoteBackendsPanel />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });
});
