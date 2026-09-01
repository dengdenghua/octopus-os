from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    # stdio transport (local subprocess server)
    command: str = ""  # e.g. "npx" / "python"
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # http/sse transport (remote server) — most of the hosted MCP ecosystem
    # (GitHub, Slack, Linear, Notion, ...) is reached this way.
    transport: Literal["stdio", "http", "sse"] = "stdio"
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    # Operator-selected workspace used by the hard stdio launcher in shared
    # deployments. None preserves local development behavior only.
    sandbox_dir: str | None = None
    # Tenant binding is carried with the client so OAuth lookup cannot fall
    # back to a process-global token when the same server name is used by
    # multiple tenants.
    tenant_id: str | None = None
    trust_level: str = "public"  # "public" | "custom" | "external"
    timeout_ms: int = Field(default=30_000, gt=0)

    @property
    def trusted_source_uri(self) -> str:
        return f"mcp://{self.name}/*"
