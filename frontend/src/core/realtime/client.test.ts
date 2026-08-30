// Tests for the RealtimeClient — request/response correlation, server-
// initiated request handling, notification dispatch, outbox buffering
// during reconnect, and reconnect bookkeeping.
//
// We don't drive a real WebSocket. The browser globals stay untouched
// for the rest of the suite; instead we install a FakeWebSocket on
// ``globalThis.WebSocket`` for the duration of each test. The fake
// records every text the client sent and lets the test step the
// connection through ``open``, ``message``, ``close`` events
// deterministically.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RealtimeClient, toWebSocketURL } from "./client";
import type { Envelope } from "./envelope";

class FakeWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSED = 3;
  static lastInstance: FakeWebSocket | null = null;
  static instances: FakeWebSocket[] = [];

  url: string;
  protocols: string[];
  readyState: number = FakeWebSocket.CONNECTING;
  sentRaw: string[] = [];

  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(url: string, protocols: string[] = []) {
    this.url = url;
    this.protocols = protocols;
    FakeWebSocket.lastInstance = this;
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sentRaw.push(data);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code: 1000, reason: "" } as CloseEvent);
  }

  // Test-only helpers.
  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.({} as Event);
  }
  receive(payload: object | string): void {
    const text =
      typeof payload === "string" ? payload : JSON.stringify(payload);
    this.onmessage?.({ data: text } as MessageEvent);
  }
  serverClose(code = 1006, reason = "abnormal"): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code, reason } as CloseEvent);
  }
  parseSent(idx: number): Envelope {
    return JSON.parse(this.sentRaw[idx]!) as Envelope;
  }
}

const ORIG_WS = (globalThis as { WebSocket?: unknown }).WebSocket;

beforeEach(() => {
  FakeWebSocket.lastInstance = null;
  FakeWebSocket.instances = [];
  (globalThis as unknown as { WebSocket: typeof FakeWebSocket }).WebSocket =
    FakeWebSocket;
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  // Drop any per-test own-property override so the jsdom prototype
  // getter (always false) is back in charge.
  Reflect.deleteProperty(document, "hidden");
  if (ORIG_WS === undefined) {
    delete (globalThis as { WebSocket?: unknown }).WebSocket;
  } else {
    (globalThis as { WebSocket?: unknown }).WebSocket = ORIG_WS;
  }
});

// jsdom's ``document.hidden`` is a prototype getter; shadow it with a
// configurable own property so tests can simulate a backgrounded tab.
function setDocumentHidden(hidden: boolean): void {
  Object.defineProperty(document, "hidden", {
    configurable: true,
    get: () => hidden,
  });
}

function makeClient(opts: {
  onIncomingRequest?: (req: any) => Promise<unknown>;
  onNotification?: (n: any) => void;
  onOpen?: () => void;
  onClose?: () => void;
}) {
  return new RealtimeClient({
    url: "ws://test/api/realtime",
    onIncomingRequest: opts.onIncomingRequest ?? (async () => null),
    onNotification: opts.onNotification ?? (() => {}),
    onOpen: opts.onOpen,
    onClose: opts.onClose,
    initialBackoffMs: 10,
    maxBackoffMs: 50,
  });
}

describe("RealtimeClient", () => {
  it("resolves requests against matching responses keyed by id", async () => {
    const client = makeClient({});
    client.connect();
    const ws = FakeWebSocket.lastInstance!;
    ws.open();

    const promise = client.request<{ ok: true }>("turn/start", {
      threadId: "t",
    });
    const sent = ws.parseSent(0);
    expect("id" in sent && sent.id).toBe(1);

    ws.receive({ jsonrpc: "2.0", id: 1, result: { ok: true } });
    await expect(promise).resolves.toEqual({ ok: true });
    client.close();
  });

  it("sends supplied auth tokens through websocket subprotocols", async () => {
    const client = new RealtimeClient({
      url: "ws://test/api/realtime",
      authToken: () => "__guest__",
      onIncomingRequest: async () => null,
      onNotification: () => {},
    });

    client.connect();

    expect(FakeWebSocket.lastInstance!.url).toBe("ws://test/api/realtime");
    expect(FakeWebSocket.lastInstance!.protocols).toEqual([
      "bearer.b64",
      "X19ndWVzdF9f",
    ]);
    client.close();
  });

  it("sends real auth tokens through websocket subprotocols", async () => {
    const client = new RealtimeClient({
      url: "ws://test/api/realtime",
      authToken: () => "sk-alice",
      onIncomingRequest: async () => null,
      onNotification: () => {},
    });

    client.connect();

    expect(FakeWebSocket.lastInstance!.url).toBe("ws://test/api/realtime");
    expect(FakeWebSocket.lastInstance!.protocols).toEqual([
      "bearer.b64",
      "c2stYWxpY2U",
    ]);
    client.close();
  });

  it("keeps unusual UTF-8 auth tokens out of the websocket URL", async () => {
    const client = new RealtimeClient({
      url: "ws://test/api/realtime",
      authToken: () => "令牌 with spaces/(test)",
      onIncomingRequest: async () => null,
      onNotification: () => {},
    });

    client.connect();

    expect(FakeWebSocket.lastInstance!.url).toBe("ws://test/api/realtime");
    expect(FakeWebSocket.lastInstance!.url).not.toContain("token=");
    expect(FakeWebSocket.lastInstance!.protocols[0]).toBe("bearer.b64");
    expect(FakeWebSocket.lastInstance!.protocols[1]).toMatch(
      /^[A-Za-z0-9_-]+$/,
    );
    client.close();
  });

  it("rejects requests with the server-supplied error", async () => {
    const client = makeClient({});
    client.connect();
    FakeWebSocket.lastInstance!.open();

    const promise = client.request("thread/archive", { threadId: "missing" });
    FakeWebSocket.lastInstance!.receive({
      jsonrpc: "2.0",
      id: 1,
      error: { code: -32010, message: "unknown thread missing" },
    });
    await expect(promise).rejects.toMatchObject({ code: -32010 });
    client.close();
  });

  it("dispatches notifications to onNotification untouched", async () => {
    const onNotification = vi.fn();
    const client = makeClient({ onNotification });
    client.connect();
    FakeWebSocket.lastInstance!.open();

    FakeWebSocket.lastInstance!.receive({
      jsonrpc: "2.0",
      method: "item/agentMessage/delta",
      params: { itemId: "x", delta: "hi" },
    });

    // Delta notifications are batched through requestAnimationFrame to
    // keep React re-renders capped at ~60fps during high-rate streaming.
    // Wait a frame so the buffered notification lands on the callback.
    await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));

    expect(onNotification).toHaveBeenCalledTimes(1);
    expect(onNotification.mock.calls[0]![0].method).toBe(
      "item/agentMessage/delta",
    );
    client.close();
  });

  it("coalesces adjacent deltas for the same streamed item in one frame", async () => {
    const onNotification = vi.fn();
    const client = makeClient({ onNotification });
    client.connect();
    const ws = FakeWebSocket.lastInstance!;
    ws.open();

    ws.receive({
      jsonrpc: "2.0",
      method: "item/agentMessage/delta",
      params: { threadId: "t", turnId: "turn", itemId: "a", delta: "hello" },
    });
    ws.receive({
      jsonrpc: "2.0",
      method: "item/agentMessage/delta",
      params: { threadId: "t", turnId: "turn", itemId: "a", delta: " world" },
    });
    ws.receive({
      jsonrpc: "2.0",
      method: "item/agentMessage/delta",
      params: { threadId: "t", turnId: "turn", itemId: "b", delta: "other" },
    });

    await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));

    expect(onNotification).toHaveBeenCalledTimes(2);
    expect(onNotification.mock.calls[0]![0]).toMatchObject({
      method: "item/agentMessage/delta",
      params: { itemId: "a", delta: "hello world" },
    });
    expect(onNotification.mock.calls[1]![0]).toMatchObject({
      method: "item/agentMessage/delta",
      params: { itemId: "b", delta: "other" },
    });
    client.close();
  });

  it("preserves item/started → deltas → item/completed order in one frame", async () => {
    // Regression guard: ``item/started`` and ``item/completed`` used to
    // bypass the RAF buffer entirely while deltas waited for the next
    // frame. That meant ``completed`` raced ahead of the deltas, the
    // reducer's status guard then dropped the deltas as stale, and the
    // user saw an empty bubble. Now all three ride the same buffer
    // and flush in arrival order on the next animation frame.
    const onNotification = vi.fn();
    const client = makeClient({ onNotification });
    client.connect();
    const ws = FakeWebSocket.lastInstance!;
    ws.open();

    ws.receive({
      jsonrpc: "2.0",
      method: "item/started",
      params: {
        threadId: "t",
        turnId: "turn",
        item: { id: "x", type: "agentMessage", text: "", status: "inProgress" },
      },
    });
    ws.receive({
      jsonrpc: "2.0",
      method: "item/agentMessage/delta",
      params: { threadId: "t", turnId: "turn", itemId: "x", delta: "hello" },
    });
    ws.receive({
      jsonrpc: "2.0",
      method: "item/agentMessage/delta",
      params: { threadId: "t", turnId: "turn", itemId: "x", delta: " world" },
    });
    ws.receive({
      jsonrpc: "2.0",
      method: "item/completed",
      params: {
        threadId: "t",
        turnId: "turn",
        item: {
          id: "x",
          type: "agentMessage",
          text: "hello world",
          status: "completed",
        },
      },
    });

    await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));

    // Two adjacent same-item deltas coalesce; started + completed
    // ride alongside without merging. Total 3 callbacks.
    expect(onNotification).toHaveBeenCalledTimes(3);
    const methods = onNotification.mock.calls.map((c) => c[0].method);
    expect(methods).toEqual([
      "item/started",
      "item/agentMessage/delta",
      "item/completed",
    ]);
    client.close();
  });

  it("flushes batched events via setTimeout, in order, while the tab is hidden", async () => {
    // Regression guard: with the buffer pinned to requestAnimationFrame
    // only, a hidden tab never flushed — the buffer grew without bound
    // and immediately-dispatched methods (turn/interrupted) overtook
    // the buffered item/started, so the reducer marked the turn
    // interrupted before the item existed and the late-flushed item
    // spun forever. Hidden tabs must fall back to a macrotask.
    const rafSpy = vi
      .spyOn(globalThis, "requestAnimationFrame")
      .mockImplementation(() => 0);
    setDocumentHidden(true);
    const onNotification = vi.fn();
    const client = makeClient({ onNotification });
    client.connect();
    const ws = FakeWebSocket.lastInstance!;
    ws.open();

    ws.receive({
      jsonrpc: "2.0",
      method: "item/started",
      params: {
        threadId: "t",
        turnId: "turn",
        item: { id: "x", type: "agentMessage", text: "", status: "inProgress" },
      },
    });
    ws.receive({
      jsonrpc: "2.0",
      method: "item/agentMessage/delta",
      params: { threadId: "t", turnId: "turn", itemId: "x", delta: "hi" },
    });
    // Buffered, not dispatched synchronously.
    expect(onNotification).not.toHaveBeenCalled();

    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(rafSpy).not.toHaveBeenCalled();
    expect(onNotification.mock.calls.map((c) => c[0].method)).toEqual([
      "item/started",
      "item/agentMessage/delta",
    ]);
    client.close();
  });

  it("flushes the buffer synchronously the moment the tab goes hidden", () => {
    // The rAF scheduled while the tab was visible will never fire once
    // it is hidden. Mock rAF to never invoke its callback so only the
    // visibilitychange path can deliver the buffered events.
    vi.spyOn(globalThis, "requestAnimationFrame").mockImplementation(() => 0);
    const onNotification = vi.fn();
    const client = makeClient({ onNotification });
    client.connect();
    const ws = FakeWebSocket.lastInstance!;
    ws.open();

    ws.receive({
      jsonrpc: "2.0",
      method: "item/started",
      params: {
        threadId: "t",
        turnId: "turn",
        item: { id: "x", type: "agentMessage", text: "", status: "inProgress" },
      },
    });
    expect(onNotification).not.toHaveBeenCalled();

    setDocumentHidden(true);
    document.dispatchEvent(new Event("visibilitychange"));

    // Synchronous flush: the buffered item/started must land before any
    // immediately-dispatched method that follows (turn/interrupted).
    expect(onNotification).toHaveBeenCalledTimes(1);
    ws.receive({
      jsonrpc: "2.0",
      method: "turn/interrupted",
      params: { threadId: "t", turnId: "turn" },
    });
    expect(onNotification.mock.calls.map((c) => c[0].method)).toEqual([
      "item/started",
      "turn/interrupted",
    ]);

    const removeSpy = vi.spyOn(document, "removeEventListener");
    client.close();
    expect(removeSpy).toHaveBeenCalledWith(
      "visibilitychange",
      expect.any(Function),
    );
  });

  it("keeps item/fileChange/hunkDelta ordered behind item/started without merging", async () => {
    // hunkDelta used to bypass the buffer: in the same frame it raced
    // ahead of the still-buffered item/started and the reducer dropped
    // it (unknown item). It must ride the buffer for ordering, but the
    // structured hunk payloads must never coalesce like text deltas.
    const onNotification = vi.fn();
    const client = makeClient({ onNotification });
    client.connect();
    const ws = FakeWebSocket.lastInstance!;
    ws.open();

    ws.receive({
      jsonrpc: "2.0",
      method: "item/started",
      params: {
        threadId: "t",
        turnId: "turn",
        item: { id: "fc", type: "fileChange", status: "inProgress" },
      },
    });
    ws.receive({
      jsonrpc: "2.0",
      method: "item/fileChange/hunkDelta",
      params: {
        threadId: "t",
        turnId: "turn",
        itemId: "fc",
        path: "a.ts",
        op: "update",
        hunk: { header: "@@ -1 +1 @@" },
      },
    });
    ws.receive({
      jsonrpc: "2.0",
      method: "item/fileChange/hunkDelta",
      params: {
        threadId: "t",
        turnId: "turn",
        itemId: "fc",
        path: "a.ts",
        op: "update",
        hunk: { header: "@@ -5 +5 @@" },
      },
    });

    await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));

    expect(onNotification).toHaveBeenCalledTimes(3);
    expect(onNotification.mock.calls.map((c) => c[0].method)).toEqual([
      "item/started",
      "item/fileChange/hunkDelta",
      "item/fileChange/hunkDelta",
    ]);
    // Each hunk arrives intact — no text-delta-style merge.
    expect(onNotification.mock.calls[1]![0].params.hunk.header).toBe(
      "@@ -1 +1 @@",
    );
    expect(onNotification.mock.calls[2]![0].params.hunk.header).toBe(
      "@@ -5 +5 @@",
    );
    client.close();
  });

  it("flushes buffered items before a non-batched method in the same frame", () => {
    // Ordering invariant: within the buffering window (one frame in the
    // foreground) an immediately-dispatched method (turn/interrupted,
    // turn/completed, error, workbench/snapshot) used to overtake the
    // still-buffered item/started — the reducer marked the turn done
    // before the item existed and the late-flushed item spun forever.
    // The dispatch path must now flush the buffer synchronously before
    // delivering any non-batched notification, so the reducer sees
    // strict WebSocket arrival order. Mock rAF to never fire so only
    // the synchronous flush can deliver the buffered event.
    vi.spyOn(globalThis, "requestAnimationFrame").mockImplementation(() => 0);
    const onNotification = vi.fn();
    const client = makeClient({ onNotification });
    client.connect();
    const ws = FakeWebSocket.lastInstance!;
    ws.open();

    ws.receive({
      jsonrpc: "2.0",
      method: "item/started",
      params: {
        threadId: "t",
        turnId: "turn",
        item: { id: "x", type: "agentMessage", text: "", status: "inProgress" },
      },
    });
    expect(onNotification).not.toHaveBeenCalled();

    ws.receive({
      jsonrpc: "2.0",
      method: "turn/interrupted",
      params: { threadId: "t", turnId: "turn" },
    });

    expect(onNotification.mock.calls.map((c) => c[0].method)).toEqual([
      "item/started",
      "turn/interrupted",
    ]);
    client.close();
  });

  it("does not re-dispatch from the scheduled rAF after a synchronous flush", () => {
    // The synchronous flush triggered by a non-batched method races the
    // rAF callback that was scheduled when the batched event arrived.
    // flushDeltaBuffer clears deltaFlushPending and drains the buffer,
    // so the late rAF callback must see an empty buffer and return
    // without duplicating anything.
    const rafCallbacks: FrameRequestCallback[] = [];
    vi.spyOn(globalThis, "requestAnimationFrame").mockImplementation((cb) => {
      rafCallbacks.push(cb);
      return rafCallbacks.length;
    });
    const onNotification = vi.fn();
    const client = makeClient({ onNotification });
    client.connect();
    const ws = FakeWebSocket.lastInstance!;
    ws.open();

    ws.receive({
      jsonrpc: "2.0",
      method: "item/started",
      params: {
        threadId: "t",
        turnId: "turn",
        item: { id: "x", type: "agentMessage", text: "", status: "inProgress" },
      },
    });
    expect(rafCallbacks.length).toBe(1);

    // Non-batched method forces the synchronous flush.
    ws.receive({
      jsonrpc: "2.0",
      method: "turn/interrupted",
      params: { threadId: "t", turnId: "turn" },
    });
    expect(onNotification).toHaveBeenCalledTimes(2);

    // Now the previously scheduled rAF fires — it must be a no-op.
    for (const cb of rafCallbacks.splice(0)) cb(0);
    expect(onNotification).toHaveBeenCalledTimes(2);
    expect(onNotification.mock.calls.map((c) => c[0].method)).toEqual([
      "item/started",
      "turn/interrupted",
    ]);
    client.close();
  });

  it("answers server-initiated requests with the handler's resolved value", async () => {
    const onIncomingRequest = vi.fn(async () => ({ action: "accept" }));
    const client = makeClient({ onIncomingRequest });
    client.connect();
    const ws = FakeWebSocket.lastInstance!;
    ws.open();

    ws.receive({
      jsonrpc: "2.0",
      id: 99,
      method: "item/commandExecution/requestApproval",
      params: { tool: "ls" },
    });

    await Promise.resolve();
    await Promise.resolve();

    expect(onIncomingRequest).toHaveBeenCalledTimes(1);
    expect(ws.sentRaw.length).toBe(1);
    const reply = ws.parseSent(0);
    expect(reply).toEqual({
      jsonrpc: "2.0",
      id: 99,
      result: { action: "accept" },
    });
    client.close();
  });

  it("translates a thrown handler into a JSON-RPC error response", async () => {
    const onIncomingRequest = vi.fn(async () => {
      throw new Error("user denied");
    });
    const client = makeClient({ onIncomingRequest });
    client.connect();
    const ws = FakeWebSocket.lastInstance!;
    ws.open();

    ws.receive({
      jsonrpc: "2.0",
      id: 7,
      method: "item/commandExecution/requestApproval",
      params: {},
    });
    await Promise.resolve();
    await Promise.resolve();

    const reply = ws.parseSent(0) as Extract<Envelope, { error?: unknown }>;
    expect(reply.id).toBe(7);
    expect("error" in reply).toBe(true);
    if ("error" in reply && reply.error) {
      expect(reply.error.message).toBe("user denied");
    }
    client.close();
  });

  it("buffers sends when the socket is not yet open and flushes on connect", async () => {
    const client = makeClient({});
    client.notify("client/say", { text: "early" });

    expect(FakeWebSocket.lastInstance).toBeNull();

    client.connect();
    const ws = FakeWebSocket.lastInstance!;
    expect(ws.sentRaw.length).toBe(0);

    ws.open();
    expect(ws.sentRaw.length).toBe(1);
    expect(ws.parseSent(0)).toMatchObject({ method: "client/say" });
    client.close();
  });

  it("fires onOpen only when the socket actually opens, not on connect()", async () => {
    // Regression guard: hooks used to setConnected(true) the moment
    // ``client.connect()`` returned, which is BEFORE the WebSocket
    // handshake completes. Drive the connected flag from onOpen
    // instead so the UI reflects real socket state.
    const onOpen = vi.fn();
    const client = makeClient({ onOpen });
    client.connect();
    expect(onOpen).not.toHaveBeenCalled();
    FakeWebSocket.lastInstance!.open();
    expect(onOpen).toHaveBeenCalledTimes(1);
    // onOpen fires again after a reconnect.
    FakeWebSocket.lastInstance!.serverClose(1006, "abnormal");
    await new Promise((r) => setTimeout(r, 60));
    FakeWebSocket.lastInstance!.open();
    expect(onOpen).toHaveBeenCalledTimes(2);
    client.close();
  });

  it("fails pending requests when the connection closes before a reply", async () => {
    const client = makeClient({});
    client.connect();
    FakeWebSocket.lastInstance!.open();

    const promise = client.request("turn/start", {});
    FakeWebSocket.lastInstance!.serverClose();

    await expect(promise).rejects.toThrow(/websocket closed/);
    client.close();
  });

  it("reconnects after an abnormal close", async () => {
    vi.useFakeTimers();
    const client = makeClient({});
    client.connect();
    const first = FakeWebSocket.lastInstance!;
    first.open();
    expect(FakeWebSocket.instances.length).toBe(1);

    first.serverClose(1006, "remote");
    await vi.advanceTimersByTimeAsync(200);

    expect(FakeWebSocket.instances.length).toBeGreaterThanOrEqual(2);
    const second = FakeWebSocket.lastInstance!;
    expect(second).not.toBe(first);
    client.close();
  });

  it("close() prevents further reconnects", async () => {
    vi.useFakeTimers();
    const client = makeClient({});
    client.connect();
    const first = FakeWebSocket.lastInstance!;
    first.open();

    client.close();
    await vi.advanceTimersByTimeAsync(500);
    expect(FakeWebSocket.instances.length).toBe(1);
  });

  it("rejects new requests after close instead of retaining an unflushable outbox", async () => {
    const client = makeClient({});
    client.connect();
    const socket = FakeWebSocket.lastInstance!;
    socket.open();

    client.close();
    client.notify("client/say", { text: "late" });

    await expect(client.request("thread/resume", {})).rejects.toThrow(
      "client closed",
    );
    expect(socket.sentRaw).toHaveLength(0);
  });

  it("does NOT replay buffered requests after a reconnect (P0-5 outbox invariant)", async () => {
    // Regression guard: a turn/start (or any other request) issued
    // while the socket was closed used to live in the outbox AND
    // the pending map. failPending rejected the Promise on close,
    // but the buffered envelope was still flushed on the next open
    // — so the server saw a duplicate request that no caller was
    // listening for. Disconnect now clears the outbox; callers are
    // expected to re-issue requests after a reconnect.
    vi.useFakeTimers();
    const client = makeClient({});
    client.connect();
    const first = FakeWebSocket.lastInstance!;
    first.open();

    // Issue a request while online — this is normal fast-path.
    const livePromise = client.request("turn/start", { input: "hello" });
    expect(first.sentRaw.length).toBe(1);

    // Server drops the connection mid-flight.
    first.serverClose(1006, "remote");
    // The pending Promise must reject so callers don't hang.
    await expect(livePromise).rejects.toThrow(/closed/);

    // Now reconnect.
    await vi.advanceTimersByTimeAsync(200);
    const second = FakeWebSocket.lastInstance!;
    expect(second).not.toBe(first);
    second.open();

    // The outbox must NOT have replayed the original request.
    // Otherwise the server would start a duplicate turn/start with
    // no client listening for the response.
    expect(second.sentRaw.length).toBe(0);

    client.close();
  });

  it("ignores responses without a matching pending request", async () => {
    const onNotification = vi.fn();
    const client = makeClient({ onNotification });
    client.connect();
    FakeWebSocket.lastInstance!.open();

    expect(() => {
      FakeWebSocket.lastInstance!.receive({
        jsonrpc: "2.0",
        id: 999,
        result: { ok: true },
      });
    }).not.toThrow();
    expect(onNotification).not.toHaveBeenCalled();
    client.close();
  });
});

describe("toWebSocketURL", () => {
  function withWindowLocation(
    location: { protocol: string; host: string } | undefined,
    assertion: () => void,
  ) {
    try {
      vi.stubGlobal("window", location ? { location } : undefined);
      assertion();
    } finally {
      vi.unstubAllGlobals();
    }
  }

  it("uses the current browser origin for same-origin backend URLs", () => {
    withWindowLocation({ protocol: "http:", host: "localhost:3000" }, () => {
      expect(toWebSocketURL("", "/api/realtime")).toBe(
        "ws://localhost:3000/api/realtime",
      );
    });
  });

  it("keeps the active loopback host instead of forcing localhost", () => {
    withWindowLocation({ protocol: "http:", host: "127.0.0.1:3000" }, () => {
      expect(toWebSocketURL("", "/api/realtime")).toBe(
        "ws://127.0.0.1:3000/api/realtime",
      );
    });
  });

  it("converts explicit backend origins to websocket origins", () => {
    expect(toWebSocketURL("http://127.0.0.1:8100", "/api/realtime")).toBe(
      "ws://127.0.0.1:8100/api/realtime",
    );
    expect(toWebSocketURL("https://example.com/base", "/api/realtime")).toBe(
      "wss://example.com/base/api/realtime",
    );
  });

  it("returns a relative path outside the browser when no base URL is known", () => {
    withWindowLocation(undefined, () => {
      expect(toWebSocketURL("", "/api/realtime")).toBe("/api/realtime");
    });
  });
});
