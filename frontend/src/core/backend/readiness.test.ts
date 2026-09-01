import { describe, expect, it, vi } from "vitest";

import { waitForBackendAvailability } from "./readiness";

describe("waitForBackendAvailability", () => {
  it("waits through startup misses and resolves when the backend is ready", async () => {
    const probe = vi
      .fn()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true);
    const sleep = vi.fn().mockResolvedValue(undefined);

    await expect(
      waitForBackendAvailability({ probe, sleep, maxWaitMs: 10_000 }),
    ).resolves.toBeUndefined();
    expect(probe).toHaveBeenCalledTimes(3);
    expect(sleep).toHaveBeenCalledTimes(2);
  });

  it("surfaces a genuine unavailable backend after the startup budget", async () => {
    const probe = vi.fn().mockResolvedValue(false);

    await expect(
      waitForBackendAvailability({
        probe,
        maxWaitMs: 0,
        sleep: () => Promise.resolve(),
      }),
    ).rejects.toThrow("Backend did not become ready");
    expect(probe).toHaveBeenCalledOnce();
  });
});
