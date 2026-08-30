import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listCapabilities: vi.fn(),
}));

vi.mock("@/core/agents/agent-world-api", () => ({
  listCapabilities: mocks.listCapabilities,
}));

import { useCapabilitySurface } from "./use-capability-surface";

function Providers({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function SurfaceProbe() {
  const chat = useCapabilitySurface("chat.recorder");
  const browser = useCapabilitySurface("browser.recorder");
  return <div>{`${chat}:${browser}`}</div>;
}

describe("useCapabilitySurface", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("shares one lookup across surfaces and does not poll during long tasks", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [
        {
          id: "echo-recorder",
          installed: true,
          enabled: true,
          surface_capabilities: ["chat.recorder", "browser.recorder"],
        },
      ],
      total: 1,
    });

    render(<SurfaceProbe />, { wrapper: Providers });

    await waitFor(() => expect(screen.getByText("true:true")).toBeVisible());
    expect(mocks.listCapabilities).toHaveBeenCalledTimes(1);

    vi.useFakeTimers();
    act(() => vi.advanceTimersByTime(60_000));
    expect(mocks.listCapabilities).toHaveBeenCalledTimes(1);
  });
});
