import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import { renderWithProviders } from "@/test/harness";

import { InvariantsPanel } from "./invariants-panel";

const fetchMock = vi.fn();

const SAMPLE = {
  rules: [
    {
      rule_id: "BDG-I1",
      enforcers: [
        { module: "runtime.platform.budget", qualname: "Budget.reserve" },
        { module: "runtime.platform.budget", qualname: "Budget.commit" },
      ],
    },
    {
      rule_id: "MEM-I1",
      enforcers: [
        { module: "runtime.memory.journal", qualname: "Journal.append" },
      ],
    },
  ],
  total_rules: 2,
  total_enforcers: 3,
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

describe("InvariantsPanel", () => {
  it("renders rules and enforcer counts", async () => {
    mockOnce(SAMPLE);
    renderWithProviders(<InvariantsPanel />);
    await waitFor(() => {
      expect(screen.getByText("BDG-I1")).toBeInTheDocument();
    });
    expect(screen.getByText("MEM-I1")).toBeInTheDocument();
    expect(screen.getByText("2 rules")).toBeInTheDocument();
    expect(screen.getByText("3 enforcers")).toBeInTheDocument();
    expect(
      screen.getByText("runtime.platform.budget:Budget.reserve"),
    ).toBeInTheDocument();
  });

  it("filter narrows the list", async () => {
    mockOnce(SAMPLE);
    renderWithProviders(<InvariantsPanel />);
    await waitFor(() => {
      expect(screen.getByText("BDG-I1")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText("Filter invariants"), {
      target: { value: "MEM" },
    });
    await waitFor(() => {
      expect(screen.queryByText("BDG-I1")).not.toBeInTheDocument();
      expect(screen.getByText("MEM-I1")).toBeInTheDocument();
    });
  });

  it("filter showing no matches renders helpful empty state", async () => {
    mockOnce(SAMPLE);
    renderWithProviders(<InvariantsPanel />);
    await waitFor(() => {
      expect(screen.getByText("BDG-I1")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText("Filter invariants"), {
      target: { value: "nothing-matches" },
    });
    await waitFor(() => {
      expect(screen.getByText(/No rules match/i)).toBeInTheDocument();
    });
  });

  it("Rebuild button POSTs to /api/invariants/refresh", async () => {
    mockOnce(SAMPLE);
    renderWithProviders(<InvariantsPanel />);
    await waitFor(() => {
      expect(screen.getByText("BDG-I1")).toBeInTheDocument();
    });

    mockOnce(SAMPLE);

    fireEvent.click(screen.getByRole("button", { name: /Rebuild/i }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).endsWith("/api/invariants/refresh"),
      );
      expect(call).toBeDefined();
      expect((call![1] as RequestInit | undefined)?.method).toBe("POST");
    });
  });

  it("renders empty state when no rules declared", async () => {
    mockOnce({ rules: [], total_rules: 0, total_enforcers: 0 });
    renderWithProviders(<InvariantsPanel />);
    await waitFor(() => {
      expect(screen.getByText(/No rules declared yet/i)).toBeInTheDocument();
    });
  });

  it("renders fetch error", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => ({}),
    });
    renderWithProviders(<InvariantsPanel />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });
});
