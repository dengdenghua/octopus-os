"""CLI for inspecting + driving ReAct checkpoint resume (P3 long-task durability).

After a process is hard-killed mid-trajectory, the journal holds
``react_checkpoint`` events that, combined with the periodic
auto-checkpoint feature, allow a future process to resume the task
where the last completed iteration left off. This CLI gives operators
the inspection AND drive tools they need:

* ``list``    - which tasks have outstanding (non-final) checkpoints?
* ``show``    - what's the most recent checkpoint for a given task?
* ``resume``  - drive ``run_react_loop`` to actually resume the task.

All subcommands read from a Journal instance loaded via the CLI's
``--journal-path`` flag, so the same tool works against any project's
JSONL journal file.

Usage::

    python -m runtime.core.cerebrum.resume_cli list
    python -m runtime.core.cerebrum.resume_cli list --journal-path data/journal.jsonl
    python -m runtime.core.cerebrum.resume_cli show <task_id>
    python -m runtime.core.cerebrum.resume_cli resume <task_id> --planner-type static

Exit codes
----------
* 0 - success
* 1 - uncaught failure (journal I/O, etc.); cron should alert
* 2 - invalid CLI usage
* 3 - task not found / already final
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_LOG = logging.getLogger("echo.resume_cli")

DEFAULT_JOURNAL_PATH = Path("data/journal.jsonl")


def _load_journal_events(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL journal and return all event dicts.

    Returns ``[]`` (with a warning) when the file is missing; operators
    new to the tool shouldn't get a traceback, just a clear "nothing to
    show".
    """
    if not path.exists():
        _LOG.warning("journal file not found: %s", path)
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            with contextlib.suppress(json.JSONDecodeError):
                d = json.loads(line)
                if isinstance(d, dict):
                    out.append(d)
    return out


def _checkpoints_by_task(
    events: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group ``react_checkpoint`` events by task_id, oldest-first."""
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e.get("event_type") != "react_checkpoint":
            continue
        tid = str(e.get("task_id") or "")
        if not tid:
            continue
        by_task[tid].append(e)
    # Each task's events are already in journal-write order.
    return dict(by_task)


def _resumable_tasks(
    by_task: dict[str, list[dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(task_id, latest_checkpoint)`` for tasks whose most
    recent checkpoint isn't a final-answer state.

    A task whose latest checkpoint has ``has_final_answer == True`` is
    already done; we don't surface it as resumable.
    """
    out: list[tuple[str, dict[str, Any]]] = []
    for tid, events in by_task.items():
        if not events:
            continue
        latest = events[-1]
        if latest.get("has_final_answer"):
            continue
        out.append((tid, latest))
    out.sort(key=lambda pair: pair[1].get("ts", ""))
    return out


def _render_list(resumable: list[tuple[str, dict[str, Any]]]) -> str:
    if not resumable:
        return "No resumable react tasks found."
    lines = [
        f"Resumable react tasks: {len(resumable)}",
        "  task_id                          iter / max   phase           ts",
    ]
    for tid, ckpt in resumable:
        it_done = ckpt.get("iteration_completed", "?")
        it_max = ckpt.get("max_iterations", "?")
        phase = (ckpt.get("current_phase") or "")[:14]
        ts = (ckpt.get("ts") or "")[:19]
        # Truncate task_id to 32 for table sanity.
        tid_short = tid[:32]
        lines.append(f"  {tid_short:<32}  {it_done:>4}/{it_max:<4}   {phase:<14}  {ts}")
    return "\n".join(lines)


def _render_show(task_id: str, ckpt: dict[str, Any] | None) -> str:
    if ckpt is None:
        return f"No checkpoint found for task: {task_id}"
    steps = ckpt.get("steps_snapshot") or []
    working_set = ckpt.get("working_set_snapshot") or []
    lines = [
        f"Task: {task_id}",
        f"  Last checkpoint:    {ckpt.get('ts', '?')}",
        f"  Iteration:          {ckpt.get('iteration_completed', '?')}"
        f" / {ckpt.get('max_iterations', '?')}",
        f"  Phase:              {ckpt.get('current_phase') or '(none)'}",
        f"  Has final answer:   {ckpt.get('has_final_answer', False)}",
        f"  Steps recorded:     {len(steps)}",
        f"  Working set size:   {len(working_set)}",
    ]
    summary = (ckpt.get("progress_summary") or "").strip()
    if summary:
        # Indent summary block so it reads cleanly.
        lines.append("  Progress summary:")
        for sline in summary.splitlines()[:10]:
            lines.append(f"    {sline}")
    last_step = steps[-1] if steps else None
    if last_step:
        lines.append("  Last step:")
        thought = (last_step.get("thought") or "").strip().splitlines()
        if thought:
            lines.append(f"    Thought: {thought[0][:120]}")
        action = (last_step.get("action") or "").strip()
        if action:
            lines.append(f"    Action:  {action[:120]}")
        observation = (last_step.get("observation") or "").strip().splitlines()
        if observation:
            lines.append(f"    Obs:     {observation[0][:120]}")
    return "\n".join(lines)


def _build_mirror(
    url: str | None,
    *,
    factory: Any = None,
) -> Any:
    """Build a CheckpointMirror from ``url`` (or via injected factory).

    Returns None when ``url`` is empty / unset OR when the build fails
    (missing redis package, malformed URL). Tests inject a ``factory``
    so they don't need a real redis connection.
    """
    if not url or not url.strip():
        return None
    if factory is not None:
        try:
            return factory(url)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("mirror factory failed for %s: %s", url, exc)
            return None
    try:
        from runtime.core.cerebrum.checkpoint_mirror import (
            build_checkpoint_mirror_from_url,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("checkpoint_mirror import failed: %s", exc)
        return None
    return build_checkpoint_mirror_from_url(url)


def _mirror_resumable(mirror: Any) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(task_id, latest_payload)`` for every task in the mirror.

    Mirror semantics: ``list_tasks()`` only contains non-final tasks
    (final-answer ``put`` removes them from the index). So no extra
    filter on ``has_final_answer`` is needed here. Output sorted by
    task_id for deterministic listing.
    """
    if mirror is None:
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    try:
        ids = mirror.list_tasks() or []
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("mirror list_tasks failed: %s", exc)
        return []
    for tid in ids:
        try:
            payload = mirror.get(tid)
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("mirror get failed for %s: %s", tid, exc)
            continue
        if isinstance(payload, dict):
            out.append((tid, payload))
    out.sort(key=lambda pair: pair[0])
    return out


def _mirror_get(mirror: Any, task_id: str) -> dict[str, Any] | None:
    if mirror is None or not task_id:
        return None
    try:
        payload = mirror.get(task_id)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("mirror get failed for %s: %s", task_id, exc)
        return None
    return payload if isinstance(payload, dict) else None


def _resume_task(
    task_id: str,
    *,
    journal_path: Path,
    planner_type: str = "static",
    planner_model: str = "mock/planner",
    max_iterations: int = 30,
    mirror_url: str | None = None,
    runner: Any = None,
    stack_builder: Any = None,
    journal_loader: Any = None,
    mirror_factory: Any = None,
) -> int:
    """Drive ``run_react_loop`` to resume the named task.

    Parameters
    ----------
    task_id :
        The task to resume.
    journal_path :
        Path to the JSONL journal that holds the task's checkpoints.
    planner_type / planner_model :
        Forwarded to ``_build_stack`` so the operator can pick a cheap
        static planner for testing or a real LLM for production.
    max_iterations :
        Hard cap forwarded to ``run_react_loop``.
    mirror_url :
        Optional Redis-shape URL for the distributed checkpoint mirror.
        When provided, the CLI looks up the task there first; falls
        back to the local journal when the mirror has nothing.
    runner / stack_builder / journal_loader / mirror_factory :
        Test-injection hooks. Defaults pull the real implementations
        lazily so importing this module doesn't drag the whole stack.

    Returns
    -------
    Exit code:
        * 0 if resume completed (final answer or budget reached).
        * 3 if task not found or already final.
        * 1 if the resume invocation raised.
    """
    # Lazy imports so the CLI module stays cheap to import for list/show.
    if journal_loader is None:
        try:
            from runtime.memory.journal.journal import (
                JSONLJournal as _RealJournal,
            )

            journal_loader = lambda p: _RealJournal(p)  # noqa: E731
        except Exception as exc:  # noqa: BLE001
            _LOG.error("could not load JSONLJournal: %s", exc)
            return 1
    if stack_builder is None:
        try:
            from runtime.cli_core import _build_stack as _real_build_stack

            stack_builder = _real_build_stack
        except Exception as exc:  # noqa: BLE001
            _LOG.error("could not import _build_stack: %s", exc)
            return 1
    if runner is None:
        try:
            from runtime.core.cerebrum.react_loop import (
                run_react_loop as _real_runner,
            )

            runner = _real_runner
        except Exception as exc:  # noqa: BLE001
            _LOG.error("could not import run_react_loop: %s", exc)
            return 1

    # Sanity: refuse to resume a task we can't find as a non-final
    # checkpoint. Consult mirror first when configured (cross-machine
    # case), then fall back to local journal (same-machine case).
    latest: dict[str, Any] | None = None
    source = "journal"
    mirror = _build_mirror(mirror_url, factory=mirror_factory)
    if mirror is not None:
        latest = _mirror_get(mirror, task_id)
        if latest is not None:
            source = "mirror"
    if latest is None:
        events = _load_journal_events(journal_path)
        by_task = _checkpoints_by_task(events)
        ckpts = by_task.get(task_id) or []
        if ckpts:
            latest = ckpts[-1]
    if latest is None:
        print(f"No checkpoints found for task: {task_id}")
        return 3
    if latest.get("has_final_answer"):
        print(f"Task {task_id} already has a final answer; nothing to resume.")
        return 3
    goal = (latest.get("progress_summary") or "").strip().splitlines()
    headline = goal[0] if goal else f"resume task {task_id}"
    print(
        f"Resuming task {task_id} from iteration "
        f"{latest.get('iteration_completed', '?')} / "
        f"{latest.get('max_iterations', '?')} (source={source})",
    )

    # Build the minimal stack. The journal is the SAME file the task
    # was writing to so resume sees the prior checkpoint.
    try:
        journal = journal_loader(journal_path)
        built_stack = stack_builder(
            planner_type=planner_type,
            planner_model=planner_model,
            journal=journal,
        )
        if isinstance(built_stack, tuple) and len(built_stack) >= 3:
            planner, executor, built_journal = built_stack[:3]
            stack = SimpleNamespace(
                planner=planner,
                executor=executor,
                journal=built_journal,
            )
        else:
            stack = built_stack
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("resume bootstrap failed: %s", exc)
        print(f"ERROR: bootstrap: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    # Build a ParsedIntent stub. We don't have the original goal text
    # (it isn't on the checkpoint), so use the progress summary headline
    # as a fallback. Keeps resume invocation possible even when the
    # original intent was lost.
    try:
        from runtime.platform.models import ParsedIntent

        intent = ParsedIntent(
            raw=headline,
            intent_type="task",
            normalized_goal=headline,
            confidence=1.0,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("intent build failed: %s", exc)
        print(f"ERROR: intent: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    # Audit: write a task_resumed event to the journal BEFORE the
    # runner starts, tagged with which source supplied the checkpoint.
    # If journal.write_task_resumed isn't available (mock journal /
    # legacy stack), the failure is swallowed.
    journal_for_audit = getattr(stack, "journal", None) or journal
    if journal_for_audit is not None and hasattr(
        journal_for_audit,
        "write_task_resumed",
    ):
        with contextlib.suppress(Exception):
            journal_for_audit.write_task_resumed(
                task_id=str(task_id),
                resumed_by=f"resume_cli/{source}",
                extra_iterations=max_iterations,
            )

    # Drive run_react_loop. Errors here propagate to exit 1 via main().
    try:
        result = runner(
            stack,
            intent,
            None,
            max_iterations=max_iterations,
            resume_task_id=task_id,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("resume runner failed: %s", exc)
        print(f"ERROR: runner: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"Resume completed: result={result!r}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="resume_cli",
        description="Inspect and drive ReAct checkpoint resume.",
    )
    parser.add_argument(
        "--journal-path",
        type=Path,
        default=DEFAULT_JOURNAL_PATH,
        help=f"Path to JSONL journal file (default: {DEFAULT_JOURNAL_PATH}).",
    )
    parser.add_argument(
        "--mirror-url",
        default=None,
        help=(
            "Optional Redis-shape URL for the distributed checkpoint "
            "mirror. When provided, list/show/resume read from the "
            "mirror first and fall back to the local journal. "
            "Defaults to the ECHO_CHECKPOINT_MIRROR_URL env var when "
            "not explicitly passed."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List tasks with outstanding checkpoints.")
    show_p = sub.add_parser("show", help="Show latest checkpoint for one task.")
    show_p.add_argument("task_id", help="The task id to inspect.")
    resume_p = sub.add_parser(
        "resume",
        help="Resume a task from its latest checkpoint.",
    )
    resume_p.add_argument("task_id", help="The task id to resume.")
    resume_p.add_argument(
        "--planner-type",
        default="static",
        choices=("static", "llm"),
        help="Stack planner type (default: static for cheap dry-runs).",
    )
    resume_p.add_argument(
        "--planner-model",
        default="mock/planner",
        help="Stack planner model identifier (default: mock/planner).",
    )
    resume_p.add_argument(
        "--max-iterations",
        type=int,
        default=30,
        help="Cap on additional iterations (default: 30).",
    )
    return parser.parse_args(argv)


def _resolve_mirror_url(args: argparse.Namespace) -> str | None:
    """Pick the mirror URL: explicit arg wins; otherwise fall back to
    ECHO_CHECKPOINT_MIRROR_URL env var. Empty/unset → None."""
    import os

    explicit = (args.mirror_url or "").strip()
    if explicit:
        return explicit
    env_url = (os.environ.get("ECHO_CHECKPOINT_MIRROR_URL") or "").strip()
    return env_url or None


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    try:
        args = _parse_args(argv)
        mirror_url = _resolve_mirror_url(args)
        if args.cmd == "resume":
            return _resume_task(
                args.task_id,
                journal_path=args.journal_path,
                planner_type=args.planner_type,
                planner_model=args.planner_model,
                max_iterations=args.max_iterations,
                mirror_url=mirror_url,
            )
        # list / show — try mirror first, fall back to journal.
        mirror = _build_mirror(mirror_url)
        if args.cmd == "list":
            mirrored = _mirror_resumable(mirror) if mirror is not None else []
            if mirrored:
                print(_render_list(mirrored))
            else:
                events = _load_journal_events(args.journal_path)
                by_task = _checkpoints_by_task(events)
                print(_render_list(_resumable_tasks(by_task)))
        elif args.cmd == "show":
            payload: dict[str, Any] | None = None
            if mirror is not None:
                payload = _mirror_get(mirror, args.task_id)
            if payload is None:
                events = _load_journal_events(args.journal_path)
                by_task = _checkpoints_by_task(events)
                ckpts = by_task.get(args.task_id) or []
                payload = ckpts[-1] if ckpts else None
            print(_render_show(args.task_id, payload))
    except SystemExit:  # argparse exit codes
        raise
    except Exception as exc:  # noqa: BLE001 - top-level CLI guard
        _LOG.exception("resume_cli failed: %s", exc)
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
