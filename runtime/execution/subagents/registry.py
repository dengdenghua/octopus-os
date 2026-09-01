"""Claude-style subagent registry.

Subagent definitions live in Markdown files with YAML-ish frontmatter:

    ---
    name: code-reviewer
    description: Reviews risky changes.
    tools: Read, Grep, Bash
    model: sonnet
    ---

    You are a senior code reviewer...

Project definitions are loaded from ``<project>/.claude/agents/*.md`` and
user definitions from ``~/.claude/agents/*.md``. Project definitions override
user definitions with the same name.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SubagentDefinition:
    name: str
    description: str
    system_prompt: str
    tools: tuple[str, ...] = ()
    model: str | None = None
    backend: str | None = None
    source_path: str = ""
    scope: str = "project"
    capabilities: tuple[str, ...] = ()

    def to_wire(self, *, include_prompt: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "tools": list(self.tools),
            "model": self.model,
            "backend": self.backend,
            "source_path": self.source_path,
            "scope": self.scope,
            "capabilities": list(self.capabilities),
        }
        if include_prompt:
            out["system_prompt"] = self.system_prompt
        return out


class SubagentRegistry:
    def __init__(self, definitions: Iterable[SubagentDefinition] = ()) -> None:
        self._defs: dict[str, SubagentDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: SubagentDefinition) -> None:
        self._defs[definition.name] = definition

    def has(self, name: str) -> bool:
        return name in self._defs

    def get(self, name: str) -> SubagentDefinition:
        return self._defs[name]

    def all(self) -> list[SubagentDefinition]:
        return [self._defs[name] for name in sorted(self._defs)]

    def all_names(self) -> list[str]:
        return sorted(self._defs)

    def supports(self, name: str, capability: str) -> bool:
        """Whether a registered definition declares ``capability``.
        Unknown names report False (fail closed)."""
        definition = self._defs.get(name)
        if definition is None:
            return False
        return capability in definition.capabilities

    def capabilities_of(self, name: str) -> tuple[str, ...]:
        """Declared capabilities of a registered definition."""
        definition = self._defs.get(name)
        return definition.capabilities if definition is not None else ()


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(part) for part in inner.split(",")]
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    return _strip_quotes(value)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()

    end_idx: int | None = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return {}, text.strip()

    meta: dict[str, Any] = {}
    for raw in lines[1:end_idx]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        meta[key.strip()] = _parse_scalar(value)
    body = "\n".join(lines[end_idx + 1 :]).strip()
    return meta, body


def _coerce_tools(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        if not value.strip():
            return ()
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


def _coerce_capabilities(value: Any) -> tuple[str, ...]:
    """Normalize a ``capabilities`` frontmatter value into a tuple of
    canonical capability names (lowercase, deduplicated, order kept)."""
    if value is None:
        return ()
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
    elif isinstance(value, list):
        parts = [str(p).strip() for p in value if str(p).strip()]
    else:
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        key = part.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return tuple(out)


def load_subagent_file(path: Path, *, scope: str) -> SubagentDefinition:
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    name = str(meta.get("name") or path.stem).strip()
    if not name:
        raise ValueError(f"subagent file {path} has no name")
    description = str(meta.get("description") or "").strip()
    model = meta.get("model")
    model_str = str(model).strip() if model not in (None, "") else None
    backend = meta.get("backend")
    backend_str = str(backend).strip() if backend not in (None, "") else None
    return SubagentDefinition(
        name=name,
        description=description,
        system_prompt=body,
        tools=_coerce_tools(meta.get("tools")),
        model=model_str,
        backend=backend_str,
        source_path=str(path),
        scope=scope,
        capabilities=_coerce_capabilities(meta.get("capabilities")),
    )


def _load_dir(path: Path, *, scope: str) -> list[SubagentDefinition]:
    if not path.exists() or not path.is_dir():
        return []
    out: list[SubagentDefinition] = []
    for item in sorted(path.glob("*.md")):
        if item.is_file():
            out.append(load_subagent_file(item, scope=scope))
    return out


def load_subagent_registry(
    *,
    project_root: Path | None = None,
    user_home: Path | None = None,
) -> SubagentRegistry:
    if project_root is None:
        from runtime.platform.process.paths import project_root as _project_root

        project_root = _project_root()
    user_home = user_home or Path.home()
    registry = SubagentRegistry()

    # Lower precedence first; project-level definitions override user-level.
    for definition in _load_dir(user_home / ".claude" / "agents", scope="user"):
        registry.register(definition)
    for definition in _load_dir(project_root / ".claude" / "agents", scope="project"):
        registry.register(definition)
    return registry


__all__ = [
    "SubagentDefinition",
    "SubagentRegistry",
    "load_subagent_file",
    "load_subagent_registry",
]
