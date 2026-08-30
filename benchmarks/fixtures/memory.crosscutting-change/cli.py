from __future__ import annotations

from config_schema import normalize_config


def turn_limit(config: dict[str, object]) -> int:
    return int(normalize_config(config)["max_turns"])
