"""PHASE 6b — LLM call + Final-Answer anchor streaming for the ReAct loop.

Extracted from ``react_loop.py`` (Wave 2 of the split documented in
``docs/design/react-loop-split-plan.md``). Builds the per-iteration
``ModelRequest``, streams the model response with a deadline, surfaces
Thought prose / the post-anchor Final Answer as
live deltas, and handles soft timeouts, cancellation, failover, retry,
and budget auto-pause bookkeeping.

Depends only on react_* leaf modules and platform layers; never imports
react_loop.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from collections.abc import Callable, Generator
from typing import Any

from runtime.core.cerebrum.react_context import (
    _compress_context,
    _estimate_messages_tokens,
    context_budget_tokens_for_model,
)
from runtime.core.cerebrum.react_final_answer_guards import (
    _final_answer_needs_pre_emit_guard,
    _looks_like_observation_echo,
)
from runtime.core.cerebrum.react_loop_controls import _emit_assistant_chunk
from runtime.core.cerebrum.react_loop_state import _LoopControl, _LoopState
from runtime.core.cerebrum.react_model_deadlines import (
    _MODEL_STREAM_DEADLINE,
    _iter_model_stream_with_deadline,
    _reasoning_only_watchdog_s,
    _stage_model_timeout_s,
)
from runtime.core.cerebrum.react_parsing import (
    _ACTION_RE,
    _FINAL_RE,
    _THOUGHT_RE,
    _has_react_protocol_stream_prefix,
    _looks_like_protocol_leak,
    _looks_like_special_tool_envelope,
    extract_streamable_thought,
)
from runtime.core.cerebrum.react_types import _safe_react_error_message
from runtime.platform.models.llm import (
    LLMResponseFormatError,
    Message,
    ModelRequest,
    thinking_budget_for_effort,
)
from runtime.platform.models.rescue_policy import is_retryable_model_error

_logger = logging.getLogger(__name__)

# Plain, unanchored prose is ambiguous while the provider is still streaming:
# it can be a normal chat answer, or the beginning of a leaked ReAct/system
# transcript whose markers arrive a few chunks later. Buffer a modest prefix
# before exposing it. This keeps ordinary long answers progressive while
# giving the protocol-echo detector enough text to make a safe decision.
_ZERO_ANCHOR_STREAM_GATE_CHARS = 24

_REACT_STREAM_LEADERS = (
    "thought:",
    "action:",
    "observation:",
    "(real tool execution succeeded)",
)


def _is_context_limit_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".casefold()
    return any(
        marker in text
        for marker in (
            "context_length_exceeded",
            "maximum context length",
            "context window",
            "too many tokens",
            "input is too long",
            "上下文超过",
        )
    )


def _stream_answer_body(text: str) -> str:
    """Return the visible answer lane from a growing provider text buffer."""

    final_match = _FINAL_RE.search(text or "")
    return final_match.group(1) if final_match else (text or "")


_PUBLIC_UPDATE_LINE_RE = re.compile(r"^\s*(?:Update|Progress)\s*:", re.IGNORECASE)


def _strip_public_update_paragraphs(text: str) -> str:
    """Remove ``Update:``/``Progress:`` checkpoint paragraphs from the answer lane.

    In the ReAct protocol these paragraphs are public progress checkpoints that
    PHASE 6d surfaces as commentary (``commentary_delta``), never as answer
    prose. When the model writes them inside plain zero-anchor text the
    ``Final Answer:`` anchor is absent, so ``_stream_answer_body`` would
    otherwise leak the whole checkpoint into the visible answer message and the
    same sentence would appear twice (answer tail + commentary). Stripping here
    keeps the answer lane clean while the parser still recovers the checkpoint
    text for the commentary channel.

    A checkpoint paragraph starts at a line whose first token is
    ``Update:``/``Progress:`` and runs until the next blank line or end of
    text. The paragraph's trailing blank line is dropped too so the answer
    never ends up with a doubled separator.
    """
    if not text:
        return text
    out: list[str] = []
    skipping = False
    for line in text.split("\n"):
        stripped = line.lstrip()
        if skipping:
            if not stripped:
                # Blank line terminates the paragraph; swallow it so the
                # separator that preceded the checkpoint is the only one kept.
                skipping = False
            continue
        if _PUBLIC_UPDATE_LINE_RE.match(line):
            skipping = True
            continue
        out.append(line)
    return "\n".join(out)


def _stream_answer_lane(text: str) -> str:
    """Return the visible answer lane, stripping public checkpoints.

    Anchored text (has a ``Final Answer:``/``<final_answer>`` marker) is
    terminal prose and is returned as-is. Zero-anchor prose additionally
    strips ``Update:``/``Progress:`` paragraphs so the parser's checkpoint
    (surfaced by PHASE 6d as commentary) does not also leak into the answer.
    """
    body = _stream_answer_body(text)
    if _FINAL_RE.search(text or ""):
        return body
    return _strip_public_update_paragraphs(body)


def _safe_stream_end(text: str) -> int:
    """Return the exclusive end that is safe to expose for this delta.

    Only a possible protocol-leader suffix is held back.  Ordinary prose is
    still released token-by-token, so this protection does not turn short
    answers back into a single post-response dump.
    """

    if not text:
        return 0
    # Protocol markers are short. Inspect only the tail so both ``\nAct`` and
    # an already completed ``Action: echo`` leader remain private until the
    # strict parser can see the complete call. No word-boundary assumption is
    # needed; holding a rare prose suffix such as "act" for one chunk is safer
    # than exposing half a control token.
    tail_start = max(0, len(text) - 64)
    folded = text.casefold()

    def _at_protocol_boundary(index: int) -> bool:
        return index == 0 or text[index - 1].isspace() or text[index - 1] in "。.!！?？:：;；"

    for leader in _REACT_STREAM_LEADERS:
        marker_at = folded.rfind(leader, tail_start)
        if marker_at != -1 and _at_protocol_boundary(marker_at):
            return marker_at
        for prefix_len in range(1, len(leader)):
            prefix = leader[:prefix_len]
            if folded.endswith(prefix):
                prefix_at = len(text) - prefix_len
                if _at_protocol_boundary(prefix_at):
                    return prefix_at
    return len(text)


def _stream_has_protocol(text: str) -> bool:
    """Strict completed-marker check for an in-flight answer lane."""

    return _has_react_protocol_stream_prefix(text) or _looks_like_protocol_leak(text)


def _ambient_subagent_session_id() -> str:
    """Return the sub-agent session id scoped by the bridge's worker thread.

    Lazily imports the ambient ContextVar so the core stream never hard-depends
    on the sub-agent runtime; unset (parent turn / one-shot child) is ``""`` and
    the usage row simply goes unattributed.
    """
    try:
        from runtime.execution.subagents._ambient import current_subagent_session_id

        return current_subagent_session_id()
    except Exception:  # noqa: BLE001 - optional attribution, never breaks streaming
        return ""


def _phase_6b_model_stream(
    state: _LoopState,
    *,
    i: int,
    model_iteration_timeout_s: Callable[[float | None], float],
    model_iteration_timeout_s_config: float | None = None,
    try_react_model_failover: Callable[[str], str | None],
) -> Generator[dict, None, _LoopControl]:
    """PHASE 6b · per-iteration model call + live anchor streaming.

    Moved verbatim from ``react_loop.py``. Returns ``NEXT_ITERATION``
    for the failover / transient-retry outer-loop continues,
    ``RETURN_NONE`` when the first iteration fails with no steps,
    ``BREAK`` for a terminal model error (``state.terminated_reason`` is
    then ``"error"``), and ``CONTINUE`` on success (``state`` carries
    ``resp`` / ``raw_text`` / ``request_has_tool_evidence`` /
    ``iteration_soft_timed_out`` / ``maybe_emit_throughput`` for the
    later phases). ``_model_iteration_timeout_s`` is injected because
    tests patch it on ``react_loop``; ``_try_react_model_failover`` is
    injected because the closure bumps ``_model_failovers`` and reads
    ``_native_mode`` through react_loop nonlocals (and its
    ``next_custom_model_fallback`` lookup must stay patchable on
    react_loop).
    """
    # Injected callables under their original names.
    _model_iteration_timeout_s = model_iteration_timeout_s
    _try_react_model_failover = try_react_model_failover
    # Reference-typed aliases — mutations propagate to the main loop.
    intent = state.intent
    steps = state.steps
    executed_beak_steps = state.executed_beak_steps
    messages = state.messages
    router = state.router
    stack = state.stack
    react_task_id = state.react_task_id
    thread_id = state.thread_id
    _pause = state.pause_controller
    # Turn-level cfg (assembled once, read-only here).
    temperature = state.temperature
    _max_tokens_per_iter = state.max_tokens_per_iter
    _wants_thinking = state.wants_thinking
    _reasoning_effort = state.reasoning_effort
    _native_evidence_update_tool_specs = state.native_evidence_update_tool_specs
    _native_public_update_tool_specs = state.native_public_update_tool_specs
    _budget_auto_pause_enabled = state.budget_auto_pause_enabled
    _budget_pause_threshold = state.budget_pause_threshold
    _budget_config = getattr(getattr(stack, "config", None), "budget", None)
    _user_context = getattr(intent, "user_context", None) or {}
    _cumulative_token_auto_pause_enabled = bool(
        _user_context.get("cumulative_token_auto_pause")
        or getattr(intent, "flags", {}).get("cumulative_token_auto_pause", False)
        or getattr(_budget_config, "cumulative_token_auto_pause", False)
    )
    _agent_id_for_pause = state.agent_id_for_pause
    _throughput_started_at = state.throughput_started_at
    _throughput_interval_s = state.throughput_interval_s
    _is_code_mode = state.is_code_mode
    _browser_operation_mode = state.browser_operation_mode
    # Scalar mailbox — pulled in, pushed back in the finally below.
    effective_model = state.effective_model
    _native_mode = state.native_mode
    _evidence_convergence_active = state.evidence_convergence_active
    _force_convergence_next = state.force_convergence_next
    _terminal_convergence_active = state.terminal_convergence_active
    _last_public_update_key = state.last_public_update_key
    _throughput_chars = state.throughput_chars
    _final_stream_started = state.final_stream_started
    _streamed_final_chars = state.streamed_final_chars
    _final_delta_emitted_this_iteration = state.final_delta_emitted_this_iteration
    terminated_reason = state.terminated_reason
    consecutive_llm_errors = state.consecutive_llm_errors
    _model_failovers = state.model_failovers
    _zero_action_rounds = state.zero_action_rounds
    resp = None
    raw_text = ""
    _request_has_tool_evidence = False
    _iteration_soft_timed_out = False
    try:
        try:
            _iteration_recovery_mode = _force_convergence_next
            _force_convergence_next = False
            _request_has_tool_evidence = bool(
                executed_beak_steps
                or any(
                    prior_step.action_results or (prior_step.action and prior_step.observation)
                    for prior_step in steps
                )
            )
            req = ModelRequest(
                model=effective_model,
                messages=list(messages),
                max_tokens=(
                    min(_max_tokens_per_iter, 4000)
                    if _iteration_recovery_mode
                    else _max_tokens_per_iter
                ),
                temperature=temperature,
                enable_thinking=_wants_thinking and not _iteration_recovery_mode,
                reasoning_effort=("low" if _iteration_recovery_mode else _reasoning_effort),
                thinking_budget=(
                    1024
                    if _iteration_recovery_mode
                    else thinking_budget_for_effort(
                        _reasoning_effort,
                        _max_tokens_per_iter,
                    )
                ),
                tools=(
                    (
                        _native_evidence_update_tool_specs
                        if _request_has_tool_evidence
                        else _native_public_update_tool_specs
                    )
                    if (
                        _native_mode
                        and _evidence_convergence_active is None
                        and not _iteration_recovery_mode
                        and not _terminal_convergence_active
                    )
                    else []
                ),
                # Action-deficit forcing. A model that answered the previous
                # round with prose only — no tool call — will usually do it
                # again: prompt-level reminders are advice, and the observed
                # failure mode is the model narrating "I'll check X next"
                # for several consecutive rounds while executing nothing
                # (trn_c2fbddce247b4164 / trn_2f015724ea194bfd: zero tool
                # calls each, terminated by the guard impasse). Constraining
                # the decode instead makes prose-only physically unavailable
                # for one round. Never applied while converging: the closing
                # round is *supposed* to be prose.
                require_tool_use=(
                    _native_mode
                    and _evidence_convergence_active is None
                    and not _iteration_recovery_mode
                    and not _terminal_convergence_active
                    and _zero_action_rounds > 0
                ),
            )
            text_parts: list[str] = []
            thinking_parts: list[str] = []
            resp = None
            # Once we detect the ``Final Answer:`` anchor in the streaming
            # text we switch to live token streaming so short tasks see
            # first-byte latency closer to the LLM's TTFT instead of full
            # response time. Pre-anchor chunks must stay buffered because
            # they may contain Thought:/Action: prose that must not leak.
            _final_stream_started = False
            _visible_stream_state = {"chars": 0}
            _streamed_final_chars = 0
            _final_stream_guarded = False
            _final_delta_emitted_this_iteration = False
            # Incremental Thought-streaming state: while the Final Answer
            # is still buffered, the Thought prose already decodes token
            # by token — surface it into the thinking block so tool-heavy
            # turns show signs of life long before the terminal answer.
            _thought_stream_cursor = 0
            _thought_stream_open = False
            _iteration_soft_timed_out = False
            _base_iteration_timeout = _model_iteration_timeout_s(model_iteration_timeout_s_config)
            _has_tool_evidence = _request_has_tool_evidence
            _reasoning_watchdog_s = _reasoning_only_watchdog_s(
                has_tool_evidence=_has_tool_evidence,
                recovery=_iteration_recovery_mode,
            )
            _reasoning_started_at = time.monotonic()
            if _iteration_recovery_mode and _evidence_convergence_active is not None:
                _iteration_timeout = _stage_model_timeout_s(
                    _base_iteration_timeout, "evidence_synthesis"
                )
            elif _iteration_recovery_mode:
                _iteration_timeout = _stage_model_timeout_s(_base_iteration_timeout, "recovery")
            elif _has_tool_evidence:
                _iteration_timeout = _stage_model_timeout_s(_base_iteration_timeout, "post_tool")
            else:
                _iteration_timeout = _base_iteration_timeout

            def _maybe_emit_throughput(chars: int) -> dict[str, Any] | None:
                # The cadence cell lives on ``state`` (not a ``nonlocal``)
                # so PHASE 6c's calls through this closure update the same
                # value the next iteration syncs in.
                _now = time.monotonic()
                if _now - state.throughput_last_emit < _throughput_interval_s:
                    return None
                _elapsed = _now - _throughput_started_at
                state.throughput_last_emit = _now
                return {
                    "type": "throughput",
                    "chars": chars,
                    "elapsed_ms": int(_elapsed * 1000),
                    "chars_per_sec": (chars / _elapsed if _elapsed > 0 else 0.0),
                }

            state.maybe_emit_throughput = _maybe_emit_throughput

            def _visible_started(state: dict[str, Any] = _visible_stream_state) -> Any:
                return state["chars"]

            for evt in _iter_model_stream_with_deadline(
                router,
                req,
                _iteration_timeout,
                _visible_started,
                # Normal rounds: any streamed thinking token is liveness, so a
                # deep-reasoning model is never judged slow while it is still
                # emitting. Evidence-convergence rounds keep the strict
                # visible-text-only liveness so a tools-disabled provider that
                # streams reasoning forever while emitting phantom actions is
                # still bounded by the deadline.
                any_activity_counts=_evidence_convergence_active is None,
            ):
                if evt is _MODEL_STREAM_DEADLINE:
                    _iteration_soft_timed_out = True
                    _logger.warning(
                        "react_loop iter %d model stream exceeded %.1fs before "
                        "a visible final answer; switching to convergence mode",
                        i + 1,
                        _iteration_timeout,
                    )
                    break
                # Check cancellation between SSE chunks so the
                # interrupt button can break us out of a slow /
                # hung upstream without waiting for the read timeout.
                # ``current_cancellation_token`` is a contextvar set
                # by the gateway's interrupt watcher when the user
                # clicks 停止.
                _ct_inner = None
                try:
                    from runtime.safety.approval.cancellation import (
                        current_cancellation_token,
                    )

                    _ct_inner = current_cancellation_token()
                except (ImportError, AttributeError, TypeError, UnboundLocalError):  # noqa: BLE001 — cancellation subsystem unavailable; mid-stream cancel check skipped
                    pass
                if _ct_inner is not None and _ct_inner.is_cancelled:
                    # A provider may have streamed answer-like prose before
                    # its terminal done/tool-call envelope. Cancellation is
                    # atomic: discard that pending lane and let PHASE 7 emit
                    # the explicit cancellation outcome.
                    terminated_reason = "cancelled"
                    return _LoopControl.BREAK
                if evt.type == "text_delta":
                    text_parts.append(evt.delta)
                    # A native provider may stream polished-looking answer prose and
                    # only reveal its structured tool calls in the terminal ``done``
                    # event. Hold the complete answer lane atomically whenever this
                    # request advertised native tools. PHASE 6c publishes it after
                    # ``done`` only when the final response has no tool calls.
                    # Thinking, structured commentary, and tool lifecycle events keep
                    # their existing live paths.
                    if _native_mode and bool(req.tools):
                        continue
                    joined = "".join(text_parts)
                    if _final_stream_started:
                        # Already past the anchor.  Re-evaluate the complete
                        # answer lane before releasing more text: providers can
                        # begin a ReAct Action several deltas after ordinary
                        # prose, and the marker itself may straddle chunks.
                        if evt.delta:
                            joined = "".join(text_parts)
                            answer_so_far = _stream_answer_lane(joined)
                            if (
                                _stream_has_protocol(answer_so_far)
                                or _looks_like_observation_echo(answer_so_far)
                                or (
                                    not _final_stream_guarded
                                    and _final_answer_needs_pre_emit_guard(
                                        answer_so_far,
                                        is_code_mode=_is_code_mode,
                                        browser_operation_mode=_browser_operation_mode,
                                    )
                                )
                            ):
                                _final_stream_guarded = True
                                _final_stream_started = False
                                continue
                            safe_end = _safe_stream_end(answer_so_far)
                            if safe_end > _streamed_final_chars:
                                delta_out = answer_so_far[_streamed_final_chars:safe_end]
                                _emit_assistant_chunk(
                                    stack,
                                    iteration=i + 1,
                                    delta=delta_out,
                                    task_id=react_task_id,
                                )
                                yield {
                                    "type": "text_delta",
                                    "delta": delta_out,
                                    "iteration": i + 1,
                                }
                                _final_delta_emitted_this_iteration = True
                                _streamed_final_chars = safe_end
                                _visible_stream_state["chars"] = safe_end
                                _throughput_chars += len(delta_out)
                                _tp = _maybe_emit_throughput(_throughput_chars)
                                if _tp is not None:
                                    yield _tp
                    else:
                        # Look for the Final Answer anchor in the joined
                        # buffer. Once it appears we can flush the
                        # post-anchor portion and switch to live mode for
                        # the rest of the stream — this is what makes
                        # short tasks feel responsive instead of
                        # blocking on full response decode.
                        joined = "".join(text_parts)
                        m = _FINAL_RE.search(joined)
                        # TTFT: while the answer is still anchored out,
                        # stream the Thought prose into the thinking
                        # block. Extraction spans only Thought→terminator
                        # inside the PRE-ANCHOR region (a "Thought:" quoted
                        # inside the answer body must not echo into the
                        # reasoning surface); skipped when the provider
                        # already streams native thinking (the two would
                        # duplicate in the reasoning surface).
                        if not thinking_parts:
                            _xml_final_at = joined.lower().find("<final_answer")
                            _thought_region_end = m.start() if m else len(joined)
                            if _xml_final_at != -1:
                                _thought_region_end = min(_thought_region_end, _xml_final_at)
                            (
                                _thought_delta,
                                _thought_stream_cursor,
                                _thought_stream_open,
                            ) = extract_streamable_thought(
                                joined[:_thought_region_end],
                                _thought_stream_cursor,
                                _thought_stream_open,
                            )
                            if _thought_delta:
                                _emit_assistant_chunk(
                                    stack,
                                    iteration=i + 1,
                                    delta=_thought_delta,
                                    task_id=react_task_id,
                                    kind="reasoning-delta",
                                )
                                yield {
                                    "type": "thinking_delta",
                                    "delta": _thought_delta,
                                    "iteration": i + 1,
                                }
                                _throughput_chars += len(_thought_delta)
                                _tp = _maybe_emit_throughput(_throughput_chars)
                                if _tp is not None:
                                    yield _tp
                        if m and m.group(1).strip():
                            answer_so_far = m.group(1)
                            # Don't pre-stream when the answer body
                            # contains tool-call leaders. The parser will
                            # later reclassify these as Actions and
                            # suppress them from the visible answer; if
                            # we leak them now the user sees raw XML/JSON
                            # before the real tool fires.
                            if (
                                "<tool_call>" in answer_so_far
                                or "<tool_invocation" in answer_so_far
                                or "<function=" in answer_so_far
                                or "<seed:tool_call" in answer_so_far.lower()
                                or _looks_like_special_tool_envelope(answer_so_far)
                                or _looks_like_observation_echo(answer_so_far)
                                or _stream_has_protocol(answer_so_far)
                                or "```" in answer_so_far
                            ):
                                # Keep buffering; the post-loop emitter
                                # will decide what (if anything) is
                                # safe to surface. A leaked ReAct
                                # ``Action:`` block inside the Final Answer
                                # body is scrubbed by ``_parse_step`` before
                                # delivery, so never pre-stream the raw markup.
                                pass
                            elif answer_so_far:
                                if (
                                    _evidence_convergence_active is not None
                                    or _final_answer_needs_pre_emit_guard(
                                        answer_so_far,
                                        is_code_mode=_is_code_mode,
                                        browser_operation_mode=_browser_operation_mode,
                                    )
                                ):
                                    _final_stream_guarded = True
                                    continue
                                safe_end = _safe_stream_end(answer_so_far)
                                if safe_end:
                                    delta_out = answer_so_far[:safe_end]
                                    _emit_assistant_chunk(
                                        stack,
                                        iteration=i + 1,
                                        delta=delta_out,
                                        task_id=react_task_id,
                                    )
                                    yield {
                                        "type": "text_delta",
                                        "delta": delta_out,
                                        "iteration": i + 1,
                                    }
                                    _final_delta_emitted_this_iteration = True
                                    _streamed_final_chars = safe_end
                                    _throughput_chars += len(delta_out)
                                    _tp = _maybe_emit_throughput(_throughput_chars)
                                    if _tp is not None:
                                        yield _tp
                                _final_stream_started = True
                                _visible_stream_state["chars"] = _streamed_final_chars
                        elif (
                            len(joined) >= _ZERO_ANCHOR_STREAM_GATE_CHARS
                            and not _THOUGHT_RE.match(joined.lstrip())
                            and not _ACTION_RE.match(joined.lstrip())
                            and not _looks_like_observation_echo(joined)
                            and not _stream_has_protocol(joined)
                            and not joined.lstrip().startswith(
                                (
                                    "<tool_call>",
                                    "<tool_invocation",
                                    "<function=",
                                    "<seed:tool_call",
                                    "<final_answer",
                                )
                            )
                            and not _looks_like_special_tool_envelope(joined[:100])
                        ):
                            # Zero-anchor chat-style answer: model is
                            # writing plain markdown (no Thought/Action/
                            # Final Answer markers). Without this branch
                            # the salvage path at end of iteration emits
                            # all 700+ chars at once after a wasted
                            # second LLM round (zero-anchor needs 2
                            # consecutive rounds to bail). With it, the
                            # user sees text streaming the moment it's
                            # clear ReAct format isn't coming.
                            if (
                                _evidence_convergence_active is not None
                                or _final_answer_needs_pre_emit_guard(
                                    joined,
                                    is_code_mode=_is_code_mode,
                                    browser_operation_mode=_browser_operation_mode,
                                )
                            ):
                                _final_stream_guarded = True
                                continue
                            # Emit only the NEWLY arrived portion so the
                            # frontend typewriter has real deltas to play —
                            # yielding the whole joined buffer here would
                            # dump the 24+ chars accumulated so far in one
                            # frame, defeating the streaming UX. The lane is
                            # the raw zero-anchor prose minus any
                            # ``Update:``/``Progress:`` checkpoints (PHASE 6d
                            # surfaces those as commentary, not answer text).
                            answer_so_far = _stream_answer_lane(joined)
                            safe_end = _safe_stream_end(answer_so_far)
                            if safe_end > _streamed_final_chars:
                                delta_out = answer_so_far[_streamed_final_chars:safe_end]
                                _emit_assistant_chunk(
                                    stack,
                                    iteration=i + 1,
                                    delta=delta_out,
                                    task_id=react_task_id,
                                )
                                yield {
                                    "type": "text_delta",
                                    "delta": delta_out,
                                    "iteration": i + 1,
                                }
                                _final_delta_emitted_this_iteration = True
                                _streamed_final_chars = safe_end
                                _throughput_chars += len(delta_out)
                                _tp = _maybe_emit_throughput(_throughput_chars)
                                if _tp is not None:
                                    yield _tp
                            _final_stream_started = True
                            _visible_stream_state["chars"] = _streamed_final_chars
                elif evt.type == "thinking_delta":
                    if (
                        _reasoning_watchdog_s is not None
                        and time.monotonic() - _reasoning_started_at >= _reasoning_watchdog_s
                    ):
                        _iteration_soft_timed_out = True
                        _logger.warning(
                            "react_loop iter %d stalled in private reasoning for %.1fs; "
                            "switching to convergence",
                            i + 1,
                            _reasoning_watchdog_s,
                        )
                        break
                    thinking_parts.append(evt.delta)
                    _emit_assistant_chunk(
                        stack,
                        iteration=i + 1,
                        delta=evt.delta or "",
                        task_id=react_task_id,
                        kind="reasoning-delta",
                    )
                    yield {
                        "type": "thinking_delta",
                        "delta": evt.delta,
                        "iteration": i + 1,
                    }
                    _throughput_chars += len(evt.delta or "")
                    _tp = _maybe_emit_throughput(_throughput_chars)
                    if _tp is not None:
                        yield _tp
                elif evt.type == "done":
                    # Release the held suffix only after the provider has
                    # completed the response and the whole candidate is known
                    # to be protocol-free.  If a late Action appeared, leave
                    # the suffix private; PHASE 6c will execute the parsed call
                    # and obtain a fresh, clean final answer on the next round.
                    if _final_stream_started and not _final_stream_guarded:
                        joined = "".join(text_parts)
                        answer_so_far = _stream_answer_lane(joined)
                        if (
                            _stream_has_protocol(answer_so_far)
                            or _looks_like_observation_echo(answer_so_far)
                            or _final_answer_needs_pre_emit_guard(
                                answer_so_far,
                                is_code_mode=_is_code_mode,
                                browser_operation_mode=_browser_operation_mode,
                            )
                        ):
                            _final_stream_guarded = True
                            _final_stream_started = False
                        elif len(answer_so_far) > _streamed_final_chars:
                            delta_out = answer_so_far[_streamed_final_chars:]
                            _emit_assistant_chunk(
                                stack,
                                iteration=i + 1,
                                delta=delta_out,
                                task_id=react_task_id,
                            )
                            yield {
                                "type": "text_delta",
                                "delta": delta_out,
                                "iteration": i + 1,
                            }
                            _final_delta_emitted_this_iteration = True
                            _streamed_final_chars = len(answer_so_far)
                            _visible_stream_state["chars"] = _streamed_final_chars
                            _throughput_chars += len(delta_out)
                            _tp = _maybe_emit_throughput(_throughput_chars)
                            if _tp is not None:
                                yield _tp
                    resp = evt.final
            if resp is None:
                # The provider iterator may end immediately after arranging
                # cancellation, leaving no next event on which the in-loop
                # check can run. Re-check before synthesizing a response from
                # pending prose; an EOF without ``done`` is not permission to
                # publish a cancelled answer lane.
                try:
                    from runtime.safety.approval.cancellation import (
                        current_cancellation_token,
                    )

                    _ct_after_stream = current_cancellation_token()
                except (ImportError, AttributeError, TypeError, UnboundLocalError):  # noqa: BLE001
                    _ct_after_stream = None
                if _ct_after_stream is not None and _ct_after_stream.is_cancelled:
                    terminated_reason = "cancelled"
                    return _LoopControl.BREAK
                if _native_mode and bool(req.tools):
                    # Native providers are allowed to stream answer-looking
                    # prose before revealing structured tool calls in the
                    # terminal envelope.  An EOF without ``done``, or a
                    # ``done`` event without its final response, is a
                    # protocol/transport failure, never evidence that the
                    # buffered prose is the final answer. Phrase this as an
                    # upstream EOF/reset so the existing model failover and
                    # transient-recovery policy can retry safely: no answer
                    # delta from this request has been exposed.
                    raise LLMResponseFormatError(
                        "terminal done event was missing or lacked its final response "
                        "(connection reset / protocol EOF)"
                    )
                from runtime.platform.models.llm import ModelResponse

                resp = ModelResponse(
                    text="".join(text_parts),
                    thinking="".join(thinking_parts),
                    model=effective_model,
                )
        except Exception as exc:
            _logger.warning(
                "react_loop iter %d LLM 调用失败 (%s): %s",
                i,
                type(exc).__name__,
                _safe_react_error_message(exc),
            )
            _error_text_was_exposed = bool(
                locals().get("_final_stream_started", False)
                or locals().get("_streamed_final_chars", 0)
            )
            if (
                not _error_text_was_exposed
                and _is_context_limit_error(exc)
                and consecutive_llm_errors < 2
            ):
                # Repeating the identical oversized request cannot recover.
                # Compact the in-memory conversation immediately, retain the
                # deterministic code continuation, and resume in this turn.
                _before_tokens = _estimate_messages_tokens(messages)
                _capacity_tokens = context_budget_tokens_for_model(effective_model)
                _target_tokens = max(
                    8_000,
                    min(int(_before_tokens * 0.60), int(_capacity_tokens * 0.30)),
                )
                _compacted = _compress_context(
                    messages,
                    max_tokens=_target_tokens,
                    router=router,
                    model=effective_model,
                    is_code_mode=_is_code_mode,
                )
                messages[:] = _compacted
                consecutive_llm_errors += 1
                messages.append(
                    Message(
                        role="user",
                        content=(
                            "[SYSTEM CHECK - context recovery]\n"
                            "The provider rejected the previous request for context size. "
                            "The conversation has been compacted while preserving successful "
                            "tool results and workspace state. Continue from the next unfinished "
                            "action; do not repeat completed writes."
                        ),
                    )
                )
                yield {
                    "type": "commentary_delta",
                    "delta": "模型上下文已接近上限，已保留工作状态并压缩后继续。",
                    "progress_source": "runtime",
                    "iteration": i + 1,
                }
                yield {
                    "type": "react_retry",
                    "kind": "context_compaction",
                    "iteration": i + 1,
                    "attempt": consecutive_llm_errors,
                }
                return _LoopControl.NEXT_ITERATION
            if not _error_text_was_exposed and is_retryable_model_error(exc):
                _fallback_model = _try_react_model_failover(type(exc).__name__)
                # The injected wrapper bumped the counter through the
                # react_loop closure; refresh the local mirror.
                _model_failovers = state.model_failovers
                if _fallback_model:
                    messages.append(
                        Message(
                            role="user",
                            content=(
                                "[SYSTEM CHECK - model failover]\n"
                                "The previous provider failed before exposing an answer. "
                                "Every prior tool result and message remains authoritative. "
                                "Continue from the exact unfinished point without repeating "
                                "successful reads, writes, or verification."
                            ),
                        )
                    )
                    yield {
                        "type": "commentary_delta",
                        "delta": "当前模型响应异常，已保留上下文并切换备用模型继续。",
                        "progress_source": "runtime",
                        "iteration": i + 1,
                    }
                    yield {
                        "type": "react_retry",
                        "kind": "model_failover",
                        "model": _fallback_model,
                        "iteration": i + 1,
                        "attempt": _model_failovers,
                    }
                    _force_convergence_next = bool(steps)
                    return _LoopControl.NEXT_ITERATION
            if not steps:
                _err_msg = _safe_react_error_message(exc)
                _err_kind = (
                    "auth" if "current_actor" in _err_msg or "登录" in _err_msg else "router"
                )
                yield {
                    "type": "react_error",
                    "kind": _err_kind,
                    "message": _err_msg,
                    "iteration": i,
                    "task_id": str(react_task_id) if react_task_id else None,
                }
                _pause.unregister_active(str(react_task_id))
                return _LoopControl.RETURN_NONE
            _error_message = str(exc).lower()
            _auth_failure = any(
                marker in _error_message
                for marker in (
                    "unauthorized",
                    "authentication",
                    "invalid api key",
                    "current_actor",
                    "登录",
                )
            )
            if not _error_text_was_exposed and not _auth_failure and consecutive_llm_errors < 2:
                consecutive_llm_errors += 1
                messages.append(
                    Message(
                        role="user",
                        content=(
                            "[SYSTEM CHECK - transient model-call recovery]\n"
                            "The previous model call failed before producing a "
                            f"user-visible answer ({type(exc).__name__}). Keep every "
                            "successful tool result already recorded, inspect current "
                            "workspace state when needed, and continue from the next "
                            "unfinished todo. Do not repeat successful writes or claim "
                            "the task is complete."
                        ),
                    )
                )
                yield {
                    "type": "react_retry",
                    "kind": "model_call",
                    "iteration": i + 1,
                    "attempt": consecutive_llm_errors,
                }
                return _LoopControl.NEXT_ITERATION
            terminated_reason = "error"
            return _LoopControl.BREAK

        consecutive_llm_errors = 0
        raw_text = "".join(text_parts)
        try:
            _in_tok = int(getattr(resp, "input_tokens", 0) or 0)
            _out_tok = int(getattr(resp, "output_tokens", 0) or 0)
            _cache_read_tok = int(getattr(resp, "cache_read_tokens", 0) or 0)
            _tok = _in_tok + _out_tok
            _cost_obj = getattr(resp, "cost", None)
            _cost = float(getattr(_cost_obj, "usd", 0) or 0) if _cost_obj else 0.0
            _journal = getattr(stack, "journal", None)
            if _journal is not None and hasattr(_journal, "write_token_usage"):
                with contextlib.suppress(Exception):
                    _journal.write_token_usage(
                        task_id=str(react_task_id),
                        session_id=_ambient_subagent_session_id(),
                        iteration=i + 1,
                        input_tokens=_in_tok,
                        output_tokens=_out_tok,
                        cost_usd=_cost,
                        model=str(getattr(resp, "model", "") or ""),
                    )
            # Feed the process-level cost ledger so ECHO_MAX_COST_USD can
            # gate further subagent spawns in bridge.py.
            if _in_tok or _out_tok:
                with contextlib.suppress(Exception):
                    from runtime.platform.budget import UsagePricing

                    UsagePricing.get().record(
                        str(getattr(resp, "model", "") or "unknown"),
                        _in_tok,
                        _out_tok,
                    )
            _updated = _pause.update_active_usage(
                str(react_task_id),
                tokens_delta=_tok,
                input_tokens_delta=_in_tok,
                output_tokens_delta=_out_tok,
                cache_read_tokens_delta=_cache_read_tok,
                # Provider input usage is the strongest available measure of
                # the live request footprint because it includes tool schemas
                # and other provider-visible prompt material that the local
                # message estimator cannot see.
                current_context_tokens=_in_tok,
                context_capacity_tokens=context_budget_tokens_for_model(
                    str(getattr(resp, "model", "") or effective_model or "")
                ),
                cost_delta=_cost,
            )
            # Three distinct quantities are intentionally kept separate:
            # current request context, cumulative token accounting, and hard
            # monetary spend. Re-sent prompt tokens make the cumulative token
            # counter grow much faster than the live context window, so it is
            # warn-only unless strict legacy accounting is explicitly enabled.
            if react_task_id is not None and _updated is not None:
                _token_pct = (
                    _updated.tokens_spent / _updated.max_tokens if _updated.max_tokens > 0 else 0
                )
                _usd_pct = _updated.cost_usd / _updated.max_usd if _updated.max_usd > 0 else 0
                _context_pct = (
                    _updated.current_context_tokens / _updated.context_capacity_tokens
                    if _updated.context_capacity_tokens > 0
                    else 0.0
                )
                _token_pressure = _token_pct >= _budget_pause_threshold
                _cost_pressure = _usd_pct >= _budget_pause_threshold
                _strict_token_pause = (
                    _budget_auto_pause_enabled
                    and _cumulative_token_auto_pause_enabled
                    and _token_pressure
                )
                _hard_cost_pause = _budget_auto_pause_enabled and _cost_pressure
                if _token_pressure or _cost_pressure:
                    _logger.warning(
                        "react_loop accounting budget above threshold · task %s · "
                        "context %d/%d (%.0f%%) · cumulative tokens %d/%d (%.0f%%) · "
                        "usd %.3f/%.3f (%.0f%%) · %s",
                        react_task_id,
                        _updated.current_context_tokens,
                        _updated.context_capacity_tokens,
                        _context_pct * 100,
                        _updated.tokens_spent,
                        _updated.max_tokens,
                        _token_pct * 100,
                        _updated.cost_usd,
                        _updated.max_usd,
                        _usd_pct * 100,
                        (
                            "auto-pausing on cost"
                            if _hard_cost_pause
                            else "auto-pausing on cumulative tokens"
                            if _strict_token_pause
                            else "warning only"
                        ),
                    )
                    if (_hard_cost_pause or _strict_token_pause) and not _pause.is_pause_requested(
                        str(react_task_id)
                    ):
                        _limit_label = "成本预算" if _hard_cost_pause else "累计处理量"
                        _pause.request_pause(
                            task_id=str(react_task_id),
                            reason="budget_near_limit",
                            requested_by="system",
                            note=(
                                f"自动暂停 · {_limit_label}临界 · 当前上下文 "
                                f"{_updated.current_context_tokens:,}/"
                                f"{_updated.context_capacity_tokens:,} · 累计 tokens "
                                f"{_updated.tokens_spent:,}/"
                                f"{_updated.max_tokens:,} "
                                f"({int(_token_pct * 100)}%) · "
                                f"${_updated.cost_usd:.3f}/"
                                f"${_updated.max_usd:.3f} "
                                f"({int(_usd_pct * 100)}%) · 加预算继续"
                            ),
                            thread_id=thread_id or "",
                            agent_id=_agent_id_for_pause,
                        )
        except (AttributeError, TypeError):
            _logger.debug("budget check failed", exc_info=True)
        return _LoopControl.CONTINUE
    finally:
        state.force_convergence_next = _force_convergence_next
        state.last_public_update_key = _last_public_update_key
        state.throughput_chars = _throughput_chars
        state.final_stream_started = _final_stream_started
        state.streamed_final_chars = _streamed_final_chars
        state.final_delta_emitted_this_iteration = _final_delta_emitted_this_iteration
        state.terminated_reason = terminated_reason
        state.consecutive_llm_errors = consecutive_llm_errors
        state.model_failovers = _model_failovers
        state.resp = resp
        state.raw_text = raw_text
        state.request_has_tool_evidence = _request_has_tool_evidence
        state.iteration_soft_timed_out = _iteration_soft_timed_out
