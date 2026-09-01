/**
 * Pet IPC client tests — verify emotion / tired / presence events
 * produce the exact UDP payload the Godot sidecar consumes
 * (semantic source: runtime/pet/pet_state_map.py).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { sendMock } = vi.hoisted(() => ({ sendMock: vi.fn() }));

vi.mock("dgram", () => ({
  default: {
    createSocket: () => ({
      send: sendMock,
      unref: vi.fn(),
      close: vi.fn(),
    }),
  },
}));

import { petIPC } from "./pet-ipc";

function drain() {
  vi.advanceTimersByTime(32);
}

function lastPayload() {
  expect(sendMock).toHaveBeenCalled();
  const [buf] = sendMock.mock.calls[0] as [Buffer];
  return JSON.parse(buf.toString()) as Record<string, unknown>;
}

describe("pet-ipc extended events", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    sendMock.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("emotion() sends agent.emotion with clamped intensity", () => {
    petIPC.emotion("happy", 0.8);
    drain();
    expect(lastPayload()).toEqual({ type: "agent.emotion", emotion: "happy", intensity: 0.8 });
  });

  it("emotion() clamps intensity into [0,1]", () => {
    petIPC.emotion("curious", 2.5);
    drain();
    expect(lastPayload().intensity).toBe(1);
    sendMock.mockClear();
    petIPC.emotion("curious", -1);
    drain();
    expect(lastPayload().intensity).toBe(0);
  });

  it("tired() sends agent.tired with default intensity 0.5", () => {
    petIPC.tired();
    drain();
    expect(lastPayload()).toEqual({ type: "agent.tired", intensity: 0.5 });
  });

  it("presence() sends agent.presence with device id", () => {
    petIPC.presence(true, "phone-7");
    drain();
    expect(lastPayload()).toEqual({
      type: "agent.presence",
      online: true,
      device_id: "phone-7",
    });
  });

  it("legacy state helpers still send canonical types", () => {
    petIPC.thinking();
    drain();
    expect(lastPayload()).toEqual({ type: "agent.thinking" });
  });
});
