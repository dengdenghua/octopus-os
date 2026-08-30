/**
 * Subscribe to the typed sub-agent event bus for one coordination root.
 *
 * Opens the `/api/subagents/stream/{root}` SSE and normalizes each frame into
 * `LiveToolEvent[]` for the AgentWorkbench panel. Handles reconnect + resume
 * by sending the last-seen `seq` as `after_seq` on every (re)connect, so a
 * dropped connection backfills instead of losing history.
 */
import { useEffect, useRef, useState } from "react";

import { getBackendBaseURL } from "@/core/config";
import { openSseStream } from "@/core/streaming/sse";
import { swallow } from "@/core/utils/log";
import type { LiveToolEvent } from "@/components/workspace/live-tool-timeline";
import {
  busEventToLiveEvent,
  type SubAgentBusEvent,
} from "./subagent-bus-events";

export type SubAgentBusStatus = "idle" | "connecting" | "live" | "error";

export interface UseSubAgentBusStreamResult {
  events: LiveToolEvent[];
  status: SubAgentBusStatus;
  lastSeq: number;
}

export function useSubAgentBusStream(
  rootThreadId: string | null | undefined,
): UseSubAgentBusStreamResult {
  const [events, setEvents] = useState<LiveToolEvent[]>([]);
  const [status, setStatus] = useState<SubAgentBusStatus>("idle");
  const [lastSeq, setLastSeq] = useState(0);
  const cursorRef = useRef(0);

  useEffect(() => {
    if (!rootThreadId) {
      setEvents([]);
      setStatus("idle");
      return;
    }
    setEvents([]);
    cursorRef.current = 0;
    setStatus("connecting");

    const base = getBackendBaseURL();
    const cleanup = openSseStream({
      url: () =>
        `${base}/api/subagents/stream/${encodeURIComponent(rootThreadId)}?after_seq=${cursorRef.current}`,
      onOpen: () => setStatus("live"),
      onReconnecting: () => setStatus("connecting"),
      onEvent: (msg) => {
        let event: SubAgentBusEvent;
        try {
          event = JSON.parse(msg.data) as SubAgentBusEvent;
        } catch (err) {
          swallow(err);
          return;
        }
        if (event.type === "subscribed" || event.type === "done") return;
        const live = busEventToLiveEvent(event, cursorRef.current);
        if (!live) return;
        const seq = typeof event.seq === "number" ? event.seq : 0;
        if (seq > cursorRef.current) cursorRef.current = seq;
        setLastSeq(seq);
        setEvents((prev) => {
          const existingIndex = prev.findIndex((item) => item.id === live.id);
          if (existingIndex < 0) return [...prev, live];
          const existing = prev[existingIndex]!;
          const next = [...prev];
          next[existingIndex] = {
            ...existing,
            ...live,
            // A terminal bus frame is timestamped when the tool ended. Keep
            // the paired start time so duration/order remain truthful.
            startedAt: existing.startedAt,
          };
          return next;
        });
      },
      onError: () => setStatus("error"),
    });
    return cleanup;
  }, [rootThreadId]);

  return { events, status, lastSeq };
}
