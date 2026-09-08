/** React hooks for the Task Board. */

import { swallow } from "@/core/utils/log";
import { currentActorId } from "@/core/auth/api";
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchAllTasks, fetchStats, fetchTimeline } from "./api";
import type {
  TaskBoardAllResponse,
  TaskBoardStats,
  TaskBoardTimelineResponse,
} from "./types";

const POLL_INTERVAL = 5_000; // 5 seconds
const EMPTY_TASKS: TaskBoardAllResponse = { tasks: [], total: 0 };
const EMPTY_STATS: TaskBoardStats = {
  total: 0,
  by_status: {},
  by_type: {},
  avg_duration_ms: 0,
  success_rate: 0,
  running_count: 0,
  queued_count: 0,
};
const EMPTY_TIMELINE: TaskBoardTimelineResponse = {
  tasks: [],
  earliest_ms: 0,
  latest_ms: 0,
};

/**
 * Hook to fetch and poll all unified tasks.
 */
export function useTaskBoardTasks(params?: { type?: string; status?: string }) {
  const actor = currentActorId();
  const actorRef = useRef(actor);
  if (actorRef.current !== actor) actorRef.current = actor;
  const [data, setData] = useState<TaskBoardAllResponse>(EMPTY_TASKS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const paramsRef = useRef(params);
  paramsRef.current = params;

  const load = useCallback(async () => {
    const requestActor = actor;
    try {
      const result = await fetchAllTasks(paramsRef.current);
      if (actorRef.current !== requestActor) return;
      setData(result);
      setError(null);
    } catch (err) {
      if (actorRef.current !== requestActor) return;
      swallow(err);
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      if (actorRef.current === requestActor) setLoading(false);
    }
  }, [actor]);

  useEffect(() => {
    setData(EMPTY_TASKS);
    setError(null);
    setLoading(true);
    void load();
    const interval = setInterval(load, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [actor, load]);

  return { data, loading, error, refresh: load };
}

/**
 * Hook to fetch and poll aggregate stats.
 */
export function useTaskBoardStats() {
  const actor = currentActorId();
  const actorRef = useRef(actor);
  if (actorRef.current !== actor) actorRef.current = actor;
  const [stats, setStats] = useState<TaskBoardStats>(EMPTY_STATS);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const requestActor = actor;
    try {
      const result = await fetchStats();
      if (actorRef.current !== requestActor) return;
      setStats(result);
    } catch (e) {
      if (actorRef.current !== requestActor) return;
      swallow(e);
      // silently ignore stats errors
    } finally {
      if (actorRef.current === requestActor) setLoading(false);
    }
  }, [actor]);

  useEffect(() => {
    setStats(EMPTY_STATS);
    setLoading(true);
    void load();
    const interval = setInterval(load, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [actor, load]);

  return { stats, loading, refresh: load };
}

/**
 * Hook to fetch timeline data.
 */
export function useTaskBoardTimeline(params?: {
  type?: string;
  hours?: number;
}) {
  const actor = currentActorId();
  const actorRef = useRef(actor);
  if (actorRef.current !== actor) actorRef.current = actor;
  const [data, setData] = useState<TaskBoardTimelineResponse>(EMPTY_TIMELINE);
  const [loading, setLoading] = useState(true);
  const paramsRef = useRef(params);
  paramsRef.current = params;

  const load = useCallback(async () => {
    const requestActor = actor;
    try {
      const result = await fetchTimeline(paramsRef.current);
      if (actorRef.current !== requestActor) return;
      setData(result);
    } catch (e) {
      if (actorRef.current !== requestActor) return;
      swallow(e);
      // silently ignore
    } finally {
      if (actorRef.current === requestActor) setLoading(false);
    }
  }, [actor]);

  useEffect(() => {
    setData(EMPTY_TIMELINE);
    setLoading(true);
    void load();
    const interval = setInterval(load, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [actor, load]);

  return { data, loading, refresh: load };
}
