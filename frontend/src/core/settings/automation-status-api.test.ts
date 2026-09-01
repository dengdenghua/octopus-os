import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer 令牌 with spaces/(test)" }),
}));

import {
  subscribeBrowserRelayStatus,
  type BrowserRelayStatus,
} from "./automation-status-api";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readonly protocols?: string | string[];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = protocols;
    FakeWebSocket.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  publish(status: BrowserRelayStatus) {
    this.onmessage?.(
      new MessageEvent("message", {
        data: JSON.stringify({ type: "browser_relay_status", status }),
      }),
    );
  }
}

describe("automation status stream", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    FakeWebSocket.instances = [];
  });

  it("subscribes to relay status and disposes cleanly", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const onStatus = vi.fn();

    const unsubscribe = subscribeBrowserRelayStatus(onStatus);
    const socket = FakeWebSocket.instances[0];
    expect(socket?.url).toContain("/api/browser/relay/status/ws");
    expect(socket?.url).not.toContain("token=");
    expect(socket?.protocols).toEqual([
      "bearer.b64",
      "5Luk54mMIHdpdGggc3BhY2VzLyh0ZXN0KQ",
    ]);

    const status: BrowserRelayStatus = {
      connected: true,
      connection_state: "online",
      extension_version: "0.2.0",
      push_connected: true,
      last_seen: 42,
      manifest_exists: true,
      extension_path: "/extension",
    };
    socket?.publish(status);
    expect(onStatus).toHaveBeenCalledWith(status);

    unsubscribe();
    expect(socket?.closed).toBe(true);
  });

  it("reconnects a dropped status stream and stops after disposal", () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeWebSocket);

    const unsubscribe = subscribeBrowserRelayStatus(vi.fn());
    const first = FakeWebSocket.instances[0];
    first?.onclose?.();
    expect(FakeWebSocket.instances).toHaveLength(1);

    vi.advanceTimersByTime(500);
    expect(FakeWebSocket.instances).toHaveLength(2);
    const second = FakeWebSocket.instances[1];

    unsubscribe();
    expect(second?.closed).toBe(true);
    vi.advanceTimersByTime(10_000);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });
});
