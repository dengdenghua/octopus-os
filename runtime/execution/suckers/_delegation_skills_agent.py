"""``_call_agent`` · single isolated subagent delegation.

Extracted from delegation_skills.py. This module holds the ``_call_agent``
handler. The three names tests monkeypatch at the ``delegation_skills`` module
level (``_allowed_agent_ids`` / ``_check_absolute_cap`` / ``_record_delegation``)
are resolved lazily via ``delegation_skills`` so a monkeypatch is still observed
at call time — the same pattern used by ``_write_skills_background``.
"""

from __future__ import annotations

import json
from typing import Any

from ._delegation_skills_common import (
    _DEFAULT_SUBAGENT_TIMEOUT_S,
    _delegation_budget_exhausted_message,
    _display_name_for_agent_id,
    _resolve_custom_agent_id,
    _resolve_session_and_turn,
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


def _call_agent(
    agent_id: str = "",
    prompt: str = "",
    *,
    role: str = "",
    task: str = "",
    name: str = "",
    message: str = "",
    query: str = "",
    context: dict[str, Any] | None = None,
    skills: Any = None,
    tools: Any = None,
    skill_pack: Any = None,
    skill_packs: Any = None,
    plugin: Any = None,
    plugins: Any = None,
    tool_allowlist: Any = None,
    timeout_s: int = _DEFAULT_SUBAGENT_TIMEOUT_S,
    session: Any = None,
    output_schema: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Spawn an isolated subagent turn — escalation when you need
    specialized expertise or parallel work.

    Pass ``output_schema`` (a JSON Schema object) to get a structured,
    validated result: the subagent is asked for JSON matching the schema and
    re-asked once on a mismatch; on success the parsed object is returned under
    ``parsed`` with ``schema_ok=True`` (the raw ``output`` is always kept too).

    Returns ``{agent_id, output, success, error}``. On budget exhaustion
    returns ``success=False`` with a message that instructs the model to
    do the work itself.

    Custom agent_ids (not in the builtin allowlist) are accepted and
    resolved to a generic builtin (researcher / explorer / general)
    based on name shape. The original custom name is preserved as a
    role label injected into the subagent prompt — see
    ``_resolve_custom_agent_id``.

    Budget rules (2026-06 smart-budget):
      - Absolute cap: 5 calls per turn (hard limit)
      - Success counts against budget
      - First-time failure is FREE (you can fix the spec and retry)
      - Repeat failure (same agent + same prompt) counts (prevents loops)
      - Transient failures (timeout / connection) auto-retry once

    TL;DR: if a delegation fails because your prompt was unclear or the
    agent_id was wrong, you get a free second chance to fix it. But if
    you keep delegating the same broken spec, that counts against budget.
    """
    from runtime.execution.subagents import call_subagent

    # Resolve the monkeypatch-visible names lazily via the delegation_skills
    # module so tests patching ``delegation_skills._allowed_agent_ids`` /
    # ``_check_absolute_cap`` / ``_record_delegation`` observe them here.
    from runtime.execution.suckers.delegation_skills import (
        _allowed_agent_ids,
        _check_absolute_cap,
        _record_delegation,
    )

    target_raw = agent_id or role or name
    if not target_raw:
        return {
            "agent_id": "",
            "output": "",
            "success": False,
            "error": "agent_id is required",
        }

    allowed = _allowed_agent_ids()
    target, role_label = _resolve_custom_agent_id(str(target_raw), allowed)
    if target not in allowed:
        return {
            "agent_id": target_raw,
            "output": "",
            "success": False,
            "error": (
                f"unknown subagent {target_raw!r} and no fallback builtin "
                f"available. Available: {sorted(allowed)}. "
                "If you really need delegation, pick one of these; "
                "otherwise just do the work yourself."
            ),
        }

    # Per-turn budget · resolves Session via ContextVar (set by the
    # agentic / react path before the tool loop). When no Session is
    # active (raw unit test, etc.) enforcement is OFF.
    #
    # Smart-budget rules (see ``_record_delegation`` for full docs):
    #   - Pre-check: absolute cap (default 5/turn)
    #   - Post-call: success counts; first-time failure is FREE;
    #     repeat failure (same agent + same prompt) counts.
    sess, turn_id = _resolve_session_and_turn()
    if session is None:
        session = sess
    orch_budget = _current_orchestration_budget()
    cur_count, within = _check_absolute_cap(turn_id, budget=orch_budget)
    if not within:
        return {
            "agent_id": target_raw,
            "output": "",
            "success": False,
            "error": _delegation_budget_exhausted_message(
                cur_count,
                budget=orch_budget,
            ),
            "error_type": "budget_exhausted",
        }

    # Inject the custom role label into the prompt so the LLM still
    # adopts the intended task framing even though the underlying
    # builtin is generic.
    final_prompt = _wrap_prompt_with_role_label(
        prompt or task or message or query,
        role_label,
    )

    # Compute fingerprint up-front so we can attribute the result
    # correctly regardless of which retry branch we end up in.
    fingerprint = _compute_fingerprint(target, final_prompt)

    # Optional structured output: the model (or an internal caller) may ask the
    # subagent for a JSON value matching a schema. Accept a dict, or a JSON
    # string some models stringify; anything else is ignored (no enforcement)
    # rather than crashing the delegation.
    schema_arg: dict[str, Any] | None = None
    if isinstance(output_schema, dict):
        schema_arg = output_schema
    elif isinstance(output_schema, str) and output_schema.strip():
        try:
            _loaded = json.loads(output_schema)
        except (json.JSONDecodeError, ValueError):
            _loaded = None
        if isinstance(_loaded, dict):
            schema_arg = _loaded

    # First attempt
    subagent_context = _skill_context_from_spec(
        {
            **_kw,
            "skills": skills,
            "tools": tools,
            "skill_pack": skill_pack,
            "skill_packs": skill_packs,
            "plugin": plugin,
            "plugins": plugins,
            "tool_allowlist": tool_allowlist,
        },
        context,
    )
    if orch_budget is not None and not orch_budget.try_charge():
        return {
            "agent_id": target_raw,
            "output": "",
            "success": False,
            "error": _delegation_budget_exhausted_message(
                orch_budget.used,
                budget=orch_budget,
            ),
            "error_type": "budget_exhausted",
        }
    result = call_subagent(
        agent_id=target,
        prompt=final_prompt,
        context=subagent_context,
        timeout_s=timeout_s,
        session=session,
        output_schema=schema_arg,
    )

    # Retry once on transient failure. Critical: retry does NOT bump
    # the budget counter — that happens in ``_record_delegation`` based
    # on the FINAL result (success vs. repeat-failure vs. first-failure).
    if _should_auto_retry(result):
        if orch_budget is not None and not orch_budget.try_charge():
            result["retry_skipped"] = True
            existing_err = result.get("error") or ""
            result["error"] = (
                f"{existing_err} (retry skipped: "
                f"{_delegation_budget_exhausted_message(orch_budget.used, budget=orch_budget, action='Retry skipped.')})"
            )
        else:
            retry_result = call_subagent(
                agent_id=target,
                prompt=final_prompt,
                context=subagent_context,
                timeout_s=timeout_s,
                session=session,
                output_schema=schema_arg,
            )
            if retry_result.get("success"):
                retry_result.setdefault("retried", True)
                result = retry_result
            else:
                # Both attempts failed — surface that fact in the error
                result["retried"] = True
                existing_err = result.get("error") or ""
                result["error"] = (
                    f"{existing_err} (retry also failed: {retry_result.get('error') or 'unknown'})"
                )

    # Record against budget AFTER we know the outcome. Smart-budget:
    # first-time failures get a free pass; repeat failures + successes
    # both count.
    if orch_budget is None:
        _record_delegation(
            turn_id,
            fingerprint,
            succeeded=bool(result.get("success")),
        )

    # Preserve the operator's original custom name in the response so
    # logs / UI show what they asked for, not the resolved builtin.
    if role_label:
        result["agent_id"] = target_raw
        result["resolved_to"] = target
        result["custom_role"] = role_label
    # Expose the human-readable role name to the UI.
    result["display_name"] = _display_name_for_agent_id(target)
    return result
