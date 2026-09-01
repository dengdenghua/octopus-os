"""Shared leaf helpers for delegation_skills · extracted from delegation_skills.py.

This module holds the low-level, dependency-free helpers that the higher-level
delegation/orchestration handlers import: role visibility policy, cheap-model
routing, dynamic skill packs, context/name coercion, the role catalog, session
resolution, budget messaging, and the pure parallel-envelope / vote aggregation
helpers. No function here spawns sub-agents or depends on another submodule.
"""

from __future__ import annotations

import contextlib
import json
from collections import OrderedDict
from collections.abc import Callable as _Callable
from contextvars import ContextVar as _ContextVar
from typing import Any

from runtime.safety.auth.arg_guard import is_model_protected_context_key

from .delegation_budget import (
    _PER_TURN_ABSOLUTE_LIMIT,
)
from .delegation_budget import (
    check_absolute_cap as _check_absolute_cap,
)

# ── Orchestration progress fan-out ────────────────────────
# The realtime gateway installs a callback here for the duration of a
# turn so ``run_orchestration``'s phase transitions (round collected /
# verified / synthesized) stream to the client as thinking deltas
# instead of arriving as one opaque blob at the end. ContextVar so
# concurrent turns cannot cross wires; emission is best-effort and must
# never break the run.
_ORCH_PROGRESS: _ContextVar[_Callable[[str], None] | None] = _ContextVar(
    "orchestration_progress_emitter",
    default=None,
)


@contextlib.contextmanager
def orchestration_progress_scope(callback: _Callable[[str], None]):
    """Install a progress callback for orchestrations run inside the scope."""
    token = _ORCH_PROGRESS.set(callback)
    try:
        yield
    finally:
        _ORCH_PROGRESS.reset(token)


def _emit_orchestration_progress(line: str) -> None:
    callback = _ORCH_PROGRESS.get()
    if callback is None:
        return
    with contextlib.suppress(Exception):
        callback(line)


# ── Workflow completion notification ──────────────────────
# Similar to orchestration progress, but for workflow settlement events.
# The realtime gateway can install a callback here to emit a
# ``workflow/completed`` notification when a workflow finishes.
_WORKFLOW_SETTLEMENT: _ContextVar[_Callable[[dict[str, Any]], None] | None] = _ContextVar(
    "workflow_settlement_emitter",
    default=None,
)


@contextlib.contextmanager
def workflow_settlement_scope(callback: _Callable[[dict[str, Any]], None]):
    """Install a settlement callback for workflows run inside the scope."""
    token = _WORKFLOW_SETTLEMENT.set(callback)
    try:
        yield
    finally:
        _WORKFLOW_SETTLEMENT.reset(token)


def _emit_workflow_settlement(payload: dict[str, Any]) -> None:
    """Emit a workflow completion event (dsh ``settlement`` analog)."""
    callback = _WORKFLOW_SETTLEMENT.get()
    if callback is None:
        return
    with contextlib.suppress(Exception):
        callback(payload)


# ── Role visibility policy ────────────────────────────────
# ``arbiter`` is internal (used by team-vote dispatcher).
# ``researcher`` / ``debugger`` / ``explorer`` / ``reviewer`` exist in
# BUILTIN_ROLES but are deliberately NOT advertised so the lead agent
# does that work itself. They remain CALLABLE if the model knows
# their name — that's a deliberate escape hatch, not a contradiction.
_INTERNAL_AGENTS: frozenset[str] = frozenset({"arbiter"})
_ADVERTISED_BUILTINS: frozenset[str] = frozenset(
    {
        "architect",
        "debugger",
        "explorer",
        "researcher",
        "reviewer",
        "security-review",
    }
)


# ── Cheap-model routing policy ────────────────────────────
# Roles whose work is "research-style" (web fetching, fact-checking,
# data extraction, code reading, surface review) auto-route to the
# cheap subagent model unless the spec explicitly opts out via
# ``"cheap": False``. The non-cheap roles — architect / synthesizer /
# designer / implementer — keep the parent's primary model since they
# carry the heavy reasoning load.
_NON_CHEAP_ROLES: frozenset[str] = frozenset(
    {
        "architect",
        "synthesizer",
        "designer",
        "implementer",
    }
)

_CHEAP_BY_DEFAULT_ROLES: frozenset[str] = frozenset(
    {
        "researcher",
        "fact_checker",
        "fact-checker",
        "security",
        "security-review",
        "performance",
        "style",
        "reproducer",
        "hypothesizer",
        "verifier",
        "debugger",
        "explorer",
        "reviewer",
    }
)

_FULL_TOOL_MARKERS: frozenset[str] = frozenset(
    {
        "*",
        "all",
        "full",
        "inherit_all",
        "全部",
    }
)

_DYNAMIC_SKILL_PACKS: dict[str, tuple[str, ...]] = {
    "research": (
        "web_search",
        "fetch_url",
        "bb_write",
        "bb_read",
        "bb_keys",
        "todo_write",
    ),
    "web": (
        "web_search",
        "fetch_url",
        "browser_get",
        "browser_extract",
        "browser_state",
        "browser_find",
    ),
    "browser": (
        "browser_get",
        "browser_extract",
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_upload",
        "browser_scroll",
        "browser_wait",
        "browser_screenshot",
        "browser_find",
        "browser_state",
        "live_browser_current_url",
        "live_browser_state",
        "live_browser_extract",
        "live_browser_find",
        "live_browser_click",
        "live_browser_type",
        "live_browser_scroll",
        "live_browser_wait",
        "live_browser_screenshot",
    ),
    "files": (
        "list_cwd",
        "tree",
        "glob_files",
        "grep_text",
        "read_file",
        "read_file_range",
        "file_stats",
    ),
    "filesystem": (
        "list_cwd",
        "tree",
        "glob_files",
        "grep_text",
        "read_file",
        "read_file_range",
        "file_stats",
    ),
    "code": (
        "list_cwd",
        "tree",
        "glob_files",
        "grep_text",
        "read_file",
        "read_file_range",
        "code_search",
        "exec_shell",
        "edit_file",
        "multi_edit_file",
        "write_text_file",
        "propose_patch",
        "bb_read",
        "bb_write",
        "bb_keys",
        "todo_read",
        "todo_write",
    ),
    "review": (
        "list_cwd",
        "glob_files",
        "grep_text",
        "read_file",
        "read_file_range",
        "file_stats",
        "bb_read",
        "bb_write",
    ),
    "write": (
        "read_file",
        "edit_file",
        "multi_edit_file",
        "write_text_file",
        "propose_patch",
        "exec_shell",
    ),
    "memory": (
        "bb_read",
        "bb_write",
        "bb_keys",
        "todo_read",
        "todo_write",
        "query_skill",
    ),
    "shell": ("exec_shell",),
}


def _role_defaults_to_cheap(role: str) -> bool:
    """Return True when the named role should auto-route to the cheap
    subagent model. Heavy-reasoning roles (architect/synthesizer/...)
    return False; the explicit research/review allowlist returns True;
    anything else falls back to False so unknown user-defined agents
    keep using the primary model unless the caller asks otherwise."""
    if not role:
        return False
    if role in _NON_CHEAP_ROLES:
        return False
    return role in _CHEAP_BY_DEFAULT_ROLES


def _route_context_risk_level(context: dict[str, Any] | None) -> str:
    ctx = context or {}
    for key in (
        "task_risk_level",
        "risk_level",
        "approval_risk_level",
        "quality_risk_level",
    ):
        value = ctx.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "low"


def _parallel_route_decision(
    agent_id: str,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    ctx = context or {}
    try:
        from runtime.safety.evolution.subagent_routing import (
            decide_subagent_route,
        )

        decision = decide_subagent_route(
            role=agent_id,
            risk_level=_route_context_risk_level(ctx),
            review_queue_path=ctx.get("review_queue_path"),
            subagent_policy_path=ctx.get("subagent_policy_path"),
            enabled=bool(ctx.get("enable_subagent_fitness_routing", True)),
        )
        return decision.to_dict()
    except Exception:  # noqa: BLE001
        return {
            "schema": "echo.subagent_route_decision.v1",
            "role": agent_id,
            "action": "allow",
            "reason": "subagent fitness routing unavailable",
            "risk_level": _route_context_risk_level(ctx),
            "verdict": "unknown",
            "score": None,
            "confidence": 0.0,
            "evidence_item_ids": [],
        }


def _dedupe_names(names: list[str]) -> list[str]:
    return list(OrderedDict((name, None) for name in names if name).keys())


def _coerce_name_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None and parsed is not value:
            return _coerce_name_list(parsed)
        for sep in ("，", "、", ";", "\n", "\t"):
            raw = raw.replace(sep, ",")
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(value, dict):
        out: list[str] = []
        for key in (
            "tools",
            "skills",
            "skill_pack",
            "skill_packs",
            "plugins",
            "items",
        ):
            out.extend(_coerce_name_list(value.get(key)))
        return out
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for item in value:
            items.extend(_coerce_name_list(item))
        return items
    return [str(value).strip()] if str(value).strip() else []


def _skill_context_from_spec(
    raw: dict[str, Any],
    base_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build dynamic tool grants for a spawned subagent.

    The lead can pass concrete tool names through ``skills`` / ``tools``
    or higher-level bundles through ``skill_pack(s)`` / ``plugins``.
    Actual tool exposure is still intersected with the live registry by
    ``ephemeral_runner``.
    """
    context: dict[str, Any] = {}
    if isinstance(base_context, dict):
        context.update(base_context)
    embedded = raw.get("context")
    stripped_context_keys: list[str] = []
    if isinstance(embedded, dict):
        for key, value in embedded.items():
            name = str(key)
            if is_model_protected_context_key(name):
                stripped_context_keys.append(name)
                continue
            context[name] = value
    if stripped_context_keys:
        context["_delegation_context_policy"] = {
            "schema": "echo.delegation_context_policy.v1",
            "monotonic": True,
            "stripped_keys": sorted(set(stripped_context_keys)),
        }

    direct = _coerce_name_list(raw.get("skills"))
    direct.extend(_coerce_name_list(raw.get("tools")))
    direct.extend(_coerce_name_list(raw.get("tool_allowlist")))
    direct.extend(_coerce_name_list(raw.get("skill_allowlist")))
    direct.extend(_coerce_name_list(raw.get("extra_skills")))
    direct.extend(_coerce_name_list(raw.get("extra_tools")))

    pack_names = _coerce_name_list(raw.get("skill_pack"))
    pack_names.extend(_coerce_name_list(raw.get("skill_packs")))
    pack_names.extend(_coerce_name_list(raw.get("pack")))
    pack_names.extend(_coerce_name_list(raw.get("packs")))

    plugin_names = _coerce_name_list(raw.get("plugin"))
    plugin_names.extend(_coerce_name_list(raw.get("plugins")))

    requested = [name.strip() for name in [*direct, *pack_names, *plugin_names]]
    if any(name.lower() in _FULL_TOOL_MARKERS for name in requested):
        context["tool_allowlist_mode"] = "all"

    expanded: list[str] = []
    for name in pack_names:
        expanded.extend(_DYNAMIC_SKILL_PACKS.get(name.strip().lower(), ()))
    for name in plugin_names:
        expanded.extend(_DYNAMIC_SKILL_PACKS.get(name.strip().lower(), ()))

    existing = _coerce_name_list(context.get("extra_tool_allowlist"))
    grants = _dedupe_names([*existing, *expanded, *direct])
    if grants:
        context["extra_tool_allowlist"] = grants
    if pack_names:
        context["skill_pack_names"] = _dedupe_names(pack_names)
    if plugin_names:
        context["plugin_grants"] = _dedupe_names(plugin_names)
    if direct:
        context["direct_skill_grants"] = _dedupe_names(direct)

    return context or None


def _isolated_judge_context(
    base_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Context for a verifier/voter spawn: starved of caller context, read-only.

    A voter exists to reach a verdict the lead cannot reach on its own. Two
    things defeat that, and each is closed here:

    * **Caller context.** A voter that can read the lead's conversation,
      inherited memory, or its own prior turns in this thread is being handed
      the conclusion it was spawned to check — it agrees with reasoning it can
      see instead of judging the claim. The ballot already carries everything a
      verdict legitimately needs (``_build_ballot_prompt`` embeds the question
      and the artifact), so withholding the rest costs nothing and removes the
      anchor. Reviewers demonstrably catch more when they cannot see the
      producer's justification.
    * **Write access.** A ballot returns a verdict, so a judge holding
      ``exec_shell`` / ``edit_file`` / ``git_commit`` can only cause damage, and
      one that "fixes" the thing it was assessing has destroyed the very
      independence being paid for. ``bb_write`` is included in that: a voter
      publishing to the turn blackboard can steer its fellow voters, which is
      the one thing an independent panel must not be able to do.

    Both keys canonicalise into ``MODEL_PROTECTED_CONTEXT_PREFIXES``
    (``toolallowlist`` / ``subagentpolicy``), so ``arg_guard`` strips a model's
    attempt to set OR clear them — this is a trusted-side switch that a spawned
    agent cannot talk its way out of.
    """
    ctx: dict[str, Any] = dict(base_context or {})
    ctx["tool_allowlist_read_only"] = True
    ctx["subagent_policy_starve_context"] = True
    # ``share_history`` is the pre-existing per-thread memory switch and is NOT
    # prefix-protected, so it is set here as well: the starve flag is what the
    # trusted path enforces, this merely makes the intent explicit at the one
    # place that already reads it.
    ctx["share_history"] = False
    return ctx


# Default per-subagent wall-clock timeout. Bumped from 300s in 2026-06
# after operator feedback: research-style subagents commonly need 10-15
# rounds × ~60s/round, hitting the old 300s budget after only 5 rounds.
# 900s gives ~15 rounds headroom for parallel fan-outs without letting
# a stuck subagent hang the whole pipeline indefinitely.
_DEFAULT_SUBAGENT_TIMEOUT_S: int = 900

# Number of times a transient subagent failure (timeout / connection
# error) is retried before being reported as final. Set to 1 because
# retrying a structural failure (unknown role, malformed prompt) just
# wastes more tokens — only timeout-class errors get a second chance.
_MAX_RETRY_ON_TRANSIENT: int = 1


def _bump_and_check(turn_id: str | None) -> tuple[int, bool]:
    """Legacy compat shim — see ``delegation_budget.bump_and_check``."""
    cur, within = _check_absolute_cap(turn_id)
    return (cur + 1, within)


def _delegation_budget_exhausted_message(
    used: int,
    *,
    budget: Any = None,
    action: str = "Do the rest of this turn's work yourself · do NOT call_agent again.",
) -> str:
    if budget is not None:
        limit = getattr(budget, "max_spawns", _PER_TURN_ABSOLUTE_LIMIT)
        return f"orchestration spawn budget exhausted for this turn (used {used}/{limit}). {action}"
    return (
        "delegation budget exhausted for this turn "
        f"(used {used}/{_PER_TURN_ABSOLUTE_LIMIT}). {action}"
    )


# ── Catalog rendering ─────────────────────────────────────


def _format_role_catalog() -> str:
    """Render the advertised specialist list.

    Delegation is still constrained by the prompt policy and per-turn
    budget; the catalog itself should be honest about the roles that can
    actually be called so swarm mode can split research/write/review work
    without guessing hidden role names.
    """
    rows: list[tuple[str, str]] = []
    try:
        from .ephemeral_agents import BUILTIN_ROLES

        for name in sorted(_ADVERTISED_BUILTINS):
            role = BUILTIN_ROLES.get(name)
            if role is None:
                continue
            desc = (role.description or "").strip().split("\n", 1)[0]
            if len(desc) > 160:
                desc = desc[:157] + "..."
            rows.append((name, desc))
    except Exception:  # noqa: BLE001
        pass
    if not rows:
        return "  (no advertised subagents)"
    return "\n".join(f"  - {name}: {desc}" for name, desc in rows)


def _display_name_for_agent_id(agent_id: str) -> str | None:
    """Return the human-readable display name for any dispatchable role."""
    try:
        from runtime.execution.subagents import get_sub_agent_runner

        runner = get_sub_agent_runner()
        registry = getattr(runner, "agent_registry", None)
        if registry is not None and registry.has(agent_id):
            display = str(getattr(registry.get(agent_id), "display_name", "") or "").strip()
            if display:
                return display
    except Exception:  # noqa: BLE001
        pass
    try:
        from .ephemeral_agents import BUILTIN_ROLES

        role = BUILTIN_ROLES.get(agent_id)
        if role is not None:
            return role.display_name or None
    except Exception:  # noqa: BLE001
        pass
    return None


def _allowed_agent_ids() -> set[str]:
    """Names of every subagent this skill is allowed to spawn — INCLUDES
    the un-advertised builtins (researcher / debugger / explorer /
    reviewer) as escape hatches, plus any user-defined ``.claude/agents/``
    entries. We just don't advertise them."""
    out: set[str] = set()
    try:
        from .ephemeral_agents import BUILTIN_ROLES

        out.update(set(BUILTIN_ROLES.keys()) - _INTERNAL_AGENTS)
    except Exception:  # noqa: BLE001
        pass
    try:
        from runtime.execution.subagents import get_subagent_registry

        reg = get_subagent_registry()
        if reg is not None:
            out.update(set(reg.all_names()) - _INTERNAL_AGENTS)
    except Exception:  # noqa: BLE001
        pass
    try:
        from runtime.execution.subagents import get_sub_agent_runner

        runner = get_sub_agent_runner()
        registry = getattr(runner, "agent_registry", None)
        if registry is not None:
            all_ids = getattr(registry, "all_ids", None)
            if callable(all_ids):
                out.update(set(all_ids()) - _INTERNAL_AGENTS)
    except Exception:  # noqa: BLE001
        pass
    return out


# ── Custom role-name resolution ──────────────────────────
# Operators frequently want to name a delegation target after the
# task ("sleep_researcher_eight" / "kyc_screener_alpha") rather than
# pick one of the 6 advertised generic builtins. Forcing them to map
# to "researcher" loses the task-shape signal in logs/UI and breaks
# parallel runs that need distinct labels.
#
# Strategy: any unknown agent_id falls back to a generic builtin
# (default ``researcher`` for research-shaped names, ``general`` for
# everything else) AND injects the original name as a role label
# into the subagent prompt — the LLM still understands the task
# framing, the bridge logs preserve the custom name in metadata,
# and the parent gets exactly the structured failures it requested.

# Names that look research-y route to the cheap researcher builtin.
_RESEARCH_NAME_HINTS: tuple[str, ...] = (
    "research",
    "researcher",
    "explore",
    "explorer",
    "investigate",
    "study",
    "analyst",
    "analyzer",
    "fact_check",
    "fact-check",
    "scout",
    "probe",
    "screen",
    "screener",
    "audit",
    "auditor",
    "monitor",
    "watcher",
    "intel",
)


def _resolve_custom_agent_id(
    requested: str,
    allowed: set[str],
) -> tuple[str, str | None]:
    """Resolve a possibly-custom ``requested`` name to a runnable
    builtin agent_id. Returns ``(actual_id, role_label)`` where
    ``role_label`` is the original name when fallback was used (None
    when the requested name was already a real agent).

    The role_label is later injected into the subagent prompt so the
    LLM still sees the task-shaped framing the operator intended.
    """
    if requested in allowed:
        return requested, None
    lower = requested.lower()
    # Audit-shaped → explorer (file traversal + grep + read, deadline-bounded).
    # A local code audit is a read-only file review, not web research, so it
    # must not collapse into the web-focused researcher persona.
    if any(hint in lower for hint in ("audit", "审计")) and "explorer" in allowed:
        return "explorer", requested
    # Research-shaped → researcher (cheap model, broad search tools)
    if any(hint in lower for hint in _RESEARCH_NAME_HINTS) and "researcher" in allowed:
        return "researcher", requested
    # Code-shaped → debugger or explorer
    if (
        any(hint in lower for hint in ("debug", "fix", "trace", "diagnose"))
        and "debugger" in allowed
    ):
        return "debugger", requested
    if any(hint in lower for hint in ("review", "critic", "audit_code")) and "reviewer" in allowed:
        return "reviewer", requested
    # Unknown shape → general/explorer fallback
    for fallback in ("explorer", "researcher", "general"):
        if fallback in allowed:
            return fallback, requested
    # No builtins at all — return as-is, caller will reject
    return requested, None


def _wrap_prompt_with_role_label(
    prompt: str,
    role_label: str | None,
) -> str:
    """When a custom name was substituted, prepend a role-framing
    header so the LLM still acts in-character. Otherwise pass through.
    """
    if not role_label:
        return prompt
    header = (
        f"# Role: {role_label}\n\n"
        f"You are acting as **{role_label}** for this task. The framework "
        f"resolved this custom role to a generic builtin, but you should "
        f"still adopt the focus implied by the name "
        f"({role_label!r}) when prioritizing what to investigate "
        f"and how to structure your output.\n\n"
        f"---\n\n"
    )
    return header + prompt


# ── Session / timeout resolution ─────────────────────────


def _resolve_session_and_turn() -> tuple[Any, str | None]:
    """Pull the active Session + turn_id from the ContextVar."""
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
        return sess, (getattr(sess, "turn_id", None) if sess else None)
    except Exception:  # noqa: BLE001
        return None, None


def _is_transient_error(result: dict[str, Any]) -> bool:
    """Heuristic: should we retry this failure once?

    Retry on timeout / connection / rate-limit class errors. Don't
    retry on structural failures (unknown role, malformed prompt,
    budget exhausted) — those won't get better next time.
    """
    if result.get("success"):
        return False
    err_type = (result.get("error_type") or "").lower()
    if err_type in {"timeout", "connectionerror", "ratelimiterror"}:
        return True
    err_msg = (result.get("error") or "").lower()
    transient_markers = (
        "timeout",
        "timed out",
        "connection",
        "rate limit",
        "rate-limit",
        "rate_limit",
        "too many requests",
        "temporarily",
        "503",
        "504",
    )
    return any(marker in err_msg for marker in transient_markers)


def _should_auto_retry(result: dict[str, Any]) -> bool:
    """Should we retry this sub-agent failure once?

    Retries transient class errors (timeout / connection / rate-limit) plus
    budget- or convergence-exhaustion runs (round-cap, early-converged,
    partial) — a fresh attempt has a real chance to finish. This is the
    automatic fallback the parent previously had to perform by hand. Structural
    failures and arbitrary custom errors are never retried because they won't
    get better on a second attempt.
    """
    if result.get("success"):
        return False
    if _is_transient_error(result):
        return True
    if result.get("partial") or result.get("round_cap_exceeded") or result.get("converged_early"):
        return True
    err_msg = (result.get("error") or "").lower()
    exhaustion_markers = (
        "round cap",
        "round_cap",
        "without converging",
        "converged early",
        "max iteration",
        "iteration cap",
        "budget",
    )
    return any(marker in err_msg for marker in exhaustion_markers)


def _derive_error_type(result: dict[str, Any]) -> str:
    """Best-effort classification of a sub-agent failure.

    Prefers an explicit ``error_type`` key from the bridge, then the
    ``status`` field (e.g. ``timeout``), then the leading exception
    class name from the ``error`` string. Falls back to ``unknown``.
    """
    et = result.get("error_type")
    if isinstance(et, str) and et:
        return et
    if result.get("status") == "timeout":
        return "timeout"
    err = str(result.get("error") or "")
    head = err.split(":", 1)[0].strip()
    if head == "TimeoutError" or "timed out" in err.lower():
        return "timeout"
    if head in ("ConnectionError", "OSError", "TransportError"):
        return "transport"
    if head:
        return head.lower()
    return "unknown"


def _empty_parallel_result(error: str) -> dict[str, Any]:
    """Validation / budget failure shape · keep legacy + new keys."""
    return {
        # legacy keys for backward compat
        "results": [],
        "count": 0,
        "outputs": [],
        "error": error,
        # new keys
        "ok": False,
        "successes": [],
        "failures": [],
        "partial": False,
        "total": 0,
        "success_count": 0,
        "notes": [],
    }


def _coerce_timeout_s(timeout_s: Any) -> int:
    if isinstance(timeout_s, bool):
        return _DEFAULT_SUBAGENT_TIMEOUT_S
    try:
        value = int(float(timeout_s))
    except (TypeError, ValueError):
        return _DEFAULT_SUBAGENT_TIMEOUT_S
    return value if value > 0 else _DEFAULT_SUBAGENT_TIMEOUT_S


# ── consensus / vote pure helpers ────────────────────────
# The aggregation (``_extract_verdict`` / ``_tally_votes``) is a pure
# function · unit tested in isolation; the spawn reuses
# ``_call_agent_parallel`` so the concurrency cap, injection taint,
# budget, retry and timeout are all the same already-tested machinery.

_VOTE_MIN = 2
_VOTE_MAX = 5  # aligns with the per-turn delegation cap


def _coerce_vote_choices(choices: Any) -> list[str] | None:
    """Normalise a ballot into distinct labels, or None for a free verdict."""
    if choices is None:
        return None
    if isinstance(choices, str):
        parts = [c.strip() for c in choices.replace("|", ",").split(",")]
    elif isinstance(choices, (list, tuple)):
        parts = [str(c).strip() for c in choices]
    else:
        return None
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out or None


def _build_ballot_prompt(question: str, choices: list[str] | None) -> str:
    # The exact JSON shape ({verdict, reason}) is enforced by the schema
    # instruction call_subagent appends — here we only state the semantics so
    # the two don't contradict ("Line 1 MUST be ..." vs "output ONLY JSON").
    if choices:
        opts = " / ".join(choices)
        decide = f"Your `verdict` MUST be exactly one of: {opts}."
    else:
        decide = "Your `verdict` is a short answer / label."
    return (
        "You are ONE independent voter on a panel. Judge the question "
        "below on your own merits — do not assume other voters agree.\n\n"
        f"QUESTION:\n{question}\n\n"
        f"{decide}\n"
        "Also give a one-sentence `reason` (<=30 words)."
    )


def _normalize_verdict(raw: str, choices: list[str] | None) -> str:
    # Whitespace-only strip is enough: the substring match below tolerates
    # markdown / trailing punctuation noise ("**yes**", "yes.") on its own.
    v = (raw or "").strip()
    if not v:
        return ""
    if not choices:
        return v[:48]
    vl = v.lower()
    for c in choices:  # exact (casefold)
        if vl == c.lower():
            return c
    for c in choices:  # verdict line may be a sentence containing the choice
        cl = c.lower()
        if vl.startswith(cl) or cl in vl:
            return c
    return ""  # could not map to a ballot choice → abstention


def _extract_verdict(output: str, choices: list[str] | None) -> tuple[str, str]:
    """Parse (verdict, reason) from a voter's free text. verdict is ""
    when unparseable (counted as an abstention)."""
    text = (output or "").strip()
    verdict_raw = ""
    reason = ""
    for line in text.splitlines():
        s = line.strip().lstrip("-*# ").strip()
        low = s.lower()
        if not verdict_raw and low.startswith("verdict"):
            verdict_raw = s.split(":", 1)[-1].strip() if ":" in s else s[7:].strip()
        elif not reason and low.startswith("reason"):
            reason = s.split(":", 1)[-1].strip() if ":" in s else s[6:].strip()
    if not verdict_raw:
        for line in text.splitlines():
            if line.strip():
                verdict_raw = line.strip()
                break
    verdict = _normalize_verdict(verdict_raw, choices)
    if not reason:
        reason = " ".join(text.split())[:200]
    return verdict, reason


def _tally_votes(
    votes: list[dict[str, Any]],
    choices: list[str] | None,
) -> dict[str, Any]:
    """Pure majority aggregation over parsed votes."""
    abstentions = sum(1 for v in votes if not v.get("verdict"))
    tally: dict[str, int] = {}
    for v in votes:
        key = v.get("verdict")
        if key:
            tally[key] = tally.get(key, 0) + 1
    votes_cast = sum(tally.values())
    if votes_cast == 0:
        return {
            "verdict": None,
            "confidence": 0.0,
            "unanimous": False,
            "tie": False,
            "tie_between": [],
            "tally": {},
            "voter_count": len(votes),
            "votes_cast": 0,
            "abstentions": abstentions,
        }
    top = max(tally.values())
    winners = sorted(k for k, c in tally.items() if c == top)
    tie = len(winners) > 1
    return {
        "verdict": None if tie else winners[0],
        "confidence": round(top / votes_cast, 3),
        "unanimous": len(tally) == 1 and abstentions == 0,
        "tie": tie,
        "tie_between": winners if tie else [],
        "tally": dict(sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))),
        "voter_count": len(votes),
        "votes_cast": votes_cast,
        "abstentions": abstentions,
    }


def _vote_note(tally: dict[str, Any]) -> str:
    if tally["votes_cast"] == 0:
        return (
            "[no-verdict] no voter produced a parseable VERDICT; treat as "
            "inconclusive and decide yourself."
        )
    if tally["tie"]:
        return (
            f"[no-consensus] split {tally['tally']} between "
            f"{', '.join(tally['tie_between'])} — no majority. Do NOT claim a "
            "decision; break the tie yourself or escalate."
        )
    if tally["unanimous"]:
        return f"[unanimous] all {tally['votes_cast']} voters agree: {tally['verdict']}."
    return (
        f"[majority] {tally['verdict']} at {tally['confidence']:.0%} "
        f"({tally['tally']}); minority dissent present — weigh it before acting."
    )
