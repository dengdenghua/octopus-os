import { getBackendBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";

export type ChannelName =
  | "feishu"
  | "slack"
  | "telegram"
  | "wecom"
  | "dingtalk"
  | "wechat"
  | "discord"
  | "signal"
  | "whatsapp"
  | "email"
  | "sms"
  | "mattermost"
  | "matrix"
  | "qqbot"
  | "teams"
  | "line"
  | "homeassistant"
  | "bluebubbles"
  | "ntfy"
  | "webhooks"
  | "google_chat"
  | "simplex"
  | "open_webui"
  | "yuanbao";

export interface ChannelStatus {
  enabled: boolean;
  running: boolean;
}

export interface ChannelsStatusResponse {
  service_running: boolean;
  channels: Record<ChannelName, ChannelStatus>;
}

export interface ChannelStats {
  paired_users: number;
  paired_groups: number;
  pending_requests: number;
}

export interface ChannelDetail {
  name: ChannelName;
  enabled: boolean;
  running: boolean;
  linked: boolean;
  assigned_agent?: string | null;
  stats: ChannelStats;
}

export interface ChannelsDetailResponse {
  channels: ChannelDetail[];
}

export interface DingtalkCredentials {
  client_id: string;
  client_secret: string;
}

export interface FeishuCredentials {
  app_id: string;
  app_secret: string;
}

export interface TelegramCredentials {
  bot_token: string;
}

export interface DiscordCredentials {
  application_id: string;
  bot_token: string;
}

export interface SlackCredentials {
  bot_token: string;
}

export interface WecomCredentials {
  corp_id: string;
  agent_id: string;
  secret: string;
}

export interface SignalCredentials {
  phone_number: string;
  api_base_url: string;
}

export interface WhatsAppCredentials {
  phone_number_id: string;
  access_token: string;
  verify_token: string;
  app_secret: string;
}

export interface EmailCredentials {
  smtp_host: string;
  smtp_port: number;
  imap_host: string;
  username: string;
  password: string;
  from_address: string;
}

export interface SmsCredentials {
  account_sid: string;
  auth_token: string;
  from_number: string;
}

export interface MattermostCredentials {
  bot_token: string;
  server_url: string;
}

export interface MatrixCredentials {
  homeserver_url: string;
  access_token: string;
}

export interface QQBotCredentials {
  app_id: string;
  app_secret: string;
}

export interface TeamsCredentials {
  app_id: string;
  app_password: string;
}

export interface LineCredentials {
  channel_access_token: string;
  channel_secret: string;
}

export interface HomeAssistantCredentials {
  ha_url: string;
  long_lived_token: string;
}

export interface BlueBubblesCredentials {
  server_url: string;
  api_key: string;
  password: string;
}

export interface NtfyCredentials {
  server_url: string;
  topic: string;
}

export interface WebhooksCredentials {
  webhook_secret: string;
  outbound_url: string;
}

export interface GoogleChatCredentials {
  service_account_key: string;
}

export interface SimpleXCredentials {
  api_base_url: string;
}

export interface OpenWebUICredentials {
  base_url: string;
  api_key: string;
}

export interface YuanbaoCredentials {
  bot_id: string;
  bot_token: string;
}

export type ChannelCredentials =
  | DingtalkCredentials
  | FeishuCredentials
  | TelegramCredentials
  | DiscordCredentials
  | SlackCredentials
  | WecomCredentials
  | SignalCredentials
  | WhatsAppCredentials
  | EmailCredentials
  | SmsCredentials
  | MattermostCredentials
  | MatrixCredentials
  | QQBotCredentials
  | TeamsCredentials
  | LineCredentials
  | HomeAssistantCredentials
  | BlueBubblesCredentials
  | NtfyCredentials
  | WebhooksCredentials
  | GoogleChatCredentials
  | SimpleXCredentials
  | OpenWebUICredentials
  | YuanbaoCredentials;

export interface WechatQRResponse {
  session_id: string;
  qr_url: string;
  status: "pending" | "scanned" | "confirmed" | "expired";
}

export type PairingStatus = "pending" | "approved" | "rejected";

export interface PairingRequest {
  id: string;
  channel: ChannelName;
  user_id: string;
  user_name: string;
  user_avatar?: string;
  group_id?: string;
  group_name?: string;
  status: PairingStatus;
  created_at: string;
  expires_at: string;
}

export interface PairingRequestsResponse {
  requests: PairingRequest[];
  total: number;
}

// ---------------------------------------------------------------------------
// Implemented endpoints (backend has these)
// ---------------------------------------------------------------------------

export async function getChannelsStatus(): Promise<ChannelsStatusResponse> {
  const res = await fetch(`${getBackendBaseURL()}/api/channels/`, {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to load channels status: ${res.statusText}`);
  return (await res.json()) as ChannelsStatusResponse;
}

export async function restartChannel(
  name: ChannelName,
): Promise<{ message: string }> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/channels/${name}/restart`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to restart channel ${name}: ${res.statusText}`);
  return (await res.json()) as { message: string };
}

// ---------------------------------------------------------------------------
// Planned endpoints · backend not yet implemented
//
// Every stub throws the SAME typed error so callers can uniformly detect
// "feature not ready" and render a coming-soon UI instead of mistaking the
// empty return for a real success. Previously some stubs returned
// `{channels: []}` / `{requests: []}` which was indistinguishable from a
// valid empty result · users saw "no data" with no hint the feature was
// unimplemented.
// ---------------------------------------------------------------------------

export class ChannelNotImplementedError extends Error {
  readonly endpoint: string;
  constructor(endpoint: string) {
    super(`Channel endpoint not implemented: ${endpoint}`);
    this.name = "ChannelNotImplementedError";
    this.endpoint = endpoint;
  }
}

/** Type guard · survives bundling / instanceof pitfalls across chunks. */
export function isChannelNotImplemented(
  err: unknown,
): err is ChannelNotImplementedError {
  return (
    err instanceof ChannelNotImplementedError ||
    (typeof err === "object" &&
      err !== null &&
      (err as { name?: string }).name === "ChannelNotImplementedError")
  );
}

export async function getChannelsDetail(): Promise<ChannelsDetailResponse> {
  const res = await fetch(`${getBackendBaseURL()}/api/channels/detail`, {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to load channels detail: ${res.statusText}`);
  return (await res.json()) as ChannelsDetailResponse;
}

export async function saveChannelCredentials(
  name: ChannelName,
  credentials: ChannelCredentials,
): Promise<{ message: string }> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/channels/credentials/${name}`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(credentials),
    },
  );
  if (!res.ok)
    throw new Error(
      `Failed to save credentials for ${name}: ${res.statusText}`,
    );
  return (await res.json()) as { message: string };
}

export async function assignChannelAgent(
  name: ChannelName,
  agentName: string,
): Promise<{ message: string }> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/channels/${name}/assistant`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ agent_id: agentName }),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to assign agent for ${name}: ${res.statusText}`);
  return (await res.json()) as { message: string };
}

export async function getWechatQRCode(): Promise<WechatQRResponse> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/channels/wechat/qr/start`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to get WeChat QR code: ${res.statusText}`);
  const data = await res.json();
  return {
    session_id: data.qrcode ?? "",
    qr_url: data.qrcode_img_content ?? "",
    status: "pending",
  } as WechatQRResponse;
}

export async function pollWechatLoginStatus(
  sessionId: string,
): Promise<WechatQRResponse> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/channels/wechat/qr/poll`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ qrcode: sessionId }),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to poll WeChat status: ${res.statusText}`);
  const data = await res.json();
  return {
    session_id: sessionId,
    qr_url: "",
    status: data.confirmed ? "confirmed" : (data.status ?? "pending"),
  } as WechatQRResponse;
}

export async function getPairingRequests(params?: {
  channel?: ChannelName;
  status?: PairingStatus;
}): Promise<PairingRequestsResponse> {
  const channelId = params?.channel ?? "all";
  const res = await fetch(
    `${getBackendBaseURL()}/api/channels/${channelId}/pairings`,
    {
      headers: authHeaders(),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to load pairing requests: ${res.statusText}`);
  const data = await res.json();
  const pending: PairingRequest[] = (data.pending ?? []).map(
    (p: Record<string, unknown>, i: number) => ({
      id: (p.sender_id as string) || String(i),
      channel: (params?.channel ?? channelId) as ChannelName,
      user_id: (p.sender_id as string) ?? "",
      user_name: (p.sender_id as string) ?? "",
      group_id: (p.thread_id as string) ?? undefined,
      group_name: undefined,
      status: "pending" as PairingStatus,
      created_at: p.ts ? new Date((p.ts as number) * 1000).toISOString() : "",
      expires_at: "",
    }),
  );
  const filtered = params?.status
    ? pending.filter((r) => r.status === params.status)
    : pending;
  return { requests: filtered, total: filtered.length };
}

export async function approvePairingRequest(
  id: string,
): Promise<{ message: string }> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/channels/pairing/${id}/approve`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to approve pairing ${id}: ${res.statusText}`);
  return (await res.json()) as { message: string };
}

export async function rejectPairingRequest(
  id: string,
): Promise<{ message: string }> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/channels/pairing/${id}/reject`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to reject pairing ${id}: ${res.statusText}`);
  return (await res.json()) as { message: string };
}
