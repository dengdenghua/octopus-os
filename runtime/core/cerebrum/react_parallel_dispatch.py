"""Concurrent multi-action dispatcher for the ReAct loop (口子 2).

Moved from ``react_loop.py``: ``_dispatch_parallel_actions`` executes a
multi-action block — threaded when safe, force-serial when any action
is a write tool / unregistered / risky-or-untrusted — while emitting the
same ``tool_start`` / ``tool_end`` event pairs the single-action path
yields, and fencing untrusted tool output against prompt injection.
"""

from __future__ import annotations

import contextvars
import logging
import time
import uuid
from collections.abc import Callable, Generator
from typing import Any, Literal

from runtime.core.cerebrum.react_execution import (
    _beak_step_effective_success,
    _execute_action_via_beak,
    _tool_event_extras_from_beak_step,
)
from runtime.core.cerebrum.react_execution_receipts import (
    _execution_effect_receipt,
    _execution_receipt_trust,
)
from runtime.core.cerebrum.react_parsing import _parse_action, _summarize_observation
from runtime.execution.tool_engine import (
    normalize_tool_lifecycle_event,
    tool_lifecycle_event_to_react_event,
)
from runtime.platform.models import ParsedIntent
from runtime.safety.validation.prompt_injection import (
    is_untrusted_tool,
    mark_injection_taint,
    scan_for_injection,
    wrap_untrusted_observation,
)

_logger = logging.getLogger(__name__)

# Tools that mutate the workspace. When a multi-action block contains
# any of these we force serial dispatch — concurrent file writes can
# clobber each other and the auto-diagnostics path expects a single
# resolved_name.
_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "write_text_file",
        "edit_file",
        "multi_edit_file",
        "edit_text_file",
        "edit_code",
        "str_replace",
        "write_file",
        "create_file",
    }
)

# Default cap on parallel actions. Beyond this we still execute every
# call but slice them into pool-sized batches; protects against a
# model hallucinating 30 read_files at once.
_MAX_PARALLEL_ACTIONS = 4


# Audit T-07: wall-clock ceiling for a parallel tool batch. A single hung
# tool must not pin the turn forever; beyond this the whole batch is
# drained (completed lanes keep their results, the rest are timed out).
_DEFAULT_PARALLEL_BATCH_TIMEOUT_S = 600.0
_PARALLEL_CANCEL_POLL_S = 0.05
_ParallelCollectOutcome = Literal["timeout", "cancelled"]


def _absorb_lane_result(
    fut: Any,
    idx: int,
    observations: list[str | None],
    beak_steps: list[Any],
) -> None:
    try:
        obs, bk = fut.result()
    except Exception as exc:  # noqa: BLE001 — surface any worker exception as a tool error observation
        obs, bk = (
            f"(工具执行异常) {type(exc).__name__}: {exc}",
            None,
        )
    observations[idx] = obs
    beak_steps[idx] = bk


def _collect_parallel_lane_results(
    futures: dict[Any, int],
    observations: list[str | None],
    beak_steps: list[Any],
    *,
    timeout_s: float,
    on_timeout: Callable[[], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    cancellation_reason: Callable[[], str] | None = None,
) -> _ParallelCollectOutcome | None:
    """Drain a parallel batch under a wall-clock ceiling (audit T-07).

    ``timeout_s <= 0`` waits indefinitely (old behaviour). On timeout,
    completed lanes keep their results; ``on_timeout`` is invoked before
    pending futures are cancelled so cooperative handlers observe the batch
    cancellation signal first.  Lanes still running are marked with an
    explicit timeout observation.  An independently cancelled parent/batch
    is polled while futures are pending and returns immediately with those
    lanes marked cancelled rather than waiting for the deadline.  The caller
    owns executor shutdown policy.
    """
    import concurrent.futures as _cf

    if (timeout_s is None or timeout_s <= 0) and is_cancelled is None:
        for fut in _cf.as_completed(futures):
            _absorb_lane_result(fut, futures[fut], observations, beak_steps)
        return None

    deadline = time.monotonic() + timeout_s if timeout_s is not None and timeout_s > 0 else None
    pending = set(futures)
    while pending:
        if is_cancelled is not None and is_cancelled():
            reason = cancellation_reason() if cancellation_reason is not None else "cancelled"
            for fut, idx in futures.items():
                if observations[idx] is not None:
                    continue
                if fut.done():
                    # Preserve work that completed before cancellation was
                    # observed; only still-pending lanes are cancelled.
                    _absorb_lane_result(fut, idx, observations, beak_steps)
                else:
                    fut.cancel()
                    observations[idx] = f"(工具执行已取消 · {reason or 'cancelled'})"
                    beak_steps[idx] = None
            return "cancelled"

        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            if on_timeout is not None:
                on_timeout()
            for fut, idx in futures.items():
                if observations[idx] is not None:
                    continue
                if fut.done():
                    # Completed between the deadline and this sweep — keep its result.
                    _absorb_lane_result(fut, idx, observations, beak_steps)
                else:
                    fut.cancel()
                    observations[idx] = f"(工具执行超时 · 超过并行批级上限 {timeout_s:g}s)"
                    beak_steps[idx] = None
            return "timeout"

        wait_s = remaining
        if is_cancelled is not None:
            wait_s = (
                _PARALLEL_CANCEL_POLL_S if wait_s is None else min(wait_s, _PARALLEL_CANCEL_POLL_S)
            )
        done, pending = _cf.wait(
            pending,
            timeout=wait_s,
            return_when=_cf.FIRST_COMPLETED,
        )
        for fut in done:
            _absorb_lane_result(fut, futures[fut], observations, beak_steps)
    return None


def _consume_late_lane_result(
    future: Any,
    *,
    lane_index: int,
    outcome: _ParallelCollectOutcome,
) -> None:
    """Observe a detached worker's terminal exception.

    Python cannot safely kill a running thread.  The dispatcher therefore
    returns after its deadline while a non-cooperative *read-only/low-risk*
    lane unwinds in the background after a timeout or parent cancellation.
    Reading ``future.exception()`` prevents a late failure from becoming an
    unobserved Future exception; failures are logged because their public
    ``tool_end`` has already been emitted and cannot be amended safely.
    """
    import concurrent.futures as _cf

    if future.cancelled():
        return
    try:
        exception = future.exception()
    except _cf.CancelledError:
        return
    except BaseException:  # noqa: BLE001 - callback must never escape its worker
        _logger.warning(
            "parallel tool lane %d could not expose its late result",
            lane_index + 1,
            exc_info=True,
        )
        return
    if exception is not None:
        _logger.warning(
            "parallel tool lane %d failed after its batch detached (%s): %s",
            lane_index + 1,
            outcome,
            exception,
            exc_info=(type(exception), exception, exception.__traceback__),
        )


def _dispatch_parallel_actions(
    actions: list[str],
    *,
    stack: Any,
    executor: Any,
    iteration: int,
    react_task_id: Any,
    agent: Any,
    intent: ParsedIntent,
    beak_step_sink: list[Any] | None = None,
    parallel_batch_timeout_s: float = _DEFAULT_PARALLEL_BATCH_TIMEOUT_S,
) -> Generator[Any, None, tuple[str, list[dict[str, object]]]]:
    """Concurrent multi-action dispatcher (口子 2).

    Generator helper invoked via ``yield from`` from the main loop.
    Yields the same ``tool_start`` / ``tool_end`` events the legacy
    single-action path emits, one pair per action, with unique
    ``call_id`` per call. Returns ``(merged_observation, results)``
    via StopIteration.value.

    Force-serial fallbacks (executes via the same path but sequenced
    rather than threaded):
      * Any action targets a known write tool.
      * Any action's parsed name is unregistered (so we surface a
        "tool not found" observation immediately rather than after
        partial work has run).
    """
    import concurrent.futures as _cf

    # ReAct multi-actions use a ThreadPoolExecutor for independent reads.
    # ContextVars do not cross that boundary, so capture the concrete Session
    # now and rebind it inside every worker. Without this, list_cwd may resolve
    # against the selected workspace while adjacent read_file calls resolve
    # against the server repository, producing the deceptive "listed but not
    # found" failure seen in production behavioral runs.
    from runtime.platform.process.session import current_session

    parent_session = current_session()
    if parent_session is not None:
        parent_session.metadata.setdefault("_read_file_paths_this_turn", [])

    parsed_pairs: list[tuple[str, dict[str, Any]] | None] = [_parse_action(a) for a in actions]
    from runtime.safety.approval.approval_gate import assess_approval_risk

    resolved_names: list[str | None] = []
    has_unregistered = False
    has_write_tool = False
    # Risky/untrusted tools must run serially (inline, in this thread) so
    # the injection-taint contextvar the executor reads/writes is visible —
    # the parallel thread-pool path doesn't propagate it. Running them
    # inline also lets an untrusted tool's taint apply to a later risky tool
    # in the same batch via the executor's chokepoint block.
    has_risky_or_untrusted = False
    # Per-action flag: does this tool's OUTPUT taint the turn (untrusted
    # source)? Used to order the serial batch so untrusted tools run before
    # risky ones (see below).
    untrusted_flags: list[bool] = []
    # Per-action capability-disabled info: when a tool is recognized by the
    # catalog but its group is excluded by ``enable_web_skills=False``, we
    # populate this dict so (a) the model gets an actionable observation
    # explaining *why* the tool is unavailable and (b) the ``tool_end``
    # event carries the info for the UI to render a one-click enable prompt.
    disabled_infos: list[dict[str, str] | None] = []
    try:
        from runtime.execution.all_skills import is_known_but_disabled_tool
    except ImportError:  # pragma: no cover — defensive

        def is_known_but_disabled_tool(name: str) -> tuple[bool, str | None]:
            return (False, None)

    for p in parsed_pairs:
        if p is None:
            resolved_names.append(None)
            untrusted_flags.append(False)
            disabled_infos.append(None)
            has_unregistered = True
            continue
        name = p[0]
        registry = getattr(executor, "registry", None)
        if registry is None or not registry.has(name):
            resolved_names.append(None)
            untrusted_flags.append(False)
            has_unregistered = True
            # Distinguish "known but config-disabled" from "completely unknown"
            # so the model and UI get actionable context instead of a generic
            # "unregistered" message that invites 7 retries.
            _hit, _group = is_known_but_disabled_tool(name)
            disabled_infos.append(
                {"group": _group, "config_flag": "enable_web_skills"} if _hit and _group else None
            )
        else:
            resolved_names.append(name)
            disabled_infos.append(None)
            try:
                _aff = registry.get(name).affinity
            except (KeyError, AttributeError):
                _aff = None
            _is_untrusted = is_untrusted_tool(name, _aff)
            untrusted_flags.append(_is_untrusted)
            if name in _WRITE_TOOLS:
                has_write_tool = True
            if assess_approval_risk(name).level in {"medium", "high", "critical"} or _is_untrusted:
                has_risky_or_untrusted = True

    # Pre-allocate per-action call_ids so tool_start/tool_end can be
    # paired even if work runs out-of-order.
    call_ids = [uuid.uuid4().hex[:12] for _ in actions]
    started_at = [time.monotonic() for _ in actions]

    # Emit tool_start for every action up-front so the UI shows them
    # in parallel even if we end up running serially below.
    for idx in range(len(actions)):
        parsed_pair = parsed_pairs[idx]
        name = resolved_names[idx] or (parsed_pair[0] if parsed_pair else "unknown")
        _input_preview = parsed_pair[1] if parsed_pair else None
        yield tool_lifecycle_event_to_react_event(
            normalize_tool_lifecycle_event(
                "tool_start",
                {
                    "tool_name": name,
                    "tool_call_id": call_ids[idx],
                    "iteration": iteration,
                    "input_preview": _input_preview,
                    "parallel_batch_size": len(actions),
                },
                origin="react_compat",
            )
        )

    serial = has_write_tool or has_unregistered or has_risky_or_untrusted

    def _run_one(idx: int) -> tuple[str | None, Any]:
        # Skip dispatch for unregistered tools — the single-action
        # path's "(tool not registered)" message is reproduced here
        # so the model gets a uniform observation. When the tool is
        # recognized as config-disabled (e.g. web_search under
        # enable_web_skills=False), augment the message with the reason
        # and the remediation path so the model stops retrying and can
        # inform the user.
        if resolved_names[idx] is None:
            _di = disabled_infos[idx]
            if _di is not None:
                _parsed_pair = parsed_pairs[idx]
                _tool_name = _parsed_pair[0] if _parsed_pair else "unknown"
                return (
                    f"(工具未注册) {_tool_name} 所属组 '{_di['group']}' 被配置关闭"
                    f"({_di['config_flag']}=false)。如需启用:在 config.local.yaml "
                    f"设置 {_di['config_flag']}: true 并重启后端,或调用 "
                    f"POST /api/capabilities/enable 临时启用。当前请改用其他工具"
                    f"或告知用户该能力不可用。",
                    None,
                )
            return (
                f"(工具未注册或无法解析) action: {actions[idx][:200]}",
                None,
            )
        if parent_session is None:
            return _execute_action_via_beak(
                stack,
                actions[idx],
                react_task_id=react_task_id,
                react_step_counter=iteration,
                agent=agent,
                intent=intent,
            )
        from runtime.platform.process.session import _current_session

        session_token = _current_session.set(parent_session)
        try:
            return _execute_action_via_beak(
                stack,
                actions[idx],
                react_task_id=react_task_id,
                react_step_counter=iteration,
                agent=agent,
                intent=intent,
            )
        finally:
            _current_session.reset(session_token)

    observations: list[str | None] = [None] * len(actions)
    beak_steps: list[Any] = [None] * len(actions)
    if serial or len(actions) <= 1:
        # Run untrusted-output tools FIRST. The serial path exists so an
        # untrusted tool's injection taint reaches a later risky tool's
        # executor chokepoint — but in DECLARATION order the model can place a
        # risky tool (exec_shell) BEFORE the untrusted one (web_fetch), so the
        # risky tool runs while taint is still "none". Reorder execution so
        # taint is set first. Results stay indexed by original position, so the
        # tool_end emit order + merged observation below are unchanged. (Stable
        # sort preserves declared order within each group.)
        exec_order = sorted(
            range(len(actions)),
            key=lambda j: 0 if (j < len(untrusted_flags) and untrusted_flags[j]) else 1,
        )
        for idx in exec_order:
            obs, bk = _run_one(idx)
            observations[idx] = obs
            beak_steps[idx] = bk
    else:
        max_workers = min(len(actions), _MAX_PARALLEL_ACTIONS)
        # A raw ThreadPoolExecutor does NOT propagate contextvars to its
        # workers, so the ambient cancellation token (and any other
        # loop-scoped state) would be invisible to parallel tool calls -
        # the single-action path checks cancellation after each tool, the
        # parallel path never saw it. Give each worker its own copy of the
        # dispatcher's context (a Context cannot be entered concurrently,
        # hence one copy per task) so cancellation reaches every lane.
        from runtime.safety.approval.cancellation import (
            CancellationSource,
            current_cancellation_token,
            scoped_cancellation,
        )

        _batch_source = CancellationSource()
        _parent_token = current_cancellation_token()

        def _cancel_from_parent(reason: str) -> None:
            _batch_source.cancel(reason=reason or "parent turn cancelled")

        _unlink_parent = _parent_token.on_cancelled(_cancel_from_parent)

        def _cancel_for_timeout() -> None:
            _batch_source.cancel(
                reason=f"parallel tool batch exceeded {parallel_batch_timeout_s:g}s"
            )

        def _run_one_in_batch(idx: int) -> tuple[str | None, Any]:
            with scoped_cancellation(_batch_source.token):
                if _batch_source.is_cancelled:
                    return (
                        "(工具执行异常) OperationCancelled: "
                        f"{_batch_source.token.reason or 'parallel batch cancelled'}",
                        None,
                    )
                return _run_one(idx)

        _parent_context = contextvars.copy_context()
        pool = _cf.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="react-tool-parallel",
        )
        futures: dict[Any, int] = {}
        collection_outcome: _ParallelCollectOutcome | None = None
        try:
            futures = {
                pool.submit(_parent_context.copy().run, _run_one_in_batch, idx): idx
                for idx in range(len(actions))
            }
            collection_outcome = _collect_parallel_lane_results(
                futures,
                observations,
                beak_steps,
                timeout_s=parallel_batch_timeout_s,
                on_timeout=_cancel_for_timeout,
                is_cancelled=lambda: _batch_source.is_cancelled,
                cancellation_reason=lambda: _batch_source.token.reason,
            )
            if collection_outcome is not None:
                # ``Future.cancel`` cannot stop a lane whose thread already
                # started.  Observe every such Future when it eventually
                # exits so a late exception is not silently discarded.
                for future, lane_index in futures.items():
                    if str(observations[lane_index] or "").startswith(
                        ("(工具执行超时", "(工具执行已取消")
                    ):
                        future.add_done_callback(
                            lambda completed, idx=lane_index, outcome=collection_outcome: (
                                _consume_late_lane_result(
                                    completed,
                                    lane_index=idx,
                                    outcome=outcome,
                                )
                            )
                        )
        except BaseException:
            # Generator cancellation / an unexpected collector failure must
            # not reintroduce the same implicit wait we avoid on timeout.
            _batch_source.cancel(reason="parallel tool batch aborted")
            for future in futures:
                future.cancel()
            for future, lane_index in futures.items():
                future.add_done_callback(
                    lambda completed, idx=lane_index: _consume_late_lane_result(
                        completed,
                        lane_index=idx,
                        outcome="cancelled",
                    )
                )
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            # Never use ThreadPoolExecutor as a context manager here: its
            # __exit__ always waits for running threads, defeating the batch
            # deadline.  Only timeout/abort may detach, and only this branch
            # is reachable for the pre-screened low-risk parallel lanes.
            pool.shutdown(wait=collection_outcome is None, cancel_futures=True)
        finally:
            _unlink_parent()

    # Emit tool_end events in declared (action) order so the UI
    # transcript matches the model's intent.
    results: list[dict[str, object]] = []
    merged_lines: list[str] = []
    n = len(actions)
    for idx in range(n):
        obs = observations[idx]
        bk = beak_steps[idx]
        if bk is not None and beak_step_sink is not None:
            # The main loop uses this ordered ledger to decide whether an
            # earlier tool failure was later recovered.  Parallel executions
            # used to disappear from that ledger, so a successful fallback
            # batch could still leave a complete answer marked as failed.
            beak_step_sink.append(bk)
        _parsed_pair = parsed_pairs[idx]
        name = resolved_names[idx] or (_parsed_pair[0] if _parsed_pair else "unknown")
        _ok = not (
            obs is not None
            and isinstance(obs, str)
            and obs.startswith(
                (
                    "(工具失败)",
                    "(工具执行异常)",
                    "(工具执行超时",
                    "(工具执行已取消",
                    "(工具未注册",
                )
            )
        )
        if bk is not None:
            _ok = _beak_step_effective_success(bk)
        _duration_ms = int((time.monotonic() - started_at[idx]) * 1000)
        # Indirect prompt-injection defense: a tool whose output is
        # external (web/browser/MCP) is attacker-influenceable. Fence its
        # observation as DATA-not-instructions before it re-enters the
        # model's context, and flag known injection markers. The UI
        # preview keeps the raw text; only the model-facing copy is
        # wrapped. Failed-tool observations are error strings, not
        # untrusted content, so they're left alone.
        model_obs = obs
        if _ok and isinstance(obs, str) and obs:
            _reg = getattr(executor, "registry", None)
            _affinity: list[str] | None = None
            if _reg is not None and resolved_names[idx] and _reg.has(name):
                try:
                    _affinity = _reg.get(name).affinity
                except (KeyError, AttributeError):
                    _affinity = None
            if is_untrusted_tool(name, _affinity):
                _scan = scan_for_injection(obs)
                model_obs = wrap_untrusted_observation(
                    obs,
                    source=name,
                    scan=_scan,
                )
                if _scan.flagged:
                    # Taint the turn so a later high-risk tool is forced
                    # through human approval (read at the approval gate).
                    mark_injection_taint(_scan.severity)
                    _logger.warning(
                        "prompt-injection markers in %s output (severity=%s, signals=%s)",
                        name,
                        _scan.severity,
                        ",".join(_scan.labels),
                    )
        _end_payload = {
            "tool_name": name,
            "tool_call_id": call_ids[idx],
            "iteration": iteration,
            "status": "success" if _ok else "error",
            "output_preview": (
                _summarize_observation(obs) if isinstance(obs, str) and obs else obs
            ),
            "duration_ms": _duration_ms,
            "parallel_batch_size": n,
            **_tool_event_extras_from_beak_step(bk, name),
        }
        # Attach capability-disabled metadata so the UI can render a
        # one-click "enable web_search" prompt instead of a bare error.
        # Routed through ``extras`` by ``normalize_tool_lifecycle_event``.
        if disabled_infos[idx] is not None:
            _end_payload["capability_disabled"] = disabled_infos[idx]
        yield tool_lifecycle_event_to_react_event(
            normalize_tool_lifecycle_event(
                "tool_end",
                _end_payload,
                origin="react_compat",
            )
        )
        _trusted_execution, _execution_source = _execution_receipt_trust(bk)
        results.append(
            {
                "tool_name": name,
                "ok": _ok,
                "observation": model_obs or "",
                "duration_ms": _duration_ms,
                "call_id": call_ids[idx],
                "trusted_execution": _trusted_execution,
                "execution_source": _execution_source,
                "effect_receipt": _execution_effect_receipt(bk),
            }
        )
        # Per-call header keeps the model from confusing which
        # observation belongs to which action.
        merged_lines.append(f"[{idx + 1}/{n} {name}]\n{model_obs or '(no output)'}")

    merged_obs = "\n\n".join(merged_lines)
    return merged_obs, results
