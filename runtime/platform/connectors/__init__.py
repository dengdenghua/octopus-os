"""echo 认证编排层(Connectors)。

对齐 WorkBuddy 连接器 / Codex connector_* 体系:
  - 连接器 = 外部服务接入单元(MCP server / CLI 包装 / 纯技能)
  - 认证编排 = 凭据加密存储 + 连接/断开/状态 + auth 头/环境变量注入

子模块:
  - credential_store    加密凭据库(AES-256-GCM)
  - connector_registry  连接器定义加载 / 安装 / 状态
  - auth_orchestrator   认证编排(connect / disconnect / status / resolve)
"""

from runtime.platform.connectors.auth_orchestrator import (
    AuthOrchestrator,
    RefreshCleanupRequiredError,
    mcp_injection_for_server,
)
from runtime.platform.connectors.connector_registry import (
    ConnectorDefinition,
    ConnectorRegistry,
)
from runtime.platform.connectors.credential_store import CredentialStore

__all__ = [
    "CredentialStore",
    "ConnectorRegistry",
    "ConnectorDefinition",
    "AuthOrchestrator",
    "RefreshCleanupRequiredError",
    "mcp_injection_for_server",
]
