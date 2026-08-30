"""Operator controls + run-budget knobs for the ReAct loop.

Moved from ``react_loop.py``: guard-hit telemetry, the per-guard
operator kill-switch (env var + settings.yaml union, with audit
logging), context-window pressure estimation, long-task budget limits,
and the A/B recipe splitter that assigns loop variants per task.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import threading
from collections.abc import Callable, Generator
from typing import Any

from runtime.core.cerebrum.react_context import (
    _estimate_messages_tokens,
    _serialize_messages_for_checkpoint,
    context_budget_tokens_for_model,
)
from runtime.core.cerebrum.react_types import _DEFAULT_REACT_RECIPES, ReActRecipe
from runtime.safety.experiments.variant import ABSplitter

_logger = logging.getLogger(__name__)

# ── Guard telemetry (P1 evolution-loop feed) ──────────────────────
# Lazily-initialised singleton sink. evaluate_guards() calls the
# returned recorder with (label, category) for every firing guard.
# Disabled by env var ECHO_DISABLE_GUARD_TELEMETRY=1 so tests and
# air-gapped runs can opt out. Initialisation failures degrade to a
# no-op — telemetry must never break the loop.
_GUARD_TELEMETRY_SINGLETON: Any = None
_GUARD_TELEMETRY_INIT_DONE = False
_GUARD_TELEMETRY_SEEN: set[tuple[str, str, str]] = set()
_GUARD_TELEMETRY_SEEN_LOCK = threading.Lock()
_GUARD_TELEMETRY_SEEN_LIMIT = 4096


def _emit_assistant_chunk(
    stack: Any,
    *,
    iteration: int,
    delta: str,
    task_id: Any = None,
    kind: str = "text-delta",
) -> None:
    """Best-effort mirror of one loop chunk event as ``assistant/chunk``.

    The react loop streams the final answer through ``text_delta``
    events and private reasoning through ``thinking_delta`` events;
    this journals each fragment so the streamed lanes are
    reconstructable from the log alone (dsh session-log invariant,
    ``assistant/chunk`` with dsh ``StreamChunk`` kinds). No journal
    on ``stack`` → no-op; write failures are swallowed — telemetry
    loss never breaks the loop.
    """
    if not delta:
        return
    journal = getattr(stack, "journal", None)
    if journal is None:
        return
    with contextlib.suppress(Exception):
        journal.write_assistant_chunk(
            iteration=iteration,
            delta=delta,
            task_id=task_id,
            kind=kind,
        )


def _guard_hit_recorder(
    *,
    dedupe_key: str = "",
    goal: str = "",
    iteration: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[str, str, str], None] | None:
    """Return a ``recorder(label, category, message)`` callable, or None when
    telemetry is disabled / unavailable."""
    global _GUARD_TELEMETRY_SINGLETON, _GUARD_TELEMETRY_INIT_DONE
    import os

    if os.environ.get("ECHO_DISABLE_GUARD_TELEMETRY") == "1":
        return None
    if not _GUARD_TELEMETRY_INIT_DONE:
        _GUARD_TELEMETRY_INIT_DONE = True
        try:
            from runtime.safety.evolution.guard_telemetry import GuardTelemetry

            _GUARD_TELEMETRY_SINGLETON = GuardTelemetry()
        except Exception as _exc:  # noqa: BLE001 — telemetry must not break loop
            _logger.debug("guard telemetry unavailable: %s", _exc)
            _GUARD_TELEMETRY_SINGLETON = None
    sink = _GUARD_TELEMETRY_SINGLETON
    if sink is None:
        return None
    goal_digest = hashlib.sha256(goal.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def _record_once(label: str, category: str, message: str) -> None:
        if dedupe_key:
            hit_key = (dedupe_key, label, category)
            with _GUARD_TELEMETRY_SEEN_LOCK:
                if hit_key in _GUARD_TELEMETRY_SEEN:
                    return
                if len(_GUARD_TELEMETRY_SEEN) >= _GUARD_TELEMETRY_SEEN_LIMIT:
                    _GUARD_TELEMETRY_SEEN.clear()
                _GUARD_TELEMETRY_SEEN.add(hit_key)
        # Merge message into metadata for telemetry enrichment
        enriched_metadata = dict(metadata) if metadata else {}
        enriched_metadata["message"] = message
        sink.record(
            label,
            category,
            goal_digest=goal_digest,
            iteration=iteration,
            metadata=enriched_metadata,
        )

    return _record_once


def _reset_guard_telemetry_for_tests() -> None:
    """Reset the telemetry singleton — used by tests for isolation."""
    global _GUARD_TELEMETRY_SINGLETON, _GUARD_TELEMETRY_INIT_DONE
    _GUARD_TELEMETRY_SINGLETON = None
    _GUARD_TELEMETRY_INIT_DONE = False
    with _GUARD_TELEMETRY_SEEN_LOCK:
        _GUARD_TELEMETRY_SEEN.clear()


# ── Operator kill-switch for individual guards ────────────────────
# Two-layer source — env var is the emergency knob, settings.yaml is
# the persistent project-level baseline.
#
# Env var: ECHO_DISABLED_GUARDS="label1,label2"
# YAML:    safety:
#            disabled_guards:
#              - label1
#              - label2
#
# Both sources are MERGED (union) — env var adds to whatever YAML
# already disables, never replaces. Operators can flip env at runtime
# to add to the persistent list without editing the file.
#
# Whitespace around labels is stripped so an env var like
# 'magic-number guard, long-function guard' works.
# Re-read fresh on each call so an operator changing the env or
# YAML at runtime takes effect on the next turn.
#
# Audit trail: when the disabled set CHANGES we emit one log line and
# (when telemetry is wired) one structured record so a future operator
# can answer "when did this guard get turned off and by whom".

_LAST_DISABLED_SET: frozenset[str] | None = None
_DEFAULT_SETTINGS_PATHS: tuple[str, ...] = (
    "config.local.yaml",
    "config.yaml",
    "config.example.yaml",
)


def _disabled_guards_from_yaml(
    candidate_paths: tuple[str, ...] = _DEFAULT_SETTINGS_PATHS,
) -> frozenset[str]:
    """Read ``safety.disabled_guards`` from the first existing config.

    Returns frozenset on success; empty frozenset on any failure
    (file missing / unreadable / no PyYAML / wrong shape). Never
    raises — settings being broken must not break the loop.
    """
    import os

    for raw_path in candidate_paths:
        try:
            if not os.path.exists(raw_path):
                continue
        except Exception:  # noqa: BLE001
            continue
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            return frozenset()
        try:
            with open(raw_path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh.read()) or {}
        except Exception:  # noqa: BLE001
            return frozenset()
        if not isinstance(data, dict):
            return frozenset()
        safety = data.get("safety") or {}
        if not isinstance(safety, dict):
            return frozenset()
        # Source A: safety.disabled_guards: [label, label, ...]
        out: set[str] = set()
        raw = safety.get("disabled_guards") or []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str) and item.strip():
                    out.add(item.strip())
        # Source B: safety.guard_overrides: {label: bool}
        # Per-spec on/off knob — operators can selectively re-enable
        # guards that the project baseline disabled, or vice versa.
        # Only the "False" entries contribute to the disabled set;
        # explicit "True" wins over a same-label disabled_guards entry.
        overrides = safety.get("guard_overrides") or {}
        if isinstance(overrides, dict):
            for label, enabled in overrides.items():
                if not isinstance(label, str) or not label.strip():
                    continue
                clean = label.strip()
                if isinstance(enabled, bool):
                    if enabled:
                        out.discard(clean)
                    else:
                        out.add(clean)
        return frozenset(out)
    return frozenset()


def _disabled_guard_labels() -> frozenset[str]:
    """Return labels of guards disabled via env var OR settings.yaml.

    Sources are unioned: env-var entries add to the YAML baseline.
    """
    import os

    raw = os.environ.get("ECHO_DISABLED_GUARDS", "")
    if not raw.strip():
        env_set: frozenset[str] = frozenset()
    else:
        env_set = frozenset(part.strip() for part in raw.split(",") if part.strip())
    yaml_set = _disabled_guards_from_yaml()
    current = env_set | yaml_set
    _audit_disabled_set_change(current)
    return current


def _audit_disabled_set_change(current: frozenset[str]) -> None:
    """Log + record telemetry when the disabled-guard set changes.

    Idempotent: only fires when ``current`` differs from the last
    observed value. The very first call after process start ALSO
    fires when the set is non-empty so a fresh process inheriting
    ECHO_DISABLED_GUARDS leaves a trail.
    """
    global _LAST_DISABLED_SET
    if current == _LAST_DISABLED_SET:
        return
    previous = _LAST_DISABLED_SET
    _LAST_DISABLED_SET = current
    if previous is None and not current:
        # Process start with empty set — nothing notable to record.
        return
    added = sorted(current - (previous or frozenset()))
    removed = sorted((previous or frozenset()) - current)
    _logger.warning(
        "ECHO_DISABLED_GUARDS changed: now=%s added=%s removed=%s",
        sorted(current),
        added,
        removed,
    )
    sink = _GUARD_TELEMETRY_SINGLETON
    if sink is None:
        return
    with contextlib.suppress(Exception):
        sink.record(
            label="__kill_switch_change__",
            category="audit",
            metadata={
                "now": sorted(current),
                "added": added,
                "removed": removed,
            },
        )


def _reset_disabled_set_for_tests() -> None:
    """Reset the cached last-seen set — used by tests for isolation."""
    global _LAST_DISABLED_SET
    _LAST_DISABLED_SET = None


def _estimate_context_fullness(messages: list, model: str | None) -> float:
    """Rough fraction of the model's context budget consumed by ``messages``.

    Uses the same approximate token counter and model-name-keyed budget as
    context compression. Returned value is clamped to ``[0.0, 1.0]``.
    """
    try:
        used_tokens = _estimate_messages_tokens(messages)
    except (TypeError, AttributeError):
        used_tokens = 0

    budget = context_budget_tokens_for_model(model)

    if budget <= 0:
        return 0.0
    ratio = used_tokens / budget
    if ratio < 0.0:
        return 0.0
    if ratio > 1.0:
        return 1.0
    return ratio


_CONTEXT_PRESSURE_NUDGE = (
    "[context-pressure] (level={level})\n"
    "You are approaching the context window. Before this turn ends:\n"
    "1. Update todo_write so every in-flight item shows accurate status.\n"
    "2. Record a compact continuation note in the internal trajectory only;\n"
    "   never copy this note into the user-facing answer. Include the next\n"
    "   concrete action and any evidence needed after compaction.\n"
    "This message survives compaction; raw step history may not."
)


def _long_task_budget_limits(
    *,
    is_research_mode: bool,
    is_swarm_mode: bool,
    is_code_mode: bool = False,
    max_tokens_budget: int,
    max_usd_budget: float,
) -> tuple[int, float, float]:
    """Return accounting limits and pause threshold for this ReAct turn.

    Capability-enhancing (not limiting): research / swarm / complex code
    tasks get expanded ceilings so long tasks are not cut off mid-synthesis.
    """
    # 复杂代码/研究/多 Agent 任务自动扩容预算，避免长任务被预算硬切断。
    if is_swarm_mode:
        base_tokens, base_usd = 250_000, 5.0
    elif is_research_mode or is_code_mode:
        base_tokens, base_usd = 150_000, 3.0
    else:
        base_tokens, base_usd = max_tokens_budget, max_usd_budget
    return (
        max(max_tokens_budget, base_tokens),
        max(max_usd_budget, base_usd),
        0.95 if (is_swarm_mode or is_research_mode or is_code_mode) else 0.8,
    )


_REACT_SPLITTER: ABSplitter | None = None


def _build_default_splitter() -> ABSplitter:
    from runtime.safety.experiments.variant import ABSplitter, Variant

    return ABSplitter(
        [Variant(name=r.name, payload=r, weight=1.0) for r in _DEFAULT_REACT_RECIPES],
        seed=42,
    )


def _get_splitter() -> ABSplitter:
    global _REACT_SPLITTER
    if _REACT_SPLITTER is None:
        _REACT_SPLITTER = _build_default_splitter()
    return _REACT_SPLITTER


def pick_react_variant(
    *,
    task_id: str | None = None,
) -> ReActRecipe:
    splitter = _get_splitter()
    v = splitter.next_variant() if task_id is None else splitter.assign_for(task_id)
    return v.payload  # type: ignore[return-value]


def record_react_variant_result(variant_name: str, *, success: bool) -> None:
    splitter = _get_splitter()
    with contextlib.suppress(KeyError):
        splitter.record_outcome(variant_name, success=success)


def get_react_variant_stats() -> list[dict[str, Any]]:
    splitter = _get_splitter()
    out: list[dict[str, Any]] = []
    for name in splitter.names:
        s = splitter.stats[name]
        v = splitter.get(name)
        recipe: ReActRecipe = v.payload
        out.append(
            {
                "name": name,
                "max_iterations": recipe.max_iterations,
                "temperature": recipe.temperature,
                "assignments": s.assignments,
                "successes": s.successes,
                "failures": s.failures,
                "success_rate": round(s.success_rate, 3),
            }
        )
    return out


def _reset_react_variants_for_tests() -> None:
    global _REACT_SPLITTER
    _REACT_SPLITTER = None


# ── Cancel / pause guard (per-iteration, PHASE 6a) ────────────────
def _cancel_pause_guard(
    *,
    iteration: int,
    react_task_id: Any,
    max_iterations: int,
    stack: Any,
    messages: list,
    steps: list,
    working_set: dict,
    progress_summary: Any,
    current_phase: Any,
    pause_controller: Any,
    append_pending_live_steering: Callable[[], int],
) -> Generator[dict[str, Any], None, str | None]:
    """Per-iteration cancel/pause guard for the ReAct main loop.

    Yields the ``react_paused`` event when a pause request lands and
    returns the ``terminated_reason`` (``"cancelled"`` / ``"paused"``)
    when the loop must break, ``None`` otherwise.
    """
    # Cancellation check — runs before pause check so a tripped
    # token wins over an in-flight pause request. The ambient
    # token is set by the request handler (e.g. FastAPI's
    # disconnect watcher); when ``CancellationToken.none()`` is
    # active the call is essentially free (one bool read).
    try:
        from runtime.safety.approval.cancellation import current_cancellation_token

        _ct = current_cancellation_token()
        if _ct.is_cancelled:
            terminated_reason = "cancelled"
            _logger.info(
                "react_loop cancelled at iteration %d (task %s) — reason=%s",
                iteration,
                react_task_id,
                _ct.reason or "client disconnected",
            )
            return terminated_reason
    except (ImportError, AttributeError, TypeError, UnboundLocalError):  # noqa: BLE001 — cancellation subsystem unavailable; proceed normally
        pass

    # Wall-time limit check — runs before explicit pause requests so
    # a runaway task gets auto-paused even without a user action. Only
    # wall-time is auto-pausing here: token/cost budget overruns are the
    # elastic budget's concern (warn-only by default, pause only when the
    # user opts into ``budget_auto_pause``), so they must NOT hard-stop the
    # loop from this guard.
    task_id_str = str(react_task_id) if react_task_id else ""
    if task_id_str:
        exceeded, reason = pause_controller.check_active_task_limits(task_id_str)
        if exceeded and reason == "wall_time_limit":
            _logger.warning(
                "react_loop wall-time limit exceeded at iteration %d (task %s) — auto-pausing",
                iteration,
                react_task_id,
            )
            # Auto-request pause so the normal pause flow handles checkpoint/journal
            pause_controller.request_pause(
                task_id=task_id_str,
                reason="external",
                requested_by="system",
                note=f"wall-time limit exceeded ({reason})",
                thread_id=getattr(pause_controller.get_request(task_id_str), "thread_id", ""),
                agent_id=getattr(pause_controller.get_request(task_id_str), "agent_id", ""),
            )

    # User follow-ups are durable, high-priority inputs. Consume them at
    # the first model-safe boundary so the next response acknowledges the
    # user before the original task continues.
    append_pending_live_steering()

    if pause_controller.is_pause_requested(task_id_str):
        terminated_reason = "paused"
        req_meta = pause_controller.get_request(task_id_str)
        _logger.info(
            "react_loop paused at iteration %d (task %s) — checkpoint written",
            iteration,
            react_task_id,
        )
        journal = getattr(stack, "journal", None)
        if journal is not None:
            try:
                journal.write_react_checkpoint(
                    task_id=react_task_id,
                    iteration_completed=iteration,
                    max_iterations=max_iterations,
                    messages_snapshot=_serialize_messages_for_checkpoint(messages),
                    steps_snapshot=[
                        {
                            "iteration": s.iteration,
                            "thought": s.thought,
                            "public_update": s.public_update,
                            "action": s.action,
                            "actions": list(s.actions),
                            "observation": s.observation,
                            "action_results": [dict(result) for result in s.action_results],
                        }
                        for s in steps
                    ],
                    has_final_answer=False,
                    working_set_snapshot=list(working_set.values()),
                    progress_summary=progress_summary,
                    current_phase=current_phase,
                )
            except Exception:  # noqa: BLE001 - pausing must not fail on journal IO
                # Loud, not silent: without this checkpoint a later resume
                # silently falls back to a fresh run and everything done
                # since the last auto-checkpoint is lost. Surface it now so
                # the operator knows before clicking Continue.
                _logger.warning(
                    "react_loop pause checkpoint write FAILED (task %s, iter %d) "
                    "- resume will fall back to a fresh run: %s",
                    react_task_id,
                    iteration,
                    exc_info=True,
                )
            try:
                journal.write_task_paused(
                    task_id=task_id_str,
                    reason=req_meta.reason if req_meta else "external",
                    requested_by=req_meta.requested_by if req_meta else "",
                    iteration=iteration,
                )
            except (AttributeError, ImportError):
                _logger.debug("pause journal write failed", exc_info=True)
        pause_controller.mark_paused(task_id_str)
        pause_controller.unregister_active(task_id_str)
        yield {
            "type": "react_paused",
            "iteration": iteration,
            "task_id": task_id_str or None,
            "reason": req_meta.reason if req_meta else "external",
            "note": req_meta.note if req_meta else "",
        }
        return terminated_reason
    return None
