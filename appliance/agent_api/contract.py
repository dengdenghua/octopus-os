"""Versioned compatibility probe for the temporary Echo Agent Python ABI."""

from __future__ import annotations

import importlib
import importlib.metadata
from dataclasses import dataclass
from typing import Any

AGENT_API_CONTRACT_SCHEMA = "echo.agent_api_contract.v1"


@dataclass(frozen=True)
class _Symbol:
    label: str
    module: str
    name: str
    members: tuple[str, ...] = ()


_DOMAINS: dict[str, tuple[_Symbol, ...]] = {
    "auth": (
        _Symbol(
            "local_router", "runtime.adapters.integrations.local_auth", "create_local_auth_router"
        ),
        _Symbol(
            "local_config", "runtime.adapters.integrations.local_auth.config", "LocalAuthConfig"
        ),
        _Symbol(
            "password_hash", "runtime.adapters.integrations.local_auth.config", "hash_password"
        ),
        _Symbol(
            "password_verify", "runtime.adapters.integrations.local_auth.config", "verify_password"
        ),
        _Symbol("jwt_error", "runtime.safety.auth.identity", "JWTError"),
        _Symbol("jwt_verify", "runtime.safety.auth.identity", "verify_jwt_hs256"),
        _Symbol("session_cookie", "runtime.safety.auth.principal", "SESSION_COOKIE_NAME"),
        _Symbol(
            "legacy_session_cookie",
            "runtime.safety.auth.principal",
            "LEGACY_SESSION_COOKIE_NAME",
        ),
        _Symbol("session_cookie_clear", "runtime.safety.auth.principal", "clear_session_cookie"),
    ),
    "audit": (
        _Symbol("audit_chain", "runtime.safety.audit.audit_chain", "AuditChain"),
        _Symbol("canonical_bytes", "runtime.safety.audit.audit_chain", "canonical_bytes"),
    ),
    "tasks": (
        _Symbol("lease_conflict", "runtime.platform.process.task_supervisor", "TaskLeaseConflict"),
        _Symbol("lease_health", "runtime.platform.process.task_supervisor", "task_lease_health"),
        _Symbol(
            "resume_checkpoint",
            "runtime.sensing.gateway._realtime_turn_lifecycle_resume",
            "_resume_checkpoint_metadata",
        ),
    ),
    "catalog": (
        _Symbol(
            "cloud_catalog",
            "runtime.platform.plugins.cloud_catalog",
            "CloudCatalog",
            ("list", "installed_plugins", "installed_skills", "plugin_statuses"),
        ),
    ),
    "capabilities": (
        _Symbol(
            "lifecycle_service",
            "runtime.platform.capabilities",
            "CapabilityLifecycleService",
            (
                "inspect",
                "install_plan",
                "install",
                "authorize",
                "disable",
                "status",
                "connection_profile",
                "connect",
                "disconnect",
                "uninstall_plan",
                "uninstall",
                "rollback_plan",
                "rollback",
            ),
        ),
        _Symbol(
            "lifecycle_principal",
            "runtime.platform.capabilities",
            "CapabilityPrincipal",
            ("create",),
        ),
        _Symbol(
            "lifecycle_error",
            "runtime.platform.capabilities",
            "CapabilityServiceError",
        ),
    ),
    "devices": (
        _Symbol("planner", "runtime.core.cerebrum.planner", "StaticPlanner", ("plan",)),
        _Symbol(
            "coordinator",
            "runtime.tentacle.coordinator",
            "TentacleCoordinator",
            ("start", "stop"),
        ),
        _Symbol(
            "decision_adapter",
            "runtime.tentacle.mobile.cerebrum_adapter",
            "CerebrumDecisionAdapter",
            ("decide",),
        ),
        _Symbol("active_bridge", "runtime.tentacle.team_bridge", "set_active_coordinator"),
    ),
    "skills": (
        _Symbol("skill", "runtime.execution.suckers.registry", "Skill"),
        _Symbol("skill_registry", "runtime.execution.suckers.registry", "SkillRegistry"),
        _Symbol("skill_expect", "runtime.execution.suckers.testing", "SkillExpect"),
        _Symbol("skill_test", "runtime.execution.suckers.testing", "SkillTestCase"),
    ),
    "images": (
        _Symbol("build_index", "runtime.memory.hemolymph.image_semantic_index", "build_index"),
        _Symbol("search", "runtime.memory.hemolymph.image_semantic_index", "search_by_text"),
        _Symbol(
            "safe_iterator_seam", "runtime.memory.hemolymph.image_semantic_index", "_iter_images"
        ),
        _Symbol(
            "safe_image_loader", "runtime.memory.hemolymph.image_semantic_index", "_load_image"
        ),
        _Symbol("safe_mtime", "runtime.memory.hemolymph.image_semantic_index", "_mtime"),
    ),
}
ALL_AGENT_API_DOMAINS = tuple(_DOMAINS)

DEFAULT_REQUIRED_DOMAINS = (
    "auth",
    "audit",
    "tasks",
)
DEFAULT_OPTIONAL_DOMAINS = ("catalog", "devices", "skills", "images", "capabilities")


class AgentApiIncompatibleError(RuntimeError):
    """The installed Agent cannot satisfy Echo OS's required compatibility contract."""


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _probe_domain(domain: str) -> dict[str, Any]:
    missing: list[str] = []
    modules: dict[str, Any | None] = {}
    for symbol in _DOMAINS[domain]:
        if symbol.module not in modules:
            try:
                modules[symbol.module] = importlib.import_module(symbol.module)
            except Exception:  # noqa: BLE001 - dependency probe returns bounded reason codes
                modules[symbol.module] = None
        module = modules[symbol.module]
        if module is None or not hasattr(module, symbol.name):
            missing.append(symbol.label)
            continue
        value = getattr(module, symbol.name)
        missing.extend(
            f"{symbol.label}.{member}"
            for member in symbol.members
            if not callable(getattr(value, member, None))
        )
    return {"id": domain, "compatible": not missing, "missing": missing}


def inspect_agent_api_contract(
    *,
    required_domains: tuple[str, ...] = DEFAULT_REQUIRED_DOMAINS,
    optional_domains: tuple[str, ...] = DEFAULT_OPTIONAL_DOMAINS,
) -> dict[str, Any]:
    unknown = (set(required_domains) | set(optional_domains)) - set(_DOMAINS)
    if unknown:
        raise ValueError(f"unknown Agent API domains: {sorted(unknown)}")
    if set(required_domains) & set(optional_domains):
        raise ValueError("Agent API domains cannot be both required and optional")

    required = [_probe_domain(domain) for domain in required_domains]
    optional = [_probe_domain(domain) for domain in optional_domains]
    unified_version = _distribution_version("echo-os")
    return {
        "schema": AGENT_API_CONTRACT_SCHEMA,
        "compatible": all(item["compatible"] for item in required),
        # Agent and device layer now ship from one distribution. The fallbacks
        # keep older appliance images readable during the migration window.
        "agentVersion": unified_version,
        "runtimeVersion": unified_version,
        "required": required,
        "optional": optional,
    }


def require_agent_api_contract(
    *,
    required_domains: tuple[str, ...] = DEFAULT_REQUIRED_DOMAINS,
    optional_domains: tuple[str, ...] = DEFAULT_OPTIONAL_DOMAINS,
) -> dict[str, Any]:
    report = inspect_agent_api_contract(
        required_domains=required_domains,
        optional_domains=optional_domains,
    )
    if report["compatible"]:
        return report
    failures = [
        f"{item['id']}[{','.join(item['missing'])}]"
        for item in report["required"]
        if not item["compatible"]
    ]
    raise AgentApiIncompatibleError(
        "installed Echo Agent is incompatible with Echo OS: " + "; ".join(failures)
    )


__all__ = [
    "AGENT_API_CONTRACT_SCHEMA",
    "ALL_AGENT_API_DOMAINS",
    "AgentApiIncompatibleError",
    "inspect_agent_api_contract",
    "require_agent_api_contract",
]
