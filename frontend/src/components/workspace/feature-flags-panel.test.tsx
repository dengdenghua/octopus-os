import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import { renderWithProviders } from "@/test/harness";

import { FeatureFlagsPanel } from "./feature-flags-panel";

const fetchMock = vi.fn();

const _SAMPLE = {
  flags: [
    {
      name: "ui.ambient_suggestions",
      value: false,
      source: "default",
      default: false,
      description: "Surface AI-generated follow-ups",
      experimental: true,
      primary_env: "ECHO_FF_UI_AMBIENT_SUGGESTIONS",
      legacy_env: [],
    },
    {
      name: "regeneration.enabled",
      value: true,
      source: "env",
      default: true,
      description: "Self-repair scheduler",
      experimental: false,
      primary_env: "ECHO_FF_REGENERATION_ENABLED",
      legacy_env: ["ECHO_REGEN_ENABLED"],
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

function mockOnce(body: unknown) {
  fetchMock.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => body,
  });
}

describe("FeatureFlagsPanel", () => {
  it("renders all flags with their sources", async () => {
    mockOnce(_SAMPLE);
    renderWithProviders(<FeatureFlagsPanel />);

    await waitFor(() => {
      expect(screen.getByText("ui.ambient_suggestions")).toBeInTheDocument();
    });
    expect(screen.getByText("regeneration.enabled")).toBeInTheDocument();
    expect(screen.getByText("Self-repair scheduler")).toBeInTheDocument();
  });

  it("filters to experimental only when prop set", async () => {
    mockOnce(_SAMPLE);
    renderWithProviders(<FeatureFlagsPanel experimentalOnly />);

    await waitFor(() => {
      expect(screen.getByText("ui.ambient_suggestions")).toBeInTheDocument();
    });
    expect(screen.queryByText("regeneration.enabled")).not.toBeInTheDocument();
  });

  it("renders an error message when fetch fails", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => ({}),
    });
    renderWithProviders(<FeatureFlagsPanel />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });

  it("reload button triggers POST /reload", async () => {
    mockOnce(_SAMPLE);
    renderWithProviders(<FeatureFlagsPanel />);

    await waitFor(() => {
      expect(screen.getByText("ui.ambient_suggestions")).toBeInTheDocument();
    });

    mockOnce({
      flags: [
        { ..._SAMPLE.flags[0], value: true, source: "file" },
        _SAMPLE.flags[1],
      ],
    });

    const btn = screen.getByRole("button", { name: /Reload/i });
    fireEvent.click(btn);

    await waitFor(() => {
      const calls = fetchMock.mock.calls.map((c) => c[0] as string);
      expect(calls.some((u) => u.endsWith("/api/feature-flags/reload"))).toBe(
        true,
      );
    });
  });
});
