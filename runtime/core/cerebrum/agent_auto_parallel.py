"""Auto-decompose + run subtasks in parallel for the first ReAct turn.

When a user goal is complex enough to be split into >=2 independent
sub-inquiries, the runtime can fan them out across parallel sub-agents
before the model's first reasoning step, then inject the aggregated
results as a synthetic Observation. The main loop then synthesises a
final answer against real per-subtask evidence instead of re-reasoning
about how to decompose the work.

This is the "自动拆解并行" mechanism: it complements (and is mutually
exclusive with) single-agent ``auto-delegation``. Where auto-delegation
routes one prompt to one pinned agent, auto-parallel splits one goal
into several independent subtasks and resolves them concurrently.

Two layers, kept separate so the plan step never touches the heavy
orchestrator:

1. ``plan_auto_parallel`` — pure heuristic gate. Decides whether a goal
   carries a strong parallel signal (explicit enumeration, multiple
   questions, parallel wording) and, if so, produces a concrete set of
   subtasks. No orchestrator is created here.
2. ``run_auto_parallel`` — executes the plan through
   ``ParallelAgentOrchestrator.split/dispatch`` (wired to a real
   sub-agent runner) and waits for the batch to reach a terminal state,
   then aggregates the per-subtask outputs.

The parallel short-circuit is always a hint, not a hard override:
callers may skip it when other constraints (safety, mode, budget,
already-delegated) demand model orchestration.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from runtime.execution.parallel_agents import (
    DispatchTaskInput,
    ParallelAgentOrchestrator,
    SplitResult,
    SplitTask,
)

_log = logging.getLogger(__name__)

# A goal shorter than this is almost certainly a single prompt, not a
# decomposable workload — skip the extra machinery.
_MIN_GOAL_CHARS = 40
# Subtasks shorter than this are treated as fragments, not independent work.
_MIN_SUBTASK_CHARS = 6
# Default cap on subtasks produced by a single split.
_DEFAULT_MAX_SUBTASKS = 6
# Wall-clock budget for waiting on a dispatched batch.
_DEFAULT_BATCH_TIMEOUT_S = 300
# How often we poll the orchestrator for a terminal batch state.
_POLL_INTERVAL_S = 0.05
# Per-subtask wall-clock budget handed to call_subagent.
_SUBAGENT_TIMEOUT_S = 240

# Explicit parallel signals — when any is present the goal is a candidate
# for decomposition. Kept conservative to avoid over-splitting cohesive
# single-sentence asks.
_PARALLEL_KEYWORDS = re.compile(
    r"分别|同时|逐个|逐一|依次|并行|各自|独立|并行地|拆分|分解|"
    r"separately|in\s+parallel|independently|concurrently|simultaneously",
    re.I,
)
_MULTI_QUESTION = re.compile(r"\?|？")
_BULLET_PREFIX = re.compile(r"^\s*(?:[-\*•◦]|\d+[\.\)、]|[（(]\d+[）)．.]|[a-zA-Z][\.\)、])\s*")
_SENTENCE_DELIM = re.compile(r"[。！？;；\n]+")


@dataclass(frozen=True)
class AutoParallelPlan:
    """A resolved decomposition plan, or empty when no split fits."""

    subtasks: tuple[SplitTask, ...]
    reason: str

    def should_parallelize(self) -> bool:
        return len(self.subtasks) >= 2


def _clean_fragment(text: str) -> str:
    """Strip list markers and surrounding whitespace from a fragment."""
    frag = _BULLET_PREFIX.sub("", text).strip()
    return frag.strip(" \t,，;；。.、:")


def build_thread_memory_summary(
    user_context: dict[str, Any] | None,
    *,
    max_turns: int = 4,
    max_chars: int = 1200,
) -> str:
    """Best-effort cross-turn memory summary from the conversation history.

    The realtime gateway stamps ``intent.user_context["conversation_messages"]``
    with the thread's recent OpenAI-style messages (the same lane the model
    prompt reads). We project the last ``max_turns`` user requests and the
    assistant answer that followed each into a compact "what happened before"
    block, so parallel sub-agents don't re-research already-settled ground.

    Pure helper — never raises, never blocks. Empty history → empty string.
    """
    if not isinstance(user_context, dict):
        return ""
    messages = user_context.get("conversation_messages")
    if not isinstance(messages, list) or not messages:
        return ""
    try:
        turns: list[str] = []
        last_user: str | None = None
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "")
            content = msg.get("content")
            if isinstance(content, list):
                content = " ".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("text")
                )
            text = str(content or "").strip()
            if not text:
                continue
            if role == "user":
                last_user = text
            elif role == "assistant" and last_user is not None:
                turns.append(f"用户: {last_user}\n助手: {text[:300]}")
                last_user = None
        if last_user is not None:
            turns.append(f"用户: {last_user}")
        if not turns:
            return ""
        body = "\n\n".join(turns[-max_turns:])
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "…"
        return f"<thread-memory>\n{body}\n</thread-memory>"
    except Exception:  # noqa: BLE001 — summary is best-effort, never fatal
        return ""


def _extract_parallel_subtasks(text: str, *, max_subtasks: int | None) -> list[str]:
    """Extract candidate subtask strings from a parallelizable goal.

    Three conservative strategies, in priority order:

    1. Explicit bullet / numbered list → each item is a subtask.
    2. Parallel keyword present:
       - ``、``/``和`` coordinate list → split on separators, each item
         prefixed by the shared leading instruction.
       - otherwise split on sentence boundaries.
    3. >=2 question marks → split into per-question subtasks.

    Returns a list of cleaned, substantive fragments (>=2 → parallelizable).
    """
    cap = max_subtasks or _DEFAULT_MAX_SUBTASKS
    lines = [ln for ln in text.splitlines() if ln.strip()]
    bullets = [_clean_fragment(ln) for ln in lines if _BULLET_PREFIX.match(ln)]
    bullets = [b for b in bullets if len(b) >= _MIN_SUBTASK_CHARS]

    if len(bullets) >= 2:
        return bullets[:cap]

    if _PARALLEL_KEYWORDS.search(text):
        # Coordinate list: "分别调研A、B、C" → shared prefix + each item.
        if "、" in text and not _SENTENCE_DELIM.sub("", text).startswith("、"):
            parts = text.split("、")
            if len(parts) >= 2:
                fragments = [_clean_fragment(p) for p in parts]
                fragments = [f for f in fragments if len(f) >= _MIN_SUBTASK_CHARS]
                if len(fragments) >= 2:
                    return fragments[:cap]
        clauses = [_clean_fragment(s) for s in _SENTENCE_DELIM.split(text)]
        clauses = [c for c in clauses if len(c) >= _MIN_SUBTASK_CHARS]
        return clauses[:cap]

    if len(_MULTI_QUESTION.findall(text)) >= 2:
        clauses = [_clean_fragment(s) for s in _MULTI_QUESTION.split(text)]
        clauses = [c for c in clauses if len(c) >= _MIN_SUBTASK_CHARS]
        return clauses[:cap]

    return []


def _heuristic_splitter(
    task: str,
    *,
    max_subtasks: int | None = None,
    context: str | None = None,
    model_name: str | None = None,
) -> SplitResult:
    """Standalone splitter used by both the plan gate and the orchestrator.

    Mirrors the ``splitter`` callable contract the orchestrator expects so
    the exact same decomposition strategy is used for planning and dispatch.

    ``context`` is optional cross-turn memory text (e.g. the ``<thread-memory>``
    block from ``build_thread_memory_summary``). It never influences whether a
    goal is parallelizable — decomposition stays conservative and content-only —
    but when a split IS chosen, the memory is appended to every subtask so each
    parallel sub-agent executes against prior context instead of a blank slate.
    """
    del model_name  # splitter contract keeps it; decomposition is content-only
    text = (task or "").strip()
    background = (context or "").strip()
    fragments = _extract_parallel_subtasks(text, max_subtasks=max_subtasks)

    def _with_background(description: str) -> str:
        if not background:
            return description
        return f"{description}\n\n{background}"

    if len(fragments) < 2:
        tid = f"task_{uuid.uuid4().hex[:10]}"
        return SplitResult(
            tasks=[SplitTask(task_id=tid, description=text)],
            dag_levels=[[tid]],
            total_levels=1,
            is_parallelizable=False,
        )

    tasks: list[SplitTask] = []
    for fragment in fragments:
        tasks.append(
            SplitTask(
                task_id=f"task_{uuid.uuid4().hex[:10]}",
                description=_with_background(fragment),
                subagent_name="general-purpose",
                depends_on=[],
                priority=0,
            )
        )
    ids = [t.task_id for t in tasks]
    return SplitResult(
        tasks=tasks,
        dag_levels=[ids],
        total_levels=1,
        is_parallelizable=True,
    )


def plan_auto_parallel(
    goal: str,
    *,
    context: str | None = None,
    max_subtasks: int | None = None,
    model_name: str | None = None,
) -> AutoParallelPlan | None:
    """Decide whether this goal should be auto-decomposed and run in parallel.

    Pure heuristic gate — does not create an orchestrator. Returns ``None``
    when the goal is too short or carries no strong parallel signal.
    """
    text = (goal or "").strip()
    if len(text) < _MIN_GOAL_CHARS:
        return None

    split = _heuristic_splitter(
        text,
        max_subtasks=max_subtasks,
        context=context,
        model_name=model_name,
    )
    if not split.is_parallelizable or len(split.tasks) < 2:
        return None

    return AutoParallelPlan(
        subtasks=tuple(split.tasks),
        reason=f"goal decomposed into {len(split.tasks)} independent sub-inquiries",
    )


def set_auto_parallel_orchestrator(orchestrator: ParallelAgentOrchestrator | None) -> None:
    """Inject the orchestrator used for dispatch (tests / app wiring)."""
    global _ORCHESTRATOR
    _ORCHESTRATOR = orchestrator


def get_auto_parallel_orchestrator() -> ParallelAgentOrchestrator:
    """Return the shared orchestrator, creating one wired to real sub-agents."""
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        _ORCHESTRATOR = ParallelAgentOrchestrator(
            max_concurrency=4,
            task_runner=_subagent_task_runner,
            splitter=_heuristic_splitter,
        )
    return _ORCHESTRATOR


def _subagent_task_runner(
    description: str,
    *,
    subagent_name: str = "general-purpose",
    context: Any = None,
    cancel_event: Any = None,
) -> str:
    """Orchestrator task runner that delegates each subtask to a sub-agent."""
    del cancel_event  # orchestrator cancellation flows through call_subagent
    from runtime.execution.subagents.bridge import call_subagent

    result = call_subagent(
        agent_id=subagent_name,
        prompt=description,
        context=context if isinstance(context, dict) else None,
        timeout_s=_SUBAGENT_TIMEOUT_S,
    )
    return str(result.get("output") or "")


def run_auto_parallel(
    plan: AutoParallelPlan,
    *,
    thread_id: str = "",
    turn_id: str | None = None,
    model_name: str | None = None,
    context: dict[str, Any] | None = None,
    owner_id: str | None = None,
    timeout_s: float | None = None,
    on_batch_started: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Dispatch the plan's subtasks in parallel and aggregate the results.

    Blocks until the batch reaches a terminal state (or the timeout).
    Returns a plain dict with ``success`` / ``content`` / ``error`` /
    ``batch_id`` / ``status`` / ``completed`` / ``total``.

    ``on_batch_started`` (if provided) is invoked with the ``batch_id``
    as soon as the batch is dispatched, BEFORE the synchronous poll, so
    a caller can wire up a live event bridge (e.g. stream the
    orchestrator's ``BatchStreamEvent``s to a frontend workbench) while
    this function is still waiting on the batch.

    ``turn_id`` (optional) lets the runner persist the turn's blackboard
    evidence to the thread's board-evidence log once the batch settles —
    the "事件日志证据复用" bridge: later turns / reconnect read it back
    via ``load_board_evidence``. Best-effort; empty turn_id is a no-op.
    """
    if not plan.should_parallelize():
        return {
            "success": False,
            "content": "",
            "error": "plan has fewer than 2 subtasks",
            "batch_id": "",
            "status": "skipped",
            "completed": 0,
            "total": 0,
        }

    orchestrator = get_auto_parallel_orchestrator()
    tasks = [
        DispatchTaskInput(
            description=t.description,
            subagent_name=t.subagent_name,
            task_id=t.task_id,
            depends_on=list(t.depends_on),
            priority=t.priority,
        )
        for t in plan.subtasks
    ]

    try:
        batch = orchestrator.dispatch(
            tasks,
            execution_mode="parallel",
            thread_id=thread_id or None,
            model_name=model_name,
            context=context,
            owner_id=owner_id,
        )
    except Exception as exc:  # noqa: BLE001 — dispatch failures are non-fatal
        _log.warning("auto-parallel dispatch failed · err=%s", exc)
        return {
            "success": False,
            "content": "",
            "error": f"{type(exc).__name__}: {exc}",
            "batch_id": "",
            "status": "failed",
            "completed": 0,
            "total": len(tasks),
        }

    batch_id = batch.batch_id
    if on_batch_started is not None:
        try:
            on_batch_started(batch_id)
        except Exception:  # noqa: BLE001 - bridge wiring is best-effort
            _log.debug("on_batch_started callback failed · batch_id=%s", batch_id, exc_info=True)
    deadline = time.monotonic() + (timeout_s or _DEFAULT_BATCH_TIMEOUT_S)
    terminal: Any = None
    while time.monotonic() < deadline:
        current = orchestrator.get_batch(batch_id)
        if current is None:
            break
        if current.status in ("completed", "failed", "timed_out", "partial", "cancelled"):
            terminal = current
            break
        time.sleep(_POLL_INTERVAL_S)

    if terminal is None:
        with contextlib.suppress(Exception):
            orchestrator.cancel_all()
        _persist_turn_board_evidence(thread_id, turn_id)
        return {
            "success": False,
            "content": "",
            "error": f"auto-parallel batch timed out after {timeout_s or _DEFAULT_BATCH_TIMEOUT_S}s",
            "batch_id": batch_id,
            "status": "timed_out",
            "completed": terminal.completed_tasks if terminal else 0,
            "total": len(tasks),
        }

    _persist_turn_board_evidence(thread_id, turn_id)

    sections: list[str] = []
    for r in terminal.results:
        if r.status == "completed" and r.result:
            sections.append(f"[{r.subagent_name}]\n{r.result}")
    content = "\n\n".join(sections)
    ok = terminal.status in ("completed", "partial") and bool(content.strip())
    return {
        "success": ok,
        "content": content,
        "error": None if ok else (terminal.error or "no usable output"),
        "batch_id": batch_id,
        "status": terminal.status,
        "completed": terminal.completed_tasks,
        "total": terminal.total_tasks,
    }


def _persist_turn_board_evidence(thread_id: str, turn_id: str | None) -> None:
    """Best-effort：batch 落定后把本轮黑板的键值证据持久化到线程证据日志。

    无 turn_id（未接线）或持久化失败均为 no-op——证据复用绝不能影响
    并行执行本身。
    """
    if not thread_id or not turn_id:
        return
    try:
        from runtime.memory.runtime_state.blackboard import get_blackboard
        from runtime.memory.threads.board_evidence import save_turn_blackboard

        board = get_blackboard(turn_id)
        if board is None:
            return
        save_turn_blackboard(thread_id, turn_id, board)
    except Exception:  # noqa: BLE001 — evidence bridge must never break the run
        _log.debug(
            "board evidence persist skipped · thread=%s turn=%s", thread_id, turn_id, exc_info=True
        )


_ORCHESTRATOR: ParallelAgentOrchestrator | None = None


__all__ = [
    "AutoParallelPlan",
    "plan_auto_parallel",
    "run_auto_parallel",
    "set_auto_parallel_orchestrator",
    "get_auto_parallel_orchestrator",
    "_heuristic_splitter",
    "_extract_parallel_subtasks",
]
