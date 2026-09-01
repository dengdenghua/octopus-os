/**
 * App Authorization API client.
 *
 * Manage third-party application authorizations.
 */

import { getBackendBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";

export interface ProviderInfo {
  id: string;
  name: string;
  provider_type: string;
  provider_type_label: string;
  auth_type: string;
  icon: string | null;
  description: string;
  color: string | null;
  auth_config: Record<string, unknown>;
}

export interface Authorization {
  id: string;
  provider: string;
  provider_type: string;
  auth_type: string;
  status: "connected" | "expired" | "revoked" | "error" | "pending";
  connected_at: number;
  expires_at: number | null;
  last_used_at: number | null;
  last_error: string | null;
  display_name: string;
  icon_url: string | null;
  description: string;
  scopes: string[];
  metadata: Record<string, unknown>;
}

export interface TestConnectionResult {
  success: boolean;
  message: string;
  details?: Record<string, unknown>;
}

// Providers
export async function listProviders(
  providerType?: string,
): Promise<ProviderInfo[]> {
  const qs = providerType
    ? `?provider_type=${encodeURIComponent(providerType)}`
    : "";
  const res = await fetch(
    `${getBackendBaseURL()}/api/integrations/auth/providers${qs}`,
    { headers: authHeaders() },
  );
  if (!res.ok) throw new Error(`Failed to list providers: ${res.statusText}`);
  return res.json();
}

export async function getProviderTypes(): Promise<
  Array<{ value: string; label: string }>
> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/integrations/auth/providers/types`,
    { headers: authHeaders() },
  );
  if (!res.ok)
    throw new Error(`Failed to get provider types: ${res.statusText}`);
  return res.json();
}

export async function getProvider(providerId: string): Promise<ProviderInfo> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/integrations/auth/providers/${providerId}`,
    { headers: authHeaders() },
  );
  if (!res.ok) throw new Error(`Failed to get provider: ${res.statusText}`);
  return res.json();
}

// Authorizations
export async function listAuthorizations(): Promise<Authorization[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/integrations/auth`, {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to list authorizations: ${res.statusText}`);
  return res.json();
}

export async function createApiKeyAuth(
  provider: string,
  apiKey: string,
  displayName?: string,
  scopes: string[] = [],
): Promise<Authorization> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/integrations/auth/api-key`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        provider,
        api_key: apiKey,
        display_name: displayName,
        scopes,
      }),
    },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(
      err.detail || `Failed to create authorization: ${res.statusText}`,
    );
  }
  return res.json();
}

export async function createTokenAuth(
  provider: string,
  token: string,
  displayName?: string,
  scopes: string[] = [],
): Promise<Authorization> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/integrations/auth/token`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        provider,
        token,
        display_name: displayName,
        scopes,
      }),
    },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(
      err.detail || `Failed to create authorization: ${res.statusText}`,
    );
  }
  return res.json();
}

export async function createCookieAuth(
  provider: string,
  cookieValue: string,
  displayName?: string,
): Promise<Authorization> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/integrations/auth/cookie`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        provider,
        cookie_value: cookieValue,
        display_name: displayName,
      }),
    },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(
      err.detail || `Failed to create authorization: ${res.statusText}`,
    );
  }
  return res.json();
}

export async function initOAuthFlow(providerId: string): Promise<{
  authorization_url: string;
  state: string;
}> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/integrations/auth/oauth/${providerId}/init`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
    },
  );
  if (!res.ok) throw new Error(`Failed to init OAuth: ${res.statusText}`);
  return res.json();
}

export async function deleteAuthorization(
  authId: string,
): Promise<{ deleted: boolean }> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/integrations/auth/${authId}`,
    {
      method: "DELETE",
      headers: authHeaders(),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to delete authorization: ${res.statusText}`);
  return res.json();
}

export async function testAuthorization(
  authId: string,
): Promise<TestConnectionResult> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/integrations/auth/${authId}/test`,
    {
      method: "POST",
      headers: authHeaders(),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to test authorization: ${res.statusText}`);
  return res.json();
}

// Browser-based capture (Playwright auto-login flow)

export interface BrowserCaptureSession {
  session_id: string;
  platform_id: string;
  status: "running" | "success" | "failed" | "cancelled";
  message: string;
  started_at?: number;
  finished_at?: number;
  cookies_captured?: boolean;
  bearer_captured?: boolean;
}

export async function startBrowserCapture(
  providerId: string,
): Promise<BrowserCaptureSession> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/integrations/auth/browser/${providerId}/start`,
    { method: "POST", headers: jsonAuthHeaders() },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(
      err.detail || `Failed to start browser capture: ${res.statusText}`,
    );
  }
  return res.json();
}

export async function getBrowserCaptureStatus(
  sessionId: string,
): Promise<BrowserCaptureSession> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/integrations/auth/browser/sessions/${sessionId}`,
    { headers: authHeaders() },
  );
  if (!res.ok) {
    throw new Error(`Failed to get capture status: ${res.statusText}`);
  }
  return res.json();
}

export async function cancelBrowserCapture(
  sessionId: string,
): Promise<{ cancelled: boolean }> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/integrations/auth/browser/sessions/${sessionId}/cancel`,
    { method: "POST", headers: authHeaders() },
  );
  if (!res.ok) {
    throw new Error(`Failed to cancel capture: ${res.statusText}`);
  }
  return res.json();
}

export async function refreshAuthorization(
  authId: string,
): Promise<Authorization> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/integrations/auth/${authId}/refresh`,
    {
      method: "POST",
      headers: authHeaders(),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to refresh authorization: ${res.statusText}`);
  return res.json();
}
