/**
 * Custom hook for managing screen-stream WebSocket connection.
 *
 * Connects to `WS /api/tentacle/screen/stream`, subscribes to a specific
 * tentacle device, and renders received binary frames onto a <canvas>.
 *
 * Supported frame types:
 *   0x01 = H.264 (requires WebCodecs — falls back to JPEG mode otherwise)
 *   0x02 = JPEG
 *   0x03 = WebP
 *
 * Binary frame format:
 *   byte 0-1   : tentacle_id length (uint16 BE)
 *   byte 2     : frame type (0x01 | 0x02 | 0x03)
 *   byte 3     : flags (bit 0 = keyframe)
 *   byte 4..   : tentacle_id (UTF-8)
 *   byte 4+len : frame payload
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { getToken } from "@/core/auth/api";
import { openAuthenticatedWebSocket } from "@/core/auth/websocket";
import { getBackendWebSocketBaseURL } from "@/core/config";

// ── Types ──────────────────────────────────────────────

export interface ScreenStreamState {
  isConnected: boolean;
  frameCount: number;
  error: string | null;
  fps: number;
}

// ── Frame header constants ─────────────────────────────

const FRAME_TYPE_H264 = 0x01;
const FRAME_TYPE_JPEG = 0x02;
const FRAME_TYPE_WEBP = 0x03;
const FLAG_KEYFRAME = 0x01;

// ── Helpers ────────────────────────────────────────────

function buildWsUrl(): string {
  return `${getBackendWebSocketBaseURL()}/api/tentacle/screen/stream`;
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
  const idLen = view.getUint16(0, false); // big-endian
  if (buf.byteLength < 4 + idLen) return null;
  const frameType = view.getUint8(2);
  const flags = view.getUint8(3);
  const decoder = new TextDecoder();
  const tentacleId = decoder.decode(new Uint8Array(buf, 4, idLen));
  const payload = buf.slice(4 + idLen);
  return { idLen, frameType, flags, tentacleId, payload };
}

// ── Hook ───────────────────────────────────────────────

export function useScreenStream(
  tentacleId: string | null,
  canvasRef: React.RefObject<HTMLCanvasElement | null>,
): ScreenStreamState {
  const [isConnected, setIsConnected] = useState(false);
  const [frameCount, setFrameCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [fps, setFps] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const fpsFramesRef = useRef<number[]>([]);
  const h264DecoderRef = useRef<VideoDecoder | null>(null);
  const h264KeyNeededRef = useRef(true);

  // ── H.264 VideoDecoder setup (WebCodecs) ─────────────

  const initH264Decoder = useCallback(() => {
    if (typeof VideoDecoder === "undefined") return null;
    try {
      const decoder = new VideoDecoder({
        output: (frame: VideoFrame) => {
          const canvas = canvasRef.current;
          if (canvas) {
            const ctx = canvas.getContext("2d");
            if (ctx) {
              if (
                canvas.width !== frame.displayWidth ||
                canvas.height !== frame.displayHeight
              ) {
                canvas.width = frame.displayWidth;
                canvas.height = frame.displayHeight;
              }
              ctx.drawImage(frame, 0, 0);
            }
          }
          frame.close();
        },
        error: () => {
          // Silently ignore decode errors — next keyframe will recover
        },
      });
      decoder.configure({
        codec: "avc1.42E01E",
        optimizeForLatency: true,
      });
      return decoder;
    } catch {
      return null;
    }
  }, [canvasRef]);

  // ── Render JPEG / WebP frame ────────────────────────

  const renderImageFrame = useCallback(
    (payload: ArrayBuffer, frameType: number) => {
      // Revoke previous object URL to prevent memory leak
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

      const { frameType, flags, payload } = parsed;

      if (frameType === FRAME_TYPE_H264) {
        const decoder = h264DecoderRef.current;
        if (decoder && decoder.state !== "closed") {
          const isKeyframe = (flags & FLAG_KEYFRAME) !== 0;
          if (isKeyframe) h264KeyNeededRef.current = false;
          if (h264KeyNeededRef.current) return; // skip until keyframe

          const chunk = new EncodedVideoChunk({
            type: isKeyframe ? "key" : "delta",
            timestamp: performance.now() * 1000, // microseconds
            data: payload,
          });
          try {
            decoder.decode(chunk);
          } catch {
            // If decode fails, wait for next keyframe
            h264KeyNeededRef.current = true;
          }
        }
        // If no WebCodecs support, H.264 frames are silently dropped
      } else if (
        frameType === FRAME_TYPE_JPEG ||
        frameType === FRAME_TYPE_WEBP
      ) {
        renderImageFrame(payload, frameType);
      }

      // Update frame counter and FPS
      setFrameCount((c) => c + 1);

      const now = performance.now();
      fpsFramesRef.current.push(now);
      // Keep only frames from the last second
      fpsFramesRef.current = fpsFramesRef.current.filter((t) => now - t < 1000);
      setFps(fpsFramesRef.current.length);
    },
    [renderImageFrame],
  );

  // ── Connect / disconnect ────────────────────────────

  const connect = useCallback(() => {
    // Clean up existing connection
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    const ws = openAuthenticatedWebSocket(buildWsUrl(), getToken());
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      setError(null);
      // Subscribe if we have a tentacleId
      if (tentacleId) {
        ws.send(
          JSON.stringify({ action: "subscribe", tentacle_id: tentacleId }),
        );
      }
    };

    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) {
        handleMessage(ev.data);
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      // Auto-reconnect after 3s
      reconnectTimerRef.current = setTimeout(() => {
        connect();
      }, 3000);
    };

    ws.onerror = () => {
      setError("WebSocket connection error");
      ws.close();
    };
  }, [tentacleId, handleMessage]);

  // ── Lifecycle ───────────────────────────────────────

  useEffect(() => {
    // Initialize H.264 decoder if available
    h264DecoderRef.current = initH264Decoder();
    h264KeyNeededRef.current = true;

    connect();

    return () => {
      // Cleanup on unmount
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (h264DecoderRef.current) {
        h264DecoderRef.current.close();
        h264DecoderRef.current = null;
      }
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
      setIsConnected(false);
      setFrameCount(0);
      setFps(0);
      setError(null);
    };
  }, [connect, initH264Decoder]);

  // ── Subscribe / unsubscribe when tentacleId changes ──

  useEffect(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    // Unsubscribe from previous (we send unsubscribe regardless —
    // the server will ignore if there was no subscription)
    ws.send(JSON.stringify({ action: "unsubscribe" }));

    // Subscribe to new device
    if (tentacleId) {
      ws.send(JSON.stringify({ action: "subscribe", tentacle_id: tentacleId }));
    }

    // Reset counters
    setFrameCount(0);
    setFps(0);
    fpsFramesRef.current = [];
    h264KeyNeededRef.current = true;
  }, [tentacleId]);

  return { isConnected, frameCount, error, fps };
}
