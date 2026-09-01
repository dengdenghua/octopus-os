/**
 * Central query key registry for React Query.
 *
 * All query keys used with @tanstack/react-query should be defined here
 * to avoid collisions and make cache invalidation predictable.
 *
 * Usage:
 *   import { queryKeys } from "@/core/api/query-keys";
 *   useQuery({ queryKey: queryKeys.taskBoard.stats, queryFn: ... });
 */
export const queryKeys = {
  taskBoard: {
    all: ["task-board"] as const,
    stats: ["task-board", "stats"] as const,
    timeline: ["task-board", "timeline"] as const,
  },
  evolution: {
    overview: ["evolution", "overview"] as const,
    skills: ["evolution", "skills"] as const,
    memory: ["evolution", "memory"] as const,
    learningCurve: ["evolution", "learning-curve"] as const,
    recommendations: ["evolution", "recommendations"] as const,
  },
  agentWorld: {
    store: (params?: Record<string, unknown>) =>
      ["agent-world", "store", params] as const,
    featured: ["agent-world", "featured"] as const,
    popular: ["agent-world", "popular"] as const,
  },
  agentTrace: {
    summary: (scope?: {
      threadId?: string | null;
      taskId?: string | null;
      agentId?: string | null;
      turnId?: string | null;
    }) => ["agent-trace", "summary", scope] as const,
    resumeRequests: (
      scope?: {
        threadId?: string | null;
      },
      status?: string,
    ) => ["agent-trace", "resume-requests", scope, status] as const,
  },
  intelligence: {
    latest: ["intelligence", "latest"] as const,
    reports: ["intelligence", "reports"] as const,
    stats: ["intelligence", "stats"] as const,
  },
} as const;
