from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .schema import AgentConfig

try:
    import yaml  # type: ignore[import-untyped]

    YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    YAML_AVAILABLE = False
    yaml = None  # type: ignore[assignment]


_BRACED_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")
_BARE_ENV_PATTERN = re.compile(r"\$([A-Z_][A-Z0-9_]*)")
_MAX_EXTENDS_DEPTH = 10  # Prevent circular references


class ConfigLoadError(ValueError):
    pass


def _interpolate_env(value: Any) -> Any:
    if isinstance(value, str):
        # Keep the convenient bare ``$NAME`` form only when it occupies the
        # complete scalar. Expanding it inside arbitrary secrets corrupts
        # standard values such as bcrypt hashes (``$2b$12$ABC...``).
        bare = _BARE_ENV_PATTERN.fullmatch(value)
        if bare is not None:
            return os.environ.get(bare.group(1), "")
        return _BRACED_ENV_PATTERN.sub(
            lambda match: os.environ.get(match.group(1), ""),
            value,
        )
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries. override takes precedence."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _resolve_extends(
    raw_data: dict[str, Any],
    config_path: Path,
    depth: int = 0,
    visited: set[Path] | None = None,
) -> dict[str, Any]:
    """Resolve extends chain and merge configurations."""
    if visited is None:
        visited = set()

    if depth > _MAX_EXTENDS_DEPTH:
        raise ConfigLoadError(
            f"extends chain too deep (>{_MAX_EXTENDS_DEPTH}), circular reference?"
        )

    # Check if this config has already been visited
    resolved_path = config_path.resolve()
    if resolved_path in visited:
        raise ConfigLoadError(f"circular extends detected: {resolved_path}")
    visited.add(resolved_path)

    # No extends, return as-is
    extends_value = raw_data.get("extends")
    if not extends_value:
        return raw_data

    # Resolve extends path (relative to current config directory)
    if not isinstance(extends_value, str):
        raise ConfigLoadError(f"extends must be a string, got {type(extends_value).__name__}")

    base_config_path = config_path.parent / extends_value
    if not base_config_path.exists():
        raise ConfigLoadError(f"extends target not found: {base_config_path}")

    # Load and resolve base config recursively
    if not YAML_AVAILABLE:
        raise ConfigLoadError("PyYAML required for extends support")

    try:
        base_raw = yaml.safe_load(base_config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigLoadError(f"YAML parse failed in {base_config_path}: {e}") from e

    if base_raw is None:
        base_raw = {}
    if not isinstance(base_raw, dict):
        raise ConfigLoadError(
            f"extends target must be a mapping, got {type(base_raw).__name__} in {base_config_path}"
        )

    # Recursively resolve base config's extends
    base_resolved = _resolve_extends(base_raw, base_config_path, depth + 1, visited)

    # Remove extends key from current config before merging
    current_data = {k: v for k, v in raw_data.items() if k != "extends"}

    # Deep merge: base <- current
    return _deep_merge(base_resolved, current_data)


def load_from_dict(data: dict[str, Any]) -> AgentConfig:
    resolved = _interpolate_env(data)
    try:
        return AgentConfig.model_validate(resolved)
    except Exception as e:
        raise ConfigLoadError(f"schema validation failed: {e}") from e


def load_from_yaml(path: str | Path, resolve_extends: bool = True) -> AgentConfig:
    """Load config from YAML file with optional extends support.

    Args:
        path: Path to the YAML config file
        resolve_extends: If True, resolve extends chain before validation

    Returns:
        Validated AgentConfig instance

    Raises:
        ConfigLoadError: If file not found, YAML invalid, or validation fails
    """
    if not YAML_AVAILABLE:
        raise ConfigLoadError("PyYAML not installed · `pip install PyYAML` or use load_from_dict()")
    p = Path(path)
    if not p.exists():
        raise ConfigLoadError(f"config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigLoadError(f"YAML parse failed in {p}: {e}") from e
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigLoadError(f"top-level YAML must be a mapping, got {type(raw).__name__}")

    # Resolve extends chain if requested
    if resolve_extends:
        raw = _resolve_extends(raw, p)

    return load_from_dict(raw)
