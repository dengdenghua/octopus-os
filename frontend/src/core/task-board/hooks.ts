/** React hooks for the Task Board. */

import { swallow } from "@/core/utils/log";
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchAllTasks, fetchStats, fetchTimeline } from "./api";
import type {
  TaskBoardAllResponse,
  TaskBoardStats,
  TaskBoardTimelineResponse,
} from "./types";

const POLL_INTERVAL = 5_000; // 5 seconds

/**
 * Hook to fetch and poll all unified tasks.
 */
export function useTaskBoardTasks(params?: { type?: string; status?: string }) {
  const [data, setData] = useState<TaskBoardAllResponse>({
    tasks: [],
    total: 0,
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const paramsRef = useRef(params);
  paramsRef.current = params;

  const load = useCallback(async () => {
    try {
      const result = await fetchAllTasks(paramsRef.current);
      setData(result);
      setError(null);
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [load]);

  return { data, loading, error, refresh: load };
}

/**
 * Hook to fetch and poll aggregate stats.
 */
export function useTaskBoardStats() {
  const [stats, setStats] = useState<TaskBoardStats>({
    total: 0,
    by_status: {},
    by_type: {},
    avg_duration_ms: 0,
    success_rate: 0,
    running_count: 0,
    queued_count: 0,
  });
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const result = await fetchStats();
      setStats(result);
    } catch (e) {
      swallow(e);
      // silently ignore stats errors
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [load]);

  return { stats, loading, refresh: load };
}

/**
 * Hook to fetch timeline data.
 */
export function useTaskBoardTimeline(params?: {
  type?: string;
  hours?: number;
}) {
  const [data, setData] = useState<TaskBoardTimelineResponse>({
    tasks: [],
    earliest_ms: 0,
    latest_ms: 0,
  });
  const [loading, setLoading] = useState(true);
  const paramsRef = useRef(params);
  paramsRef.current = params;

  const load = useCallback(async () => {
    try {
      const result = await fetchTimeline(paramsRef.current);
      setData(result);
    } catch (e) {
      swallow(e);
      // silently ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [load]);

  return { data, loading, refresh: load };
}
