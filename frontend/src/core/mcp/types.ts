export interface MCPServerConfig extends Record<string, unknown> {
  enabled: boolean;
  description: string;
  transport?: "stdio" | "http" | "sse";
  command?: string;
  url?: string;
  error?: string;
}

export interface MCPConfig {
  mcp_servers: Record<string, MCPServerConfig>;
}

export interface MCPConfigUpdateResponse extends MCPConfig {
  _status?: Record<string, { ok?: boolean; error?: string }>;
}
