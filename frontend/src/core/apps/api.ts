import { getBackendBaseURL } from "@/core/config";

export interface EchoAppAction {
  name: string;
  description?: string;
  input_schema?: Record<string, unknown>;
  requires_confirmation?: boolean;
}

export interface EchoApp {
  id: string;
  name: string;
  description?: string;
  author?: string | null;
  plugin?: string | null;
  source_plugin?: string | null;
  path?: string;
  route?: string | null;
  entry?: string | null;
  icon?: string | null;
  category?: string | null;
  schema_version?: string | null;
  connector_id?: string | null;
  permissions?: string[];
  actions?: EchoAppAction[];
  action_count?: number;
}

export async function listApps(): Promise<EchoApp[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/apps`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to load apps: ${res.statusText}`);
  return res.json() as Promise<EchoApp[]>;
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("echo:token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}
