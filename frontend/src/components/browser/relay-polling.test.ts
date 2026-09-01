import { describe, expect, test } from "vitest";

import {
  RELAY_AUTH_RETRY_MS,
  RELAY_MAX_RETRY_MS,
  getRelayStatusRetryDelay,
} from "./relay-polling";

describe("getRelayStatusRetryDelay", () => {
  test("backs off authentication failures instead of retrying every 3 seconds", () => {
    expect(getRelayStatusRetryDelay(401, 1)).toBe(RELAY_AUTH_RETRY_MS);
    expect(getRelayStatusRetryDelay(403, 4)).toBe(RELAY_AUTH_RETRY_MS);
  });

  test("uses a capped exponential delay for transient failures", () => {
    expect(getRelayStatusRetryDelay(null, 1)).toBe(6000);
    expect(getRelayStatusRetryDelay(500, 2)).toBe(12000);
    expect(getRelayStatusRetryDelay(500, 20)).toBe(RELAY_MAX_RETRY_MS);
  });
});
