/* Implementation note. */
import { describe, expect, test, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { AllProviders } from "@/test/harness";
import {
  GlobalControlPlaneAccessError,
  type EvolutionStatus,
} from "@/core/observability/api";

// Mock the API module BEFORE importing the component under test so
// the query hook picks up the mock factory.
vi.mock("@/core/observability/api", async () => {
  const actual = await vi.importActual<
    typeof import("@/core/observability/api") // eslint-disable-line @typescript-eslint/consistent-type-imports
  >("@/core/observability/api");
  return {
    ...actual,
    getEvolutionStatus: vi.fn(),
  };
});

import { getEvolutionStatus } from "@/core/observability/api";

import { EvolutionIndicator } from "./evolution-indicator";

vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({
    authStatus: { enabled: false },
    user: null,
  }),
  useOptionalAuth: () => ({
    authStatus: { enabled: false },
    user: null,
  }),
}));

const mockedGetStatus = vi.mocked(getEvolutionStatus);

function mockStatus(overrides: Partial<EvolutionStatus> = {}): EvolutionStatus {
  return {
    enabled: true,
    rules_count: 0,
    memories_count: 0,
    rules_lines: [],
    memories_lines: [],
    trajectories: { total: 0, react_loop: 0, react_loop_failures: 0 },
    react_variants: [],
    ...overrides,
  };
}

describe("EvolutionIndicator", () => {
  beforeEach(() => {
    mockedGetStatus.mockReset();
  });

  test("renders nothing when evolution is disabled", async () => {
    mockedGetStatus.mockResolvedValue(mockStatus({ enabled: false }));
    const { container } = render(
      <AllProviders>
        <EvolutionIndicator />
      </AllProviders>,
    );
    // Wait for the query to settle · disabled → returns null
    await waitFor(() => expect(mockedGetStatus).toHaveBeenCalled());
    expect(
      container.querySelector("[data-testid=evolution-indicator]"),
    ).toBeNull();
  });

  test("hides the optional indicator and does not retry a 403", async () => {
    mockedGetStatus.mockRejectedValue(new GlobalControlPlaneAccessError());
    const { container } = render(
      <AllProviders locale="en-US">
        <EvolutionIndicator />
      </AllProviders>,
    );

    await waitFor(() => expect(mockedGetStatus).toHaveBeenCalledTimes(1));
    expect(
      container.querySelector("[data-testid=evolution-admin-gate]"),
    ).toBeNull();
  });

  test("renders nothing when both counters are 0 and showWhenEmpty is false", async () => {
    mockedGetStatus.mockResolvedValue(
      mockStatus({ rules_count: 0, memories_count: 0 }),
    );
    const { container } = render(
      <AllProviders>
        <EvolutionIndicator />
      </AllProviders>,
    );
    await waitFor(() => expect(mockedGetStatus).toHaveBeenCalled());
    expect(
      container.querySelector("[data-testid=evolution-indicator]"),
    ).toBeNull();
  });

  test("renders 0/0 when showWhenEmpty=true", async () => {
    mockedGetStatus.mockResolvedValue(
      mockStatus({ rules_count: 0, memories_count: 0 }),
    );
    render(
      <AllProviders>
        <EvolutionIndicator showWhenEmpty />
      </AllProviders>,
    );
    const trigger = await screen.findByTestId("evolution-indicator");
    expect(trigger.textContent).toContain("0 rules");
    expect(trigger.textContent).toContain("0 memories");
  });

  test("renders the live rule / memory counts when non-zero", async () => {
    mockedGetStatus.mockResolvedValue(
      mockStatus({ rules_count: 3, memories_count: 7 }),
    );
    render(
      <AllProviders>
        <EvolutionIndicator />
      </AllProviders>,
    );
    const trigger = await screen.findByTestId("evolution-indicator");
    expect(trigger.textContent).toContain("3 rules");
    expect(trigger.textContent).toContain("7 memories");
  });

  test("compact mode keeps counts accessible while visually collapsing the label", async () => {
    mockedGetStatus.mockResolvedValue(
      mockStatus({ rules_count: 3, memories_count: 7 }),
    );
    render(
      <AllProviders>
        <EvolutionIndicator compact />
      </AllProviders>,
    );
    const trigger = await screen.findByTestId("evolution-indicator");
    expect(trigger.getAttribute("title")).toContain("3 rules");
    expect(trigger.querySelector(".sr-only")?.textContent).toContain(
      "7 memories",
    );
    expect(trigger.textContent).toContain("10");
  });

  test("first render with non-zero data does not fire the delta badge", async () => {
    // Guards against the obvious "page refresh shows +3 flash" bug —
    // the first time we see a value, we record it as baseline, not
    // treat it as a learning increment.
    mockedGetStatus.mockResolvedValue(
      mockStatus({ rules_count: 3, memories_count: 7 }),
    );
    render(
      <AllProviders>
        <EvolutionIndicator />
      </AllProviders>,
    );
    const trigger = await screen.findByTestId("evolution-indicator");
    // Implementation note.
    // appear on first paint.
    expect(trigger.textContent).not.toMatch(/\+\d+ rules/);
    expect(trigger.textContent).not.toMatch(/\+\d+ memories/);
  });
});
