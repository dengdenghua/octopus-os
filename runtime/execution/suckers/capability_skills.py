from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .codex_plugin_skills import action_tail, plugin_action_name
from .registry import Skill, SkillRegistry

CAPABILITY_SKILL_NAMES = [
    "search_capabilities",
    "query_capability",
    "use_capability",
]


def _as_text(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_skill_frontmatter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            out[key] = value
    return out


def _scan_codex_plugin_skills(plugin_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw = manifest.get("skills") or "./skills"
    if isinstance(raw, str):
        roots = [plugin_dir / raw]
    elif isinstance(raw, list):
        roots = [plugin_dir / str(item) for item in raw if str(item).strip()]
    else:
        roots = []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        root = root.resolve()
        if not root.is_dir():
            continue
        for skill_file in sorted(root.rglob("SKILL.md")):
            meta = _parse_skill_frontmatter(skill_file)
            name = meta.get("name") or skill_file.parent.name
            if name in seen:
                continue
            seen.add(name)
            out.append(
                {
                    "name": name,
                    "description": meta.get("description", ""),
                    "path": str(skill_file.parent),
                    "registered": False,
                }
            )
    return out


def _scan_codex_mcp_servers(plugin_dir: Path, manifest: dict[str, Any]) -> list[str]:
    raw = manifest.get("mcpServers")
    paths: list[Path] = []
    if isinstance(raw, str):
        paths.append(plugin_dir / raw)
    elif isinstance(raw, dict):
        data = raw
        return sorted(str(name) for name in data.get("mcpServers", {}))
    if not paths and (plugin_dir / ".mcp.json").is_file():
        paths.append(plugin_dir / ".mcp.json")
    servers: set[str] = set()
    for path in paths:
        data = _safe_read_json(path)
        raw_servers = data.get("mcpServers")
        if isinstance(raw_servers, dict):
            servers.update(str(name) for name in raw_servers)
    return sorted(servers)


def _registry_plugin_entries(registry: SkillRegistry) -> dict[str, dict[str, Any]]:
    by_plugin: dict[str, dict[str, Any]] = {}
    for name in registry.all_names():
        try:
            skill = registry.get(name)
        except KeyError:
            continue
        source = _as_text(getattr(skill, "trusted_source", ""))
        if not source.startswith("plugin://"):
            continue
        rest = source.removeprefix("plugin://")
        plugin_id = rest.split("/", 1)[0].strip()
        if not plugin_id:
            continue
        entry = by_plugin.setdefault(
            plugin_id,
            {
                "id": plugin_id,
                "kind": "plugin",
                "source": "pluginhub-runtime",
                "display_name": plugin_id,
                "description": "Runtime plugin registered with executable skills.",
                "status": "registered",
                "capabilities": [],
                "skills": [],
                "registered_actions": [],
                "mcp_servers": [],
                "path": "",
            },
        )
        entry["skills"].append(
            {
                "name": name,
                "description": skill.effective_summary,
                "registered": True,
                "registered_as": name,
            }
        )
        entry["registered_actions"].append(name)
    return by_plugin


def _codex_plugin_entries(registry: SkillRegistry) -> dict[str, dict[str, Any]]:
    try:
        from runtime.platform.plugins.codex_discovery import discover_codex_plugins
    except Exception:  # noqa: BLE001 — best-effort; fail-open
        return {}
    registered = set(registry.all_names())
    out: dict[str, dict[str, Any]] = {}
    for plugin in discover_codex_plugins():
        plugin_id = _as_text(plugin.get("id") or plugin.get("name")).strip()
        if not plugin_id:
            continue
        plugin_dir = Path(_as_text(plugin.get("path"))).expanduser()
        manifest = _safe_read_json(plugin_dir / ".codex-plugin" / "plugin.json")
        skills = _scan_codex_plugin_skills(plugin_dir, manifest)
        registered_actions: list[str] = []
        for item in skills:
            name = _as_text(item.get("name"))
            action_name = plugin_action_name(plugin_id, name)
            if action_name in registered:
                item["registered"] = True
                item["registered_as"] = action_name
                registered_actions.append(action_name)
            elif name in registered:
                # Backward compatibility for older runtime/plugin bridges that
                # registered the plugin-local skill name directly.
                item["registered"] = True
                item["registered_as"] = name
                registered_actions.append(name)
        out[plugin_id] = {
            "id": plugin_id,
            "kind": "plugin",
            "source": "codex-plugin",
            "display_name": _as_text(plugin.get("name"), plugin_id),
            "description": _as_text(plugin.get("description")),
            "status": "discovered",
            "capabilities": [
                _as_text(cap.get("name") if isinstance(cap, dict) else cap)
                for cap in _as_list(plugin.get("capabilities"))
            ],
            "skills": skills,
            "registered_actions": registered_actions,
            "mcp_servers": _scan_codex_mcp_servers(plugin_dir, manifest),
            "path": str(plugin_dir),
        }
    return out


def _meta_skill_entries() -> dict[str, dict[str, Any]]:
    try:
        from runtime.memory.skills_lib.meta_skill import list_meta_skills
    except Exception:  # noqa: BLE001 — best-effort; fail-open
        return {}
    out: dict[str, dict[str, Any]] = {}
    for pack in list_meta_skills():
        name = _as_text(pack.get("name")).strip()
        if not name:
            continue
        out[name] = {
            "id": name,
            "kind": "skill_pack",
            "source": "meta-skill",
            "display_name": _as_text(pack.get("display_name"), name),
            "description": _as_text(pack.get("description")),
            "status": "available",
            "capabilities": _as_list(pack.get("affinity")),
            "skills": [
                {"name": step, "description": "", "registered": False}
                for step in _as_list(pack.get("steps"))
            ],
            "registered_actions": [],
            "mcp_servers": [],
            "path": _as_text(pack.get("path")),
            "step_count": pack.get("step_count", 0),
            "when_to_use": _as_text(pack.get("when_to_use")),
        }
    return out


def _registered_skill_pack_entries(registry: SkillRegistry) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in registry.all_names():
        try:
            skill = registry.get(name)
        except KeyError:
            continue
        source = _as_text(getattr(skill, "trusted_source", ""))
        if not source.startswith("skill://all_skills/"):
            continue
        pack_id = source.removeprefix("skill://all_skills/").split("#", 1)[0]
        if not pack_id:
            continue
        entry = out.setdefault(
            pack_id,
            {
                "id": pack_id,
                "kind": "skill_pack",
                "source": "registered-skill-pack",
                "display_name": pack_id,
                "description": skill.effective_summary,
                "status": "registered",
                "capabilities": list(skill.affinity),
                "skills": [],
                "registered_actions": [],
                "mcp_servers": [],
                "path": "",
            },
        )
        entry["skills"].append(
            {
                "name": name,
                "description": skill.effective_summary,
                "registered": True,
            }
        )
        entry["registered_actions"].append(name)
    return out


def _merge_entries(entries: list[dict[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for group in entries:
        for _, entry in group.items():
            key = (entry["kind"], entry["id"])
            current = merged.get(key)
            if current is None:
                merged[key] = {**entry}
                continue
            current["status"] = (
                "registered"
                if current.get("status") == "registered" or entry.get("status") == "registered"
                else current.get("status") or entry.get("status")
            )
            for field in ("skills", "registered_actions", "mcp_servers", "capabilities"):
                values = list(current.get(field) or [])
                for value in entry.get(field) or []:
                    if value not in values:
                        values.append(value)
                current[field] = values
            if not current.get("description") and entry.get("description"):
                current["description"] = entry["description"]
            if not current.get("path") and entry.get("path"):
                current["path"] = entry["path"]
    return sorted(
        merged.values(),
        key=lambda item: (
            0 if item.get("status") == "registered" else 1,
            item.get("kind", ""),
            item.get("display_name", item.get("id", "")).lower(),
        ),
    )


def list_capability_entries(registry: SkillRegistry) -> list[dict[str, Any]]:
    return _merge_entries(
        [
            _registry_plugin_entries(registry),
            _codex_plugin_entries(registry),
            _meta_skill_entries(),
            _registered_skill_pack_entries(registry),
        ]
    )


def _score(entry: dict[str, Any], query: str) -> int:
    q = query.strip().lower()
    if not q:
        return 1
    fields = [
        _as_text(entry.get("id")),
        _as_text(entry.get("display_name")),
        _as_text(entry.get("description")),
        _as_text(entry.get("source")),
        " ".join(_as_text(v) for v in entry.get("capabilities") or []),
        " ".join(_as_text(s.get("name")) for s in entry.get("skills") or [] if isinstance(s, dict)),
    ]
    haystack = " ".join(fields).lower()
    if q == _as_text(entry.get("id")).lower():
        return 120
    if q == _as_text(entry.get("display_name")).lower():
        return 110
    if q in _as_text(entry.get("id")).lower():
        return 90
    if q in haystack:
        return 60
    tokens = [token for token in q.replace("-", " ").replace("_", " ").split() if token]
    hits = sum(1 for token in tokens if token in haystack)
    return hits * 10


def _compact_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry.get("id"),
        "kind": entry.get("kind"),
        "name": entry.get("display_name") or entry.get("id"),
        "source": entry.get("source"),
        "status": entry.get("status"),
        "description": entry.get("description"),
        "capabilities": entry.get("capabilities") or [],
        "skill_count": len(entry.get("skills") or []),
        "registered_actions": entry.get("registered_actions") or [],
        "mcp_servers": entry.get("mcp_servers") or [],
    }


def _find_capability(
    registry: SkillRegistry,
    capability_id: str,
    kind: str = "",
) -> dict[str, Any] | None:
    wanted = capability_id.strip().lower()
    wanted = wanted.removeprefix("plugin:").removeprefix("skill_pack:")
    wanted = wanted.removeprefix("meta_skill:").removeprefix("capability:")
    kind = kind.strip().lower()
    for entry in list_capability_entries(registry):
        if kind and entry.get("kind") != kind:
            continue
        names = {
            _as_text(entry.get("id")).lower(),
            _as_text(entry.get("display_name")).lower(),
        }
        if wanted in names:
            return entry
    return None


def _search_capabilities_for_registry(registry: SkillRegistry):
    def _search_capabilities(
        query: str = "",
        kind: str = "",
        limit: int = 10,
        **extra: Any,
    ) -> dict[str, Any]:
        # Reject queries passed under an unrecognized key (e.g. ``q``,
        # ``search``, ``keyword``) — without this the empty ``query``
        # silently returns every capability, the model thinks the search
        # succeeded, and it can loop on the same wrong shape.
        if not query and extra:
            misplaced = sorted(k for k, v in extra.items() if isinstance(v, str) and v.strip())
            if misplaced:
                return {
                    "ok": False,
                    "error": (
                        f"search_capabilities received a query under "
                        f"unrecognized key(s) {misplaced}, but 'query' is "
                        f'empty. Re-issue as search_capabilities(query="...").'
                    ),
                }
        q = _as_text(query).strip()
        kind_filter = _as_text(kind).strip().lower()
        try:
            lim = max(1, min(int(limit), 25))
        except (TypeError, ValueError):
            lim = 10
        scored: list[tuple[int, dict[str, Any]]] = []
        for entry in list_capability_entries(registry):
            if kind_filter and entry.get("kind") != kind_filter:
                continue
            score = _score(entry, q)
            if q and score <= 0:
                continue
            scored.append((score, entry))
        scored.sort(key=lambda item: (-item[0], item[1].get("display_name", "")))
        results = [_compact_entry(entry) for _, entry in scored[:lim]]
        return {
            "ok": True,
            "query": q,
            "kind": kind_filter or None,
            "count": len(results),
            "total_matches": len(scored),
            "results": results,
            "guidance": (
                "Pick a capability first. Use query_capability for details, "
                "then use_capability with an action only when a registered action is listed."
            ),
        }

    return _search_capabilities


def _query_capability_for_registry(registry: SkillRegistry):
    def _query_capability(
        capability_id: str = "",
        id: str = "",  # noqa: A002 - model-friendly alias
        kind: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        cap_id = _as_text(capability_id or id).strip()
        if not cap_id:
            return {"ok": False, "error": "capability_id is required"}
        entry = _find_capability(registry, cap_id, kind=kind)
        if entry is None:
            return {"ok": False, "error": f"capability not found: {cap_id}"}
        return {
            "ok": True,
            **_compact_entry(entry),
            "skills": entry.get("skills") or [],
            "path": entry.get("path") or "",
            "when_to_use": entry.get("when_to_use") or "",
            "step_count": entry.get("step_count", 0),
            "guidance": (
                "This is the package-level view. Prefer use_capability instead of "
                "calling low-level child skills directly."
            ),
        }

    return _query_capability


def _coerce_args(args: Any) -> dict[str, Any]:
    if args is None:
        return {}
    if isinstance(args, dict):
        return dict(args)
    if isinstance(args, str) and args.strip():
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            return {"request": args}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {"value": args}


def _resolve_action(registry: SkillRegistry, entry: dict[str, Any], action: str) -> str:
    raw = action.strip()
    if not raw:
        return ""
    actions = [_as_text(name) for name in entry.get("registered_actions") or []]
    if raw in actions and registry.has(raw):
        return raw
    lowered = raw.lower()
    for name in actions:
        if name.lower() == lowered and registry.has(name):
            return name
    for name in actions:
        tail = action_tail(name)
        if tail.lower() == lowered and registry.has(name):
            return name
    return raw if registry.has(raw) else ""


def _use_capability_for_registry(registry: SkillRegistry):
    def _use_capability(
        capability_id: str = "",
        id: str = "",  # noqa: A002 - model-friendly alias
        action: str = "",
        args: Any = None,
        request: str = "",
        **extra: Any,
    ) -> dict[str, Any]:
        # Reject args passed under an unrecognized key (e.g. ``input``,
        # ``params``, ``arguments``) — without this ``args=None``
        # silently coerces to {} and the inner skill is called with
        # empty arguments, the model thinks the call succeeded, and it
        # can loop on the same wrong shape.
        if args is None and extra:
            misplaced = sorted(
                k for k, v in extra.items() if isinstance(v, (dict, list, str)) and v
            )
            if misplaced:
                return {
                    "ok": False,
                    "error": (
                        f"use_capability received arguments under "
                        f"unrecognized key(s) {misplaced}, but 'args' is "
                        f"empty. Re-issue as use_capability(args={{...}})."
                    ),
                }
        cap_id = _as_text(capability_id or id).strip()
        if not cap_id:
            return {"ok": False, "error": "capability_id is required"}
        entry = _find_capability(registry, cap_id)
        if entry is None:
            return {"ok": False, "error": f"capability not found: {cap_id}"}
        resolved = _resolve_action(registry, entry, _as_text(action))
        if not resolved:
            return {
                "ok": True,
                "activated": True,
                **_compact_entry(entry),
                "skills": entry.get("skills") or [],
                "request": _as_text(request),
                "next_step": (
                    "Choose one registered action from registered_actions and call "
                    "use_capability again with action and args. If no action is registered, "
                    "use this package as guidance only."
                ),
            }
        try:
            skill = registry.get(resolved)
        except KeyError:
            return {"ok": False, "error": f"registered action not found: {resolved}"}
        call_args = _coerce_args(args)
        if request and "request" not in call_args and "query" not in call_args:
            call_args["request"] = request
        # Safety chokepoint. This meta-skill dispatches to the inner handler
        # DIRECTLY (not via executor.execute_step), so EVERY pre-execution
        # gate the executor enforces — capability-permission, injection-taint,
        # immunity, file-safety — is bypassed. A tainted / denied / untrusted /
        # credential-targeting inner action could otherwise be laundered
        # through this low-risk-named meta-skill. Re-apply the shared gate
        # sequence here, fail-closed (defer_taint_if_handled=False: the
        # single-action approval gate reviewed use_capability, NOT this inner
        # tool).
        from runtime.execution.tool_engine.skill_gate import gate_inner_dispatch

        _block = gate_inner_dispatch(
            skill,
            call_args,
            caller=f"use_capability:{entry.get('id')}",
        )
        if _block is not None:
            return {
                "ok": False,
                "capability_id": entry.get("id"),
                "action": resolved,
                "error": _block.message,
            }
        try:
            result = skill.handler(**call_args)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "capability_id": entry.get("id"),
                "action": resolved,
                "error": f"{type(exc).__name__}: {exc}",
            }
        return {
            "ok": True,
            "capability_id": entry.get("id"),
            "capability_name": entry.get("display_name") or entry.get("id"),
            "action": resolved,
            "result": result,
        }

    return _use_capability


def register_capability_skills(registry: SkillRegistry) -> int:
    registry.register(
        Skill(
            name="search_capabilities",
            summary="Search top-level plugins and skill packs.",
            description=(
                "Search package-level capabilities first: plugins, skill packs, and "
                "meta-skill packages. Use this before searching low-level skills when "
                "the task sounds like a product capability, plugin, workflow, or tool box."
            ),
            affinity=["meta", "capability", "plugin", "skill-pack"],
            cost_profile="low",
            trusted_source="skill://public/search_capabilities",
            handler=_search_capabilities_for_registry(registry),
        )
    )
    registry.register(
        Skill(
            name="query_capability",
            summary="Inspect one plugin or skill pack.",
            description=(
                "Return the package-level details for one plugin or skill pack, including "
                "registered actions, child skills, MCP servers, and when-to-use guidance."
            ),
            affinity=["meta", "capability", "plugin", "skill-pack"],
            cost_profile="low",
            trusted_source="skill://public/query_capability",
            handler=_query_capability_for_registry(registry),
        )
    )
    registry.register(
        Skill(
            name="use_capability",
            summary="Activate or run a package-level capability.",
            description=(
                "Use a plugin or skill pack as a top-level capability. Without action it "
                "returns the package's registered actions and guidance. With action and "
                "args it runs that registered child action internally, so the model does "
                "not need to see every child tool in the first tool list."
            ),
            affinity=["meta", "capability", "plugin", "skill-pack"],
            cost_profile="low",
            trusted_source="skill://public/use_capability",
            handler=_use_capability_for_registry(registry),
        )
    )
    return len(CAPABILITY_SKILL_NAMES)


__all__ = [
    "CAPABILITY_SKILL_NAMES",
    "list_capability_entries",
    "register_capability_skills",
]
