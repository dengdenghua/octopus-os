import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

const BASE = () => `${getBackendBaseURL()}/api/computer`;

export const CONTROL_SESSION_REPLAY_SCHEMA =
  "echo.control_session_replay.v1" as const;

export type ComputerLeaseOwner = {
  owner_id: string;
  owner_label: string;
};

export type ComputerLease = {
  held: boolean;
  owner_id?: string;
  owner_label?: string;
  acquired_at?: number;
  updated_at?: number;
  expires_at?: number;
  ttl_seconds: number;
  lease_ttl_seconds: number;
};

export type ComputerRuntimeHealth = "ready" | "degraded" | "blocked" | string;

export type ComputerReplayEvidenceHint = {
  schema?: "echo.computer_replay_evidence_hint.v1" | string;
  control_session_schema?: typeof CONTROL_SESSION_REPLAY_SCHEMA | string;
  replay_ready?: boolean;
  case_id?: string;
  fingerprint?: string;
  [key: string]: unknown;
};

export type ComputerCapability = {
  id: string;
  title: string;
  available: boolean;
  critical: boolean;
  mode: string;
  reason?: string;
  recommended_action?: string;
  metadata?: Record<string, unknown>;
};

export type ComputerRuntimeReadiness = {
  schema?: "echo.computer_runtime_readiness.v1" | string;
  ready: boolean;
  health: ComputerRuntimeHealth;
  capabilities: ComputerCapability[];
  degraded_capabilities: ComputerCapability[];
  critical_blockers: ComputerCapability[];
  recommended_actions: string[];
  replay_evidence?: ComputerReplayEvidenceHint;
};

export type ComputerStatus = {
  schema?: "echo.computer_runtime_status.v1" | string;
  ok: boolean;
  ready?: boolean;
  health?: ComputerRuntimeHealth;
  pyautogui_available: boolean;
  uia_available?: boolean;
  uia?: Record<string, unknown>;
  lease?: ComputerLease;
  screen: {
    width?: number;
    height?: number;
    cursor_x?: number;
    cursor_y?: number;
    error?: string;
  };
  readiness?: ComputerRuntimeReadiness;
  capabilities?: ComputerCapability[];
  degraded_capabilities?: ComputerCapability[];
  critical_blockers?: ComputerCapability[];
  recommended_actions?: string[];
  replay_evidence?: ComputerReplayEvidenceHint;
  activity_count?: number;
  recent_activity?: unknown[];
  skills: string[];
  mode: string;
};

export type ComputerScreenshot = {
  ok: boolean;
  path?: string;
  size_bytes?: number;
  data_url?: string;
  created_at?: number;
  error?: string;
};

export type AutomationTarget = {
  kind: "browser_tab" | "desktop_window";
  source: "browser_relay" | "computer" | string;
  id: string;
  title: string;
  url?: string;
  app_id?: string;
  app_name?: string;
};

export type ComputerAutomationTarget = AutomationTarget & {
  frontmost?: boolean;
  position?: unknown;
  size?: unknown;
};

export type ComputerTargetsResponse = {
  schema: "echo.automation_targets.v1" | string;
  targets: ComputerAutomationTarget[];
  count: number;
  backend: string;
  error?: string;
};

export type ComputerAppshot = {
  schema: "echo.appshot.v1" | string;
  ok: boolean;
  snapshot_id: string;
  created_at: number;
  target: AutomationTarget;
  screenshot: ComputerScreenshot;
  accessibility: {
    available?: boolean;
    backend?: string;
    error?: string;
    focused?: string;
    elements?: Array<Record<string, unknown>>;
    [key: string]: unknown;
  };
};

export type ComputerMatchedControl = {
  id?: string | number | null;
  name?: string | null;
  control_type?: string | null;
  class_name?: string | null;
  automation_id?: string | null;
  center?: { x?: number | null; y?: number | null } | null;
  rect?: {
    x?: number | null;
    y?: number | null;
    width?: number | null;
    height?: number | null;
    left?: number | null;
    top?: number | null;
    right?: number | null;
    bottom?: number | null;
  } | null;
  query?: string | null;
  score?: number | null;
};

export type ComputerActionMetadata = {
  source?: string;
  matched_control?: ComputerMatchedControl | null;
  [key: string]: unknown;
};

export type ComputerActionPayload = Record<string, unknown> &
  ComputerActionMetadata;

export type ComputerAction =
  | (ComputerActionMetadata & {
      action: "click" | "move";
      x: number;
      y: number;
      button?: "left" | "right" | "middle";
      clicks?: number;
      duration?: number;
    })
  | (ComputerActionMetadata & {
      action: "type";
      text: string;
      interval?: number;
    })
  | (ComputerActionMetadata & { action: "key"; keys: string[] | string })
  | (ComputerActionMetadata & { action: "wait"; ms: number });

export type ComputerPreview = {
  ok: boolean;
  token: string;
  action: ComputerActionPayload;
  risk: { level: "low" | "medium" | "high" | string; reason: string };
  expires_in_seconds: number;
  lease?: ComputerLease;
  lease_owner?: ComputerLeaseOwner;
};

export type ComputerExecuteResult = {
  ok: boolean;
  action: ComputerActionPayload;
  risk: { level: string; reason: string };
  result: Record<string, unknown>;
  lease?: ComputerLease;
  executed_at: number;
};

export type ComputerPlanSuggestion = {
  id: string;
  title: string;
  rationale: string;
  token: string;
  action: ComputerActionPayload;
  risk: { level: "low" | "medium" | "high" | string; reason: string };
  expires_in_seconds: number;
  lease_owner?: ComputerLeaseOwner;
};

export type ComputerActionPlan = {
  ok: boolean;
  goal: string;
  screenshot?: ComputerScreenshot | null;
  suggestions: ComputerPlanSuggestion[];
  mode: string;
  limitations: string[];
  lease?: ComputerLease;
};

export type ComputerLeaseReleaseResult = {
  ok: boolean;
  lease: ComputerLease;
};

function leaseOwnerBody(leaseOwner?: ComputerLeaseOwner | null) {
  return leaseOwner
    ? {
        lease_owner_id: leaseOwner.owner_id,
        lease_owner_label: leaseOwner.owner_label,
      }
    : {};
}

type ComputerControlSessionOptions = {
  controlSessionId?: string | null;
  controlActionId?: string | null;
};

function controlSessionBody(options?: ComputerControlSessionOptions) {
  return {
    ...(options?.controlSessionId
      ? { control_session_id: options.controlSessionId }
      : {}),
    ...(options?.controlActionId
      ? { control_action_id: options.controlActionId }
      : {}),
  };
}

export async function getComputerStatus(): Promise<ComputerStatus> {
  const res = await fetch(`${BASE()}/status`, { headers: authHeaders() });
  if (!res.ok)
    throw new Error(`Failed to load computer status: ${res.statusText}`);
  return (await res.json()) as ComputerStatus;
}

export async function captureComputerScreen(
  options: ComputerControlSessionOptions = {},
): Promise<ComputerScreenshot> {
  const res = await fetch(`${BASE()}/screenshot`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(controlSessionBody(options)),
  });
  if (!res.ok) throw new Error(`Failed to capture screen: ${res.statusText}`);
  return (await res.json()) as ComputerScreenshot;
}

export async function captureComputerAppshot(
  options: ComputerControlSessionOptions & { maxNodes?: number } = {},
): Promise<ComputerAppshot> {
  const res = await fetch(`${BASE()}/appshot`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({
      ...controlSessionBody(options),
      max_nodes: options.maxNodes ?? 120,
    }),
  });
  if (!res.ok) throw new Error(`Failed to capture appshot: ${res.statusText}`);
  const payload = (await res.json()) as ComputerAppshot;
  if (!payload.ok || !payload.screenshot?.data_url) {
    throw new Error(payload.screenshot?.error || "Appshot capture failed");
  }
  return payload;
}

export async function listComputerTargets(): Promise<ComputerTargetsResponse> {
  const res = await fetch(`${BASE()}/targets`, { headers: authHeaders() });
  if (!res.ok) {
    throw new Error(`Failed to list automation targets: ${res.statusText}`);
  }
  return (await res.json()) as ComputerTargetsResponse;
}

export async function previewAppshotElement(
  snapshotId: string,
  elementIndex: number,
  options: {
    action?: "click" | "move";
    leaseOwner?: ComputerLeaseOwner | null;
  } & ComputerControlSessionOptions = {},
): Promise<ComputerPreview> {
  const res = await fetch(
    `${BASE()}/appshots/${encodeURIComponent(snapshotId)}/elements/${elementIndex}/preview`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        action: options.action || "click",
        ...leaseOwnerBody(options.leaseOwner),
        ...controlSessionBody(options),
      }),
    },
  );
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Failed to preview Appshot element: ${res.status}${text ? ` ${text}` : ""}`,
    );
  }
  return (await res.json()) as ComputerPreview;
}

export async function previewComputerAction(
  action: ComputerAction,
  options: {
    leaseOwner?: ComputerLeaseOwner | null;
  } & ComputerControlSessionOptions = {},
): Promise<ComputerPreview> {
  const res = await fetch(`${BASE()}/actions/preview`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({
      ...action,
      ...leaseOwnerBody(options.leaseOwner),
      ...controlSessionBody(options),
    }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Failed to preview action: ${res.status}${text ? ` ${text}` : ""}`,
    );
  }
  return (await res.json()) as ComputerPreview;
}

export async function planComputerActions(
  goal: string,
  options: {
    capture?: boolean;
    leaseOwner?: ComputerLeaseOwner | null;
  } & ComputerControlSessionOptions = {},
): Promise<ComputerActionPlan> {
  const res = await fetch(`${BASE()}/actions/plan`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({
      goal,
      capture: options.capture ?? true,
      ...leaseOwnerBody(options.leaseOwner),
      ...controlSessionBody(options),
    }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Failed to plan actions: ${res.status}${text ? ` ${text}` : ""}`,
    );
  }
  return (await res.json()) as ComputerActionPlan;
}

export async function groundComputerActions(
  goal: string,
  output: string,
  options: {
    capture?: boolean;
    leaseOwner?: ComputerLeaseOwner | null;
  } & ComputerControlSessionOptions = {},
): Promise<ComputerActionPlan> {
  const res = await fetch(`${BASE()}/actions/ground`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({
      goal,
      output,
      capture: options.capture ?? true,
      ...leaseOwnerBody(options.leaseOwner),
      ...controlSessionBody(options),
    }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Failed to ground vision output: ${res.status}${text ? ` ${text}` : ""}`,
    );
  }
  return (await res.json()) as ComputerActionPlan;
}

export async function askVisionModelForComputerActions(
  goal: string,
  modelId: string,
  options: {
    leaseOwner?: ComputerLeaseOwner | null;
  } & ComputerControlSessionOptions = {},
): Promise<ComputerActionPlan> {
  const res = await fetch(`${BASE()}/actions/vision`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({
      goal,
      model_id: modelId,
      ...leaseOwnerBody(options.leaseOwner),
      ...controlSessionBody(options),
    }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Failed to ask vision model: ${res.status}${text ? ` ${text}` : ""}`,
    );
  }
  return (await res.json()) as ComputerActionPlan;
}

export async function executeComputerAction(
  token: string,
  options: {
    leaseOwner?: ComputerLeaseOwner | null;
  } & ComputerControlSessionOptions = {},
): Promise<ComputerExecuteResult> {
  const res = await fetch(`${BASE()}/actions/execute`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({
      token,
      ...leaseOwnerBody(options.leaseOwner),
      ...controlSessionBody(options),
    }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Failed to execute action: ${res.status}${text ? ` ${text}` : ""}`,
    );
  }
  return (await res.json()) as ComputerExecuteResult;
}

export async function releaseComputerLease(
  leaseOwner: ComputerLeaseOwner,
  options: ComputerControlSessionOptions = {},
): Promise<ComputerLeaseReleaseResult> {
  const res = await fetch(`${BASE()}/lease/release`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({
      ...leaseOwnerBody(leaseOwner),
      ...controlSessionBody(options),
    }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Failed to release computer lease: ${res.status}${text ? ` ${text}` : ""}`,
    );
  }
  return (await res.json()) as ComputerLeaseReleaseResult;
}
