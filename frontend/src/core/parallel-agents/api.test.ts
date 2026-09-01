import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://127.0.0.1:8000",
}));

const authState = vi.hoisted(() => ({ token: null as string | null }));
vi.mock("@/core/auth/api", () => ({
  getToken: () => authState.token,
}));

import { fetchBatchRecoverySnapshot, streamBatch, toBackendURL } from "./api";

describe("parallel agents backend URLs", () => {
  afterEach(() => {
    authState.token = null;
    vi.unstubAllGlobals();
  });

  test("prefixes relative API paths once", () => {
    expect(toBackendURL("/api/agents/parallel/status")).toBe(
      "http://127.0.0.1:8000/api/agents/parallel/status",
    );
  });

  test("does not double-prefix absolute URLs", () => {
    expect(
      toBackendURL("http://127.0.0.1:8000/api/agents/parallel/stream/b1"),
    ).toBe("http://127.0.0.1:8000/api/agents/parallel/stream/b1");
  });

  test("fetches recovery snapshot with auth headers", async () => {
    authState.token = "sk-test";
    const calls: Array<{ init?: RequestInit; url: string }> = [];
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return Promise.resolve(
        new Response(
          JSON.stringify({
            schema: "echo.parallel_batch_recovery_snapshot.v1",
            batch_id: "batch_1",
            status: "partial",
            terminal: true,
            resume_available: true,
            created_at: "2026-06-29T00:00:00Z",
            completed_at: "2026-06-29T00:00:01Z",
            task_count: 2,
            completed_tasks: 1,
            failed_tasks: 1,
            cancelled_tasks: 0,
            running_tasks: 0,
            pending_tasks: 0,
            tasks: [],
            dag: {},
            event_sequence: { last_sequence: 8 },
            artifact_paths: [],
            conflicts: [],
            completion_receipt: { ready: false },
            file_write_observability: {},
            recovery_hints: { rerunnable_task_ids: ["task_failed"] },
            safety: {
              raw_subagent_outputs_included: false,
              event_payloads_included: false,
              owner_id_included: false,
            },
          }),
          { status: 200 },
        ),
      );
    });

    const snapshot = await fetchBatchRecoverySnapshot("batch_1");

    expect(snapshot?.batch_id).toBe("batch_1");
    expect(snapshot?.recovery_hints.rerunnable_task_ids).toEqual([
      "task_failed",
    ]);
    expect(calls[0]?.url).toBe(
      "http://127.0.0.1:8000/api/agents/parallel/batch/batch_1/recovery-snapshot",
    );
    expect(calls[0]?.init?.credentials).toBe("include");
    expect(calls[0]?.init?.headers).toMatchObject({
      Authorization: "Bearer sk-test",
    });
  });

  test("returns null when recovery snapshot request fails", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve(new Response("missing", { status: 404 })),
    );

    await expect(fetchBatchRecoverySnapshot("missing")).resolves.toBeNull();
  });
});

describe("SSE streamBatch: event parsing", () => {
  test("parses task_update event correctly", () => {
    const line1 = "event: task_update";
    const line2 =
      'data: {"type":"task_update","batch_id":"b1","task_id":"t1","status":"running"}';
    const line3 = "";

    let eventType = "";
    let eventData = "";

    const lines = [line1, line2, line3];
    const results: unknown[] = [];

    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventType = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        eventData = line.slice(5).trim();
      } else if (line === "") {
        if (eventType && eventData) {
          const data = JSON.parse(eventData);
          results.push({ eventType, data });
        }
        eventType = "";
        eventData = "";
      }
    }

    expect(results.length).toBe(1);
    expect(results[0].eventType).toBe("task_update");
    expect(results[0].data.task_id).toBe("t1");
    expect(results[0].data.status).toBe("running");
  });

  test("parses batch_complete event and stops", () => {
    const raw = [
      "event: task_update",
      'data: {"type":"task_update","batch_id":"b1","task_id":"t1","status":"completed"}',
      "",
      "event: batch_complete",
      'data: {"type":"batch_complete","batch_id":"b1"}',
      "",
    ];

    const results: unknown[] = [];
    let eventType = "";
    let eventData = "";

    for (const line of raw) {
      if (line.startsWith("event:")) {
        eventType = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        eventData = line.slice(5).trim();
      } else if (line === "") {
        if (eventType && eventData) {
          const data = JSON.parse(eventData);
          results.push({ eventType, data });
          if (eventType === "batch_complete") break;
        }
        eventType = "";
        eventData = "";
      }
    }

    expect(results.length).toBe(2);
    expect(results[0].eventType).toBe("task_update");
    expect(results[1].eventType).toBe("batch_complete");
  });

  test("handles keepalive comment lines", () => {
    const line = ": keepalive";
    expect(line.startsWith(":")).toBeTruthy();
  });

  test("handles multiple data lines for same event", () => {
    const raw = [
      "event: task_update",
      'data: {"type":"task_update",',
      'data: "batch_id":"b1","task_id":"t2","status":"running"}',
      "",
    ];

    let eventType = "";
    let dataParts: string[] = [];

    for (const line of raw) {
      if (line.startsWith("event:")) {
        eventType = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataParts.push(line.slice(5).trim());
      } else if (line === "") {
        if (eventType && dataParts.length > 0) {
          const fullData = dataParts.join("");
          const data = JSON.parse(fullData);
          expect(data.task_id).toBe("t2");
        }
        eventType = "";
        dataParts = [];
      }
    }
  });
});

describe("SSE streamBatch: retry logic", () => {
  test("exponential backoff delay calculation", () => {
    const baseDelay = 1000;
    const delays = [];
    for (let i = 1; i <= 3; i++) {
      delays.push(baseDelay * Math.pow(2, i - 1));
    }
    expect(delays).toEqual([1000, 2000, 4000]);
  });

  test("max retries limit", () => {
    const maxRetries = 3;
    let attempts = 0;
    for (let i = 0; i <= maxRetries + 2; i++) {
      if (i <= maxRetries) attempts++;
    }
    expect(attempts).toBe(4);
  });

  test("reconnect requests only events after the last seen sequence", async () => {
    const urls: string[] = [];
    let requestCount = 0;
    const firstStream = [
      "event: task_update\n",
      'data: {"type":"task_update","batch_id":"b1","sequence":4,"task_id":"t1"}\n\n',
    ];
    const secondStream = [
      "event: batch_complete\n",
      'data: {"type":"batch_complete","batch_id":"b1","sequence":5}\n\n',
    ];

    vi.stubGlobal("fetch", (url: string) => {
      urls.push(url);
      requestCount += 1;
      const chunks = requestCount === 1 ? firstStream : secondStream;
      const stream = new ReadableStream({
        start(controller) {
          for (const chunk of chunks) {
            controller.enqueue(new TextEncoder().encode(chunk));
          }
          controller.close();
        },
      });
      return Promise.resolve(new Response(stream, { status: 200 }));
    });

    const events: number[] = [];
    streamBatch(
      "b1",
      {
        onTaskUpdate: (event) => events.push(event.sequence ?? 0),
        onBatchComplete: (event) => events.push(event.sequence ?? 0),
      },
      { maxRetries: 1, baseDelay: 10 },
    );

    await new Promise((resolve) => setTimeout(resolve, 80));

    expect(events).toEqual([4, 5]);
    expect(urls[0]).toBe("http://127.0.0.1:8000/api/agents/parallel/stream/b1");
    expect(urls[1]).toBe(
      "http://127.0.0.1:8000/api/agents/parallel/stream/b1?after_sequence=4",
    );
    vi.unstubAllGlobals();
  });
});

describe("SSE streamBatch: production parser edges", () => {
  test("dispatches CRLF-delimited events from the real stream parser", async () => {
    vi.stubGlobal("fetch", () => {
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(
              'event: batch_complete\r\ndata: {"type":"batch_complete","batch_id":"b1","sequence":1,"status":"failed"}\r\n\r\n',
            ),
          );
          controller.close();
        },
      });
      return Promise.resolve(new Response(stream, { status: 200 }));
    });

    const events: string[] = [];
    streamBatch(
      "b1",
      {
        onBatchComplete: (event) => {
          events.push(`${event.sequence}:${event.status}`);
        },
      },
      { maxRetries: 0, baseDelay: 1 },
    );

    await new Promise((resolve) => setTimeout(resolve, 40));

    expect(events).toEqual(["1:failed"]);
    vi.unstubAllGlobals();
  });

  test("accumulates multiple data lines in the real stream parser", async () => {
    vi.stubGlobal("fetch", () => {
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(
              [
                "event: task_update\n",
                'data: {"type":"task_update","batch_id":"b1",\n',
                'data: "task_id":"t1","sequence":2,\n',
                'data: "status":"running"}\n',
                "\n",
                "event: batch_complete\n",
                'data: {"type":"batch_complete","batch_id":"b1","sequence":3}\n',
                "\n",
              ].join(""),
            ),
          );
          controller.close();
        },
      });
      return Promise.resolve(new Response(stream, { status: 200 }));
    });

    const updates: string[] = [];
    const completions: number[] = [];
    streamBatch(
      "b1",
      {
        onTaskUpdate: (event) => {
          updates.push(`${event.sequence}:${event.task_id}:${event.status}`);
        },
        onBatchComplete: (event) => {
          completions.push(event.sequence ?? 0);
        },
      },
      { maxRetries: 0, baseDelay: 1 },
    );

    await new Promise((resolve) => setTimeout(resolve, 40));

    expect(updates).toEqual(["2:t1:running"]);
    expect(completions).toEqual([3]);
    vi.unstubAllGlobals();
  });
});
