import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { useMobileDevices } from "./mobile-devices";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };
}

describe("useMobileDevices", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("does not query devices until the owning surface enables it", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    });
    vi.stubGlobal("fetch", fetchMock);

    const rendered = renderHook(
      ({ enabled }) => useMobileDevices({ enabled }),
      {
        initialProps: { enabled: false },
        wrapper: createWrapper(),
      },
    );

    expect(fetchMock).not.toHaveBeenCalled();

    rendered.rerender({ enabled: true });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });
});
