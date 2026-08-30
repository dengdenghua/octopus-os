"""Workflow meta validation (dsh ``workflow-worker-thread/src/meta.ts``).

``validate_meta`` throws ``META_INVALID`` naming every violation (unknown
fields, missing/mistyped ``name``/``description``, malformed ``phases``)
and returns a NORMALIZED copy, so the engine never aliases the caller's
object.
"""

from __future__ import annotations

from typing import Any

from .types import WorkflowError, WorkflowMeta, WorkflowPhase

_ALLOWED_META_KEYS = frozenset({"name", "description", "whenToUse", "phases"})
_ALLOWED_PHASE_KEYS = frozenset({"title", "detail", "provider", "model"})


def _shape_violations(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["meta must be an object"]
    violations: list[str] = []
    for key in sorted(value):
        if key not in _ALLOWED_META_KEYS:
            violations.append(f'unknown meta field "{key}"')
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        violations.append("meta.name must be a non-empty string")
    description = value.get("description")
    if not isinstance(description, str) or not description.strip():
        violations.append("meta.description must be a non-empty string")
    when_to_use = value.get("whenToUse")
    if when_to_use is not None and (not isinstance(when_to_use, str) or not when_to_use.strip()):
        violations.append("meta.whenToUse must be a non-empty string when present")
    phases = value.get("phases")
    if phases is not None:
        if not isinstance(phases, list):
            violations.append("meta.phases must be an array")
        else:
            for index, phase in enumerate(phases):
                if not isinstance(phase, dict):
                    violations.append(f"meta.phases[{index}] must be an object")
                    continue
                for key in sorted(phase):
                    if key not in _ALLOWED_PHASE_KEYS:
                        violations.append(f'meta.phases[{index}] has unknown field "{key}"')
                title = phase.get("title")
                if not isinstance(title, str) or not title.strip():
                    violations.append(f"meta.phases[{index}].title must be a non-empty string")
                for key in ("detail", "provider", "model"):
                    raw = phase.get(key)
                    if raw is not None and (not isinstance(raw, str) or not raw.strip()):
                        violations.append(
                            f"meta.phases[{index}].{key} must be a non-empty string when present"
                        )
    return violations


def validate_meta(value: Any) -> WorkflowMeta:
    """Validate a caller-provided meta value against the seam contract.

    Raises :class:`WorkflowError` (``META_INVALID``) naming every violation.
    """
    violations = _shape_violations(value)
    if violations:
        raise WorkflowError(f"invalid meta: {'; '.join(violations)}", "META_INVALID")
    phases = []
    for phase in value.get("phases") or []:
        phases.append(
            WorkflowPhase(
                title=phase["title"].strip(),
                detail=phase.get("detail"),
                provider=phase.get("provider"),
                model=phase.get("model"),
            )
        )
    return WorkflowMeta(
        name=value["name"].strip(),
        description=value["description"].strip(),
        when_to_use=value.get("whenToUse"),
        phases=phases,
    )


__all__ = ["validate_meta"]
