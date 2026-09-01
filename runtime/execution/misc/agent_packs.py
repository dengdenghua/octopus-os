"""Agent pack discovery for file-based, pluggable agent repositories.

This module intentionally stops at *preview*. External repositories can carry
powerful prompts, skills, commands, and MCP connector definitions; importing
them into Echo should first produce a normalized manifest that the UI can
review, diff, and permission-gate before anything is installed or executed.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

from runtime.execution.misc.agent_avatar import pixel_agent_avatar_svg
from runtime.platform.io import atomic_write_json, atomic_write_text

_FRONTMATTER_RE = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_SAFE_PACK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_PACK_IMPORT_SOURCE = "agent-pack-import"

# Manifest directories this scanner understands. WorkBuddy/CodeBuddy experts use
# `.codebuddy-plugin/plugin.json` (same agents/*.md + skills/**/SKILL.md shapes),
# so we treat it as a first-class peer of Claude/Codex packs.
_PACK_MANIFEST_FORMATS: tuple[tuple[str, str], ...] = (
    (".claude-plugin", "claude"),
    (".codex-plugin", "codex"),
    (".codebuddy-plugin", "codebuddy"),
)


class AgentPackAgentNotFound(ValueError):
    """Raised when a requested agent is not present in a scanned pack."""


@dataclass(frozen=True)
class PackModule:
    id: str
    kind: str
    name: str
    path: str
    description: str = ""
    source_plugin: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentPackPreview:
    root: str
    marketplace: dict[str, Any] | None
    plugins: list[PackModule]
    apps: list[PackModule]
    agents: list[PackModule]
    skills: list[PackModule]
    commands: list[PackModule]
    mcp_servers: list[PackModule]
    managed_agents: list[PackModule]
    subagents: list[PackModule]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentPackImportResult:
    agent_id: str
    agent_name: str
    agent_path: str
    copied_skills: list[str]
    skipped_skills: list[str]
    warnings: list[str]
    already_exists: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scan_agent_pack(root: str | Path) -> AgentPackPreview:
    base = Path(root).expanduser().resolve()
    if not base.exists():
        raise FileNotFoundError(f"agent pack root not found: {base}")
    if not base.is_dir():
        raise NotADirectoryError(f"agent pack root is not a directory: {base}")

    warnings: list[str] = []
    marketplace = _read_marketplace(base, warnings)
    plugins = _scan_plugins(base, warnings)
    plugin_by_dir = {Path(module.path): module for module in plugins}

    agents: list[PackModule] = []
    apps: list[PackModule] = []
    skills: list[PackModule] = []
    commands: list[PackModule] = []
    mcp_servers: list[PackModule] = []

    for plugin_dir, plugin in plugin_by_dir.items():
        apps.extend(_scan_app_modules(plugin_dir, plugin, warnings))
        agents.extend(_scan_markdown_modules(plugin_dir / "agents", "agent", plugin, warnings))
        skills.extend(_scan_skill_modules(plugin_dir / "skills", plugin, warnings))
        commands.extend(
            _scan_markdown_modules(plugin_dir / "commands", "command", plugin, warnings)
        )
        mcp_servers.extend(_scan_mcp_servers(plugin_dir / ".mcp.json", plugin, warnings))

    managed_agents, subagents = _scan_managed_agents(base, warnings)
    _append_security_warnings(warnings, mcp_servers, managed_agents)

    return AgentPackPreview(
        root=str(base),
        marketplace=marketplace,
        plugins=plugins,
        apps=apps,
        agents=agents,
        skills=skills,
        commands=commands,
        mcp_servers=mcp_servers,
        managed_agents=managed_agents,
        subagents=subagents,
        warnings=warnings,
    )


def import_agent_from_pack(
    root: str | Path,
    agent_name: str,
    agents_root: str | Path,
    skills_root: str | Path,
) -> AgentPackImportResult:
    preview = scan_agent_pack(root)
    module = next(
        (agent for agent in preview.agents if agent.name == agent_name or agent.id == agent_name),
        None,
    )
    if module is None:
        raise AgentPackAgentNotFound(f"agent not found in pack: {agent_name}")

    pack_root = Path(preview.root)
    source_path = _require_real_file_under(Path(module.path), pack_root, "agent markdown")
    plugin_dir = source_path.parent.parent
    plugin = next((item for item in preview.plugins if item.id == module.source_plugin), None)
    plugin_name = plugin.name if plugin else str(module.source_plugin or plugin_dir.name)
    plugin_version = str((plugin.metadata.get("version") if plugin else "") or "0.1.0")
    agent_id = _slugify_agent_id(module.name)
    agents_base = _ensure_real_directory(Path(agents_root).expanduser(), "agents root")
    skills_base = Path(skills_root).expanduser().resolve()
    agent_dir = agents_base / agent_id
    warnings = list(preview.warnings)

    if agent_dir.is_symlink() or (agent_dir.exists() and not agent_dir.is_dir()):
        raise FileExistsError(f"agent path already exists but is not a real directory: {agent_dir}")
    if agent_dir.exists():
        return AgentPackImportResult(
            agent_id=agent_id,
            agent_name=_titleize(module.name),
            agent_path=str(agent_dir),
            copied_skills=[],
            skipped_skills=[],
            warnings=[*warnings, f"agent already exists: {agent_id}"],
            already_exists=True,
        )

    text = source_path.read_text(encoding="utf-8")
    body = _strip_frontmatter(text).strip()
    tools = module.metadata.get("tools") if isinstance(module.metadata, dict) else []
    mcp_tools = [tool for tool in tools if isinstance(tool, str) and tool.startswith("mcp__")]
    if mcp_tools:
        warnings.append("MCP tools were detected and left disabled: " + ", ".join(mcp_tools))

    plugin_skills = [
        skill for skill in preview.skills if skill.source_plugin == module.source_plugin
    ]
    selected_skills = _select_agent_skills(body, plugin_skills)
    copied_skills: list[str] = []
    skipped_skills: list[str] = []
    created_agent = False
    try:
        copied_skills, skipped_skills = _copy_pack_skills(
            selected_skills,
            skills_base,
            pack_root=pack_root,
        )

        core_dir = agent_dir / "agent-core"
        agent_dir.mkdir(parents=True)
        created_agent = True
        core_dir.mkdir()
        profile = {
            "id": agent_id,
            "templateId": f"{plugin_name}:{module.name}",
            "templateVersion": plugin_version,
            "source_kind": _PACK_IMPORT_SOURCE,
            "managed_by": "agent-pack",
            "source_pack_root": preview.root,
            "source_plugin": module.source_plugin,
            "source_agent_path": str(source_path),
            "name": _titleize(module.name),
            "icon": "Agent",
            "description": module.description,
            "avatar": "avatar.svg",
            "model": {"provider": "auto", "name": "auto"},
            "runtime": "local",
            "capabilities": {"execution_backend": "codex_app_server"},
            "creator": f"pack:{plugin_name}",
            "category": "researcher",
            "tags": ["finance", "research", "market", "analysis"],
        }
        atomic_write_json(agent_dir / "profile.jsonc", profile, ensure_ascii=False, indent=2)
        atomic_write_text(
            core_dir / "SOUL.md",
            (
                f"# Imported Agent: {_titleize(module.name)}\n\n"
                f"Source pack: {plugin_name} ({plugin_version}).\n"
                "External MCP connectors from the source pack are not enabled by default. "
                "Use only locally available tools and copied skills unless the user explicitly configures trusted connectors.\n\n"
                f"{body}\n"
            ),
            newline=None,
        )
        atomic_write_text(
            core_dir / "IDENTITY.md",
            (
                f"- Name: {_titleize(module.name)}\n"
                "- Role: Financial research agent\n"
                f"- Source: {plugin_name}\n"
            ),
            newline=None,
        )
        atomic_write_json(
            core_dir / "tool-registry.jsonc",
            {
                "arms": ["web_read", "fs_writer"],
                "extra_affinity": ["finance", "research", "market", "sector", "comps", "analysis"],
                "private_skills": [skill.name for skill in selected_skills],
                "disabled_source_tools": mcp_tools,
            },
            ensure_ascii=False,
            indent=2,
        )
        _write_agent_avatar(agent_dir / "avatar.svg", _titleize(module.name))
    except Exception:
        if created_agent and agent_dir.is_dir() and not agent_dir.is_symlink():
            shutil.rmtree(agent_dir, ignore_errors=True)
        _cleanup_copied_skill_dirs(skills_base, copied_skills)
        raise

    return AgentPackImportResult(
        agent_id=agent_id,
        agent_name=_titleize(module.name),
        agent_path=str(agent_dir),
        copied_skills=copied_skills,
        skipped_skills=skipped_skills,
        warnings=warnings,
    )


def _scan_plugins(base: Path, warnings: list[str]) -> list[PackModule]:
    modules: list[PackModule] = []
    seen_ids: set[str] = set()
    for manifest_dir, format_name in _PACK_MANIFEST_FORMATS:
        for manifest in sorted(base.rglob(f"{manifest_dir}/plugin.json")):
            plugin_dir = manifest.parent.parent
            data = _read_json(manifest, warnings) or {}
            raw_name = str(data.get("name") or plugin_dir.name)
            module_id = raw_name
            if module_id in seen_ids:
                module_id = f"{format_name}:{raw_name}"
            seen_ids.add(module_id)
            modules.append(
                PackModule(
                    id=module_id,
                    kind="plugin",
                    name=raw_name,
                    path=str(plugin_dir),
                    description=str(data.get("description") or ""),
                    metadata={
                        "version": data.get("version"),
                        "author": data.get("author"),
                        "manifest": str(manifest),
                        "format": format_name,
                        "layout": _infer_plugin_layout(plugin_dir),
                    },
                )
            )
    return modules


def _infer_plugin_layout(plugin_dir: Path) -> list[str]:
    present: list[str] = []
    for name in ("agents", "skills", "commands", "apps", "bin"):
        if (plugin_dir / name).is_dir():
            present.append(name)
    if (plugin_dir / ".mcp.json").is_file():
        present.append("mcp")
    if (
        (plugin_dir / "echo-app.jsonc").is_file()
        or (plugin_dir / ".echo-app.jsonc").is_file()
        or (plugin_dir / ".app.json").is_file()
    ):
        present.append("app")
    return present


def _read_marketplace(base: Path, warnings: list[str]) -> dict[str, Any] | None:
    marketplaces: dict[str, dict[str, Any]] = {}
    for manifest_dir, format_name in _PACK_MANIFEST_FORMATS:
        data = _read_json(base / manifest_dir / "marketplace.json", warnings)
        if data is not None:
            marketplaces[format_name] = data
    if not marketplaces:
        return None
    if len(marketplaces) == 1:
        format_name, data = next(iter(marketplaces.items()))
        return {**data, "_format": format_name}
    return {"_format": "mixed", "marketplaces": marketplaces}


def _scan_markdown_modules(
    directory: Path,
    kind: str,
    plugin: PackModule,
    warnings: list[str],
) -> list[PackModule]:
    if not directory.is_dir():
        return []
    modules: list[PackModule] = []
    for path in sorted(directory.glob("*.md")):
        text = _read_text(path, warnings)
        meta = _parse_frontmatter(text, path, warnings)
        name = str(meta.get("name") or path.stem)
        modules.append(
            PackModule(
                id=f"{plugin.id}:{kind}:{name}",
                kind=kind,
                name=name,
                path=str(path),
                description=str(meta.get("description") or _first_heading_or_line(text)),
                source_plugin=plugin.id,
                metadata={
                    "frontmatter": meta,
                    "tools": _coerce_tool_list(meta.get("tools")),
                },
            )
        )
    return modules


def _scan_skill_modules(
    directory: Path,
    plugin: PackModule,
    warnings: list[str],
) -> list[PackModule]:
    if not directory.is_dir():
        return []
    modules: list[PackModule] = []
    for path in sorted(directory.rglob("SKILL.md")):
        text = _read_text(path, warnings)
        meta = _parse_frontmatter(text, path, warnings)
        skill_dir = path.parent
        name = str(meta.get("name") or skill_dir.name)
        modules.append(
            PackModule(
                id=f"{plugin.id}:skill:{name}",
                kind="skill",
                name=name,
                path=str(skill_dir),
                description=str(meta.get("description") or _first_heading_or_line(text)),
                source_plugin=plugin.id,
                metadata={
                    "frontmatter": meta,
                    "skill_file": str(path),
                    "asset_count": _count_assets(skill_dir),
                },
            )
        )
    return modules


def _scan_mcp_servers(
    path: Path,
    plugin: PackModule,
    warnings: list[str],
) -> list[PackModule]:
    data = _read_json(path, warnings)
    if not data:
        return []
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        warnings.append(f"{path}: missing mcpServers mapping")
        return []
    modules: list[PackModule] = []
    for name, config in sorted(servers.items()):
        cfg = config if isinstance(config, dict) else {}
        modules.append(
            PackModule(
                id=f"{plugin.id}:mcp:{name}",
                kind="mcp_server",
                name=str(name),
                path=str(path),
                description=str(cfg.get("url") or cfg.get("command") or ""),
                source_plugin=plugin.id,
                metadata={"config": cfg},
            )
        )
    return modules


def _scan_app_modules(
    plugin_dir: Path,
    plugin: PackModule,
    warnings: list[str],
) -> list[PackModule]:
    modules: list[PackModule] = []
    seen_paths: set[Path] = set()
    candidates = [
        plugin_dir / "echo-app.jsonc",
        plugin_dir / ".echo-app.jsonc",
        plugin_dir / ".app.json",
        plugin_dir / "app.jsonc",
        plugin_dir / "app.json",
        plugin_dir / "app.yaml",
    ]
    apps_dir = plugin_dir / "apps"
    if apps_dir.is_dir():
        for child in sorted(apps_dir.iterdir()):
            if not child.is_dir():
                continue
            candidates.extend(
                [
                    child / "echo-app.jsonc",
                    child / "app.jsonc",
                    child / "app.json",
                    child / "app.yaml",
                    child / "meta.jsonc",
                    child / "meta.json",
                    child / "meta.yaml",
                ]
            )

    for manifest in candidates:
        if manifest in seen_paths or not manifest.is_file():
            continue
        seen_paths.add(manifest)
        data = _read_app_manifest(manifest, warnings) or {}
        for name, app_data in _iter_app_manifest_entries(data, manifest, warnings):
            title = str(
                app_data.get("title")
                or app_data.get("displayName")
                or app_data.get("display_name")
                or app_data.get("name")
                or name
            )
            actions = _normalize_app_actions(app_data.get("actions"))
            modules.append(
                PackModule(
                    id=f"{plugin.id}:app:{name}",
                    kind="app",
                    name=name,
                    path=str(
                        plugin_dir
                        if manifest.name in {"echo-app.jsonc", ".echo-app.jsonc", ".app.json"}
                        else manifest.parent
                    ),
                    description=str(
                        app_data.get("description")
                        or app_data.get("shortDescription")
                        or app_data.get("short_description")
                        or ""
                    ),
                    source_plugin=plugin.id,
                    metadata={
                        "manifest": str(manifest),
                        "schema_version": app_data.get("schema_version")
                        or data.get("schema_version")
                        or data.get("version"),
                        "title": title,
                        "category": app_data.get("category") or data.get("category") or "connector",
                        "route": app_data.get("route") or app_data.get("path"),
                        "entry": app_data.get("entry") or app_data.get("main"),
                        "icon": app_data.get("icon"),
                        "connector_id": app_data.get("connector_id")
                        or app_data.get("connectorId")
                        or (
                            app_data.get("id")
                            if isinstance(app_data.get("id"), str)
                            and str(app_data.get("id")).startswith("connector_")
                            else None
                        ),
                        "permissions": app_data.get("permissions")
                        if isinstance(app_data.get("permissions"), list)
                        else [],
                        "actions": actions,
                        "action_count": len(actions),
                    },
                )
            )
    return modules


def _read_app_manifest(path: Path, warnings: list[str]) -> dict[str, Any] | None:
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return _read_yaml(path, warnings)
    if suffix == ".jsonc":
        return _read_jsonc(path, warnings)
    return _read_json(path, warnings)


def _iter_app_manifest_entries(
    data: dict[str, Any],
    manifest: Path,
    warnings: list[str],
) -> list[tuple[str, dict[str, Any]]]:
    raw_apps = data.get("apps")
    entries: list[tuple[str, dict[str, Any]]] = []
    if isinstance(raw_apps, dict):
        for key, value in sorted(raw_apps.items()):
            if not isinstance(value, dict):
                warnings.append(f"{manifest}: app `{key}` must be an object")
                continue
            name = str(value.get("app_id") or value.get("slug") or key).strip()
            if not name:
                warnings.append(f"{manifest}: app key `{key}` resolved to an empty id")
                continue
            entries.append((name, value))
        return entries
    if isinstance(raw_apps, list):
        for index, value in enumerate(raw_apps):
            if not isinstance(value, dict):
                warnings.append(f"{manifest}: apps[{index}] must be an object")
                continue
            name = str(value.get("id") or value.get("name") or "").strip()
            if not name:
                warnings.append(f"{manifest}: apps[{index}] is missing id")
                continue
            entries.append((name, value))
        return entries

    name = str(data.get("id") or data.get("name") or manifest.parent.name).strip()
    if not name:
        warnings.append(f"{manifest}: app manifest resolved to an empty id")
        return []
    return [(name, data)]


def _normalize_app_actions(raw: Any) -> list[dict[str, Any]]:
    items: list[Any]
    if isinstance(raw, dict):
        items = [
            {**value, "name": key} if isinstance(value, dict) else {"name": key}
            for key, value in raw.items()
        ]
    elif isinstance(raw, list):
        items = raw
    else:
        return []

    actions: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            name = item.strip()
            if name:
                actions.append({"name": name, "description": "", "input_schema": {}})
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or "").strip()
        if not name:
            continue
        schema = (
            item.get("input_schema")
            or item.get("inputSchema")
            or item.get("schema")
            or item.get("parameters")
            or {}
        )
        actions.append(
            {
                "name": name,
                "description": str(item.get("description") or ""),
                "input_schema": schema if isinstance(schema, dict) else {},
                "requires_confirmation": bool(
                    item.get("requires_confirmation") or item.get("confirm")
                ),
            }
        )
    return actions


def _scan_managed_agents(
    base: Path,
    warnings: list[str],
) -> tuple[list[PackModule], list[PackModule]]:
    cookbook_root = base / "managed-agent-cookbooks"
    if not cookbook_root.is_dir():
        return [], []
    managed: list[PackModule] = []
    subagents: list[PackModule] = []
    for agent_yaml in sorted(cookbook_root.glob("*/agent.yaml")):
        data = _read_yaml(agent_yaml, warnings) or {}
        name = str(data.get("name") or agent_yaml.parent.name)
        managed.append(
            PackModule(
                id=f"managed:{name}",
                kind="managed_agent",
                name=name,
                path=str(agent_yaml),
                description=_read_readme_summary(agent_yaml.parent, warnings),
                metadata={
                    "model": data.get("model"),
                    "system": data.get("system"),
                    "tools": data.get("tools") or [],
                    "mcp_servers": data.get("mcp_servers") or [],
                    "skills": data.get("skills") or [],
                    "callable_agents": data.get("callable_agents") or [],
                },
            )
        )
        for sub_manifest in sorted((agent_yaml.parent / "subagents").glob("*.yaml")):
            sub = _read_yaml(sub_manifest, warnings) or {}
            sub_name = str(sub.get("name") or sub_manifest.stem)
            subagents.append(
                PackModule(
                    id=f"managed:{name}:subagent:{sub_name}",
                    kind="subagent",
                    name=sub_name,
                    path=str(sub_manifest),
                    description=str(sub.get("description") or ""),
                    source_plugin=name,
                    metadata={
                        "model": sub.get("model"),
                        "tools": sub.get("tools") or [],
                        "system": sub.get("system"),
                    },
                )
            )
    return managed, subagents


def _append_security_warnings(
    warnings: list[str],
    mcp_servers: list[PackModule],
    managed_agents: list[PackModule],
) -> None:
    if mcp_servers:
        warnings.append(
            f"{len(mcp_servers)} MCP server(s) found; require trust review and credentials before enabling."
        )
    write_enabled = [
        agent.name
        for agent in managed_agents
        if "write" in json.dumps(agent.metadata.get("tools", []), ensure_ascii=False).lower()
    ]
    if write_enabled:
        warnings.append(
            "Managed agent(s) reference write-capable tools: "
            + ", ".join(write_enabled)
            + ". Install should require explicit human approval."
        )


def _read_json(path: Path, warnings: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"{path}: JSON parse failed: {exc}")
        return None
    if not isinstance(data, dict):
        warnings.append(f"{path}: expected JSON object")
        return None
    return data


def _read_jsonc(path: Path, warnings: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(_remove_jsonc_trailing_commas(_strip_jsonc_comments(text)))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"{path}: JSONC parse failed: {exc}")
        return None
    if not isinstance(data, dict):
        warnings.append(f"{path}: expected JSONC object")
        return None
    return data


def _strip_jsonc_comments(text: str) -> str:
    result: list[str] = []
    i = 0
    in_string = False
    quote = ""
    escaped = False
    while i < len(text):
        char = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            i += 1
            continue
        if char in {'"', "'"}:
            in_string = True
            quote = char
            result.append(char)
            i += 1
            continue
        if char == "/" and nxt == "/":
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if char == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        result.append(char)
        i += 1
    return "".join(result)


def _remove_jsonc_trailing_commas(text: str) -> str:
    result: list[str] = []
    i = 0
    in_string = False
    quote = ""
    escaped = False
    while i < len(text):
        char = text[i]
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            i += 1
            continue
        if char in {'"', "'"}:
            in_string = True
            quote = char
            result.append(char)
            i += 1
            continue
        if char == ",":
            j = i + 1
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j] in "]}":
                i += 1
                continue
        result.append(char)
        i += 1
    return "".join(result)


def _read_yaml(path: Path, warnings: list[str]) -> dict[str, Any] | None:
    if yaml is None:
        warnings.append("PyYAML unavailable; YAML managed-agent manifests were skipped")
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:  # type: ignore[attr-defined]
        warnings.append(f"{path}: YAML parse failed: {exc}")
        return None
    if not isinstance(data, dict):
        warnings.append(f"{path}: expected YAML mapping")
        return None
    return data


def _read_text(path: Path, warnings: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"{path}: read failed: {exc}")
        return ""


def _parse_frontmatter(
    text: str,
    path: Path,
    warnings: list[str],
) -> dict[str, Any]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    if yaml is None:
        warnings.append("PyYAML unavailable; markdown frontmatter was skipped")
        return {}
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:  # type: ignore[attr-defined]
        warnings.append(f"{path}: frontmatter parse failed: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _coerce_tool_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _first_heading_or_line(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip()
        if not clean or clean == "---":
            continue
        if clean.startswith("#"):
            return clean.lstrip("#").strip()
        if not clean.startswith(("name:", "description:", "tools:")):
            return clean[:180]
    return ""


def _count_assets(directory: Path) -> int:
    return sum(1 for path in directory.rglob("*") if path.is_file() and path.name != "SKILL.md")


def _read_readme_summary(directory: Path, warnings: list[str]) -> str:
    readme = directory / "README.md"
    if not readme.is_file():
        return ""
    text = _read_text(readme, warnings)
    for line in text.splitlines():
        clean = line.strip()
        if clean and not clean.startswith("#"):
            return clean[:240]
    return ""


def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1)


def _slugify_agent_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "imported_agent"


def _require_safe_pack_name(value: str, *, label: str) -> str:
    name = str(value or "").strip()
    if not _SAFE_PACK_NAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid {label}: only alphanumeric characters, hyphens, and underscores are allowed"
        )
    return name


def _ensure_real_directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} must be a real directory: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"{label} must be a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{label} must be a real directory: {path}")
    return path.resolve()


def _require_under(path: Path, root: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    root_resolved = root.expanduser().resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside pack root: {resolved}") from exc
    return resolved


def _require_real_file_under(path: Path, root: Path, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    resolved = _require_under(path, root, label)
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} not found: {resolved}")
    return resolved


def _require_real_dir_under(path: Path, root: Path, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    resolved = _require_under(path, root, label)
    if not resolved.is_dir():
        raise NotADirectoryError(f"{label} is not a directory: {resolved}")
    return resolved


def _contains_symlink(directory: Path) -> bool:
    return any(child.is_symlink() for child in directory.rglob("*"))


def _cleanup_copied_skill_dirs(skills_root: Path, copied_skills: list[str]) -> None:
    for skill_name in copied_skills:
        try:
            safe_name = _require_safe_pack_name(skill_name, label="skill name")
        except ValueError:
            continue
        target = skills_root / safe_name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target, ignore_errors=True)


def _titleize(value: str) -> str:
    return (
        " ".join(part.capitalize() for part in re.split(r"[-_\s]+", value.strip()) if part) or value
    )


def _select_agent_skills(body: str, plugin_skills: list[PackModule]) -> list[PackModule]:
    lowered = body.lower()
    selected = [skill for skill in plugin_skills if skill.name.lower() in lowered]
    if selected:
        return selected
    return plugin_skills


def _copy_pack_skills(
    skills: list[PackModule],
    skills_root: Path,
    *,
    pack_root: Path,
) -> tuple[list[str], list[str]]:
    copied: list[str] = []
    skipped: list[str] = []
    skills_base = _ensure_real_directory(skills_root, "skills root")
    try:
        for skill in skills:
            skill_name = _require_safe_pack_name(skill.name, label="skill name")
            source = _require_real_dir_under(Path(skill.path), pack_root, "skill source")
            if _contains_symlink(source):
                raise ValueError(f"skill source must not contain symlinks: {source}")
            if not (source / "SKILL.md").is_file():
                raise FileNotFoundError(f"skill source missing SKILL.md: {source}")
            target = skills_base / skill_name
            if target.exists() or target.is_symlink():
                skipped.append(skill_name)
                continue
            shutil.copytree(source, target)
            copied.append(skill_name)
        return copied, skipped
    except Exception:
        _cleanup_copied_skill_dirs(skills_base, copied)
        raise


def _write_agent_avatar(path: Path, label: str) -> None:
    atomic_write_text(path, pixel_agent_avatar_svg(label), newline=None)


__all__ = [
    "AgentPackAgentNotFound",
    "AgentPackImportResult",
    "AgentPackPreview",
    "PackModule",
    "import_agent_from_pack",
    "scan_agent_pack",
]
