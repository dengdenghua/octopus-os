/**
 * Arena Mode API client.
 *
 * Communicates with the backend arena endpoints for battles, voting,
 * leaderboard, and history.
 */

import { getBackendBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";

import type {
  ArenaStats,
  BattleRequest,
  BattleResponse,
  HistoryResponse,
  LeaderboardResponse,
  VoteChoice,
  VoteResponse,
} from "./types";

const BASE = () => `${getBackendBaseURL()}/api/arena`;

/**
 * Start a new arena battle. Sends the prompt to two models (randomly
 * selected or caller-specified) and returns anonymised responses.
 */
export async function createBattle(
  req: BattleRequest,
): Promise<BattleResponse> {
  const res = await fetch(`${BASE()}/battle`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`Arena battle failed (${res.status}): ${detail}`);
  }
  return res.json();
}

/**
 * Record a vote for a battle and receive revealed model identities.
 */
export async function submitVote(
  sessionId: string,
  vote: VoteChoice,
): Promise<VoteResponse> {
  const res = await fetch(`${BASE()}/vote`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ session_id: sessionId, vote }),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`Arena vote failed (${res.status}): ${detail}`);
  }
  return res.json();
}

/**
 * Fetch the ELO-rated leaderboard.
 */
export async function fetchLeaderboard(): Promise<LeaderboardResponse> {
  const res = await fetch(`${BASE()}/leaderboard`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Leaderboard fetch failed: ${res.status}`);
  return res.json();
}

/**
 * Fetch recent battle history.
 */
export async function fetchHistory(
  limit = 50,
  offset = 0,
): Promise<HistoryResponse> {
  const res = await fetch(`${BASE()}/history?limit=${limit}&offset=${offset}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`History fetch failed: ${res.status}`);
  return res.json();
}

/**
 * Fetch aggregate arena statistics.
 */
export async function fetchArenaStats(): Promise<ArenaStats> {
  const res = await fetch(`${BASE()}/stats`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Stats fetch failed: ${res.status}`);
  return res.json();
}
