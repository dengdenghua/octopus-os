"""Load the fixed behavioral suite into executable eval cases."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from benchmarks.eval_harness import EvalCase, Grader
from runtime.safety.evolution.behavioral_surpass_evidence import (
    ALLOWED_EXECUTION_MODES,
    REQUIRED_DOMAINS,
)

GraderFactory = Callable[[str, dict[str, Any]], Grader]
LifecycleHook = Callable[[], None]


def load_behavioral_suite(
    path: str | Path,
    *,
    grader_factories: Mapping[str, GraderFactory],
    setup_hooks: Mapping[str, LifecycleHook] | None = None,
    teardown_hooks: Mapping[str, LifecycleHook] | None = None,
    case_ids: set[str] | None = None,
) -> list[EvalCase]:
    """Load manifest cases and bind explicit outcome graders.

    Missing graders fail closed: a case can never silently become a text or
    path heuristic just to make a benchmark runnable.
    """

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != (
        "echo.behavioral_surpass_suite.v1"
    ):
        raise ValueError("invalid behavioral suite schema")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("behavioral suite must contain cases")
    setups = setup_hooks or {}
    teardowns = teardown_hooks or {}
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise ValueError(f"suite case {index} must be an object")
        case_id = str(raw_case.get("id") or "").strip()
        prompt = raw_case.get("prompt")
        rubric = raw_case.get("rubric")
        if not case_id or case_id in seen or not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"suite case {index} has invalid identity or prompt")
        if case_ids is not None and case_id not in case_ids:
            continue
        if not isinstance(rubric, dict) or not rubric:
            raise ValueError(f"suite case {case_id} has no outcome rubric")
        domain = str(raw_case.get("domain") or "")
        execution_mode = str(raw_case.get("execution_mode") or "")
        if domain not in REQUIRED_DOMAINS or execution_mode not in ALLOWED_EXECUTION_MODES[domain]:
            raise ValueError(f"suite case {case_id} has invalid domain or execution mode")
        grader_id = str(rubric.get("grader") or "").strip()
        factory = grader_factories.get(grader_id)
        if factory is None:
            raise ValueError(f"no grader factory registered for {grader_id!r} ({case_id})")
        seen.add(case_id)
        rubric_digest = hashlib.sha256(
            json.dumps(
                rubric,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        phases = raw_case.get("phases")
        phase_rows = phases if isinstance(phases, list) else []
        if any(not isinstance(phase, str) or not phase.strip() for phase in phase_rows):
            raise ValueError(f"suite case {case_id} has invalid phases")
        prompt_digest = _prompt_digest(prompt, phase_rows)
        allowed_write_paths = raw_case.get("allowed_write_paths")
        if allowed_write_paths is not None and (
            not isinstance(allowed_write_paths, list)
            or not allowed_write_paths
            or any(not isinstance(path, str) or not path.strip() for path in allowed_write_paths)
        ):
            raise ValueError(f"suite case {case_id} has invalid allowed_write_paths")
        case_metadata = {
            "domain": domain,
            "execution_mode": execution_mode,
            "outcome_grader": True,
            "isolated_state": True,
            "rubric_digest": rubric_digest,
            "prompt_digest": prompt_digest,
            "phases": list(phase_rows),
            "suite_id": str(payload.get("suite_id") or ""),
            "grader_id": grader_id,
        }
        if isinstance(allowed_write_paths, list):
            case_metadata["allowed_write_paths"] = [path.strip() for path in allowed_write_paths]
        cases.append(
            EvalCase(
                id=case_id,
                prompt=prompt,
                grader=factory(case_id, rubric),
                setup=setups.get(case_id),
                teardown=teardowns.get(case_id),
                metadata=case_metadata,
            )
        )
    if case_ids is not None:
        missing = case_ids - {case.id for case in cases}
        if missing:
            raise ValueError(f"requested suite cases are missing: {sorted(missing)}")
    return cases


def _prompt_digest(prompt: str, phases: list[str]) -> str:
    if not phases:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return hashlib.sha256(
        json.dumps(
            {"prompt": prompt, "phases": phases},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = ["GraderFactory", "LifecycleHook", "load_behavioral_suite"]


