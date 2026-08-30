import { describe, expect, it, vi } from "vitest";

import { openSseStream } from "./sse";

async function flushAsync(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

describe("openSseStream auth failures", () => {
  it.each([401, 403])(
    "treats HTTP %s as terminal instead of reconnecting forever",
    async (status) => {
      vi.useFakeTimers();
      const fetchImpl = vi.fn(async () => new Response(null, { status }));
      const onError = vi.fn();
      const onReconnecting = vi.fn();

      const cleanup = openSseStream({
        url: "/api/preview/stream",
        fetchImpl,
        onEvent: vi.fn(),
        onError,
        onReconnecting,
      });
      await flushAsync();
      await vi.runAllTimersAsync();

      expect(fetchImpl).toHaveBeenCalledTimes(1);
      expect(onReconnecting).not.toHaveBeenCalled();
      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({ message: `SSE HTTP ${status}` }),
      );

      cleanup();
      vi.useRealTimers();
    },
  );
});
