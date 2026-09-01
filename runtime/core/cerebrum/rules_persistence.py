from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from runtime.platform.models import SkillId

from .planner import Rule

_FRONTMATTER_PREFIX = "rules:"


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def dump_rules_to_yaml(rules: list[Rule]) -> str:
    lines = ["rules:"]
    if not rules:
        return lines[0] + " []\n"

    for r in rules:
        lines.append(f"  - name: {r.name}")
        if r.intent_types:
            lines.append(f"    intent_types: [{_list_inline(r.intent_types)}]")
        if r.keywords:
            lines.append(f"    keywords: [{_list_inline(r.keywords)}]")
        if r.skill_sequence:
            lines.append(
                f"    skill_sequence: [{_list_inline([str(s) for s in r.skill_sequence])}]"
            )
        if r.node_args_templates:
            lines.append("    node_args_templates:")
            for t in r.node_args_templates:
                if t is None:
                    lines.append("      - null")
                else:
                    lines.append(f"      - {_dict_inline(t)}")
        if r.priority != 0:
            lines.append(f"    priority: {r.priority}")
    return "\n".join(lines) + "\n"


def dump_rules_to_file(rules: list[Rule], path: Path | str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump_rules_to_yaml(rules), encoding="utf-8")
    return p


def _list_inline(items: list[str]) -> str:
    out = []
    for item in items:
        s = str(item)
        if _needs_quoting(s):
            s = "'" + s.replace("'", "''") + "'"
        out.append(s)
    return ", ".join(out)


def _dict_inline(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False)


def _needs_quoting(s: str) -> bool:
    return bool(re.search(r"[,\[\]{}:#&*!|>'\"%@`]|\s", s)) or not s


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def load_rules_from_yaml(text: str) -> list[Rule]:
    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(text) or {}
    except ImportError:
        data = _fallback_parse(text)

    raw = data.get("rules") if isinstance(data, dict) else None
    if raw is None or raw == []:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"'rules' must be a list, got {type(raw).__name__}")

    out: list[Rule] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"rules[{i}] must be a mapping")
        out.append(
            Rule(
                name=str(item["name"]),
                intent_types=list(item.get("intent_types") or []),
                keywords=list(item.get("keywords") or []),
                skill_sequence=[SkillId(s) for s in (item.get("skill_sequence") or [])],
                node_args_templates=list(item.get("node_args_templates") or []),
                priority=int(item.get("priority", 0)),
            )
        )
    return out


def load_rules_from_file(path: Path | str) -> list[Rule]:
    p = Path(path)
    if not p.exists():
        return []
    return load_rules_from_yaml(p.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _fallback_parse(text: str) -> dict[str, Any]:
    if not text.strip().startswith(_FRONTMATTER_PREFIX):
        return {}
    rules: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_templates: list | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line == "rules: []":
            return {"rules": []}
        if line == "rules:":
            continue
        if line.startswith("  - name:"):
            if current is not None:
                rules.append(current)
            current = {"name": line.split(":", 1)[1].strip()}
            current_templates = None
            continue
        if current is None:
            continue
        if line.startswith("    node_args_templates:"):
            current_templates = []
            current["node_args_templates"] = current_templates
            continue
        if current_templates is not None and line.startswith("      - "):
            val = line[len("      - ") :].strip()
            if val == "null":
                current_templates.append(None)
            else:
                try:
                    current_templates.append(json.loads(val))
                except json.JSONDecodeError:
                    current_templates.append(None)
            continue
        current_templates = None
        stripped = line.lstrip()
        if ":" not in stripped:
            continue
        key, _, raw_val = stripped.partition(":")
        key = key.strip()
        raw_val = raw_val.strip()
        current[key] = _parse_scalar(raw_val)
    if current is not None:
        rules.append(current)
    return {"rules": rules}


def _parse_scalar(raw: str) -> Any:
    if not raw:
        return ""
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        parts = [p.strip() for p in inner.split(",")]
        out: list[Any] = []
        for p in parts:
            if (p.startswith("'") and p.endswith("'")) or (p.startswith('"') and p.endswith('"')):
                s = p[1:-1]
                if p[0] == "'":
                    s = s.replace("''", "'")
                out.append(s)
            else:
                out.append(p)
        return out
    if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        s = raw[1:-1]
        if raw[0] == "'":
            s = s.replace("''", "'")
        return s
    try:
        return int(raw)
    except ValueError:  # noqa: BLE001 — int/float coercion chain; fallthrough to next type is the semantic
        pass
    try:
        return float(raw)
    except ValueError:  # noqa: BLE001 — int/float coercion chain; fallthrough returns raw
        pass
    return raw
