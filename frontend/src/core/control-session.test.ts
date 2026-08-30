import { describe, expect, it, vi } from "vitest";

import {
  controlInterruptionDetail,
  getControlSessionEvidenceDetail,
  getControlSessionTimeline,
  setControlSessionState,
  getControlStopReason,
  mergeControlSessionTimeline,
  mergeControlSessionTimelineItems,
  runControlSessionAction,
  type ControlEvidence,
  type ControlSessionReplayTimeline,
} from "./control-session";

describe("control-session", () => {
  it("normalizes boolean stop checks to operator_stop", () => {
    expect(getControlStopReason({ getStopped: () => true })).toBe(
      "operator_stop",
    );
    expect(getControlStopReason({ getStopped: () => "target_changed" })).toBe(
      "target_changed",
    );
    expect(getControlStopReason({ getStopped: () => false })).toBeNull();
  });

  it("returns compact interruption details", () => {
    expect(
      controlInterruptionDetail("target_changed", {
        surface: "electron_webview",
        targetId: "tab_1",
      }),
    ).toEqual({
      code: "control_session_interrupted",
      reason: "target_changed",
      surface: "electron_webview",
      targetId: "tab_1",
    });
  });

  it("wraps a successful action with indicator and evidence events", async () => {
    const setIndicator = vi.fn();
    const evidence: ControlEvidence[] = [];

    const result = await runControlSessionAction(
      { type: "click", selector: "#go" },
      async () => "ok",
      {
        control: {
          surface: "browser",
          targetId: "tab_1",
          setIndicator,
          recordEvidence: (item) => evidence.push(item),
          now: () => 100,
        },
        interrupted: (reason) => `interrupted:${reason}`,
      },
    );

    expect(result).toBe("ok");
    expect(setIndicator).toHaveBeenNthCalledWith(1, "action", {
      action: "click",
      surface: "browser",
      targetId: "tab_1",
    });
    expect(setIndicator).toHaveBeenLastCalledWith("idle", {
      action: "click",
    });
    expect(evidence).toEqual([
      {
        kind: "action",
        at: 100,
        action: "click",
        summary: "started",
      },
      {
        kind: "result",
        at: 100,
        action: "click",
        ok: true,
        summary: "completed",
      },
    ]);
  });

  it("does not run the action when already stopped", async () => {
    const run = vi.fn();
    const setIndicator = vi.fn();

    const result = await runControlSessionAction("click", run, {
      control: {
        surface: "computer",
        getStopped: () => "lease_lost",
        setIndicator,
      },
      interrupted: (reason) => `interrupted:${reason}`,
    });

    expect(run).not.toHaveBeenCalled();
    expect(result).toBe("interrupted:lease_lost");
    expect(setIndicator).toHaveBeenCalledWith("paused", {
      action: "click",
      reason: "lease_lost",
    });
  });

  it("fetches a full evidence detail through the control-session API", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        schema: "echo.control_evidence_detail.v1",
        session_id: "ctrl swarm",
        evidence_id: "evidence/replay",
        source: "blob",
        detail: {
          schema: "echo.swarm_replay_package.v1",
          timeline: [{ id: "evt-1" }],
        },
      }),
      text: async () => "",
    }));
    globalThis.fetch = fetchMock as typeof fetch;

    try {
      const detail = await getControlSessionEvidenceDetail(
        "ctrl swarm",
        "evidence/replay",
      );

      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(
          "/api/control-sessions/ctrl%20swarm/evidence/evidence%2Freplay/detail",
        ),
        expect.objectContaining({ method: "GET" }),
      );
      expect(detail).toMatchObject({
        schema: "echo.control_evidence_detail.v1",
        source: "blob",
        detail: {
          schema: "echo.swarm_replay_package.v1",
        },
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("fetches the lightweight control-session replay timeline", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        schema: "echo.control_session_replay_timeline.v1",
        session_id: "ctrl-1",
        status: "idle",
        count: 1,
        after: 100,
        after_cursor: "100.000000|action%3A1",
        next_after: 110,
        next_cursor: "110.000000|evidence%3Aevidence-1",
        has_more: false,
        items: [
          {
            id: "evidence:evidence-1",
            kind: "evidence",
            phase: "evidence",
            at: 100,
            cursor: "100.000000|evidence%3Aevidence-1",
            evidence_id: "evidence-1",
            detail_href:
              "/api/control-sessions/ctrl-1/evidence/evidence-1/detail",
            summary: "loaded",
          },
        ],
      }),
      text: async () => "",
    }));
    globalThis.fetch = fetchMock as typeof fetch;

    try {
      const timeline = await getControlSessionTimeline("ctrl-1", {
        after: 100,
        afterCursor: "100.000000|action%3A1",
        limit: 25,
      });

      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(
          "/api/control-sessions/ctrl-1/timeline?limit=25&after=100&after_cursor=100.000000%7Caction%253A1",
        ),
        expect.objectContaining({ method: "GET" }),
      );
      expect(timeline).toMatchObject({
        schema: "echo.control_session_replay_timeline.v1",
        session_id: "ctrl-1",
        count: 1,
        after_cursor: "100.000000|action%3A1",
        next_after: 110,
        next_cursor: "110.000000|evidence%3Aevidence-1",
        has_more: false,
      });
      expect(timeline.items[0]?.detail_href).toContain("/detail");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("sends explicit pause / takeover state commands", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        session: {
          session_id: "ctrl-1",
          status: "paused",
          paused: true,
        },
      }),
      text: async () => "",
    }));
    globalThis.fetch = fetchMock as typeof fetch;

    try {
      await setControlSessionState("ctrl-1", "takeover", "user takeover");
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/control-sessions/ctrl-1/takeover"),
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ reason: "user takeover" }),
        }),
      );
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("merges overlapping control-session timeline pages without duplicates", () => {
    const items = mergeControlSessionTimelineItems(
      [
        {
          id: "action:a1:queued",
          kind: "action",
          phase: "queued",
          at: 100,
          action_id: "a1",
          action: "click",
          status: "running",
          summary: "click queued",
          cursor: "100.000000|action%3Aa1%3Aqueued",
        },
        {
          id: "evidence:e1",
          kind: "evidence",
          phase: "evidence",
          at: 101,
          evidence_id: "e1",
          summary: "observed",
          cursor: "101.000000|evidence%3Ae1",
        },
      ],
      [
        {
          id: "evidence:e1",
          kind: "evidence",
          phase: "evidence",
          at: 101,
          evidence_id: "e1",
          summary: "observed updated",
          detail_href: "/api/control-sessions/ctrl/evidence/e1/detail",
          cursor: "101.000000|evidence%3Ae1",
        },
        {
          id: "action:a1:completed",
          kind: "action",
          phase: "completed",
          at: 102,
          action_id: "a1",
          action: "click",
          status: "done",
          summary: "click done",
          cursor: "102.000000|action%3Aa1%3Acompleted",
        },
      ],
    );

    expect(items.map((item) => item.id)).toEqual([
      "action:a1:queued",
      "evidence:e1",
      "action:a1:completed",
    ]);
    expect(items[1]).toMatchObject({
      summary: "observed updated",
      detail_href: "/api/control-sessions/ctrl/evidence/e1/detail",
    });
  });

  it("merges control-session timelines with stable ordering and latest cursors", () => {
    const existing: ControlSessionReplayTimeline = {
      schema: "echo.control_session_replay_timeline.v1",
      session_id: "ctrl-1",
      status: "running",
      count: 2,
      next_after: 101,
      next_cursor: "101.000000|evidence%3Ae1",
      has_more: true,
      items: [
        {
          id: "evidence:e1",
          kind: "evidence",
          phase: "evidence",
          at: 101,
          summary: "observed",
          cursor: "101.000000|evidence%3Ae1",
        },
        {
          id: "action:a1:queued",
          kind: "action",
          phase: "queued",
          at: 100,
          summary: "click queued",
          cursor: "100.000000|action%3Aa1%3Aqueued",
        },
      ],
    };
    const incoming: ControlSessionReplayTimeline = {
      schema: "echo.control_session_replay_timeline.v1",
      session_id: "ctrl-1",
      status: "idle",
      count: 2,
      after_cursor: "101.000000|evidence%3Ae1",
      next_after: 102,
      next_cursor: "102.000000|action%3Aa1%3Acompleted",
      has_more: false,
      items: [
        {
          id: "action:a1:completed",
          kind: "action",
          phase: "completed",
          at: 102,
          summary: "click done",
          cursor: "102.000000|action%3Aa1%3Acompleted",
        },
        {
          id: "evidence:e1",
          kind: "evidence",
          phase: "evidence",
          at: 101,
          summary: "observed again",
          cursor: "101.000000|evidence%3Ae1",
        },
      ],
    };

    const merged = mergeControlSessionTimeline(existing, incoming);

    expect(merged.status).toBe("idle");
    expect(merged.count).toBe(3);
    expect(merged.next_cursor).toBe("102.000000|action%3Aa1%3Acompleted");
    expect(merged.has_more).toBe(false);
    expect(merged.items.map((item) => item.id)).toEqual([
      "action:a1:queued",
      "evidence:e1",
      "action:a1:completed",
    ]);
    expect(merged.items[1]?.summary).toBe("observed again");
  });
});
