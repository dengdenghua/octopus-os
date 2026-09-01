"""``_call_agent_parallel`` · concurrent fan-out + graceful-degradation envelope.

Extracted from delegation_skills.py. This module holds the parallel
delegation machinery: spec coercion, the concurrent fan-out worker, and the
pure ``_build_parallel_envelope`` aggregator. The names tests monkeypatch at
the ``delegation_skills`` module level (``_allowed_agent_ids`` /
``_check_absolute_cap`` / ``_record_delegation``) are resolved lazily via
``delegation_skills`` so a monkeypatch is still observed at call time — the
same pattern used by ``_delegation_skills_agent`` / ``_write_skills_background``.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from ._delegation_skills_common import (
    _DEFAULT_SUBAGENT_TIMEOUT_S,
    _coerce_timeout_s,
    _delegation_budget_exhausted_message,
    _derive_error_type,
    _display_name_for_agent_id,
    _empty_parallel_result,
    _parallel_route_decision,
    _resolve_custom_agent_id,
    _resolve_session_and_turn,
    _role_defaults_to_cheap,
    _should_auto_retry,
    _skill_context_from_spec,
    _wrap_prompt_with_role_label,
)
from .delegation_budget import (
    compute_fingerprint as _compute_fingerprint,
)
from .delegation_budget import (
    current_orchestration_budget as _current_orchestration_budget,
)
from .delegation_budget import (
    remaining_flat_delegations as _remaining_flat_delegations,
)

_log = logging.getLogger(__name__)


def _coerce_parallel_specs(specs: Any) -> list[dict[str, Any]] | None:
    """Accept the common LLM shape where ``specs`` arrives as JSON text."""
    if isinstance(specs, list):
        return specs
    if isinstance(specs, dict):
        nested = specs.get("specs") or specs.get("agents") or specs.get("items")
        if isinstance(nested, list):
            return nested
        return None
    if not isinstance(specs, str):
        return None
    raw = specs.strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        nested = parsed.get("specs") or parsed.get("agents") or parsed.get("items")
        if isinstance(nested, list):
            return nested
    return None


def _call_agent_parallel(
    specs: list[dict[str, Any]] | str | None = None,
    *,
    timeout_s: int | str = _DEFAULT_SUBAGENT_TIMEOUT_S,
    context: dict[str, Any] | None = None,
    session: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Spawn N sub-agents concurrently and gather their results.

    Args:
        specs: list of ``{"agent_id": "<role>", "prompt": "<brief>"}``
            dicts. Each spec triggers one sub-agent run in its own
            worker thread; all share the parent's blackboard
            (``bb_read`` / ``bb_write``) via the parent's ``turn_id``.
        timeout_s: per-sub-agent timeout (default 900s, applies to
            EACH sub-agent independently — total wall clock is
            ``max(per-agent durations)``, not the sum). Bumped from
            300s in 2026-06 after research workloads hit the 5-round
            ceiling. Failed subagents auto-retry ONCE on transient
            (timeout / connection / rate-limit) errors.

    Returns:
        ``{"ok": bool, "successes": [...], "failures": [...],
        "partial": bool, "total": int, "success_count": int,
        "notes": list[str], "outputs": [<success output strings>],
        "results": [...], "count": int}``

        ``ok=False`` only when ZERO sub-agents succeeded. ``partial=True``
        when at least one succeeded AND at least one failed — in that
        case ``notes`` carries a ``[partial-degradation]`` line so the
        main agent can decide whether to synthesise from the available
        outputs or escalate.

        Legacy ``results`` / ``count`` / ``outputs`` keys are preserved
        for callers written against the original shape.

    Budget rules (2026-06 smart-budget):
      - Absolute cap: 5 delegations per turn (hard limit)
      - Each spec counts independently against the budget:
          * Success counts
          * First-time spec failure is FREE (you can adjust and retry
            that one spec without burning the whole batch's budget)
          * Repeat failure on the same {agent, prompt} counts
      - Transient failures auto-retry once per spec
      - Parallel batches can iterate: if 1/3 specs fails, you can
        retry just that one spec for free in your next call

    TL;DR: failure is a learning opportunity, not a punishment. The
    framework only penalizes you for wasting calls on the EXACT SAME
    broken spec twice.
    """
    # Resolve the monkeypatch-visible names lazily via the delegation_skills
    # module so tests patching ``delegation_skills._allowed_agent_ids`` /
    # ``_check_absolute_cap`` / ``_record_delegation`` observe them here.
    from runtime.execution.suckers.delegation_skills import (
        _allowed_agent_ids,
        _check_absolute_cap,
        _record_delegation,
    )

    specs = _coerce_parallel_specs(specs)
    timeout_s = _coerce_timeout_s(timeout_s)
    if not specs or not isinstance(specs, list):
        return _empty_parallel_result(
            "specs is required (list of {agent_id, prompt})",
        )

    # Validate every spec up front so a bad input doesn't waste an
    # LLM round-trip.
    cleaned: list[dict[str, Any]] = []
    allowed = _allowed_agent_ids()
    for raw in specs:
        if not isinstance(raw, dict):
            continue
        aid_raw = (
            raw.get("agent_id")
            or raw.get("agent_name")
            or raw.get("agent")
            or raw.get("role")
            or raw.get("name")
            or ""
        )
        prm = (
            raw.get("prompt")
            or raw.get("task")
            or raw.get("message")
            or raw.get("query")
            or raw.get("description")
            or raw.get("instruction")
            or raw.get("goal")
            or ""
        )
        if not aid_raw and prm:
            aid_raw = "researcher"
        if not aid_raw or not prm:
            continue
        # Custom names are resolved to a generic builtin via the
        # fallback chain (see _resolve_custom_agent_id). Only fail
        # when there are NO builtins at all (which would mean the
        # registry is broken).
        aid_resolved, role_label = _resolve_custom_agent_id(str(aid_raw), allowed)
        if aid_resolved not in allowed:
            return _empty_parallel_result(
                f"no fallback subagent available for {aid_raw!r}. Available: {sorted(allowed)}.",
            )
        # Cheap routing: explicit ``cheap`` in the spec wins; otherwise
        # the role-name policy decides. ``cheap=False`` disables the
        # auto-cheap behavior so heavy-reasoning roles dropped into the
        # default-cheap allowlist by mistake can still be pinned to the
        # primary model from the call site.
        cheap_flag = (
            bool(raw.get("cheap")) if "cheap" in raw else _role_defaults_to_cheap(str(aid_resolved))
        )
        cleaned.append(
            {
                "spec_index": len(cleaned),
                "agent_id": aid_resolved,
                "agent_id_original": str(aid_raw),
                "bb_key": str(raw.get("bb_key") or raw.get("key") or "").strip(),
                "prompt": _wrap_prompt_with_role_label(str(prm), role_label),
                "task_preview": str(prm).replace("\n", " ").strip()[:240],
                "role_label": role_label,
                "cheap": cheap_flag,
                "context": _skill_context_from_spec(raw, context),
                # Optional JSON Schema: when a spec carries one, the sub-agent's
                # reply is validated (and re-asked once on mismatch) by
                # call_subagent, and the parsed object rides back in the envelope.
                "output_schema": raw.get("output_schema"),
                # Filesystem isolation opt-in. A BOOLEAN, never a path: the
                # worktree is created on the trusted side and its path handed to
                # call_subagent. ``workspace`` is in
                # MODEL_PROTECTED_CONTEXT_PREFIXES precisely so a model cannot
                # aim confinement at a directory of its choosing, and this flag
                # must not become a way around that.
                "isolate": bool(raw.get("isolate")),
                # Hierarchical delegation: allow this sub-agent to spawn its own
                # sub-agents if explicitly enabled in the spec.
                "allow_subdelegation": bool(raw.get("allow_subdelegation", False)),
            }
        )

    if not cleaned:
        return _empty_parallel_result(
            "no valid {agent_id, prompt} entries in specs",
        )

    # Budget · pre-check absolute cap. Smart-budget rules apply
    # per-spec inside the worker thread (see _record_delegation calls
    # in _run_one), so the parallel call as a whole can succeed even
    # when one spec fails (and that single failure won't count).
    parent_sess, turn_id = _resolve_session_and_turn()
    if session is None:
        session = parent_sess
    parent_meta = getattr(session, "metadata", None) if session is not None else None
    try:
        from runtime.platform.process.session import current_parent_tool_use_id

        parent_tool_use_id = str(current_parent_tool_use_id() or "")
    except (ImportError, AttributeError):
        parent_tool_use_id = ""
    if not parent_tool_use_id and isinstance(parent_meta, dict):
        parent_tool_use_id = str(parent_meta.get("_active_parent_tool_use_id") or "")
    # Captured on THIS (calling) thread so the pool workers can charge it via
    # closure — the ContextVar itself doesn't propagate into the pool.
    orch_budget = _current_orchestration_budget()
    cur_count, within = _check_absolute_cap(turn_id, budget=orch_budget)
    if not within:
        return _empty_parallel_result(
            _delegation_budget_exhausted_message(
                cur_count,
                budget=orch_budget,
                action="Do the rest yourself · do NOT call_agent again.",
            ),
        )

    # Guardrail: the flat per-turn cap is a per-SPAWN budget, but the pre-check
    # above only reads it once. On the flat (non-orchestration) path, truncate
    # the batch to the remaining slots so a single call can't pack N specs past
    # the cap. (Under an orchestration budget, per-spec ``try_charge`` already
    # enforces this inside ``_run_one``.)
    dropped_specs = 0
    if orch_budget is None:
        slots = _remaining_flat_delegations(turn_id)
        if slots is not None and len(cleaned) > slots:
            dropped_specs = len(cleaned) - slots
            cleaned = cleaned[:slots]

    # Concurrent fan-out · one worker thread per spec. Each worker
    # binds the parent's Session into its own ContextVar so
    # blackboard / memory skills inside the sub-agent see the same
    # turn_id and can exchange data via bb_read/bb_write.
    import concurrent.futures as _cf

    from runtime.execution.subagents import call_subagent

    # ContextVars don't propagate across threads, so capture the parent's
    # react stack HERE (parent thread) and hand it to each worker explicitly.
    # This lets the ephemeral runner drive a parallel child through the MAIN
    # react loop too. Ambient only — never persisted into session metadata.
    try:
        from runtime.execution.subagents._ambient import current_react_stack

        _ambient_react_stack = current_react_stack()
    except (ImportError, AttributeError):
        _ambient_react_stack = None

    def _invoke(spec: dict[str, Any], call_context: dict[str, Any]) -> dict[str, Any]:
        """Spawn one sub-agent, in its own git worktree when ``isolate`` is set.

        The trusted side creates the worktree and passes its path, which
        ``call_subagent`` pins as the session's write root — a model-supplied
        path would be stripped by ``arg_guard``, and rightly so.

        The scope DELETES the worktree on exit, so the diff is captured inside
        it. Without that the isolated writes would simply vanish and isolation
        would silently mean "discard the work". Nothing is auto-merged:
        reconciling parallel edits stays a human call, matching ``tournament``.
        """
        if not spec.get("isolate"):
            return call_subagent(
                agent_id=spec["agent_id"],
                prompt=spec["prompt"],
                context=call_context,
                timeout_s=timeout_s,
                session=session,
                use_cheap_model=bool(spec.get("cheap")),
                output_schema=spec.get("output_schema"),
            )

        import os

        from runtime.execution.subagents.worktree_loop import (
            _capture_diff,
            is_git_repo,
            worktree_scope,
        )

        repo_root = os.getcwd()
        if not is_git_repo(repo_root):
            # Fail closed rather than silently running unisolated: the caller
            # asked for confinement, and quietly writing to the live tree would
            # be the opposite of what was requested.
            return {
                "agent_id": spec["agent_id"],
                "output": "",
                "success": False,
                "error": f"isolate requested but not a git repo: {repo_root}",
                "error_type": "isolation_unavailable",
            }

        label = str(spec.get("bb_key") or spec.get("spec_index") or "spawn")
        with worktree_scope(repo_root, f"spawn-{label}") as (path, branch):
            result = call_subagent(
                agent_id=spec["agent_id"],
                prompt=spec["prompt"],
                context=call_context,
                timeout_s=timeout_s,
                session=session,
                use_cheap_model=bool(spec.get("cheap")),
                output_schema=spec.get("output_schema"),
                workspace_path=path,
            )
            diff, files = _capture_diff(path)
        result["isolated"] = True
        # Audit F-08: the worktree branch is deleted right after capture, so
        # the branch name in the envelope would be stale/misleading — the
        # lane is identified by bb_key/spec_index instead.
        result["diff"] = diff
        result["files_touched"] = files
        return result

    def _run_one(spec: dict[str, Any]) -> dict[str, Any]:
        # Bind parent session in this worker thread · ContextVars
        # don't propagate across threads automatically.
        if session is not None:
            try:
                from runtime.platform.process.session import _current_session

                _current_session.set(session)
            except Exception:  # noqa: BLE001
                pass
        original_id = spec.get("agent_id_original") or spec["agent_id"]
        role_label = spec.get("role_label")
        task_label = spec.get("bb_key") or role_label or original_id
        route_decision = _parallel_route_decision(
            str(spec["agent_id"]),
            spec.get("context"),
        )
        if route_decision.get("action") == "block":
            return {
                "agent_id": original_id,
                "resolved_to": spec.get("agent_id"),
                "custom_role": role_label,
                "output": "",
                "success": False,
                "status": "blocked",
                "error": str(route_decision.get("reason") or "subagent blocked by routing policy"),
                "error_type": "subagent_route_blocked",
                "spec_index": spec.get("spec_index"),
                "task_label": task_label,
                "bb_key": spec.get("bb_key"),
                "task_preview": spec.get("task_preview"),
                "subagent_route_decision": route_decision,
            }
        call_context = dict(spec.get("context") or {})
        # Keep the operator/model-declared lane identity separate from the
        # builtin role selected by fallback routing. Several custom lanes can
        # legitimately resolve to the same builtin (for example
        # ``reader_readme`` and ``reader_pyproject`` -> ``explorer``); using the
        # resolved role as the public id makes those parallel children collapse
        # into one card in live/replay observability.
        call_context["requested_agent_id"] = str(original_id)
        call_context["resolved_agent_id"] = str(spec["agent_id"])

        # Hierarchical delegation: propagate depth and budget to sub-agents
        parent_depth = context.get("delegation_depth", 0) if context else 0
        parent_budget = context.get("subdelegation_budget", 0) if context else 0

        # ROOT LAYER AUTO-SEEDING: If this is depth=0 and no subdelegation_budget
        # was explicitly set, but orchestration_token_budget is available,
        # automatically allocate 1/4 of the orchestration budget for recursive delegation
        if parent_depth == 0 and parent_budget == 0 and context:
            orch_budget_val = context.get("orchestration_token_budget", 0)
            if orch_budget_val > 0:
                # Auto-seed: allocate 25% of orchestration budget for subdelegation
                parent_budget = orch_budget_val // 4

        # Always propagate depth (even from depth=0)
        call_context["delegation_depth"] = parent_depth + 1

        # Propagate budget if available
        if parent_budget > 0:
            # Split budget among siblings (simplified: equal distribution)
            per_node_budget = parent_budget // len(cleaned) if cleaned else 0
            call_context["orchestration_token_budget"] = per_node_budget
            call_context["subdelegation_budget"] = per_node_budget // 2
            # Allow subdelegation if spec explicitly enables it AND depth limit allows
            # Import MAX_DELEGATION_DEPTH from ephemeral_runner to avoid duplication
            from runtime.execution.suckers.ephemeral_runner import MAX_DELEGATION_DEPTH

            next_depth = parent_depth + 1
            can_spawn_further = next_depth < MAX_DELEGATION_DEPTH
            call_context["allow_subdelegation"] = (
                spec.get("allow_subdelegation", False) and can_spawn_further
            )

            # Inject role-specific delegation guidance when subdelegation is allowed
            if call_context.get("allow_subdelegation"):
                try:
                    from runtime.execution.suckers.role_delegation_guidance import (
                        get_delegation_guidance,
                    )

                    guidance = get_delegation_guidance(spec["agent_id"])
                    if guidance:
                        call_context["delegation_guidance"] = guidance
                except (ImportError, KeyError, AttributeError) as exc:
                    _log.debug("optional delegation guidance unavailable", exc_info=exc)
        if parent_tool_use_id:
            call_context["parent_tool_use_id"] = parent_tool_use_id
        if call_context.get("react_stack") is None and _ambient_react_stack is not None:
            call_context["react_stack"] = _ambient_react_stack
        call_context["subagent_route_decision"] = route_decision
        if orch_budget is not None and not orch_budget.try_charge():
            return {
                "agent_id": original_id,
                "resolved_to": spec.get("agent_id"),
                "custom_role": role_label,
                "output": "",
                "success": False,
                "status": "budget_exhausted",
                "error": _delegation_budget_exhausted_message(
                    orch_budget.used,
                    budget=orch_budget,
                    action="This lane was not spawned.",
                ),
                "error_type": "budget_exhausted",
                "spec_index": spec.get("spec_index"),
                "task_label": task_label,
                "bb_key": spec.get("bb_key"),
                "task_preview": spec.get("task_preview"),
                "subagent_route_decision": route_decision,
            }
        try:
            result = _invoke(spec, call_context)
        except (
            ConnectionError,
            TimeoutError,
            TypeError,
            ValueError,
            OSError,
            subprocess.SubprocessError,
        ) as exc:  # noqa: BLE001
            # OSError joins the list because worktree creation touches git and
            # the filesystem; an isolation failure must degrade to one failed
            # lane, not take down the whole batch.
            result = {
                "agent_id": spec["agent_id"],
                "output": "",
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "error_type": type(exc).__name__,
            }
        result["spec_index"] = spec.get("spec_index")
        result["task_label"] = task_label
        result["bb_key"] = spec.get("bb_key")
        result["task_preview"] = spec.get("task_preview")
        result["subagent_route_decision"] = route_decision
        # Retry once on transient failure. Per-spec retry, not per
        # parallel batch — one slow worker shouldn't block faster ones.
        if _should_auto_retry(result):
            if orch_budget is not None and not orch_budget.try_charge():
                result["retry_skipped"] = True
                existing_err = result.get("error") or ""
                result["error"] = (
                    f"{existing_err} (retry skipped: "
                    f"{_delegation_budget_exhausted_message(orch_budget.used, budget=orch_budget, action='Retry skipped.')})"
                )
            else:
                try:
                    # A retried isolated lane gets a FRESH worktree: the first
                    # attempt's tree is already gone, and reusing a half-written
                    # one would hand the retry a dirty starting state.
                    retry = _invoke(spec, call_context)
                    if retry.get("success"):
                        retry["retried"] = True
                        result = retry
                        result["spec_index"] = spec.get("spec_index")
                        result["task_label"] = task_label
                        result["bb_key"] = spec.get("bb_key")
                        result["task_preview"] = spec.get("task_preview")
                    else:
                        result["retried"] = True
                        existing_err = result.get("error") or ""
                        # Keep the error tied to any partial output stable.
                        # The aggregate failure may include retry diagnostics,
                        # while consumers of ``partial_outputs`` need to know
                        # why the output-producing attempt stopped.
                        result["initial_error"] = existing_err
                        result["retry_error"] = retry.get("error") or "unknown"
                        result["error"] = (
                            f"{existing_err} (retry also failed: {result['retry_error']})"
                        )
                except (
                    ConnectionError,
                    TimeoutError,
                    TypeError,
                    ValueError,
                    OSError,
                    subprocess.SubprocessError,
                ):
                    # OSError: the retry's worktree creation can fail on its own.
                    result["retried"] = True
        # Record this spec's outcome against the smart-budget. Each
        # spec gets its own fingerprint so a failed spec doesn't
        # spend budget on a first try (LLM gets a chance to fix it),
        # but a repeat of the same {agent, prompt} does count.
        if orch_budget is None:
            spec_fingerprint = _compute_fingerprint(spec["agent_id"], spec["prompt"])
            _record_delegation(
                turn_id,
                spec_fingerprint,
                succeeded=bool(result.get("success")),
            )
        # Preserve operator's original agent_id in response
        if role_label:
            result["agent_id"] = original_id
            result["resolved_to"] = spec["agent_id"]
            result["custom_role"] = role_label
        return result

    results: list[dict[str, Any]] = []
    # Bound concurrency · 8 workers covers most real fan-outs; more
    # would just thrash on LLM API rate limits anyway.
    max_workers = min(len(cleaned), 8)
    pool = _cf.ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="subagent-parallel",
    )
    try:
        future_specs = {pool.submit(_run_one, s): s for s in cleaned}
        # ``timeout_s`` here is a batch-level guard. Finished workers
        # still return normally; stragglers become per-agent failures
        # instead of blowing away the whole parallel envelope.
        done, not_done = _cf.wait(
            future_specs.keys(),
            timeout=timeout_s + 30,
            return_when=_cf.ALL_COMPLETED,
        )
        for f in done:
            try:
                results.append(f.result(timeout=1))
            except (
                ConnectionError,
                TimeoutError,
                TypeError,
                ValueError,
                subprocess.SubprocessError,
            ) as exc:  # noqa: BLE001
                spec = future_specs.get(f, {})
                task_label = (
                    spec.get("bb_key") or spec.get("role_label") or spec.get("agent_id_original")
                )
                results.append(
                    {
                        "agent_id": (spec.get("agent_id_original") or spec.get("agent_id") or "?"),
                        "spec_index": spec.get("spec_index"),
                        "task_label": task_label,
                        "bb_key": spec.get("bb_key"),
                        "task_preview": spec.get("task_preview"),
                        "output": "",
                        "success": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "error_type": type(exc).__name__,
                    }
                )
        for f in not_done:
            spec = future_specs.get(f, {})
            f.cancel()
            task_label = (
                spec.get("bb_key") or spec.get("role_label") or spec.get("agent_id_original")
            )
            results.append(
                {
                    "agent_id": spec.get("agent_id_original") or spec.get("agent_id") or "?",
                    "spec_index": spec.get("spec_index"),
                    "task_label": task_label,
                    "bb_key": spec.get("bb_key"),
                    "task_preview": spec.get("task_preview"),
                    "resolved_to": spec.get("agent_id"),
                    "custom_role": spec.get("role_label"),
                    "output": "",
                    "success": False,
                    "status": "timeout",
                    "error": f"subagent timed out after {timeout_s}s",
                    "error_type": "timeout",
                }
            )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    envelope = _build_parallel_envelope(results, total=len(cleaned))
    if dropped_specs:
        # Honesty: the lead must know it did NOT run every requested lane.
        envelope["dropped"] = dropped_specs
        envelope.setdefault("notes", []).append(
            f"[budget-clipped] {dropped_specs} spec(s) not spawned "
            "(per-turn delegation cap reached)."
        )
    return envelope


def _build_parallel_envelope(
    results: list[dict[str, Any]],
    *,
    total: int,
) -> dict[str, Any]:
    """Split raw per-spec results into successes/failures and build
    the graceful-degradation envelope. Pure function · easy to unit
    test in isolation."""
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    outputs: list[str] = []
    partial_outputs: list[dict[str, Any]] = []

    for r in results:
        if not isinstance(r, dict):
            failures.append(
                {
                    "role": "?",
                    "agent_id": "?",
                    "error": f"non-dict result: {r!r}",
                    "error_type": "malformed",
                }
            )
            continue
        agent_id = str(r.get("agent_id") or "")
        resolved_to = r.get("resolved_to")
        output = str(r.get("output") or "")
        common = {
            "role": str(r.get("role") or r.get("custom_role") or agent_id),
            "agent_id": agent_id,
            "display_name": r.get("display_name")
            or _display_name_for_agent_id(str(resolved_to or agent_id)),
            "spec_index": r.get("spec_index"),
            "task_label": r.get("task_label"),
            "bb_key": r.get("bb_key"),
            "task_preview": r.get("task_preview"),
            "codename": r.get("codename"),
            "avatar": r.get("avatar"),
            "resolved_to": resolved_to,
            "custom_role": r.get("custom_role"),
            "iteration_count": r.get("iteration_count") or r.get("rounds_completed"),
            "rounds_completed": r.get("rounds_completed") or r.get("iteration_count"),
            "duration_s": r.get("duration_s"),
            "files_touched": list(r.get("files_touched") or []),
            "retried": bool(r.get("retried") or r.get("retry_attempted")),
            "round_cap_exceeded": bool(r.get("round_cap_exceeded")),
            "partial": bool(r.get("partial")),
            "subagent_route_decision": r.get("subagent_route_decision"),
        }
        if r.get("success"):
            success_entry = {**common, "output": output}
            # Carry schema-validated output through the envelope when present
            # (a spec passed ``output_schema``); absent for plain free-text
            # specs, so non-schema callers see no shape change.
            if "parsed" in r:
                success_entry["parsed"] = r.get("parsed")
            if "schema_ok" in r:
                success_entry["schema_ok"] = r.get("schema_ok")
            # Isolation evidence. This envelope is a WHITELIST projection, so a
            # field not named here is silently dropped - which made ``isolate``
            # a no-op end to end: the worktree was created, written, and cleaned
            # up correctly, but the diff never reached the caller, so isolation
            # meant "discard the work". ``files_touched`` survived only because
            # ``common`` already projected it, which made the loss harder to see.
            for isolation_field in ("isolated", "branch", "diff"):
                if isolation_field in r:
                    success_entry[isolation_field] = r.get(isolation_field)
            successes.append(success_entry)
            if output.strip():
                outputs.append(output)
        else:
            error = str(r.get("error") or "")
            failure = {
                **common,
                "error": error,
                "error_type": _derive_error_type(r),
                "output": output,
                "partial_output": output,
                "status": r.get("status"),
            }
            failures.append(
                {key: value for key, value in failure.items() if value not in (None, "", [], False)}
            )
            if output.strip():
                partial_outputs.append(
                    {
                        "agent_id": agent_id,
                        "spec_index": r.get("spec_index"),
                        "task_label": r.get("task_label"),
                        "error": str(r.get("initial_error") or error),
                        "error_type": _derive_error_type(r),
                        "output": output,
                    }
                )

    success_count = len(successes)
    failure_count = len(failures)
    partial = success_count > 0 and failure_count > 0
    ok = success_count > 0

    notes: list[str] = []
    if partial:
        # Stable de-duplicated reason list, ordered by first appearance.
        seen: set[str] = set()
        reasons: list[str] = []
        for f in failures:
            et = f.get("error_type") or "unknown"
            if et not in seen:
                seen.add(et)
                reasons.append(et)
        notes.append(
            f"[partial-degradation] {success_count}/{total} sub-agents "
            f"completed; {failure_count} failed "
            f"(reasons: {', '.join(reasons)}). Synthesise from the "
            "available outputs unless they're insufficient — in that "
            "case escalate to the user."
        )

    status_summary = (
        f"{success_count}/{total} sub-agents succeeded" if total else "0/0 sub-agents succeeded"
    )
    honesty_warning = ""
    if partial:
        failed_labels = [
            str(f.get("task_label") or f.get("agent_id") or f.get("role") or "?") for f in failures
        ]
        honesty_warning = (
            "PARTIAL RUN: do not claim all sub-agents completed. "
            f"State that {status_summary}; failed lanes: "
            f"{', '.join(failed_labels)}."
        )
    elif failure_count > 0:
        honesty_warning = (
            "FAILED RUN: no sub-agent completed successfully. Do not present "
            "a complete multi-agent result unless you independently filled "
            "the gaps and disclose that fallback."
        )

    return {
        # new graceful-degradation envelope
        "ok": ok,
        "status_summary": status_summary,
        "honesty_warning": honesty_warning,
        "successes": successes,
        "failures": failures,
        "partial": partial,
        "total": total,
        "success_count": success_count,
        "notes": notes,
        "partial_outputs": partial_outputs,
        # legacy keys preserved
        "results": results,
        "count": len(results),
        "succeeded": success_count,
        "failed": failure_count,
        "outputs": outputs,
    }
