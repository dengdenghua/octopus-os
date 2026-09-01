"""``_run_pipeline`` · ordered per-item stage chains, run concurrently.

Extracted from delegation_skills.py. This module holds the pipeline handler:
each item is pushed through the same ordered stages independently (item A can
be in stage 3 while item B is still in stage 1), with stage failure halting only
that item's chain. The names tests monkeypatch at the ``delegation_skills``
module level (``_check_absolute_cap`` / ``_record_delegation``) are resolved
lazily via ``delegation_skills`` so a monkeypatch is still observed at call
time — the same pattern used by ``_delegation_skills_agent`` /
``_write_skills_background``.
"""

from __future__ import annotations

from typing import Any

from ._delegation_skills_common import (
    _DEFAULT_SUBAGENT_TIMEOUT_S,
    _coerce_timeout_s,
    _delegation_budget_exhausted_message,
    _resolve_session_and_turn,
)
from .delegation_budget import (
    compute_fingerprint as _compute_fingerprint,
)
from .delegation_budget import (
    current_orchestration_budget as _current_orchestration_budget,
)
from .delegation_budget import (
    orchestration_budget_scope as _orchestration_budget_scope,
)

_PIPELINE_MAX_ITEMS = 16
_PIPELINE_MAX_STAGES = 4


def _run_pipeline(
    items: list[Any] | str | None = None,
    *,
    stages: list[dict[str, Any]] | None = None,
    default_agent_id: str = "researcher",
    timeout_s: int | str = _DEFAULT_SUBAGENT_TIMEOUT_S,
    context: dict[str, Any] | None = None,
    session: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Process each item through ordered stages independently and concurrently.

    Unlike ``call_agent_parallel`` (which fans out identical agents on
    independent specs), ``run_pipeline`` chains stages for EACH item:
    item-A runs stage1 → stage2 → stage3 while item-B is still in stage1.
    Wall-clock = max(slowest single-item chain), not sum-of-slowest-per-stage.

    Each stage prompt template may use:
        {item}   — the original item value
        {prev}   — the previous stage's output (empty for stage 0)
        {stage0_output}, {stage1_output}, ... — any prior stage's output

    Stage failure halts that item's chain; remaining stages are skipped.
    """
    import concurrent.futures as _cf
    import contextvars as _ctxvars

    from runtime.execution.subagents import call_subagent

    # Resolve the monkeypatch-visible names lazily via the delegation_skills
    # module so tests patching ``delegation_skills._check_absolute_cap`` /
    # ``_record_delegation`` observe them here.
    from runtime.execution.suckers.delegation_skills import (
        _check_absolute_cap,
        _record_delegation,
    )

    # ── coerce items ─────────────────────────────────────────────────
    if isinstance(items, str):
        try:
            import json as _json

            _parsed = _json.loads(items)
            items = _parsed if isinstance(_parsed, list) else [items]
        except Exception:  # noqa: BLE001
            items = [items]

    if not items or not isinstance(items, list):
        return {
            "ok": False,
            "error": "items is required (list of strings or dicts)",
            "results": [],
            "success_count": 0,
            "failure_count": 0,
            "total": 0,
            "stages_run": 0,
        }

    # ── coerce stages ────────────────────────────────────────────────
    if not stages or not isinstance(stages, list):
        return {
            "ok": False,
            "error": "stages is required (list of {prompt_template, agent_id?})",
            "results": [],
            "success_count": 0,
            "failure_count": 0,
            "total": len(items),
            "stages_run": 0,
        }

    items = list(items[:_PIPELINE_MAX_ITEMS])
    stages = list(stages[:_PIPELINE_MAX_STAGES])
    timeout_s = _coerce_timeout_s(timeout_s)
    default_role = str(default_agent_id or "researcher").strip() or "researcher"

    # ── delegation budget ─────────────────────────────────────────────
    parent_sess, turn_id = _resolve_session_and_turn()
    if session is None:
        session = parent_sess
    _, within = _check_absolute_cap(turn_id)
    if not within:
        return {
            "ok": False,
            "error": (
                "delegation budget exhausted for this turn — do the rest "
                "yourself, don't launch another pipeline."
            ),
            "results": [],
            "success_count": 0,
            "failure_count": 0,
            "total": len(items),
            "stages_run": 0,
        }
    _record_delegation(
        turn_id,
        _compute_fingerprint("run_pipeline", str(items[:3])),
        succeeded=True,
    )

    max_spawns = len(items) * len(stages)

    # ── per-item chain worker ────────────────────────────────────────
    def _run_item_chain(item: Any) -> dict[str, Any]:
        item_str = item if isinstance(item, str) else str(item)
        stage_outputs: list[dict[str, Any]] = []
        prev = ""
        chain_ok = True

        for s_idx, stage_spec in enumerate(stages):
            if not chain_ok:
                # Previous stage failed — skip remaining stages for this item
                stage_outputs.append(
                    {
                        "stage": s_idx,
                        "agent_id": str(stage_spec.get("agent_id") or default_role),
                        "output": "",
                        "ok": False,
                        "skipped": True,
                    }
                )
                continue

            tmpl = str(stage_spec.get("prompt_template") or stage_spec.get("prompt") or "").strip()
            if not tmpl:
                stage_outputs.append(
                    {
                        "stage": s_idx,
                        "agent_id": str(stage_spec.get("agent_id") or default_role),
                        "output": "",
                        "ok": False,
                        "error": "prompt_template is required for this stage",
                    }
                )
                chain_ok = False
                continue

            # Build substitution context from prior stage outputs
            sub_ctx: dict[str, str] = {"item": item_str, "prev": prev}
            for i, prior in enumerate(stage_outputs):
                sub_ctx[f"stage{i}_output"] = str(prior.get("output") or "")
            try:
                prompt = tmpl.format_map(sub_ctx)
            except (KeyError, ValueError):
                prompt = tmpl  # unknown placeholder — use template raw

            role = str(stage_spec.get("agent_id") or default_role).strip() or default_role
            stage_budget = _current_orchestration_budget()
            if stage_budget is not None and not stage_budget.try_charge():
                stage_outputs.append(
                    {
                        "stage": s_idx,
                        "agent_id": role,
                        "output": "",
                        "ok": False,
                        "error": _delegation_budget_exhausted_message(
                            stage_budget.used,
                            budget=stage_budget,
                            action="This pipeline stage was not spawned.",
                        ),
                        "error_type": "budget_exhausted",
                    }
                )
                chain_ok = False
                continue

            try:
                result = call_subagent(
                    agent_id=role,
                    prompt=prompt,
                    context=context,
                    timeout_s=timeout_s,
                    session=session,
                )
            except Exception as exc:  # noqa: BLE001
                result = {
                    "success": False,
                    "output": "",
                    "error": f"{type(exc).__name__}: {exc}",
                }

            ok = bool(result.get("success"))
            out = str(result.get("output") or "")
            entry: dict[str, Any] = {
                "stage": s_idx,
                "agent_id": role,
                "output": out,
                "ok": ok,
            }
            if not ok:
                entry["error"] = result.get("error") or "subagent failed"
            stage_outputs.append(entry)

            if ok:
                prev = out
            else:
                chain_ok = False

        return {
            "item": item_str,
            "stages": stage_outputs,
            "final_output": prev,
            "ok": chain_ok,
        }

    # ── concurrent fan-out of item chains ───────────────────────────
    item_results: list[dict[str, Any]] = []
    max_workers = min(len(items), 8)

    with (
        _orchestration_budget_scope(max_spawns),
        _cf.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="pipeline",
        ) as pool,
    ):
        # copy_context so each worker thread inherits the orchestration
        # budget ContextVar from the calling thread.
        futures = {
            pool.submit(_ctxvars.copy_context().run, _run_item_chain, it): it for it in items
        }
        done, not_done = _cf.wait(
            futures.keys(),
            timeout=timeout_s * len(stages) + 30,
            return_when=_cf.ALL_COMPLETED,
        )
        for f in done:
            try:
                item_results.append(f.result(timeout=1))
            except Exception as exc:  # noqa: BLE001
                it = futures.get(f, "")
                item_results.append(
                    {
                        "item": str(it),
                        "stages": [],
                        "final_output": "",
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        for f in not_done:
            f.cancel()
            it = futures.get(f, "")
            item_results.append(
                {
                    "item": str(it),
                    "stages": [],
                    "final_output": "",
                    "ok": False,
                    "error": "timeout",
                }
            )

    # Restore original insertion order
    _order = {(it if isinstance(it, str) else str(it)): i for i, it in enumerate(items)}
    item_results.sort(key=lambda r: _order.get(r["item"], 9999))

    success_count = sum(1 for r in item_results if r["ok"])
    return {
        "ok": success_count > 0,
        "results": item_results,
        "success_count": success_count,
        "failure_count": len(item_results) - success_count,
        "total": len(items),
        "stages_run": len(stages),
    }
