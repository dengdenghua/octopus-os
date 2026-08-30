from __future__ import annotations


def normalize_config(config: dict[str, object]) -> dict[str, object]:
    return {"max_turns": int(config.get("max_turns", 8))}
