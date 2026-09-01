/**
 * Echo Native Session API v2 client
 *
 * Client functions for Session-query, Feedback, and Export features.
 */

import { getBackendBaseURL } from "@/core/config";

// ============================================================================
// Types
// ============================================================================

export interface SearchResult {
  thread_id: string;
  title: string;
  snippet: string;
  rank: number;
  created_at: string;
  updated_at: string;
}

export interface SearchResponse {
  results: SearchResult[];
  count: number;
}

export interface SearchParams {
  q: string;
  agent_id?: string;
  team_id?: string;
  after?: string;
  before?: string;
  limit?: number;
}

export type FeedbackType = "thumbs_up" | "thumbs_down";

export interface MessageFeedback {
  thread_id: string;
  message_index: number;
  feedback_type: FeedbackType;
  tags: string[];
  comment: string;
  timestamp: string;
  user_id: string | null;
}

export interface FeedbackStats {
  total: number;
  thumbs_up: number;
  thumbs_down: number;
  tags: Record<string, number>;
  messages_with_feedback: number[];
}

export interface AddFeedbackParams {
  thread_id: string;
  message_index: number;
  feedback_type: FeedbackType;
  tags?: string[];
  comment?: string;
}

export interface GetFeedbackParams {
  thread_id: string;
  message_index?: number;
}

// ============================================================================
// API Functions
// ============================================================================

/**
 * Search threads using full-text search (FTS5)
 */
export async function searchThreadsFTS(
  params: SearchParams,
): Promise<SearchResponse> {
  const url = new URL(`${getBackendBaseURL()}/api/threads/fts`);

  url.searchParams.set("q", params.q);
  if (params.agent_id) url.searchParams.set("agent_id", params.agent_id);
  if (params.team_id) url.searchParams.set("team_id", params.team_id);
  if (params.after) url.searchParams.set("after", params.after);
  if (params.before) url.searchParams.set("before", params.before);
  if (params.limit) url.searchParams.set("limit", params.limit.toString());

  const response = await fetch(url.toString());

  if (!response.ok) {
    throw new Error(`Search failed: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Export thread as Markdown
 */
export async function exportThreadMarkdown(thread_id: string): Promise<string> {
  const url = `${getBackendBaseURL()}/api/threads/${thread_id}/export`;
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(`Export failed: ${response.statusText}`);
  }

  return response.text();
}

/**
 * Download thread as Markdown file
 */
export async function downloadThreadMarkdown(
  thread_id: string,
  filename?: string,
): Promise<void> {
  const markdown = await exportThreadMarkdown(thread_id);
  const blob = new Blob([markdown], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);

  const a = document.createElement("a");
  a.href = url;
  a.download = filename || `thread-${thread_id}.md`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * Add feedback to a message
 */
export async function addMessageFeedback(
  params: AddFeedbackParams,
): Promise<MessageFeedback> {
  const url = `${getBackendBaseURL()}/api/threads/${params.thread_id}/feedback`;

  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message_index: params.message_index,
      feedback_type: params.feedback_type,
      tags: params.tags || [],
      comment: params.comment || "",
    }),
  });

  if (!response.ok) {
    throw new Error(`Add feedback failed: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Get feedback for a thread or specific message
 */
export async function getMessageFeedback(
  params: GetFeedbackParams,
): Promise<{ feedbacks: MessageFeedback[] }> {
  const url = new URL(
    `${getBackendBaseURL()}/api/threads/${params.thread_id}/feedback`,
  );

  if (params.message_index !== undefined) {
    url.searchParams.set("message_index", params.message_index.toString());
  }

  const response = await fetch(url.toString());

  if (!response.ok) {
    throw new Error(`Get feedback failed: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Get feedback statistics for a thread
 */
export async function getFeedbackStats(
  thread_id: string,
): Promise<FeedbackStats> {
  const url = `${getBackendBaseURL()}/api/threads/${thread_id}/feedback/stats`;
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(`Get feedback stats failed: ${response.statusText}`);
  }

  return response.json();
}

// ============================================================================
// Exports
// ============================================================================

export const p2API = {
  searchThreadsFTS,
  exportThreadMarkdown,
  downloadThreadMarkdown,
  addMessageFeedback,
  getMessageFeedback,
  getFeedbackStats,
};

export default p2API;
