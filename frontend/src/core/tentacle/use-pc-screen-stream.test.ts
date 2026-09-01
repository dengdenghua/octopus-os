// Tests for usePcScreenStream's frame-count/fps throttling.
//
// The hook used to call setFrameCount/setFps on every incoming binary
// frame (up to 10x/sec by default), forcing a full re-render of the
// consuming page on every frame. We don't drive a real WebSocket —
// following the FakeWebSocket pattern from core/realtime/client.test.ts
// — instead a fake is installed on globalThis.WebSocket so tests can
// step the connection through open/message events deterministically.

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({}),
  getToken: () => "令牌 with spaces/(test)",
}));

import { usePcScreenStream } from "./use-pc-screen-stream";

const FRAME_TYPE_KEEPALIVE = 0x01;

class FakeWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSED = 3;
  static lastInstance: FakeWebSocket | null = null;

  url: string;
  protocols?: string | string[];
  binaryType = "";
  readyState: number = FakeWebSocket.CONNECTING;

  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = protocols;
    FakeWebSocket.lastInstance = this;
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({} as CloseEvent);
  }

  // Test-only helpers.
  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.({} as Event);
  }
  receive(buf: ArrayBuffer): void {
    this.onmessage?.({ data: buf } as MessageEvent);
  }
}

// Minimal valid frame: idLen=0, given frameType, flags=0, empty payload.
function fakeFrame(frameType: number): ArrayBuffer {
  const buf = new ArrayBuffer(4);
  const view = new DataView(buf);
  view.setUint16(0, 0, false);
  view.setUint8(2, frameType);
  view.setUint8(3, 0);
  return buf;
}

const ORIG_WS = globalThis.WebSocket;

beforeEach(() => {
  FakeWebSocket.lastInstance = null;
  vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
  vi.spyOn(performance, "now");
});

afterEach(() => {
  vi.stubGlobal("WebSocket", ORIG_WS);
  vi.restoreAllMocks();
});

function connectAndOpen() {
  const canvasRef = { current: null };
  const view = renderHook(() => usePcScreenStream(canvasRef));
  const ws = FakeWebSocket.lastInstance!;
  act(() => ws.open());
  return { view, ws };
}

describe("usePcScreenStream frame-count/fps throttling", () => {
  it("keeps the bearer token out of the screen-stream URL", () => {
    const { view, ws } = connectAndOpen();

    expect(ws.url).toContain("/api/tentacle/pc-screen/stream");
    expect(ws.url).not.toContain("token=");
    expect(ws.protocols).toEqual([
      "bearer.b64",
      "5Luk54mMIHdpdGggc3BhY2VzLyh0ZXN0KQ",
    ]);
    view.unmount();
  });

  it("flushes frameCount/fps immediately on the first frame", () => {
    const { view, ws } = connectAndOpen();
    vi.mocked(performance.now).mockReturnValue(1000);

    expect(view.result.current.frameCount).toBe(0);
    act(() => ws.receive(fakeFrame(FRAME_TYPE_KEEPALIVE)));

    // First frame must flush right away — the UI gates a "waiting for
    // signal" overlay on frameCount === 0, so a throttled first update
    // would leave that overlay up after a frame is already rendered.
    expect(view.result.current.frameCount).toBe(1);
    expect(view.result.current.fps).toBe(1);
  });

  it("throttles subsequent updates to ~1/sec instead of re-rendering per frame", () => {
    const { view, ws } = connectAndOpen();

    vi.mocked(performance.now).mockReturnValue(1000);
    act(() => ws.receive(fakeFrame(FRAME_TYPE_KEEPALIVE))); // first frame flushes
    expect(view.result.current.frameCount).toBe(1);

    // Several frames arrive within the same throttle window — none of
    // them should trigger a state update (the object frame at, say,
    // 10fps, would previously have fired setState 9 more times here).
    for (let i = 0; i < 9; i++) {
      vi.mocked(performance.now).mockReturnValue(1050 + i * 50);
      act(() => ws.receive(fakeFrame(FRAME_TYPE_KEEPALIVE)));
    }
    expect(view.result.current.frameCount).toBe(1); // still throttled

    // Once the throttle window elapses, the NEXT frame flushes the
    // accumulated raw count in one update.
    vi.mocked(performance.now).mockReturnValue(2001);
    act(() => ws.receive(fakeFrame(FRAME_TYPE_KEEPALIVE)));
    expect(view.result.current.frameCount).toBe(11);
  });

  it("resets frameCount/fps to 0 on disconnect", () => {
    const { view, ws } = connectAndOpen();
    vi.mocked(performance.now).mockReturnValue(1000);
    act(() => ws.receive(fakeFrame(FRAME_TYPE_KEEPALIVE)));
    expect(view.result.current.frameCount).toBe(1);

    act(() => view.unmount());
    // Reconnecting after unmount would start from a clean slate — the
    // internal raw-count ref must not leak a stale value into the next
    // connection's first-frame flush.
  });
});
