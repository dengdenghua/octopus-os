from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from runtime.adapters.instrumentation import trace_stage

from .registry import Skill, SkillRegistry

_FRONTMATTER_RE = re.compile(
    r"\A\s*---\s*\n(.*?)\n---\s*(?:\n|$)",
    re.DOTALL,
)


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def dump_forged_skill_to_md(candidate: Any, out_dir: Path | str) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{candidate.name}.md"

    underlying = list(candidate.underlying_sequence)
    seq_str = ", ".join(underlying)

    lines = [
        "---",
        f"name: {candidate.name}",
        f"description: '{_escape(candidate.description)}'",
        f"trusted_source: skill://forged/{candidate.candidate_id}",
        "affinity: [forged]",
        "cost_profile: mid",
        "origin: forged",
        f"candidate_id: {candidate.candidate_id}",
        f"underlying_sequence: [{seq_str}]",
        f"source_sample_count: {candidate.source_sample_count}",
        f"source_success_rate: {candidate.source_success_rate}",
        f"path_signature: '{_escape(candidate.path_signature)}'",
        "step_templates_json: "
        f"'{_escape(json.dumps(candidate.step_templates, ensure_ascii=False))}'",
        "---",
        "",
        f"# {candidate.name}",
        "",
        f"Auto-forged skill · aggregates `{' → '.join(underlying)}`.",
        "",
        f"Source: {candidate.source_sample_count} success samples · "
        f"{candidate.source_success_rate * 100:.1f}% success rate.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _escape(s: str) -> str:
    return (s or "").replace("'", "''")


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def load_forged_skills_from_dir(
    dir_path: Path | str,
    registry: SkillRegistry,
    *,
    skip_missing_subskills: bool = True,
) -> list[str]:
    d = Path(dir_path)
    if not d.is_dir():
        return []

    loaded: list[str] = []
    with trace_stage("suckers.forged_persistence.load_dir") as span:
        for md in sorted(d.glob("*.md")):
            try:
                meta = _parse_forged_md(md)
            except (OSError, ValueError):  # noqa: BLE001
                continue  # Implementation note.

            if meta is None:
                continue

            underlying = meta["underlying_sequence"]
            missing = [s for s in underlying if not registry.has(s)]
            if missing:
                if skip_missing_subskills:
                    continue
                raise ValueError(f"{md.name}: sub-skills missing from registry: {missing}")

            handler = _build_composite_handler(
                underlying,
                registry,
                step_templates=meta.get("step_templates", []),
            )
            skill = Skill(
                name=meta["name"],
                description=meta.get("description", ""),
                affinity=meta.get("affinity", ["forged"]),
                cost_profile=meta.get("cost_profile", "mid"),
                trusted_source=meta["trusted_source"],
                handler=handler,
            )
            try:
                registry.register(skill, verify_tests=False)
                loaded.append(skill.name)
            except Exception:  # Implementation note.
                continue

        span.set_attribute("echo.forged.loaded", len(loaded))
    return loaded


def _build_composite_handler(
    underlying: list[str],
    registry: SkillRegistry,
    *,
    step_templates: list[dict[str, Any]] | None = None,
) -> Any:
    """重建 meta-handler · 依次跑 sub-skills · 与 SkillForge._build_meta_handler 一致。

    Thin public entry point — the live implementation is
    :func:`_build_composite_handler_with_templates`.
    """
    return _build_composite_handler_with_templates(
        underlying,
        registry,
        step_templates=step_templates,
    )


def _build_composite_handler_with_templates(
    underlying: list[str],
    registry: SkillRegistry,
    *,
    step_templates: list[dict[str, Any]] | None = None,
) -> Any:
    templates = list(step_templates or [])
    has_templates = len(templates) == len(underlying) and any(template for template in templates)

    def _meta(**kwargs: Any) -> dict[str, Any]:
        from runtime.core.graph_runtime.runtime import (
            TemplateResolutionError,
            resolve_templates,
        )

        outputs: dict[str, Any] = {}
        success = True
        failed_at: int | None = None
        error_type: str | None = None
        error_msg: str | None = None

        for i, skill_name in enumerate(underlying):
            skill = registry.get(skill_name)
            try:
                if has_templates:
                    if i == 0:
                        call_args = {**templates[i], **kwargs}
                    else:
                        call_args = resolve_templates(templates[i], outputs)
                else:
                    call_args = dict(kwargs)
                    if i > 0 and f"n{i - 1}" in outputs:
                        call_args.setdefault("_prev", outputs[f"n{i - 1}"])
                # Safety chokepoint. A forged composite calls each sub-skill's
                # handler DIRECTLY (not via executor.execute_step), so EVERY
                # pre-execution gate — capability-permission, injection-taint,
                # immunity, file-safety — is bypassed. A tainted turn (or a
                # denied / untrusted / credential-targeting sub-skill) could
                # otherwise be laundered through a low-risk-named composite.
                # Re-apply the shared gate sequence per sub-skill, fail-closed
                # (defer_taint_if_handled=False: the approval gate reviewed the
                # OUTER composite, not this inner skill).
                from runtime.execution.tool_engine.skill_gate import (
                    gate_inner_dispatch,
                )

                _block = gate_inner_dispatch(
                    skill,
                    call_args,
                    caller="forged_composite",
                )
                if _block is not None:
                    success = False
                    failed_at = i
                    error_type = _block.error_type
                    error_msg = _block.message
                    break
                outputs[f"n{i}"] = skill.handler(**call_args)
            except TemplateResolutionError as e:
                success = False
                failed_at = i
                error_type = "TemplateResolutionError"
                error_msg = str(e)
                break
            except Exception as e:  # noqa: BLE001
                success = False
                failed_at = i
                error_type = type(e).__name__
                error_msg = str(e)
                break

        result: dict[str, Any] = {
            "composite_output": outputs,
            "steps": len(outputs),
            "total_steps": len(underlying),
            "success": success,
        }
        if not success:
            result["failed_at"] = failed_at
            result["error_type"] = error_type
            result["error"] = error_msg
        return result

    return _meta


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _parse_forged_md(path: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    body = m.group(1)

    meta: dict[str, Any] = {}
    for line in body.splitlines():
        line = line.rstrip()
        if not line or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        meta[key] = _parse_value(raw)

    if "underlying_sequence" not in meta:
        return None
    if "name" not in meta or "trusted_source" not in meta:
        return None
    if not isinstance(meta["underlying_sequence"], list):
        return None
    meta["underlying_sequence"] = [str(s) for s in meta["underlying_sequence"]]
    raw_templates = meta.get("step_templates_json")
    if isinstance(raw_templates, str) and raw_templates:
        try:
            parsed_templates = json.loads(raw_templates)
            if isinstance(parsed_templates, list):
                meta["step_templates"] = [
                    dict(item) for item in parsed_templates if isinstance(item, dict)
                ]
        except Exception:  # noqa: BLE001
            meta["step_templates"] = []
    return meta


def _parse_value(raw: str) -> Any:
    if not raw:
        return ""
    # quoted string
    if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        s = raw[1:-1]
        if raw[0] == "'":
            s = s.replace("''", "'")
        return s
    # list [a, b, c]
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        parts = [p.strip() for p in inner.split(",")]
        return [_parse_value(p) for p in parts]
    try:
        return int(raw)
    except ValueError:  # noqa: BLE001 — int/float coerce fallthrough
        pass
    try:
        return float(raw)
    except ValueError:  # noqa: BLE001 — int/float coerce fallthrough
        pass
    return raw
