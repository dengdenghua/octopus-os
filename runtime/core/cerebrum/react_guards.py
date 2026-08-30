"""ReAct trajectory guards: post-step / pre-Final-Answer quality gates.

╔══════════════════════════════════════════════════════════════════════════╗
║ react_guards.py · orchestration + registry (~850 lines).                ║
║                                                                          ║
║ This module is the dependency-graph top: it owns the ``_invoke_*``      ║
║ wrappers, the ``_final_answer_security_guard``, the ``GUARD_REGISTRY``  ║
║ and ``evaluate_guards``, and re-exports every guard from the leaf       ║
║ sub-modules so the public API (used by tests and sibling modules) is    ║
║ unchanged.                                                               ║
║                                                                          ║
║   react_guard_types.py            GuardContext / GuardSpec + spec fns   ║
║   react_goal_analysis.py          goal-intent + evidence-path analysis  ║
║   react_concurrency_guards.py     single-flight / concurrency guards    ║
║   react_test_quality_guards.py    weak-test / mock-only / skip guards   ║
║   react_code_smell_guards.py      trajectory anti-pattern guards        ║
║   react_verification_guards.py    write-followup + verification guards  ║
║   react_code_mode_guards.py       code-mode completion / inspection     ║
║   react_security_guards.py        secret / destructive / exec guards    ║
║   react_todo_protocol_guards.py   todo-protocol + completion phrase     ║
║   react_browser_guards.py         browser interaction / mixed-mode      ║
║   react_final_answer_content_guards.py  incomplete / citation / counts  ║
║                                                                          ║
║ Each guard takes a ``GuardContext`` (steps + final_answer + flags) and  ║
║ returns either ``None`` (let the Final Answer through) or a message      ║
║ string explaining why the model must keep working. The guard registry    ║
║ at the bottom (``GuardSpec`` + ``evaluate_guards``) wires named guards   ║
║ to the predicates above and applies user-controlled disables.            ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable

from runtime.core.cerebrum.react_browser_guards import (  # noqa: F401 — re-exported
    _browser_goal_is_ui_only,
    _browser_interaction_completion_guard,
    _mixed_mode_completion_guard,
)
from runtime.core.cerebrum.react_code_mode_guards import (  # noqa: F401 — re-exported
    _code_mode_completion_guard,
    _code_mode_false_no_tool_guard,
    _code_mode_false_tool_result_guard,
    _code_mode_inspection_answer_fragment_guard,
    _code_mode_missing_inspection_tool_guard,
    _code_mode_missing_write_guard,
    _explicit_tool_request_guard,
    _has_successful_code_write,
)
from runtime.core.cerebrum.react_code_smell_guards import (  # noqa: F401 — re-exported
    _async_without_await_guard,
    _broad_except_suppression_guard,
    _commented_out_as_fix_guard,
    _exception_swallow_via_log_guard,
    _frontend_outside_tsconfig_include_guard,
    _full_file_rewrite_guard,
    _hardcoded_personal_path_guard,
    _long_function_guard,
    _oversized_single_edit_guard,
    _print_in_production_guard,
    _sleep_in_production_guard,
)
from runtime.core.cerebrum.react_concurrency_guards import (  # noqa: F401 — re-exported for tool_bridge / react_execution / react_in_flight_nudges / tests
    _ambiguous_inflight_leader_election_guard,
    _code_semantic_followup_guard,
    _concurrency_semantic_followup_guard,
    _destructive_waiter_result_guard,
    _loader_barrier_deadlock_guard,
    _path_boundary_decode_guard,
    _stale_immutable_waiter_snapshot_guard,
    _terminal_pending_entry_leak_guard,
    _wait_while_lock_held_guard,
)
from runtime.core.cerebrum.react_final_answer_content_guards import (  # noqa: F401 — re-exported
    _answer_item_count_guard,
    _control_tag_leak_guard,
    _fabricated_citation_guard,
    _incomplete_final_answer_guard,
    _research_low_quality_evidence_guard,
    _research_missing_lookup_guard,
    _ungrounded_external_fact_guard,
)
from runtime.core.cerebrum.react_goal_analysis import (  # noqa: F401 — re-exported for tool_bridge / react_convergence / react_explicit_reads / react_prompt_assembly / tests
    _explicit_source_paths,
    _explicitly_requested_tool_names,
    _final_answer_requests_user_help,
    _goal_requests_code_mutation,
    _goal_requests_project_inspection,
    _goal_requests_research_lookup,
    _goal_requires_file_content,
    _normalize_evidence_path,
    _path_evidence_matches,
    _successful_read_paths,
)
from runtime.core.cerebrum.react_guard_types import (
    GuardContext,
    GuardSpec,
    _spec_code_mode,
    _spec_security,
)
from runtime.core.cerebrum.react_parsing import (
    _detect_destructive_calls_in_payload,
    _detect_dynamic_exec_in_payload,
    _detect_secrets_in_payload,
    _detect_shell_injection_in_payload,
    _detect_unsafe_deser_in_payload,
)
from runtime.core.cerebrum.react_repeat_tool_guards import (  # noqa: F401 — re-exported
    _consecutive_same_tool_guard,
    _repeat_tool_reminder_guard,
)
from runtime.core.cerebrum.react_security_guards import (  # noqa: F401 — re-exported for backward compatibility
    _dynamic_exec_guard,
    _magic_number_guard,
    _network_in_loop_guard,
    _new_destructive_call_guard,
    _repeated_literal_guard,
    _secret_in_payload_guard,
    _shell_injection_guard,
    _unsafe_deser_guard,
)
from runtime.core.cerebrum.react_test_quality_guards import (  # noqa: F401 — re-exported for tests
    _deleted_test_guard,
    _generic_test_name_guard,
    _mock_only_test_guard,
    _no_assertion_test_guard,
    _trajectory_no_assertion_test_hits,
    _undocumented_skip_guard,
    _weak_test_assertion_guard,
)
from runtime.core.cerebrum.react_timeout_guards import (  # noqa: F401 — re-exported
    _consecutive_timeout_guard,
    _timeout_policy_guard,
)
from runtime.core.cerebrum.react_todo_protocol_guards import (  # noqa: F401 — re-exported for react_in_flight_nudges / react_loop / tests
    _completion_phrase_without_todo_guard,
    _looks_like_completion_phrase,
    _step_is_failed_execution,
    _todo_protocol_completion_guard,
)
from runtime.core.cerebrum.react_verification_guards import (  # noqa: F401 — re-exported for react_in_flight_nudges / react_loop / tests
    _failed_verification_followup_guard,
    _false_verification_claim_guard,
    _language_mismatched_verification_guard,
    _new_python_code_without_test_guard,
    _new_third_party_import_without_dep_guard,
    _path_verification_policy_guard,
    _red_verification_observation_guard,
    _redundant_green_verification_guard,
    _signature_changed_without_typecheck_guard,
    _unverified_write_followup_guard,
    _wire_schema_change_without_compat_test_guard,
)


def _invoke_missing_inspection(ctx: GuardContext) -> str | None:
    if ctx.is_code_mode:
        if ctx.browser_operation_mode and _browser_goal_is_ui_only(ctx.goal):
            # Browser turns inspect the app through browser_state/browser_get;
            # the file-inspection requirement belongs to workspace code tasks.
            return None
        return _code_mode_missing_inspection_tool_guard(
            ctx.steps,
            ctx.final_answer,
            goal=ctx.goal,
            file_tools_visible=ctx.file_inspection_tools_visible,
            grounded_source_paths=ctx.grounded_source_paths,
        )
    if ctx.browser_operation_mode:
        # Browser turns prove their work through browser-action evidence
        # (_browser_interaction_completion_guard), not a lookup.
        return None
    # Non-code (research/chat) turns: the same "goal demands work → require a
    # successful tool observation" contract as code mode, keyed to lookup
    # vocabulary and gated on any tools being present (pure chat has none).
    return _research_missing_lookup_guard(
        ctx.steps,
        ctx.final_answer,
        goal=ctx.goal,
        tools_active=ctx.tools_active,
    )


def _invoke_inspection_answer_fragment(ctx: GuardContext) -> str | None:
    if not ctx.is_code_mode:
        return None
    return _code_mode_inspection_answer_fragment_guard(
        ctx.final_answer,
        goal=ctx.goal,
        file_tools_visible=ctx.file_inspection_tools_visible,
    )


def _invoke_incomplete_final(ctx: GuardContext) -> str | None:
    return _incomplete_final_answer_guard(ctx.final_answer)


def _invoke_control_tag_leak(ctx: GuardContext) -> str | None:
    return _control_tag_leak_guard(ctx.final_answer)


def _invoke_answer_item_count(ctx: GuardContext) -> str | None:
    return _answer_item_count_guard(ctx.goal, ctx.final_answer)


def _invoke_false_no_tool(ctx: GuardContext) -> str | None:
    if not ctx.is_code_mode:
        return None
    return _code_mode_false_no_tool_guard(
        ctx.steps,
        ctx.final_answer,
        goal=ctx.goal,
        tools_active=ctx.file_inspection_tools_visible,
    )


def _invoke_false_tool_result(ctx: GuardContext) -> str | None:
    if not ctx.is_code_mode:
        return None
    return _code_mode_false_tool_result_guard(
        ctx.steps,
        ctx.final_answer,
        tools_active=ctx.tools_active,
    )


def _invoke_explicit_tool_request(ctx: GuardContext) -> str | None:
    if not ctx.tools_active:
        return None
    return _explicit_tool_request_guard(
        ctx.steps,
        ctx.final_answer,
        goal=ctx.goal,
    )


def _invoke_missing_write(ctx: GuardContext) -> str | None:
    if not ctx.is_code_mode or not ctx.tools_active:
        return None
    if ctx.browser_operation_mode and _browser_goal_is_ui_only(ctx.goal):
        # A browser turn proves its work through browser-action evidence
        # (see _browser_interaction_completion_guard) — its goal wording
        # ("create/edit/delete …") matches the mutation markers, but the
        # mutations land in the app under test, not in workspace files.
        # Demanding a file write here derails the model into writing
        # throwaway files just to appease this guard.
        return None
    return _code_mode_missing_write_guard(
        ctx.steps,
        ctx.final_answer,
        goal=ctx.goal,
    )


def _invoke_todo_protocol(ctx: GuardContext) -> str | None:
    if not (ctx.todo_protocol_required and ctx.todo_protocol_visible):
        return None
    return _todo_protocol_completion_guard(ctx.steps, ctx.final_answer, goal=ctx.goal)


def _invoke_code_mode_completion(ctx: GuardContext) -> str | None:
    if not ctx.is_code_mode:
        return None
    return _code_mode_completion_guard(
        ctx.steps,
        ctx.final_answer,
        todo_protocol_required=ctx.todo_protocol_required,
        execution_degraded=ctx.execution_degraded,
    )


def _invoke_fabricated_citation(ctx: GuardContext) -> str | None:
    # Research / chat only — code turns cite files, not URLs, and have
    # their own verification cluster.
    if ctx.is_code_mode:
        return None
    return _fabricated_citation_guard(
        ctx.steps,
        ctx.final_answer,
        prior_observations=ctx.prior_grounding_text,
    )


def _invoke_ungrounded_fact(ctx: GuardContext) -> str | None:
    # Research / chat only — code turns have their own evidence cluster
    # (language / path / typecheck / test-coverage guards).
    if ctx.is_code_mode:
        return None
    return _ungrounded_external_fact_guard(
        ctx.steps,
        ctx.final_answer,
        prior_observations=ctx.prior_grounding_text,
    )


def _invoke_low_quality_research(ctx: GuardContext) -> str | None:
    if ctx.is_code_mode:
        return None
    return _research_low_quality_evidence_guard(
        ctx.steps,
        ctx.final_answer,
        goal=ctx.goal,
    )


def _invoke_browser_completion(ctx: GuardContext) -> str | None:
    return _browser_interaction_completion_guard(ctx)


def _invoke_mixed_mode_completion(ctx: GuardContext) -> str | None:
    return _mixed_mode_completion_guard(ctx)


def _invoke_repeat_tool_reminder(ctx: GuardContext) -> str | None:
    """Check for repeated tool calls within a window."""
    if not ctx.tools_active:
        return None
    return _repeat_tool_reminder_guard(ctx.steps, ctx.final_answer, threshold=3, window=5)


def _invoke_consecutive_same_tool(ctx: GuardContext) -> str | None:
    """Check for consecutive identical tool calls (stricter)."""
    if not ctx.tools_active:
        return None
    return _consecutive_same_tool_guard(ctx.steps, ctx.final_answer, threshold=3)


def _invoke_timeout_policy(ctx: GuardContext) -> str | None:
    """Check for repeated tool timeouts within a window."""
    if not ctx.tools_active:
        return None
    return _timeout_policy_guard(ctx.steps, ctx.final_answer, threshold=2, window=5)


def _invoke_consecutive_timeout(ctx: GuardContext) -> str | None:
    """Check for consecutive tool timeouts (stricter)."""
    if not ctx.tools_active:
        return None
    return _consecutive_timeout_guard(ctx.steps, ctx.final_answer, threshold=2)


def _preview_labels(labels: list[str], limit: int = 3) -> str:
    preview = ", ".join(labels[:limit])
    if len(labels) > limit:
        preview += f", +{len(labels) - limit} more"
    return preview


def _final_answer_security_guard(
    ctx: GuardContext,
) -> tuple[str, str] | None:
    """Scan the final answer itself for security-sensitive code snippets.

    Trajectory guards catch unsafe code written via tools. This catches the
    separate failure mode where a chat/research answer contains a fenced code
    block or command snippet with obvious unsafe patterns.
    """
    text = ctx.final_answer or ""
    if not text:
        return None

    secret_hits = _detect_secrets_in_payload(text)
    if secret_hits:
        return (
            "secret-leak guard",
            "Cannot finish yet: the final answer itself contains a "
            f"credential-shaped value ({_preview_labels(secret_hits)}). "
            "Do not reveal API keys, access tokens, private keys, or "
            "password-like literals in the user-visible answer. Redact the "
            "value and explain how to store it safely.",
        )

    dynamic_hits = _detect_dynamic_exec_in_payload(text)
    if dynamic_hits:
        return (
            "dynamic-exec guard",
            "Cannot finish yet: the final answer includes dynamic-execution "
            f"code ({_preview_labels(dynamic_hits)}). Replace it with a safer "
            "pattern such as ast.literal_eval, explicit dispatch, or a "
            "trusted import allowlist, or clearly mark it as unsafe and do "
            "not present it as recommended code.",
        )

    shell_hits = _detect_shell_injection_in_payload(text)
    if shell_hits:
        return (
            "shell-injection guard",
            "Cannot finish yet: the final answer includes shell-injection "
            f"surface(s) ({_preview_labels(shell_hits)}). Prefer argv-list "
            "subprocess calls and avoid shell=True/os.system/os.popen in "
            "recommended code.",
        )

    deser_hits = _detect_unsafe_deser_in_payload(text)
    if deser_hits:
        return (
            "unsafe-deser guard",
            "Cannot finish yet: the final answer includes unsafe "
            f"deserialization ({_preview_labels(deser_hits)}). Recommend "
            "json.loads, yaml.safe_load, or a typed schema validator instead.",
        )

    destructive_hits = _detect_destructive_calls_in_payload(text)
    if destructive_hits:
        return (
            "destructive-call guard",
            "Cannot finish yet: the final answer includes destructive "
            f"filesystem/process calls ({_preview_labels(destructive_hits)}). "
            "Add explicit path validation, dry-run/confirmation semantics, "
            "or avoid presenting the snippet as safe production code.",
        )

    return None


# ── The registry: ordered by precedence (security → quality) ──────
# Order here REPLACES the old if-elif chain order exactly. Security
# guards fire first (highest blast radius), protocol/tool-availability
# next, then test-quality, verification, code-smell, and finally the
# catch-all completion guard.

GUARD_REGISTRY: list[GuardSpec] = [
    # ── Security cluster (highest priority) ──
    _spec_security("secret-leak guard", "security", _secret_in_payload_guard),
    _spec_security("destructive-call guard", "security", _new_destructive_call_guard),
    _spec_security("dynamic-exec guard", "security", _dynamic_exec_guard),
    _spec_security("shell-injection guard", "security", _shell_injection_guard),
    _spec_security("unsafe-deser guard", "security", _unsafe_deser_guard),
    _spec_code_mode("path-boundary decode guard", "security", _path_boundary_decode_guard),
    # ── Loop detection (DSH P1: repeat-tool-reminder) ──
    GuardSpec("consecutive-same-tool guard", "protocol", _invoke_consecutive_same_tool),
    GuardSpec("repeat-tool-reminder guard", "protocol", _invoke_repeat_tool_reminder),
    # ── Timeout detection (DSH P1: timeout-policy) ──
    GuardSpec("consecutive-timeout guard", "protocol", _invoke_consecutive_timeout),
    GuardSpec("timeout-policy guard", "protocol", _invoke_timeout_policy),
    # ── Tool-availability / inspection-evidence ──
    GuardSpec("control-tag leak guard", "protocol", _invoke_control_tag_leak),
    GuardSpec("final-answer completeness guard", "protocol", _invoke_incomplete_final),
    GuardSpec("answer-item-count guard", "protocol", _invoke_answer_item_count),
    GuardSpec("inspection-evidence guard", "protocol", _invoke_missing_inspection),
    GuardSpec(
        "inspection-answer-fragment guard",
        "protocol",
        _invoke_inspection_answer_fragment,
    ),
    GuardSpec("tool-availability guard", "protocol", _invoke_false_no_tool),
    GuardSpec("tool-result guard", "protocol", _invoke_false_tool_result),
    GuardSpec("explicit-tool-contract guard", "protocol", _invoke_explicit_tool_request),
    GuardSpec("implementation-write guard", "protocol", _invoke_missing_write),
    GuardSpec("todo-protocol guard", "protocol", _invoke_todo_protocol),
    GuardSpec("mixed-mode completion guard", "protocol", _invoke_mixed_mode_completion),
    GuardSpec("browser-completion guard", "protocol", _invoke_browser_completion),
    # ── Research / chat quality (non-code turns) ──
    GuardSpec("citation-grounding guard", "research", _invoke_fabricated_citation),
    GuardSpec("fact-grounding guard", "research", _invoke_ungrounded_fact),
    GuardSpec("research-evidence-quality guard", "research", _invoke_low_quality_research),
    # ── Verification completeness ──
    _spec_code_mode(
        "language-verification guard", "verification", _language_mismatched_verification_guard
    ),
    _spec_code_mode("path-verification guard", "verification", _path_verification_policy_guard),
    _spec_code_mode("test-coverage guard", "verification", _new_python_code_without_test_guard),
    # ── Test-quality cluster ──
    _spec_code_mode("weak-test guard", "test-quality", _weak_test_assertion_guard),
    _spec_code_mode("mock-only-test guard", "test-quality", _mock_only_test_guard),
    _spec_code_mode("undocumented-skip guard", "test-quality", _undocumented_skip_guard),
    _spec_code_mode("deleted-test guard", "test-quality", _deleted_test_guard),
    _spec_code_mode("generic-test-name guard", "test-quality", _generic_test_name_guard),
    _spec_code_mode("no-assertion-test guard", "test-quality", _no_assertion_test_guard),
    # ── Interface / dependency safety ──
    _spec_code_mode(
        "signature-typecheck guard", "verification", _signature_changed_without_typecheck_guard
    ),
    _spec_code_mode(
        "wire-schema guard", "verification", _wire_schema_change_without_compat_test_guard
    ),
    _spec_code_mode(
        "dependency-declaration guard", "verification", _new_third_party_import_without_dep_guard
    ),
    _spec_code_mode("false-verification guard", "verification", _false_verification_claim_guard),
    _spec_code_mode("red-verification guard", "verification", _red_verification_observation_guard),
    # ── Code-smell cluster ──
    _spec_code_mode("comment-out-fix guard", "code-smell", _commented_out_as_fix_guard),
    _spec_code_mode("broad-except guard", "code-smell", _broad_except_suppression_guard),
    _spec_code_mode("exception-swallow guard", "code-smell", _exception_swallow_via_log_guard),
    _spec_code_mode(
        "tsconfig-include guard", "code-smell", _frontend_outside_tsconfig_include_guard
    ),
    _spec_code_mode("oversized-edit guard", "code-smell", _oversized_single_edit_guard),
    _spec_code_mode("sleep-in-prod guard", "code-smell", _sleep_in_production_guard),
    _spec_code_mode("async-without-await guard", "code-smell", _async_without_await_guard),
    _spec_code_mode("full-rewrite guard", "code-smell", _full_file_rewrite_guard),
    _spec_code_mode(
        "single-flight wait-under-lock guard",
        "code-smell",
        _wait_while_lock_held_guard,
    ),
    _spec_code_mode(
        "single-flight leader-election guard",
        "code-smell",
        _ambiguous_inflight_leader_election_guard,
    ),
    _spec_code_mode(
        "single-flight waiter-result guard",
        "code-smell",
        _destructive_waiter_result_guard,
    ),
    _spec_code_mode(
        "single-flight immutable-snapshot guard",
        "code-smell",
        _stale_immutable_waiter_snapshot_guard,
    ),
    _spec_code_mode(
        "single-flight terminal-pending guard",
        "code-smell",
        _terminal_pending_entry_leak_guard,
    ),
    _spec_code_mode(
        "single-flight test-barrier guard",
        "test-quality",
        _loader_barrier_deadlock_guard,
    ),
    _spec_code_mode("print-in-prod guard", "code-smell", _print_in_production_guard),
    _spec_code_mode("hardcoded-path guard", "code-smell", _hardcoded_personal_path_guard),
    _spec_code_mode("long-function guard", "code-smell", _long_function_guard),
    _spec_code_mode("network-in-loop guard", "code-smell", _network_in_loop_guard),
    _spec_code_mode("repeated-literal guard", "code-smell", _repeated_literal_guard),
    _spec_code_mode("magic-number guard", "code-smell", _magic_number_guard),
    # ── Catch-all completion guard (lowest priority) ──
    GuardSpec("code-mode guard", "protocol", _invoke_code_mode_completion),
]


# Detection and enforcement are deliberately separate. A detector answers
# "is this worth mentioning?"; it must not automatically gain authority to
# fail the user's task. Security/integrity findings remain fail-closed,
# contract findings get one bounded repair opportunity in the loop, and
# style-quality findings are telemetry/advice only.
_ADVISORY_GUARD_CATEGORIES = frozenset({"test-quality", "code-smell"})
_ADVISORY_GUARD_LABELS = frozenset(
    {
        "inspection-answer-fragment guard",
        # The checklist is a coordination/UI aid, not execution evidence.
        # Missing or stale todos remain observable through guard telemetry,
        # but must never replace a useful final answer with internal protocol
        # wording. Safety, write, tool-result and verification guards still
        # enforce the actual completion contract independently.
        "todo-protocol guard",
    }
)
_HARD_GUARD_LABELS = frozenset(
    {
        "secret-leak guard",
        "destructive-call guard",
        "dynamic-exec guard",
        "shell-injection guard",
        "unsafe-deser guard",
        "path-boundary decode guard",
        "control-tag leak guard",
        "citation-grounding guard",
        "final-answer completeness guard",
        "implementation-write guard",
        "tool-result guard",
        "false-verification guard",
        "red-verification guard",
    }
)

# Guards whose evidence contract REQUIRES RUNNING a test / typechecker /
# verification command — not merely writing or reading files. When the
# execution environment is degraded (sandbox / network / OS-permission
# blocks, detected live as ≥2 environmental failures in the trajectory),
# the model physically cannot satisfy these, so ``evaluate_guards``
# downgrades them from ``repair`` to ``advisory`` for that turn instead of
# three-striking a turn whose demanded evidence cannot exist.
#
#   path-verification guard    → demands running the suggested checks
#   signature-typecheck guard  → demands running mypy / pyright / pyrefly
#   language-verification guard → demands a matching-language verifier run
#
# Deliberately excludes read/write-based guards: test-coverage (write a
# test file), wire-schema (write a contract test), dependency-declaration
# (write a dep-manifest entry) and todo-protocol (write a checklist) all
# hold even when exec is blocked. The hard-tier false/red-verification
# guards stay fail-closed: fabrication is never environmentally justified.
_EXECUTION_EVIDENCE_GUARDS: frozenset[str] = frozenset(
    {
        "language-verification guard",
        "path-verification guard",
        "signature-typecheck guard",
    }
)


def _guard_effectively_advisory(ctx: GuardContext, spec: GuardSpec) -> bool:
    """Whether a firing guard is telemetry-only for this turn.

    Two routes to advisory: the guard is inherently advisory (style /
    quality tiers), or the execution environment is degraded and the guard
    demands run-produced evidence the environment cannot supply — downgrade
    so a sandboxed / network-blocked turn isn't vetoed for evidence that
    can never exist. Hard-tier and read/write-based guards are never
    downgraded: fabrication and static-evidence contracts hold even when
    exec is blocked.
    """
    if guard_disposition(spec.label, spec.category) == "advisory":
        return True
    return ctx.execution_degraded and spec.label in _EXECUTION_EVIDENCE_GUARDS


def guard_disposition(label: str, category: str | None = None) -> str:
    """Return ``hard``, ``repair`` or ``advisory`` for a guard finding."""
    if label in _HARD_GUARD_LABELS or category == "security":
        return "hard"
    if label in _ADVISORY_GUARD_LABELS or category in _ADVISORY_GUARD_CATEGORIES:
        return "advisory"
    return "repair"


def evaluate_guards(
    ctx: GuardContext,
    *,
    registry: list[GuardSpec] | None = None,
    recorder: Callable[[str, str, str], None] | None = None,  # Now takes message too
    disabled_labels: frozenset[str] | set[str] | None = None,
    categories: frozenset[str] | set[str] | None = None,
) -> tuple[str, str] | None:
    """Walk the registry in priority order; return the first
    ``(label, message)`` that fires, or ``None`` if all pass.

    Mirrors the old chain's short-circuit semantics exactly: the
    highest-priority guard that returns a non-empty message wins.

    ``recorder`` (optional) is called as ``recorder(label, category, message)``
    for the firing guard — this is the P1 evolution-loop telemetry
    hook. It is wrapped so a recorder failure can never break the
    ReAct loop. Defaults to None (no telemetry) so the hot path and
    tests stay side-effect-free unless a sink is explicitly injected.

    ``disabled_labels`` (optional) is a runtime kill-switch: any guard
    whose ``label`` is in this set is skipped even if its ``enabled``
    field is True. Designed for emergency response — when a guard
    fires false positives in production, an operator can set
    ``ECHO_DISABLED_GUARDS="magic-number guard,long-function guard"``
    and restart the loop without a code release. Disabled hits are NOT
    recorded to telemetry (they didn't actually block anything).

    ``categories`` (optional) narrows evaluation to coarse guard groups.
    Salvage paths use this to skip mutation-specific quality checks while
    retaining security, protocol-completeness, and research-grounding gates.

    Model-aware routing: if ctx.model is set, code-smell guards are only
    applied to cheap models (Haiku, Flash, mini). Premium models (Opus,
    Sonnet) skip code-smell guards as they rarely make basic mistakes.
    """
    # Model-aware category filtering
    if ctx.model and categories is None:
        from runtime.core.cerebrum.guard_model_policy import guard_categories_for_model

        # Default categories that always apply. "research" is deliberately
        # in the always-on base set: the docstring contract says salvage
        # paths retain research-grounding gates (citation / fact-grounding),
        # which are both gated on `fetched=True` so pure code turns never
        # fire them. Omitting it here let react_terminal's forced-convergence
        # path silently drop research guards whenever a model was passed.
        base_categories = {"security", "protocol", "verification", "evidence", "other", "research"}
        categories = guard_categories_for_model(ctx.model, base_categories=base_categories)

    specs = registry if registry is not None else GUARD_REGISTRY
    if registry is None and (categories is None or "security" in categories):
        final_answer_hit = _final_answer_security_guard(ctx)
        if final_answer_hit is not None:
            label, message = final_answer_hit
            if not disabled_labels or label not in disabled_labels:
                if recorder is not None:
                    with contextlib.suppress(Exception):
                        recorder(label, "security", message)
                return (label, message)
    for spec in specs:
        if not spec.enabled:
            continue
        if categories is not None and spec.category not in categories:
            continue
        if disabled_labels and spec.label in disabled_labels:
            continue
        msg = spec.invoke(ctx)
        if msg:
            if recorder is not None:
                with contextlib.suppress(Exception):
                    recorder(spec.label, spec.category, msg)
            # Preserve custom-registry behavior for external callers. Policy
            # applies to the built-in production registry: quality/style
            # findings still produce telemetry but cannot consume another
            # model iteration or block delivery. The same telemetry-only
            # treatment extends to execution-evidence guards when the
            # trajectory shows a degraded execution environment — the
            # demanded test/typecheck evidence physically cannot exist.
            if registry is None and _guard_effectively_advisory(ctx, spec):
                continue
            return (spec.label, msg)
    return None


__all__ = [
    # ── Core types / registry ──
    "GuardContext",
    "GuardSpec",
    "GUARD_REGISTRY",
    "evaluate_guards",
    "guard_disposition",
    "_guard_effectively_advisory",
    # ── Goal-intent / evidence-path analysis (re-exported) ──
    "_explicit_source_paths",
    "_explicitly_requested_tool_names",
    "_final_answer_requests_user_help",
    "_goal_requests_code_mutation",
    "_goal_requests_project_inspection",
    "_goal_requests_research_lookup",
    "_goal_requires_file_content",
    "_normalize_evidence_path",
    "_path_evidence_matches",
    "_successful_read_paths",
    # ── Code-mode guards (re-exported) ──
    "_code_mode_completion_guard",
    "_code_mode_false_no_tool_guard",
    "_code_mode_false_tool_result_guard",
    "_code_mode_inspection_answer_fragment_guard",
    "_code_mode_missing_inspection_tool_guard",
    "_code_mode_missing_write_guard",
    "_explicit_tool_request_guard",
    # ── Code-smell guards (re-exported) ──
    "_async_without_await_guard",
    "_broad_except_suppression_guard",
    "_commented_out_as_fix_guard",
    "_exception_swallow_via_log_guard",
    "_frontend_outside_tsconfig_include_guard",
    "_full_file_rewrite_guard",
    "_hardcoded_personal_path_guard",
    "_long_function_guard",
    "_oversized_single_edit_guard",
    "_print_in_production_guard",
    "_sleep_in_production_guard",
    # ── Concurrency guards (re-exported) ──
    "_ambiguous_inflight_leader_election_guard",
    "_code_semantic_followup_guard",
    "_concurrency_semantic_followup_guard",
    "_destructive_waiter_result_guard",
    "_loader_barrier_deadlock_guard",
    "_path_boundary_decode_guard",
    "_stale_immutable_waiter_snapshot_guard",
    "_terminal_pending_entry_leak_guard",
    "_wait_while_lock_held_guard",
    # ── Final-answer content guards (re-exported) ──
    "_answer_item_count_guard",
    "_fabricated_citation_guard",
    "_incomplete_final_answer_guard",
    "_research_missing_lookup_guard",
    "_ungrounded_external_fact_guard",
    # ── Security guards (re-exported) ──
    "_dynamic_exec_guard",
    "_magic_number_guard",
    "_network_in_loop_guard",
    "_new_destructive_call_guard",
    "_repeated_literal_guard",
    "_secret_in_payload_guard",
    "_shell_injection_guard",
    "_unsafe_deser_guard",
    # ── Test-quality guards (re-exported) ──
    "_deleted_test_guard",
    "_generic_test_name_guard",
    "_mock_only_test_guard",
    "_no_assertion_test_guard",
    "_trajectory_no_assertion_test_hits",
    "_undocumented_skip_guard",
    "_weak_test_assertion_guard",
    # ── Todo-protocol / completion phrase (re-exported) ──
    "_completion_phrase_without_todo_guard",
    "_looks_like_completion_phrase",
    "_todo_protocol_completion_guard",
    # ── Verification guards (re-exported) ──
    "_failed_verification_followup_guard",
    "_false_verification_claim_guard",
    "_language_mismatched_verification_guard",
    "_new_python_code_without_test_guard",
    "_new_third_party_import_without_dep_guard",
    "_path_verification_policy_guard",
    "_red_verification_observation_guard",
    "_redundant_green_verification_guard",
    "_signature_changed_without_typecheck_guard",
    "_unverified_write_followup_guard",
    "_wire_schema_change_without_compat_test_guard",
    # ── Browser guards (re-exported) ──
    "_browser_goal_is_ui_only",
    "_browser_interaction_completion_guard",
    "_mixed_mode_completion_guard",
    # ── Invoke wrappers / final-answer scan ──
    "_invoke_answer_item_count",
    "_invoke_browser_completion",
    "_invoke_code_mode_completion",
    "_invoke_explicit_tool_request",
    "_invoke_fabricated_citation",
    "_invoke_false_no_tool",
    "_invoke_false_tool_result",
    "_invoke_incomplete_final",
    "_invoke_inspection_answer_fragment",
    "_invoke_missing_inspection",
    "_invoke_missing_write",
    "_invoke_mixed_mode_completion",
    "_invoke_todo_protocol",
    "_final_answer_security_guard",
    "_preview_labels",
]
