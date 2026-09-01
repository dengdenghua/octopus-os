"""Unified read model for skills, tools, and external capability surfaces."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from runtime.execution.misc.capability_permissions import (
    is_capability_group_enabled,
    permission_group_for_skill,
)
from runtime.safety.approval.approval_gate import approval_action_for_tool

_SCHEMA = "echo.capability_catalog.v1"
_SOURCE_ORDER = {
    "runtime_skill": 0,
    "tool_registry": 1,
    "mobile_mcp": 2,
}
_RISK_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def build_capability_catalog(
    *,
    registry: Any = None,
    tool_registry: Any = None,
    mobile_skills_root: str | Path | None = None,
    include_mobile: bool = True,
    tool_scope: str | None = None,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    entries.extend(_runtime_skill_entries(registry))
    entries.extend(_tool_registry_entries(tool_registry, scope=tool_scope))
    if include_mobile:
        entries.extend(_mobile_mcp_entries(mobile_skills_root))
    entries = sorted(entries, key=_entry_sort_key)
    return {
        "schema": _SCHEMA,
        "capabilities": entries,
        "summary": _summary(entries),
    }


def filter_capability_entries(
    entries: list[dict[str, Any]],
    *,
    q: str | None = None,
    source: str | None = None,
    kind: str | None = None,
    risk_level: str | None = None,
    permission_group: str | None = None,
    available_only: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    rows = list(entries)
    query = _clean(q).casefold()
    if query:
        rows = [row for row in rows if query in _search_blob(row).casefold()]
    if source:
        rows = [row for row in rows if row.get("source") == source]
    if kind:
        rows = [row for row in rows if row.get("kind") == kind]
    if risk_level:
        rows = [row for row in rows if row.get("risk", {}).get("level") == risk_level]
    if permission_group:
        rows = [row for row in rows if row.get("permission", {}).get("group") == permission_group]
    if available_only:
        rows = [row for row in rows if row.get("available") is True]
    total = len(rows)
    return {
        "schema": _SCHEMA,
        "capabilities": rows[offset : offset + limit],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _runtime_skill_entries(registry: Any) -> list[dict[str, Any]]:
    if registry is None:
        return []
    try:
        names = list(registry.all_names())
    except Exception:
        return []
    entries: list[dict[str, Any]] = []
    for name in names:
        try:
            skill = registry.get(name)
        except Exception:
            continue
        skill_name = _clean(getattr(skill, "name", name))
        if not skill_name:
            continue
        group = _skill_group(skill_name)
        kind = _skill_kind(skill_name)
        permission = _permission_for_skill(skill_name)
        try:
            enabled = bool(registry.is_enabled(skill_name))
        except Exception:
            enabled = True
        risk = _risk_for_tool(skill_name)
        entries.append(
            {
                "id": f"runtime:{skill_name}",
                "name": skill_name,
                "canonical_name": skill_name,
                "display_name": skill_name,
                "description": _clean(getattr(skill, "description", "")),
                "summary": _clean(getattr(skill, "effective_summary", "")),
                "source": "runtime_skill",
                "kind": kind,
                "group": group,
                "provider": None,
                "affinity": _string_list(getattr(skill, "affinity", [])),
                "cost_profile": _clean(getattr(skill, "cost_profile", "")),
                "trusted_source": _clean(getattr(skill, "trusted_source", "")),
                "enabled": enabled,
                "available": enabled and bool(permission["enabled"]),
                "permission": permission,
                "risk": risk,
                "input_schema": None,
                "planning_hints": _planning_hints(risk, permission),
            }
        )
    return entries


def _tool_registry_entries(
    tool_registry: Any,
    *,
    scope: str | None = None,
) -> list[dict[str, Any]]:
    if tool_registry is None:
        return []
    try:
        schemas = list(tool_registry.get_all_tool_schemas(scope=scope))
    except Exception:
        return []
    providers_by_tool = _providers_by_tool(tool_registry)
    entries: list[dict[str, Any]] = []
    for schema in schemas:
        if not isinstance(schema, dict):
            continue
        name = _clean(schema.get("name"))
        if not name:
            continue
        provider = providers_by_tool.get(name, {})
        provider_id = _clean(provider.get("id"))
        permission = _permission_for_skill(name)
        risk = _risk_for_tool(name)
        entries.append(
            {
                "id": f"tool:{name}",
                "name": name,
                "canonical_name": name,
                "display_name": name,
                "description": _clean(schema.get("description")),
                "summary": _clean(schema.get("description"), limit=180),
                "source": "tool_registry",
                "kind": "tool",
                "group": _skill_group(name),
                "provider": {
                    "id": provider_id,
                    "display_name": _clean(provider.get("display_name")) or provider_id,
                    "ready": bool(provider.get("is_ready")),
                    "feature_flags": _string_list(provider.get("feature_flags", [])),
                }
                if provider_id
                else None,
                "affinity": [],
                "cost_profile": "",
                "trusted_source": f"tool-registry://{provider_id or 'default'}/{name}",
                "enabled": True,
                "available": bool(permission["enabled"]),
                "permission": permission,
                "risk": risk,
                "input_schema": schema.get("inputSchema") or {},
                "planning_hints": _planning_hints(risk, permission),
            }
        )
    return entries


def _mobile_mcp_entries(mobile_skills_root: str | Path | None) -> list[dict[str, Any]]:
    try:
        from runtime.tentacle.mobile.mcp_server import load_all_skill_tools
    except Exception:
        return []
    root = Path(mobile_skills_root) if mobile_skills_root is not None else None
    try:
        tools = load_all_skill_tools(root)
    except Exception:
        return []
    entries: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = _clean(tool.get("name"))
        if not name:
            continue
        meta = tool.get("_meta") if isinstance(tool.get("_meta"), dict) else {}
        canonical = _clean(meta.get("skill_name")) or name
        group = "mobile_browser" if ".browser." in canonical else "mobile"
        permission = {
            "group": None,
            "enabled": True,
            "reason": "",
        }
        risk = _risk_for_tool(name)
        entries.append(
            {
                "id": f"mobile:{name}",
                "name": name,
                "canonical_name": canonical,
                "display_name": canonical,
                "description": _clean(tool.get("description")),
                "summary": _clean(tool.get("description"), limit=180),
                "source": "mobile_mcp",
                "kind": "mobile_tool",
                "group": group,
                "provider": {
                    "id": "echo-tentacle",
                    "display_name": "Echo Tentacle",
                    "ready": True,
                    "feature_flags": ["mobile", "mcp"],
                },
                "affinity": ["mobile", "android"],
                "cost_profile": "mid",
                "trusted_source": f"tentacle-mobile://skills/{canonical}",
                "enabled": True,
                "available": True,
                "permission": permission,
                "risk": risk,
                "input_schema": tool.get("inputSchema") or {},
                "planning_hints": _planning_hints(risk, permission),
            }
        )
    return entries


def _providers_by_tool(tool_registry: Any) -> dict[str, dict[str, Any]]:
    try:
        providers = dict(tool_registry.providers)
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for provider in providers.values():
        provider_info = {
            "id": _clean(getattr(provider, "id", "")),
            "display_name": _clean(getattr(provider, "display_name", "")),
            "feature_flags": _string_list(getattr(provider, "feature_flags", [])),
            "is_ready": bool(getattr(provider, "is_ready", False)),
        }
        for tool in list(getattr(provider, "tools", []) or []):
            name = _clean(getattr(tool, "name", ""))
            if name:
                out[name] = provider_info
    return out


def _permission_for_skill(skill_name: str) -> dict[str, Any]:
    group = permission_group_for_skill(skill_name)
    enabled = is_capability_group_enabled(group) if group else True
    return {
        "group": group,
        "enabled": enabled,
        "reason": "" if enabled else f"capability group disabled: {group}",
    }


def _risk_for_tool(tool_name: str) -> dict[str, Any]:
    risk, action, policy = approval_action_for_tool(tool_name)
    return {
        "level": risk.level,
        "categories": list(risk.categories),
        "reason": risk.reason,
        "requires_approval": risk.requires_approval,
        "default_action": action,
        "policy": policy.to_dict(),
    }


def _planning_hints(risk: dict[str, Any], permission: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    if permission.get("enabled") is False:
        hints.append("permission_disabled")
    if risk.get("default_action") in {"ask", "confirm"}:
        hints.append("requires_user_consent")
    if risk.get("level") in {"high", "critical"}:
        hints.append("preflight_or_scope_check")
    if not hints:
        hints.append("safe_for_autonomous_planning")
    return hints


def _summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_source = Counter(str(row.get("source") or "unknown") for row in entries)
    by_kind = Counter(str(row.get("kind") or "unknown") for row in entries)
    by_risk = Counter(str(row.get("risk", {}).get("level") or "unknown") for row in entries)
    by_permission = Counter(
        str(row.get("permission", {}).get("group") or "ungrouped") for row in entries
    )
    unavailable = [row["id"] for row in entries if row.get("available") is not True]
    return {
        "total": len(entries),
        "by_source": dict(sorted(by_source.items())),
        "by_kind": dict(sorted(by_kind.items())),
        "by_risk": dict(sorted(by_risk.items())),
        "by_permission_group": dict(sorted(by_permission.items())),
        "unavailable_count": len(unavailable),
        "unavailable_ids": unavailable[:50],
    }


def _skill_group(skill_name: str) -> str | None:
    try:
        from runtime.execution.all_skills import skill_group

        return skill_group(skill_name)
    except Exception:
        return None


def _skill_kind(skill_name: str) -> str:
    try:
        from runtime.execution.all_skills import skill_kind

        return skill_kind(skill_name)
    except Exception:
        return "domain"


def _entry_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    return (
        _SOURCE_ORDER.get(str(row.get("source") or ""), 99),
        _RISK_ORDER.get(str(row.get("risk", {}).get("level") or "low"), 3),
        str(row.get("name") or ""),
    )


def _search_blob(row: dict[str, Any]) -> str:
    fields = [
        row.get("id"),
        row.get("name"),
        row.get("canonical_name"),
        row.get("description"),
        row.get("source"),
        row.get("kind"),
        row.get("group"),
        " ".join(_string_list(row.get("affinity"))),
    ]
    return " ".join(_clean(field) for field in fields)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _clean(item, limit=120)
        if text and text not in out:
            out.append(text)
    return out


def _clean(value: Any, *, limit: int = 640) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


__all__ = [
    "build_capability_catalog",
    "filter_capability_entries",
]
