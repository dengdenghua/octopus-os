/**
 * Custom hook for managing PC screen-stream WebSocket connection.
 *
 * Connects to `WS /api/tentacle/pc-screen/stream`, receives binary frames
 * of the PC desktop, and renders them onto a <canvas>.
 *
 * Also supports sending remote input events (tap, swipe, type) back to
 * the PC via `POST /api/tentacle/remote-input`.
 *
 * Binary frame format is the same as device screen stream:
 *   byte 0-1   : tentacle_id length (uint16 BE)
 *   byte 2     : frame type (0x01 | 0x02 | 0x03)
 *   byte 3     : flags (bit 0 = keyframe)
 *   byte 4..   : tentacle_id (UTF-8)
 *   byte 4+len : frame payload
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { authHeaders, getToken } from "@/core/auth/api";
import { openAuthenticatedWebSocket } from "@/core/auth/websocket";
import { getBackendBaseURL, getBackendWebSocketBaseURL } from "@/core/config";

// ── Types ──────────────────────────────────────────────

export interface PcScreenStreamState {
  isConnected: boolean;
  frameCount: number;
  error: string | null;
  fps: number;
}

export interface RemoteInputEvent {
  action:
    | "tap"
    | "double_tap"
    | "long_press"
    | "swipe"
    | "type_text"
    | "key_press";
  x?: number;
  y?: number;
  x2?: number;
  y2?: number;
  duration_ms?: number;
  text?: string;
  key?: string;
}

// ── Frame header constants ─────────────────────────────

const FRAME_TYPE_JPEG = 0x02;
const FRAME_TYPE_WEBP = 0x03;

// ── Helpers ────────────────────────────────────────────

function buildPcWsUrl(): string {
  return `${getBackendWebSocketBaseURL()}/api/tentacle/pc-screen/stream`;
}

function parseFrameHeader(buf: ArrayBuffer): {
  idLen: number;
  frameType: number;
  flags: number;
  tentacleId: string;
  payload: ArrayBuffer;
} | null {
  if (buf.byteLength < 4) return null;
  const view = new DataView(buf);
  const idLen = view.getUint16(0, false);
  if (buf.byteLength < 4 + idLen) return null;
  const frameType = view.getUint8(2);
  const flags = view.getUint8(3);
  const decoder = new TextDecoder();
  const tentacleId = decoder.decode(new Uint8Array(buf, 4, idLen));
  const payload = buf.slice(4 + idLen);
  return { idLen, frameType, flags, tentacleId, payload };
}

// ── Hook ───────────────────────────────────────────────

export function usePcScreenStream(
  canvasRef: React.RefObject<HTMLCanvasElement | null>,
  options: { enabled?: boolean } = {},
): PcScreenStreamState & {
  sendInput: (event: RemoteInputEvent) => Promise<void>;
} {
  const enabled = options.enabled ?? true;
  const [isConnected, setIsConnected] = useState(false);
  const [frameCount, setFrameCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [fps, setFps] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const fpsFramesRef = useRef<number[]>([]);
  const shouldReconnectRef = useRef(false);
  // Raw per-frame counter, always accurate — the `frameCount`/`fps`
  // STATE below is only a display readout, so it's throttled to ~1Hz
  // (see handleMessage) rather than triggering a re-render on every
  // incoming stream frame (up to 10x/sec by default).
  const rawFrameCountRef = useRef(0);
  const lastStatsFlushRef = useRef(0);
  const STATS_FLUSH_INTERVAL_MS = 1000;

  // ── Render JPEG / WebP frame ────────────────────────

  const renderImageFrame = useCallback(
    (payload: ArrayBuffer, frameType: number) => {
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }

      const mime = frameType === FRAME_TYPE_WEBP ? "image/webp" : "image/jpeg";
      const blob = new Blob([payload], { type: mime });
      const url = URL.createObjectURL(blob);
      objectUrlRef.current = url;

      const img = new Image();
      img.onload = () => {
        const canvas = canvasRef.current;
        if (canvas) {
          const ctx = canvas.getContext("2d");
          if (ctx) {
            if (
              canvas.width !== img.naturalWidth ||
              canvas.height !== img.naturalHeight
            ) {
              canvas.width = img.naturalWidth;
              canvas.height = img.naturalHeight;
            }
            ctx.drawImage(img, 0, 0);
          }
        }
      };
      img.src = url;
    },
    [canvasRef],
  );

  // ── Handle incoming binary message ──────────────────

  const handleMessage = useCallback(
    (data: ArrayBuffer) => {
      const parsed = parseFrameHeader(data);
      if (!parsed) return;

      const { frameType, payload } = parsed;

      if (frameType === FRAME_TYPE_JPEG || frameType === FRAME_TYPE_WEBP) {
        renderImageFrame(payload, frameType);
      }

      const isFirstFrame = rawFrameCountRef.current === 0;
      rawFrameCountRef.current += 1;

      const now = performance.now();
      fpsFramesRef.current.push(now);
      fpsFramesRef.current = fpsFramesRef.current.filter((t) => now - t < 1000);

      // Flush immediately on the very first frame — the UI gates a
      // "waiting for signal" overlay on `frameCount === 0`, and
      // throttling that transition would leave the overlay covering
      // an already-rendered frame for up to a second.
      if (
        isFirstFrame ||
        now - lastStatsFlushRef.current >= STATS_FLUSH_INTERVAL_MS
      ) {
        lastStatsFlushRef.current = now;
        setFrameCount(rawFrameCountRef.current);
        setFps(fpsFramesRef.current.length);
      }
    },
    [renderImageFrame],
  );

  // ── Send remote input event ────────────────────────

  const sendInput = useCallback(async (event: RemoteInputEvent) => {
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/tentacle/remote-input`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify(event),
        },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        console.error("Remote input failed:", body.detail || res.statusText);
      }
    } catch (e) {
      console.error("Remote input error:", e);
    }
  }, []);

  // ── Connect / disconnect ────────────────────────────

  const disconnect = useCallback(() => {
    shouldReconnectRef.current = false;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (wsRef.current) {
      const ws = wsRef.current;
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      ws.onopen = null;
      ws.close();
      wsRef.current = null;
    }
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
    fpsFramesRef.current = [];
    rawFrameCountRef.current = 0;
    lastStatsFlushRef.current = 0;
    setIsConnected(false);
    setFrameCount(0);
    setFps(0);
    setError(null);
  }, []);

  const connect = useCallback(() => {
    if (!enabled) return;
    shouldReconnectRef.current = true;
    if (wsRef.current) {
      const previous = wsRef.current;
      previous.onclose = null;
      previous.onerror = null;
      previous.onmessage = null;
      previous.onopen = null;
      previous.close();
      wsRef.current = null;
    }

    const ws = openAuthenticatedWebSocket(buildPcWsUrl(), getToken());
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      setError(null);
    };

    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) {
        handleMessage(ev.data);
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      if (!shouldReconnectRef.current) return;
      // Auto-reconnect after 3s
      reconnectTimerRef.current = setTimeout(() => {
        connect();
      }, 3000);
    };

    ws.onerror = () => {
      setError("PC screen WebSocket connection error");
      ws.close();
    };
  }, [enabled, handleMessage]);

  // ── Lifecycle ───────────────────────────────────────

  useEffect(() => {
    if (!enabled) {
      disconnect();
      return;
    }
    connect();

    return disconnect;
  }, [connect, disconnect, enabled]);

  return { isConnected, frameCount, error, fps, sendInput };
}
