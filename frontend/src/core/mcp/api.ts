import { getBackendBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";

import type { MCPConfig, MCPConfigUpdateResponse } from "./types";

async function assertOk(response: Response, label: string): Promise<void> {
  if (!response.ok) {
    const err = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new Error(
      (err as { detail?: string }).detail ?? `${label}: ${response.statusText}`,
    );
  }
}

export async function loadMCPConfig() {
  const response = await fetch(`${getBackendBaseURL()}/api/mcp/config`, {
    headers: authHeaders(),
  });
  await assertOk(response, "Failed to load MCP config");
  return response.json() as Promise<MCPConfig>;
}

export async function updateMCPConfig(config: MCPConfig) {
  const response = await fetch(`${getBackendBaseURL()}/api/mcp/config`, {
    method: "PUT",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(config),
  });
  await assertOk(response, "Failed to update MCP config");
  return response.json() as Promise<MCPConfigUpdateResponse>;
}

export async function forgetMCPOAuth(serverName: string): Promise<void> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/mcp/oauth/${encodeURIComponent(serverName)}`,
    { method: "DELETE", headers: authHeaders() },
  );
  await assertOk(response, "Failed to remove MCP OAuth credentials");
}

// ───────────────────────────── OAuth 网页授权 ─────────────────────────────
//
// 走 /api/mcp/oauth/*(见 mcp_router.py):发现服务商授权端点 → PKCE → 返回
// authorize_url 让前端开浏览器授权;回调后轮询 /status 确认已授权。

export interface MCPOAuthAuthorizeResult {
  ok: boolean;
  authorize_url: string;
  /** Some provider consent pages return through a desktop custom scheme. */
  callback_transport?: "standard" | "desktop-deep-link";
  /** 服务商直连 OAuth(GitHub 等)尚未配置 OAuth App 凭据 → 前端引导填写。 */
  needs_app_credentials?: boolean;
  provider?: string;
  provider_name?: string;
  docs_url?: string;
  redirect_uri?: string;
  requires_client_secret?: boolean;
}

/** 服务商 OAuth App 凭据信息(绝不返回明文 secret)。 */
export interface OAuthAppInfo {
  provider: string;
  provider_name: string;
  has_app: boolean;
  configured: boolean;
  client_id_masked: string;
}

export async function oauthAuthorize(
  server: string,
  url: string,
  provider?: string,
): Promise<MCPOAuthAuthorizeResult> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/mcp/oauth/authorize`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ server, url, provider }),
    },
  );
  if (!response.ok) {
    const err = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new Error(
      (err as { detail?: string }).detail ??
        `OAuth authorize: ${response.statusText}`,
    );
  }
  return response.json() as Promise<MCPOAuthAuthorizeResult>;
}

export async function getOAuthApp(provider: string): Promise<OAuthAppInfo> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/mcp/oauth/app/${encodeURIComponent(provider)}`,
    { headers: authHeaders() },
  );
  await assertOk(response, "Failed to load OAuth app credentials");
  return response.json() as Promise<OAuthAppInfo>;
}

export async function saveOAuthApp(
  provider: string,
  clientId: string,
  clientSecret: string,
): Promise<OAuthAppInfo> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/mcp/oauth/app/${encodeURIComponent(provider)}`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        client_id: clientId,
        client_secret: clientSecret,
      }),
    },
  );
  await assertOk(response, "Failed to save OAuth app credentials");
  return response.json() as Promise<OAuthAppInfo>;
}

export async function deleteOAuthApp(provider: string): Promise<void> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/mcp/oauth/app/${encodeURIComponent(provider)}`,
    { method: "DELETE", headers: authHeaders() },
  );
  await assertOk(response, "Failed to remove OAuth app credentials");
}

export async function oauthStatus(
  server: string,
): Promise<{ server: string; authorized: boolean }> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/mcp/oauth/status?server=${encodeURIComponent(server)}`,
    { headers: authHeaders() },
  );
  await assertOk(response, "Failed to load MCP OAuth status");
  return response.json() as Promise<{ server: string; authorized: boolean }>;
}

// ───────────────────────────── Trust store ─────────────────────────────
//
// MCP servers run arbitrary code on the user's machine. Per ADR-007, the
// runtime refuses to register a server's tools as skills until the user
// explicitly approves it. These wrappers talk to /api/mcp/trust so the
// settings page can show approval state and surface an Approve button.

export interface MCPTrustEntry {
  server_name: string;
  approved: boolean;
  added_ts: number;
  tool_digest: string;
  note: string;
}

export async function listMCPTrust(): Promise<{ entries: MCPTrustEntry[] }> {
  const response = await fetch(`${getBackendBaseURL()}/api/mcp/trust`, {
    headers: authHeaders(),
  });
  await assertOk(response, "Failed to list MCP trust entries");
  return response.json();
}

export async function approveMCPTrust(
  server_name: string,
  tool_names: string[] = [],
  note = "",
): Promise<{ ok: boolean; entry: MCPTrustEntry }> {
  const response = await fetch(`${getBackendBaseURL()}/api/mcp/trust`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ server_name, tool_names, note }),
  });
  await assertOk(response, "Failed to approve MCP trust");
  return response.json();
}

export async function revokeMCPTrust(
  server_name: string,
): Promise<{ ok: boolean; server_name: string }> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/mcp/trust/${encodeURIComponent(server_name)}`,
    { method: "DELETE", headers: authHeaders() },
  );
  await assertOk(response, "Failed to revoke MCP trust");
  return response.json();
}
