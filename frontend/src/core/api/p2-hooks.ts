/**
 * Echo Native Session API v2 React hooks
 *
 * Custom hooks for Session-query, Feedback, and Export features.
 */

import { useState, useCallback, useEffect, useRef } from "react";
import type {
  SearchParams,
  SearchResult,
  AddFeedbackParams,
  MessageFeedback,
  FeedbackStats,
  FeedbackType,
} from "@/core/api/p2";
import {
  searchThreadsFTS,
  addMessageFeedback,
  getMessageFeedback,
  getFeedbackStats,
  downloadThreadMarkdown,
} from "@/core/api/p2";

// ============================================================================
// useThreadSearch Hook
// ============================================================================

export interface UseThreadSearchOptions {
  debounceMs?: number;
  minQueryLength?: number;
  agent_id?: string;
  team_id?: string;
}

export interface UseThreadSearchResult {
  query: string;
  setQuery: (query: string) => void;
  results: SearchResult[];
  loading: boolean;
  error: Error | null;
  search: (q: string) => Promise<void>;
  clear: () => void;
}

/**
 * Hook for full-text search of threads
 */
export function useThreadSearch(
  options: UseThreadSearchOptions = {},
): UseThreadSearchResult {
  const { debounceMs = 300, minQueryLength = 2, agent_id, team_id } = options;

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );

  const search = useCallback(
    async (q: string) => {
      if (q.trim().length < minQueryLength) {
        setResults([]);
        return;
      }

      setLoading(true);
      setError(null);

      try {
        const params: SearchParams = { q: q.trim() };
        if (agent_id) params.agent_id = agent_id;
        if (team_id) params.team_id = team_id;

        const response = await searchThreadsFTS(params);
        setResults(response.results);
      } catch (e) {
        setError(e as Error);
        setResults([]);
      } finally {
        setLoading(false);
      }
    },
    [minQueryLength, agent_id, team_id],
  );

  const handleSetQuery = useCallback(
    (newQuery: string) => {
      setQuery(newQuery);

      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }

      debounceRef.current = setTimeout(() => {
        search(newQuery);
      }, debounceMs);
    },
    [search, debounceMs],
  );

  const clear = useCallback(() => {
    setQuery("");
    setResults([]);
    setError(null);
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
  }, []);

  useEffect(() => {
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
    };
  }, []);

  return {
    query,
    setQuery: handleSetQuery,
    results,
    loading,
    error,
    search,
    clear,
  };
}

// ============================================================================
// useMessageFeedback Hook
// ============================================================================

export interface UseMessageFeedbackResult {
  feedbacks: MessageFeedback[];
  stats: FeedbackStats | null;
  loading: boolean;
  error: Error | null;
  addFeedback: (
    messageIndex: number,
    feedbackType: FeedbackType,
    tags?: string[],
    comment?: string,
  ) => Promise<void>;
  refresh: () => Promise<void>;
}

/**
 * Hook for managing message feedback
 */
export function useMessageFeedback(
  thread_id: string | null,
): UseMessageFeedbackResult {
  const [feedbacks, setFeedbacks] = useState<MessageFeedback[]>([]);
  const [stats, setStats] = useState<FeedbackStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const refresh = useCallback(async () => {
    if (!thread_id) {
      setFeedbacks([]);
      setStats(null);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const [feedbackData, statsData] = await Promise.all([
        getMessageFeedback({ thread_id }),
        getFeedbackStats(thread_id),
      ]);

      setFeedbacks(feedbackData.feedbacks);
      setStats(statsData);
    } catch (e) {
      setError(e as Error);
    } finally {
      setLoading(false);
    }
  }, [thread_id]);

  const addFeedback = useCallback(
    async (
      messageIndex: number,
      feedbackType: FeedbackType,
      tags: string[] = [],
      comment = "",
    ) => {
      if (!thread_id) return;

      setLoading(true);
      setError(null);

      try {
        const params: AddFeedbackParams = {
          thread_id,
          message_index: messageIndex,
          feedback_type: feedbackType,
          tags,
          comment,
        };

        await addMessageFeedback(params);
        await refresh();
      } catch (e) {
        setError(e as Error);
      } finally {
        setLoading(false);
      }
    },
    [thread_id, refresh],
  );

  useEffect(() => {
    if (thread_id) {
      refresh();
    }
  }, [thread_id, refresh]);

  return {
    feedbacks,
    stats,
    loading,
    error,
    addFeedback,
    refresh,
  };
}

// ============================================================================
// useThreadExport Hook
// ============================================================================

export interface UseThreadExportResult {
  exporting: boolean;
  error: Error | null;
  exportMarkdown: (threadId: string, filename?: string) => Promise<void>;
}

/**
 * Hook for exporting threads as Markdown
 */
export function useThreadExport(): UseThreadExportResult {
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const exportMarkdown = useCallback(
    async (threadId: string, filename?: string) => {
      setExporting(true);
      setError(null);

      try {
        await downloadThreadMarkdown(threadId, filename);
      } catch (e) {
        setError(e as Error);
      } finally {
        setExporting(false);
      }
    },
    [],
  );

  return {
    exporting,
    error,
    exportMarkdown,
  };
}

// ============================================================================
// Exports
// ============================================================================

export const p2Hooks = {
  useThreadSearch,
  useMessageFeedback,
  useThreadExport,
};

export default p2Hooks;
