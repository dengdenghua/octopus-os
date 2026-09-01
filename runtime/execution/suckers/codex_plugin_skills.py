from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .market_skills import (
    _MAX_INSTRUCTIONS_BYTES,
    _make_prompt_handler,
    _parse_frontmatter,
)
from .registry import Skill, SkillRegistry

_LOG = logging.getLogger(__name__)


def _safe_action_part(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(value or "").strip())
    return cleaned.strip("_") or fallback


def plugin_action_name(plugin_id: str, skill_name: str) -> str:
    """Return the runtime action name for a Codex plugin skill.

    Keep this provider-tool-safe: letters, digits, underscores, and hyphens.
    The double underscore separates the plugin id from the plugin-local skill id,
    so ``use_capability(action="prototype")`` can still resolve it by tail.
    """

    plugin_part = _safe_action_part(plugin_id, "plugin")
    skill_part = _safe_action_part(skill_name, "skill")
    return f"{plugin_part}__{skill_part}"


def action_tail(action_name: str) -> str:
    tail = str(action_name or "").rsplit(".", 1)[-1].rsplit("/", 1)[-1]
    if "__" in tail:
        tail = tail.rsplit("__", 1)[-1]
    return tail


@dataclass(frozen=True)
class CodexPluginSkillLoad:
    plugin_id: str
    found: bool
    loaded_actions: tuple[str, ...] = ()
    already_registered: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    error: str = ""

    @property
    def handled(self) -> bool:
        return self.found


@dataclass(frozen=True)
class CodexPluginSkillLoadReport:
    loads: tuple[CodexPluginSkillLoad, ...] = field(default_factory=tuple)

    @property
    def handled_plugin_ids(self) -> tuple[str, ...]:
        return tuple(load.plugin_id for load in self.loads if load.handled)

    def render_observation(self) -> str:
        if not self.loads:
            return ""
        parts: list[str] = []
        for load in self.loads:
            if not load.found:
                continue
            if load.loaded_actions:
                actions = ", ".join(f"`{name}`" for name in load.loaded_actions[:12])
                suffix = "" if len(load.loaded_actions) <= 12 else " ..."
                parts.append(
                    f"Injected Codex plugin `{load.plugin_id}` actions: {actions}{suffix}.",
                )
            elif load.already_registered:
                actions = ", ".join(f"`{name}`" for name in load.already_registered[:12])
                suffix = "" if len(load.already_registered) <= 12 else " ..."
                parts.append(
                    f"Codex plugin `{load.plugin_id}` actions already registered: "
                    f"{actions}{suffix}.",
                )
            elif load.error:
                parts.append(f"Codex plugin `{load.plugin_id}` unavailable: {load.error}.")
            else:
                parts.append(
                    f"Codex plugin `{load.plugin_id}` was found but exposes no prompt actions.",
                )
        return " ".join(parts)


def _match_plugin(
    plugins: list[dict[str, Any]],
    plugin_id: str,
) -> dict[str, Any] | None:
    wanted = str(plugin_id or "").strip().lower()
    if not wanted:
        return None
    for plugin in plugins:
        candidates = {
            str(plugin.get("id") or "").strip().lower(),
            str(plugin.get("name") or "").strip().lower(),
        }
        if wanted in candidates:
            return plugin
    return None


def _skill_roots(plugin_dir: Path, manifest: dict[str, Any]) -> list[Path]:
    raw = manifest.get("skills") or "./skills"
    if isinstance(raw, str):
        return [plugin_dir / raw]
    if isinstance(raw, list):
        return [plugin_dir / str(item) for item in raw if str(item).strip()]
    return []


def _iter_skill_files(plugin_dir: Path, manifest: dict[str, Any]) -> list[Path]:
    skill_files: list[Path] = []
    seen: set[Path] = set()
    for root in _skill_roots(plugin_dir, manifest):
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if not resolved.is_dir():
            continue
        for skill_file in sorted(resolved.rglob("SKILL.md")):
            try:
                real = skill_file.resolve()
                real.relative_to(plugin_dir.resolve())
            except (OSError, ValueError):
                continue
            if real in seen:
                continue
            seen.add(real)
            skill_files.append(real)
    return skill_files


def _read_manifest(plugin_dir: Path) -> dict[str, Any]:
    try:
        import json

        data = json.loads(
            (plugin_dir / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8",
            ),
        )
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _allowed_tuple(meta: dict[str, Any]) -> tuple[str, ...]:
    allowed = meta.get("allowed-tools") or meta.get("allowed_tools") or ()
    if isinstance(allowed, str):
        return tuple(t.strip() for t in allowed.split(",") if t.strip())
    if isinstance(allowed, (list, tuple)):
        return tuple(str(t).strip() for t in allowed if str(t).strip())
    return ()


def _affinity(meta: dict[str, Any], plugin_id: str) -> list[str]:
    raw = meta.get("tags") or meta.get("affinity") or []
    if isinstance(raw, str):
        affinity = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, (list, tuple)):
        affinity = [str(item).strip() for item in raw if str(item).strip()]
    else:
        affinity = []
    for tag in ("plugin", "codex-plugin", f"plugin:{plugin_id}"):
        if tag not in affinity:
            affinity.append(tag)
    return affinity


def _make_codex_plugin_handler(
    *,
    skill_dir: Path,
    action_name: str,
    plugin_id: str,
    plugin_dir: Path,
    plugin_skill_name: str,
    body: str,
    allowed_tools: tuple[str, ...],
):
    base_handler = _make_prompt_handler(skill_dir, action_name, body, allowed_tools)

    def _handler(**kw: Any) -> dict[str, Any]:
        result = base_handler(**kw)
        if isinstance(result, dict):
            result.update(
                {
                    "plugin": plugin_id,
                    "plugin_root": str(plugin_dir.resolve()),
                    "plugin_skill": plugin_skill_name,
                    "action": action_name,
                }
            )
        return result

    _handler.__name__ = f"_codex_plugin_skill_{action_name}"
    return _handler


def load_codex_plugin_skills(
    registry: SkillRegistry,
    plugin_ids: list[str] | tuple[str, ...],
    *,
    roots: list[Path] | None = None,
    verify_tests: bool = False,
) -> CodexPluginSkillLoadReport:
    """Register prompt actions from Codex-format plugins for this registry.

    This intentionally injects only ``skills/*/SKILL.md`` prompt actions. It
    does not auto-enable MCP servers, app entries, or commands; those remain
    governed by the plugin permission-review surfaces.
    """

    ids = tuple(dict.fromkeys(str(item).strip() for item in plugin_ids if str(item).strip()))
    if not ids:
        return CodexPluginSkillLoadReport(())
    try:
        from runtime.platform.plugins.codex_discovery import discover_codex_plugins

        plugins = discover_codex_plugins(roots)
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        _LOG.debug("Codex plugin discovery failed: %s", exc, exc_info=True)
        return CodexPluginSkillLoadReport(
            tuple(CodexPluginSkillLoad(plugin_id=pid, found=False, error=str(exc)) for pid in ids)
        )

    loads: list[CodexPluginSkillLoad] = []
    for requested_id in ids:
        plugin = _match_plugin(plugins, requested_id)
        if plugin is None:
            loads.append(CodexPluginSkillLoad(plugin_id=requested_id, found=False))
            continue

        plugin_id = str(plugin.get("id") or requested_id).strip()
        smoke = plugin.get("smoke") if isinstance(plugin.get("smoke"), dict) else {}
        if smoke and smoke.get("ok") is False:
            issues = smoke.get("issues") if isinstance(smoke.get("issues"), list) else []
            loads.append(
                CodexPluginSkillLoad(
                    plugin_id=plugin_id,
                    found=True,
                    error=", ".join(str(item) for item in issues) or "smoke check failed",
                )
            )
            continue

        plugin_dir = Path(str(plugin.get("path") or "")).expanduser()
        if not plugin_dir.is_dir():
            loads.append(
                CodexPluginSkillLoad(
                    plugin_id=plugin_id,
                    found=True,
                    error="plugin directory is missing",
                )
            )
            continue

        manifest = _read_manifest(plugin_dir)
        loaded: list[str] = []
        already: list[str] = []
        skipped: list[str] = []
        for skill_file in _iter_skill_files(plugin_dir, manifest):
            skill_dir = skill_file.parent
            try:
                text = skill_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                skipped.append(str(skill_file))
                continue
            meta, body = _parse_frontmatter(text)
            plugin_skill_name = _safe_action_part(
                str(meta.get("name") or skill_dir.name),
                skill_dir.name,
            )
            action_name = plugin_action_name(plugin_id, plugin_skill_name)
            if registry.has(action_name):
                already.append(action_name)
                continue
            if len(body.encode("utf-8")) > _MAX_INSTRUCTIONS_BYTES:
                body = (
                    body.encode("utf-8")[:_MAX_INSTRUCTIONS_BYTES].decode(
                        "utf-8",
                        errors="ignore",
                    )
                    + "\n\n[... truncated ...]"
                )
            description = (
                str(meta.get("description") or "").strip()
                or f"Prompt action from Codex plugin {plugin_id}/{plugin_skill_name}."
            )
            try:
                registry.register(
                    Skill(
                        name=action_name,
                        summary=f"{plugin_id}: {plugin_skill_name}",
                        description=description,
                        affinity=_affinity(meta, plugin_id),
                        cost_profile="low",
                        trusted_source=f"plugin://{plugin_id}/{plugin_skill_name}",
                        handler=_make_codex_plugin_handler(
                            skill_dir=skill_dir,
                            action_name=action_name,
                            plugin_id=plugin_id,
                            plugin_dir=plugin_dir,
                            plugin_skill_name=plugin_skill_name,
                            body=body,
                            allowed_tools=_allowed_tuple(meta),
                        ),
                    ),
                    verify_tests=verify_tests,
                )
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"{action_name}: {type(exc).__name__}: {exc}")
                continue
            loaded.append(action_name)

        loads.append(
            CodexPluginSkillLoad(
                plugin_id=plugin_id,
                found=True,
                loaded_actions=tuple(loaded),
                already_registered=tuple(already),
                skipped=tuple(skipped),
            )
        )

    return CodexPluginSkillLoadReport(tuple(loads))


__all__ = [
    "CodexPluginSkillLoad",
    "CodexPluginSkillLoadReport",
    "action_tail",
    "load_codex_plugin_skills",
    "plugin_action_name",
]
