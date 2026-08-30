"""Concurrency / path-boundary semantic guards (single-flight family).

Extracted from ``react_guards.py`` (Wave 3, cluster 2a). The six
single-flight trajectory guards, the path-boundary URL-decode guard,
and the two follow-up aggregators consumed by tool_bridge /
react_execution / react_in_flight_nudges. Leaf module: depends only on
react_goal_analysis / react_parsing / react_types — must never import
react_guards.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_goal_analysis import _final_answer_requests_user_help
from runtime.core.cerebrum.react_parsing import (
    _extract_step_payloads,
    _is_code_write_step,
    _is_test_path,
    _parse_action,
    _payload_has_ambiguous_inflight_leader_election,
    _payload_has_destructive_waiter_result_pop,
    _payload_has_inflight_identity_comparison,
    _payload_has_loader_barrier_deadlock,
    _payload_has_single_pass_url_decode,
    _payload_has_stale_immutable_waiter_snapshot,
    _payload_has_terminal_pending_entry_leak,
    _payload_has_wait_while_lock_held,
    _payload_looks_like_path_boundary,
)
from runtime.core.cerebrum.react_types import ReActStep


def _ambiguous_inflight_leader_election_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject a common single-flight race that local smoke tests can miss."""
    if not is_code_mode or not steps or _final_answer_requests_user_help(final_answer):
        return None
    affected: set[str] = set()
    for step in steps:
        if not _is_code_write_step(step):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        tool_name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if not isinstance(path, str) or _is_test_path(path):
            continue
        new_text, old_text = _extract_step_payloads(step)
        old_hit = _payload_has_ambiguous_inflight_leader_election(old_text)
        new_hit = _payload_has_ambiguous_inflight_leader_election(new_text)
        full_clean_rewrite = (
            tool_name in {"write_text_file", "write_file", "create_file"}
            and path in affected
            and bool(new_text)
            and not new_hit
        )
        removed_identity_election = _payload_has_inflight_identity_comparison(
            old_text
        ) and not _payload_has_inflight_identity_comparison(new_text)
        if (old_hit or removed_identity_election or full_clean_rewrite) and not new_hit:
            affected.discard(path)
        elif new_hit:
            affected.add(path)
    if not affected:
        return None
    preview = ", ".join(sorted(affected)[:3])
    return (
        "Cannot finish yet: the single-flight implementation in "
        f"{preview} tries to distinguish leader from follower by re-reading the pending map and "
        "comparing object identity after the lock. Creator and followers all observe the same "
        "pending object, so multiple callers can execute the loader. Capture an explicit leader "
        "boolean inside the locked `pending is None` branch (or keep the loader branch tied to "
        "entry creation), make followers wait outside the map lock, then rerun a contention test "
        "whose loader stays in-flight until followers have joined."
    )


def _destructive_waiter_result_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject single-flight state that only one of many waiters can consume."""
    if not is_code_mode or not steps or _final_answer_requests_user_help(final_answer):
        return None
    affected: set[str] = set()
    for step in steps:
        if not _is_code_write_step(step):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        tool_name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if not isinstance(path, str) or _is_test_path(path):
            continue
        new_text, old_text = _extract_step_payloads(step)
        old_hit = _payload_has_destructive_waiter_result_pop(old_text)
        new_hit = _payload_has_destructive_waiter_result_pop(new_text)
        removed_pop = ".pop(" in old_text and ".pop(" not in new_text
        full_clean_rewrite = (
            tool_name in {"write_text_file", "write_file", "create_file"}
            and path in affected
            and bool(new_text)
            and not new_hit
        )
        if (old_hit or removed_pop or full_clean_rewrite) and not new_hit:
            affected.discard(path)
        elif new_hit:
            affected.add(path)
    if not affected:
        return None
    preview = ", ".join(sorted(affected)[:3])
    return (
        "Cannot finish yet: follower waiters in "
        f"{preview} read the shared loader result with `pop`. With multiple waiters, the first "
        "follower consumes that result and later followers can miss it, retry the loader, or "
        "return no value. Store result/exception on the per-flight pending object (or read the "
        "fresh cache entry non-destructively) until every waiter has observed it; only remove the "
        "in-flight map entry after wake-up state is durable. Then run a contention test with at "
        "least eight callers and a loader held in-flight long enough for followers to join."
    )


def _stale_immutable_waiter_snapshot_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject tuple-based pending state that becomes stale across wait()."""
    if not is_code_mode or not steps or _final_answer_requests_user_help(final_answer):
        return None
    affected: set[str] = set()
    for step in steps:
        if not _is_code_write_step(step):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        tool_name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if not isinstance(path, str) or _is_test_path(path):
            continue
        new_text, old_text = _extract_step_payloads(step)
        old_hit = _payload_has_stale_immutable_waiter_snapshot(old_text)
        new_hit = _payload_has_stale_immutable_waiter_snapshot(new_text)
        full_clean_rewrite = (
            tool_name in {"write_text_file", "write_file", "create_file"}
            and path in affected
            and bool(new_text)
            and not new_hit
        )
        removed_stale_fallback = (
            ".get(" in old_text
            and ", pending" in old_text
            and not (".get(" in new_text and ", pending" in new_text)
        )
        if (old_hit or removed_stale_fallback or full_clean_rewrite) and not new_hit:
            affected.discard(path)
        elif new_hit:
            affected.add(path)
    if not affected:
        return None
    preview = ", ".join(sorted(affected)[:3])
    return (
        "Cannot finish yet: follower waiters in "
        f"{preview} capture an immutable pending tuple before waiting, while the leader later "
        "replaces and deletes the map entry. The post-wait fallback therefore reads the stale "
        "tuple and can return `None` or lose the loader exception. Store result/exception on a "
        "mutable per-flight object that every waiter shares, or keep a durable completed entry "
        "until all waiters can read it. Then run an eight-caller test whose loader remains "
        "blocked until every follower has joined."
    )


def _terminal_pending_entry_leak_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject completed in-flight entries that permanently shadow TTL reloads."""
    if not is_code_mode or not steps or _final_answer_requests_user_help(final_answer):
        return None
    affected: set[str] = set()
    for step in steps:
        if not _is_code_write_step(step):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        tool_name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if not isinstance(path, str) or _is_test_path(path):
            continue
        new_text, old_text = _extract_step_payloads(step)
        old_hit = _payload_has_terminal_pending_entry_leak(old_text)
        new_hit = _payload_has_terminal_pending_entry_leak(new_text)
        full_clean_rewrite = (
            tool_name in {"write_text_file", "write_file", "create_file"}
            and path in affected
            and bool(new_text)
            and not new_hit
        )
        if (old_hit or full_clean_rewrite) and not new_hit:
            affected.discard(path)
        elif new_hit:
            affected.add(path)
    if not affected:
        return None
    preview = ", ".join(sorted(affected)[:3])
    return (
        "Cannot finish yet: the completed single-flight entry in "
        f"{preview} remains in the pending/in-flight map after the event is signalled. Future "
        "calls therefore keep taking the follower branch; an expired TTL value can never reload "
        "and a failed load can poison every retry. Publish result/exception on a mutable flight "
        "object retained by existing waiters, then remove the key from the in-flight map before "
        "returning. Add a regression that loads, advances the clock past TTL, and proves a new "
        "loader invocation occurs."
    )


def _loader_barrier_deadlock_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject concurrency tests whose loader can never clear its barrier."""
    if not is_code_mode or not steps or _final_answer_requests_user_help(final_answer):
        return None
    affected: set[str] = set()
    for step in steps:
        if not _is_code_write_step(step):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        tool_name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if not isinstance(path, str) or not _is_test_path(path):
            continue
        new_text, old_text = _extract_step_payloads(step)
        old_hit = _payload_has_loader_barrier_deadlock(old_text)
        new_hit = _payload_has_loader_barrier_deadlock(new_text)
        full_clean_rewrite = (
            tool_name in {"write_text_file", "write_file", "create_file"}
            and path in affected
            and bool(new_text)
            and not new_hit
        )
        if (old_hit or full_clean_rewrite) and not new_hit:
            affected.discard(path)
        elif new_hit:
            affected.add(path)
    if not affected:
        return None
    preview = ", ".join(sorted(affected)[:3])
    return (
        "Cannot finish yet: the single-flight test in "
        f"{preview} waits on a threading.Barrier inside the loader, but only the elected leader "
        "enters that loader; followers wait on the flight event, so the barrier can never fill. "
        "Move the barrier to each worker immediately before get_or_load. If the loader must stay "
        "in flight, have it wait on a separate Event that the test thread releases after callers "
        "have started, and use bounded waits/joins."
    )


def _wait_while_lock_held_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject follower waits that retain the lock the leader must acquire."""
    if not is_code_mode or not steps or _final_answer_requests_user_help(final_answer):
        return None
    affected: set[str] = set()
    for step in steps:
        if not _is_code_write_step(step):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        tool_name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if not isinstance(path, str) or _is_test_path(path):
            continue
        new_text, old_text = _extract_step_payloads(step)
        old_hit = _payload_has_wait_while_lock_held(old_text)
        new_hit = _payload_has_wait_while_lock_held(new_text)
        full_clean_rewrite = (
            tool_name in {"write_text_file", "write_file", "create_file"}
            and path in affected
            and bool(new_text)
            and not new_hit
        )
        if (old_hit or full_clean_rewrite) and not new_hit:
            affected.discard(path)
        elif new_hit:
            affected.add(path)
    if not affected:
        return None
    preview = ", ".join(sorted(affected)[:3])
    return (
        "Cannot finish yet: follower code in "
        f"{preview} calls wait() while still holding the shared lock. The leader must acquire "
        "that same lock to publish the result and signal the event, so the two sides deadlock. "
        "Capture the pending object and an explicit leader flag under the lock, leave the locked "
        "block, then make followers wait. The loader and every blocking wait must run outside the "
        "shared map lock; reacquire it only for short state updates."
    )


def _concurrency_semantic_followup_guard(
    steps: list[ReActStep],
    *,
    is_code_mode: bool,
) -> str | None:
    """Surface deterministic concurrency defects immediately after a write."""
    for guard in (
        _wait_while_lock_held_guard,
        _ambiguous_inflight_leader_election_guard,
        _destructive_waiter_result_guard,
        _stale_immutable_waiter_snapshot_guard,
        _terminal_pending_entry_leak_guard,
        _loader_barrier_deadlock_guard,
    ):
        message = guard(steps, "implementation complete", is_code_mode=is_code_mode)
        if message is not None:
            return message.replace("Cannot finish yet: ", "Before verification: ", 1)
    return None


def _path_boundary_decode_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject path validation that decodes attacker input only once."""
    if not is_code_mode or not steps or _final_answer_requests_user_help(final_answer):
        return None
    affected: set[str] = set()
    for step in steps:
        if not _is_code_write_step(step):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        tool_name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if not isinstance(path, str) or _is_test_path(path):
            continue
        new_text, old_text = _extract_step_payloads(step)
        new_hit = _payload_has_single_pass_url_decode(new_text)
        old_hit = _payload_has_single_pass_url_decode(old_text)
        introduces_boundary_defect = new_hit and _payload_looks_like_path_boundary(new_text)
        full_clean_rewrite = (
            tool_name in {"write_text_file", "write_file", "create_file"}
            and path in affected
            and bool(new_text)
            and not new_hit
        )
        surgical_repair = path in affected and old_hit and not new_hit
        if full_clean_rewrite or surgical_repair:
            affected.discard(path)
        elif introduces_boundary_defect or (path in affected and new_hit):
            affected.add(path)
    if not affected:
        return None
    preview = ", ".join(sorted(affected)[:3])
    return (
        "Cannot finish yet: path-boundary validation in "
        f"{preview} URL-decodes attacker input only once. A payload such as "
        "`%252e%252e%252fsecret` remains `%2e%2e%2fsecret` after that pass and can bypass "
        "the canonical containment check. Decode repeatedly to a stable value with a bounded "
        "round/size guard (or otherwise reject residual encoded separators/traversal), normalize "
        "separators, then resolve and prove containment. Add a focused double-encoded traversal "
        "regression that requires the public boundary exception."
    )


def _code_semantic_followup_guard(
    steps: list[ReActStep],
    *,
    is_code_mode: bool,
) -> str | None:
    """Surface deterministic source defects before another verifier cycle."""
    path_message = _path_boundary_decode_guard(
        steps,
        "implementation complete",
        is_code_mode=is_code_mode,
    )
    if path_message is not None:
        return path_message.replace("Cannot finish yet: ", "Before verification: ", 1)
    return _concurrency_semantic_followup_guard(steps, is_code_mode=is_code_mode)
