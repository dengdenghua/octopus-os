import { afterEach, describe, expect, test, vi } from "vitest";

import {
  E2E_SURPASS_TARGET_SCORE,
  queueAgentScorecardGaps,
} from "./api";

describe("agent trace evolution API helpers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("queues scorecard gaps against the E2E surpass target by default", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        schema: "echo.agent_scorecard_gap_queue.v1",
        created: 0,
        updated: 0,
        total: 0,
        items: [],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await queueAgentScorecardGaps();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/evolution/agent-scorecard/gaps/queue",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          target_score: E2E_SURPASS_TARGET_SCORE,
          limit: 10,
          reason: "operator panel real score gap review",
          dimension_id: "",
        }),
      }),
    );
  });
});
