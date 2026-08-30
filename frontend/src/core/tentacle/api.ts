/**
 * Tentacle (Mobile Device) API client.
 *
 * Wraps the REST endpoints exposed by `runtime.tentacle.dashboard`:
 *   GET  /api/tentacle/devices          — list connected devices
 *   GET  /api/tentacle/devices/:id      — device detail
 *   POST /api/tentacle/task             — submit natural-language task
 *   GET  /api/tentacle/tasks            — task history
 *   GET  /api/tentacle/stats            — coordinator stats
 */

import { authHeaders } from "@/core/auth/api";

// ── Types ──────────────────────────────────────────────

export interface TentacleDevice {
  tentacle_id: string;
  type: string;
  platform: string;
  status: "online" | "offline" | "busy" | "connecting";
  is_online: boolean;
  is_busy: boolean;
  capabilities: string[];
  total_capabilities: number;
  last_used_ago: number | null;
  meta: Record<string, unknown>;
}

export interface TaskStep {
  call_id: string;
  tool: string;
  args: Record<string, unknown>;
  success: boolean;
  data: string | null;
  error: string | null;
  duration_ms: number;
}

export interface TaskRecord {
  task_id: string;
  task: string;
  tentacle_id: string;
  success: boolean;
  steps: number;
  results: TaskStep[];
  duration_ms: number;
  timestamp: number;
  error?: string;
}

export interface TentacleStats {
  pool: {
    total: number;
    online: number;
    busy: number;
    offline: number;
  };
  ws_connected: number;
}

export interface SkillInfo {
  name: string;
  description?: string;
  category?: string;
  enabled?: boolean;
}

// ── VLM / Screen-stream types ──────────────────────────

export interface SuggestedAction {
  action: string; // "tap", "swipe", "type" etc.
  target: string; // target description
  coordinates: [number, number] | null;
  text: string | null;
  confidence: number;
}

export interface ScreenAnalysis {
  description: string;
  suggested_actions: SuggestedAction[];
  current_app: string | null;
  screen_state: string;
}

// ── API ────────────────────────────────────────────────

const TENTACLE_BASE = "/api/tentacle";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${TENTACLE_BASE}${path}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || res.statusText);
  }
  return res.json();
}

export async function listDevices(): Promise<TentacleDevice[]> {
  return request<TentacleDevice[]>("/devices");
}

export async function getDevice(tentacleId: string): Promise<TentacleDevice> {
  return request<TentacleDevice>(`/devices/${encodeURIComponent(tentacleId)}`);
}

export async function submitTask(
  task: string,
  tentacleId?: string,
): Promise<TaskRecord> {
  const body: Record<string, string> = { task };
  if (tentacleId) body.tentacle_id = tentacleId;
  return request<TaskRecord>("/task", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function listTasks(): Promise<TaskRecord[]> {
  return request<TaskRecord[]>("/tasks");
}

export async function getStats(): Promise<TentacleStats> {
  return request<TentacleStats>("/stats");
}

// ── VLM / Screenshot ───────────────────────────────────

export async function analyzeDevice(
  tentacleId: string,
  task: string,
): Promise<ScreenAnalysis> {
  return request<ScreenAnalysis>(
    `/devices/${encodeURIComponent(tentacleId)}/analyze`,
    {
      method: "POST",
      body: JSON.stringify({ task }),
    },
  );
}

export async function getDeviceScreenshot(tentacleId: string): Promise<Blob> {
  const url = `${TENTACLE_BASE}/devices/${encodeURIComponent(tentacleId)}/screenshot`;
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || res.statusText);
  }
  return res.blob();
}

// ── PC Screen Capture ──────────────────────────────────

export interface PcScreenStats {
  running: boolean;
  frame_count: number;
  last_frame_ts: number;
  avg_frame_size_kb: number;
  errors: number;
  config: {
    fps: number;
    scale: number;
    jpeg_quality: number;
    backend: string;
  };
}

export async function startPcScreenCapture(opts?: {
  fps?: number;
  scale?: number;
  quality?: number;
}): Promise<{ status: string; stats: PcScreenStats }> {
  return request("/pc-screen/start", {
    method: "POST",
    body: JSON.stringify(opts || {}),
  });
}

export async function stopPcScreenCapture(): Promise<{
  status: string;
  last_stats: PcScreenStats;
}> {
  return request("/pc-screen/stop", { method: "POST" });
}

export async function getPcScreenStats(): Promise<PcScreenStats> {
  return request("/pc-screen/stats");
}

// ── Skills ─────────────────────────────────────────────

export async function listSkills(): Promise<SkillInfo[]> {
  return request("/skills");
}

// ── Remote Input ───────────────────────────────────────

export interface RemoteInputEvent {
  action:
    | "tap"
    | "double_tap"
    | "long_press"
    | "swipe"
    | "type_text"
    | "key_press"
    | "click"
    | "double_click"
    | "right_click"
    | "middle_click"
    | "mouse_move"
    | "drag_start"
    | "drag_move"
    | "drag_end"
    | "scroll"
    | "zoom"
    | "key_combo";
  x?: number;
  y?: number;
  x2?: number;
  y2?: number;
  dx?: number;
  dy?: number;
  deltaX?: number;
  deltaY?: number;
  duration_ms?: number;
  text?: string;
  key?: string;
  button?: "left" | "right" | "middle";
}

export async function sendRemoteInput(
  event: RemoteInputEvent,
): Promise<{ success: boolean; error: string | null }> {
  return request("/remote-input", {
    method: "POST",
    body: JSON.stringify(event),
  });
}
