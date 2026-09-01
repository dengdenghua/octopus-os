// 资产 Registry 消费端 API(母体接 registry · echo-runtime)。
// 走后端 /api/registry/* 路由(registry_consumer_router),浏览/安装公网 registry 技能。
import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

export interface RegistrySkill {
  id: string; // "skill/<slug>"
  type: string;
  kind: string; // data | code
  version: string;
  name: string;
  description: string;
  category?: string | null;
  tags?: string[] | null;
  icon?: string | null;
  logo_url?: string | null;
  icon_url?: string | null;
  platforms?: string[] | null;
  content?: { checksum?: string | null } | null;
}

export interface RegistrySkillsResponse {
  skills: RegistrySkill[];
  total: number;
  offset: number;
  limit: number;
  source: string;
}

export interface InstallResult {
  installed: string;
  path: string | null;
  registered_now: number;
}

export function registrySlug(id: string): string {
  return id.split("/").pop() ?? id;
}

export async function listRegistrySkills(params?: {
  search?: string;
  category?: string;
  limit?: number;
}): Promise<RegistrySkillsResponse> {
  const q = new URLSearchParams();
  if (params?.search) q.set("search", params.search);
  if (params?.category) q.set("category", params.category);
  q.set("limit", String(params?.limit ?? 300));
  const res = await fetch(
    `${getBackendBaseURL()}/api/registry/skills?${q.toString()}`,
    {
      headers: authHeaders(),
    },
  );
  if (!res.ok) throw new Error(`registry list failed: HTTP ${res.status}`);
  return (await res.json()) as RegistrySkillsResponse;
}

export async function installRegistrySkill(
  slug: string,
): Promise<InstallResult> {
  const bare = registrySlug(slug);
  const res = await fetch(
    `${getBackendBaseURL()}/api/registry/skills/${encodeURIComponent(bare)}/install`,
    { method: "POST", headers: authHeaders() },
  );
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`install failed: HTTP ${res.status} ${txt}`.trim());
  }
  return (await res.json()) as InstallResult;
}

// 角色(role / twin-role · 数字分身岗位模板)——同一 registry,单独端点。
export interface RegistryRole {
  id: string; // "role/<slug>" | "twin-role/<slug>"
  type: string;
  kind: string;
  version: string;
  name: string;
  description: string;
  category?: string | null;
  tags?: string[] | null;
  icon?: string | null;
  logo_url?: string | null;
  icon_url?: string | null;
}

export interface RegistryRolesResponse {
  roles: RegistryRole[];
  total: number;
  offset: number;
  limit: number;
  source: string;
}

export interface RoleInstallResult {
  installed: boolean;
  agent_id: string;
  name: string;
  path: string | null;
}

export async function listRegistryRoles(params?: {
  search?: string;
  category?: string;
  type?: "role" | "twin-role";
  limit?: number;
}): Promise<RegistryRolesResponse> {
  const q = new URLSearchParams();
  if (params?.search) q.set("search", params.search);
  if (params?.category) q.set("category", params.category);
  if (params?.type) q.set("type", params.type);
  q.set("limit", String(params?.limit ?? 300));
  const res = await fetch(
    `${getBackendBaseURL()}/api/registry/roles?${q.toString()}`,
    {
      headers: authHeaders(),
    },
  );
  if (!res.ok)
    throw new Error(`registry roles list failed: HTTP ${res.status}`);
  return (await res.json()) as RegistryRolesResponse;
}

export async function installRegistryRole(
  id: string,
): Promise<RoleInstallResult> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/registry/roles/${encodeURIComponent(id)}/install`,
    { method: "POST", headers: authHeaders() },
  );
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`install failed: HTTP ${res.status} ${txt}`.trim());
  }
  return (await res.json()) as RoleInstallResult;
}

// 插件(plugin)——安装为 prompt-only 本地能力；不会下载或执行远程代码。
export interface RegistryPlugin {
  id: string; // "plugin/<slug>"
  type: string;
  kind: string;
  version: string;
  name: string;
  description: string;
  category?: string | null;
  tags?: string[] | null;
  /** Publisher fallback icon (usually an emoji) when no trusted local logo exists. */
  icon?: string | null;
  /** Trusted local plugin asset URLs, added by the runtime consumer route. */
  logo_url?: string | null;
  icon_url?: string | null;
  brand_color?: string | null;
  local_plugin_id?: string | null;
  bundle?: {
    ref?: string | null;
    checksum?: string | null;
    size?: number | null;
  } | null;
  install_mode?: "plugin-bundle" | "prompt-only" | string;
}

export interface RegistryPluginsResponse {
  plugins: RegistryPlugin[];
  total: number;
  offset: number;
  limit: number;
  source: string;
  installable: boolean;
  install_mode?: "prompt-only" | string;
}

export interface PluginInstallResult {
  installed: string;
  installed_name: string;
  path: string;
  registered_now: number;
  install_mode: "prompt-only" | string;
}

export async function listRegistryPlugins(params?: {
  search?: string;
  category?: string;
  limit?: number;
}): Promise<RegistryPluginsResponse> {
  const q = new URLSearchParams();
  if (params?.search) q.set("search", params.search);
  if (params?.category) q.set("category", params.category);
  q.set("limit", String(params?.limit ?? 300));
  const res = await fetch(
    `${getBackendBaseURL()}/api/registry/plugins?${q.toString()}`,
    {
      headers: authHeaders(),
    },
  );
  if (!res.ok)
    throw new Error(`registry plugins list failed: HTTP ${res.status}`);
  return (await res.json()) as RegistryPluginsResponse;
}

export async function installRegistryPlugin(
  slug: string,
): Promise<PluginInstallResult> {
  const bare = registrySlug(slug);
  const res = await fetch(
    `${getBackendBaseURL()}/api/registry/plugins/${encodeURIComponent(bare)}/install`,
    { method: "POST", headers: authHeaders() },
  );
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`install failed: HTTP ${res.status} ${txt}`.trim());
  }
  return (await res.json()) as PluginInstallResult;
}
