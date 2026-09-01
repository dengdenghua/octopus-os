"""Private helpers extracted from ``gepa_bridge.py``.

Pure structural split · no logic changes. These utilities support
the GEPA bridge orchestration layer:

* failure-sample + positive-dataset merging
* canary-key + sidecar-path derivation
* replay summary shaping (candidate / sandbox / turn / LLM)
* recipe-scope splitting
* LLM-as-judge eval-fn factory + static failure sampler
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from runtime.safety.recovery.evolution_dataset import (
    EvolutionDataset,
    EvolutionDatasetBuilder,
)
from runtime.safety.recovery.native_llm_replay import LLMReplayReport
from runtime.safety.recovery.native_replay import ReplayReport
from runtime.safety.recovery.native_replay_sandbox import SandboxReplayReport
from runtime.safety.recovery.native_turn_replay import TurnReplayReport

_LOG = logging.getLogger("echo.gepa.bridge")


def _merge_failure_samples(
    primary: list[dict[str, Any]],
    supplemental: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_signatures: set[tuple[str, str]] = set()
    for sample in [*primary, *supplemental]:
        goal = str(sample.get("goal") or "").strip()
        if not goal:
            continue
        stable_id = str(sample.get("turn_id") or sample.get("proposal_id") or "").strip()
        error = str(sample.get("last_error") or sample.get("failure_source") or "")[:120]
        signature = (goal, error)
        if stable_id and stable_id in seen_ids:
            continue
        if signature in seen_signatures:
            continue
        if stable_id:
            seen_ids.add(stable_id)
        seen_signatures.add(signature)
        merged.append(sample)
        if len(merged) >= limit:
            break
    return merged


def _merge_positive_datasets(
    primary: EvolutionDataset,
    supplemental: EvolutionDataset,
    *,
    limit: int,
) -> EvolutionDataset:
    examples = []
    seen: set[tuple[str, str]] = set()
    for example in [*primary.all_examples, *supplemental.all_examples]:
        signature = (example.source, example.task_input)
        if signature in seen:
            continue
        seen.add(signature)
        examples.append(example)
        if len(examples) >= max(1, int(limit)):
            break
    return EvolutionDatasetBuilder()._split(examples)


def _safe_canary_part(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    return text[:80] or fallback


def _winner_canary_key(
    *,
    recipe_id: str | None,
    candidate_id: str,
) -> str:
    scope = _safe_canary_part(recipe_id, fallback="__global__")
    candidate = _safe_canary_part(candidate_id, fallback="winner")
    return f"prompt_optimizer_{scope}_{candidate}"


def _candidate_replay_summary(
    report: ReplayReport | None,
    *,
    candidate_id: str,
) -> dict[str, Any] | None:
    if report is None:
        return None
    for candidate in report.candidates:
        if candidate.candidate_id != candidate_id:
            continue
        return {
            "candidate_id": candidate.candidate_id,
            "total": candidate.total,
            "reasons": candidate.reasons,
            "case_count": len(candidate.case_results),
            "weak_cases": [
                result.to_dict() for result in candidate.case_results if result.score < 0.55
            ][:5],
        }
    return None


def _candidate_sandbox_replay_summary(
    report: SandboxReplayReport | None,
    *,
    candidate_id: str,
) -> dict[str, Any] | None:
    if report is None:
        return None
    for candidate in report.candidates:
        if candidate.candidate_id != candidate_id:
            continue
        return {
            "candidate_id": candidate.candidate_id,
            "total": candidate.total,
            "passed": candidate.passed,
            "case_count": len(candidate.case_results),
            "weak_cases": [
                result.to_dict()
                for result in candidate.case_results
                if result.score < 0.55 or not result.sandbox_passed
            ][:5],
        }
    return None


def _candidate_turn_replay_summary(
    report: TurnReplayReport | None,
    *,
    candidate_id: str,
) -> dict[str, Any] | None:
    if report is None:
        return None
    for candidate in report.candidates:
        if candidate.candidate_id != candidate_id:
            continue
        return {
            "candidate_id": candidate.candidate_id,
            "total": candidate.total,
            "passed": candidate.passed,
            "case_count": len(candidate.case_results),
            "weak_cases": [
                result.to_dict() for result in candidate.case_results if not result.passed
            ][:5],
        }
    return None


def _candidate_llm_replay_summary(
    report: LLMReplayReport | None,
    *,
    candidate_id: str,
) -> dict[str, Any] | None:
    if report is None:
        return None
    for candidate in report.candidates:
        if candidate.candidate_id != candidate_id:
            continue
        return {
            "candidate_id": candidate.candidate_id,
            "total": candidate.total,
            "passed": candidate.passed,
            "case_count": len(candidate.case_results),
            "weak_cases": [
                result.to_dict() for result in candidate.case_results if not result.passed
            ][:5],
        }
    return None


def _winner_sidecar_path(
    *,
    recipe_id: str | None,
    variant_id: str | None = None,
    metadata_root: Any = None,
) -> Path:
    root = Path(metadata_root) if metadata_root is not None else None
    if root is None:
        from runtime.safety.recovery.gepa_addendum_store import _root as _addendum_root

        root = _addendum_root()
    root.mkdir(parents=True, exist_ok=True)
    scope = _safe_canary_part(recipe_id, fallback="__global__")
    if variant_id and variant_id != "__default__":
        variant = _safe_canary_part(variant_id, fallback="variant")
        return root / f"{scope}__{variant}__winner.json"
    return root / f"{scope}__winner.json"


def _split_recipe_scope(recipe_hash: str | None) -> tuple[str | None, str | None]:
    if not recipe_hash:
        return None, None
    if "#" not in recipe_hash:
        return recipe_hash, None
    base, suffix = recipe_hash.split("#", 1)
    return (base or None, suffix or None)


_JUDGE_SYSTEM = """\
You are scoring a candidate planner system-prompt against a
batch of real user goals. For each goal, predict whether a
planner using the candidate prompt would produce a good plan.

Score each goal 0.0 to 1.0:
  1.0 = clearly handled, planner picks the right tools cleanly
  0.7 = mostly handled, may need 1 retry
  0.4 = partial · misses a step or picks suboptimal tool
  0.0 = fails outright (wrong tool, infinite loop, refuses)

Output STRICT JSON:
  {"scores": [0.7, 0.4, ...]}   # one float per input goal, in order

No commentary. No markdown fences. Just the JSON."""


def _make_eval_fn(
    sample_goals: list[str],
    *,
    router: Any,
    judge_model: str,
) -> Any:
    """Curry a goal list into an eval_fn the optimizer can call.

    The optimizer calls eval_fn(prompt, n) and gets back a
    list of n scores. We just judge the first n goals in
    sample_goals · keeps the eval set FIXED across iterations
    so candidate scores are comparable.
    """
    from runtime.platform.models.llm import Message, ModelRequest

    def _eval(prompt: str, n: int) -> list[float]:
        goals = sample_goals[:n]
        if not goals:
            return []
        user_msg = (
            "# CANDIDATE PROMPT\n" + prompt + "\n\n"
            "# GOALS TO SCORE\n"
            + json.dumps(goals, ensure_ascii=False, indent=2)
            + "\n\nReturn the scores JSON."
        )
        req = ModelRequest(
            model=judge_model,
            messages=[
                Message(role="system", content=_JUDGE_SYSTEM),
                Message(role="user", content=user_msg),
            ],
            max_tokens=512,
            temperature=0.1,  # judge should be near-deterministic
        )
        try:
            resp = router.call(req)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("gepa eval: judge call failed · %s", exc)
            return [0.0] * len(goals)
        text = (getattr(resp, "text", "") or "").strip()
        # Tolerate fences.
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                lines[1:-1] if lines[-1].startswith("```") else lines[1:],
            )
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            s, e = text.find("{"), text.rfind("}")
            if s < 0 or e <= s:
                return [0.0] * len(goals)
            try:
                obj = json.loads(text[s : e + 1])
            except json.JSONDecodeError:
                return [0.0] * len(goals)
        scores = obj.get("scores") if isinstance(obj, dict) else None
        if not isinstance(scores, list):
            return [0.0] * len(goals)
        # Coerce + pad/truncate to expected length.
        out: list[float] = []
        for s_val in scores[: len(goals)]:
            try:
                out.append(max(0.0, min(1.0, float(s_val))))
            except (TypeError, ValueError):
                out.append(0.0)
        while len(out) < len(goals):
            out.append(0.0)
        return out

    return _eval


def _make_failure_sampler(
    failures: list[dict[str, Any]],
) -> Any:
    """Static failure sampler · returns the same failures list
    on every call (deterministic mutator input). The optimizer
    asks for ``n`` and we return at most ``n``."""

    def _sample(_prompt: str, n: int) -> list[dict[str, Any]]:
        return failures[:n]

    return _sample
