import { afterEach, describe, expect, test, vi } from "vitest";

import { EchoClient, STUB_RESPONSE_EVENT } from "./client";

describe("EchoClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  test("can be instantiated with an API URL", () => {
    const client = new EchoClient({ apiUrl: "http://localhost:8001" });
    expect(client).toBeTruthy();
    // The client exposes namespaced API groups (threads / runs /
    // agents / ...). Just assert the instance was constructed;
    // specific method names live on sub-objects and would need a
    // deeper test if we wanted to pin them down.
  });

  test("resolves gateway /api paths against absolute desktop backend origin", () => {
    const client = new EchoClient({ apiUrl: "http://127.0.0.1:4105/api" });

    expect(
      (
        client as unknown as { _resolveUrl: (path: string) => string }
      )._resolveUrl("/api/models"),
    ).toBe("http://127.0.0.1:4105/api/models");
  });

  test("keeps gateway /api paths relative for dev proxy base", () => {
    const client = new EchoClient({ apiUrl: "/api" });

    expect(
      (
        client as unknown as { _resolveUrl: (path: string) => string }
      )._resolveUrl("/api/models"),
    ).toBe("/api/models");
  });

  test("warns once when a generic API call receives a stub response", async () => {
    const client = new EchoClient({ apiUrl: "/api" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ _stub: true, success: true }),
      }),
    );
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    await client.get("/api/account/profile");
    await client.get("/api/account/profile");

    expect(warnSpy).toHaveBeenCalledTimes(1);
    expect(warnSpy.mock.calls[0]?.[0]).toContain("stub response");
  });

  test("emits a window event when a generic API call receives a stub response", async () => {
    const client = new EchoClient({ apiUrl: "/api" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ _stub: true, success: true }),
      }),
    );
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const handler = vi.fn();
    window.addEventListener(STUB_RESPONSE_EVENT, handler);

    await client.get("/api/account/privacy");

    expect(handler).toHaveBeenCalledTimes(1);
    expect((handler.mock.calls[0]?.[0] as CustomEvent).detail).toEqual({
      method: "GET",
      path: "/api/account/privacy",
    });

    window.removeEventListener(STUB_RESPONSE_EVENT, handler);
  });
});

describe("SSE line parsing", () => {
  test("event type is correctly captured from event: line", () => {
    const events: Array<{ event: string; data: unknown }> = [];
    let currentEventType = "";

    function processLine(line: string) {
      if (line.startsWith("event: ")) {
        currentEventType = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        const eventType = currentEventType || "unknown";
        const dataStr = line.slice(6);
        let data: unknown;
        try {
          data = JSON.parse(dataStr);
        } catch {
          data = dataStr;
        }
        events.push({ event: eventType, data });
        currentEventType = "";
      }
    }

    processLine("event: values");
    processLine('data: {"messages": []}');

    expect(events.length).toBe(1);
    expect(events[0]!.event).toBe("values");
    expect(events[0]!.data).toEqual({ messages: [] });
  });

  test("event type resets after data line", () => {
    let currentEventType = "";
    const events: string[] = [];

    function processLine(line: string) {
      if (line.startsWith("event: ")) {
        currentEventType = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        events.push(currentEventType || "unknown");
        currentEventType = "";
      }
    }

    processLine("event: values");
    processLine("data: {}");
    processLine("data: {}");

    expect(events[0]).toBe("values");
    expect(events[1]).toBe("unknown");
  });

  test("multiple events are parsed in order", () => {
    let currentEventType = "";
    const events: Array<{ event: string; data: unknown }> = [];

    function processLine(line: string) {
      if (line.startsWith("event: ")) {
        currentEventType = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        const eventType = currentEventType || "unknown";
        events.push({ event: eventType, data: line.slice(6) });
        currentEventType = "";
      }
    }

    processLine("event: metadata");
    processLine('data: {"thread_id":"t1"}');
    processLine("event: values");
    processLine('data: {"messages":[]}');
    processLine("event: end");
    processLine("data: {}");

    expect(events.length).toBe(3);
    expect(events[0]!.event).toBe("metadata");
    expect(events[1]!.event).toBe("values");
    expect(events[2]!.event).toBe("end");
  });

  test("non-JSON data is passed as string", () => {
    let _currentEventType = "";
    let result: unknown = null;

    function processLine(line: string) {
      if (line.startsWith("event: ")) {
        _currentEventType = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        const dataStr = line.slice(6);
        try {
          result = JSON.parse(dataStr);
        } catch {
          result = dataStr;
        }
        _currentEventType = "";
      }
    }

    processLine("event: custom");
    processLine("data: not-json");

    expect(result).toBe("not-json");
  });
});
