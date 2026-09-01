"""Working-set / phase / progress-summary helpers and trajectory persistence
+ planner learning throttles for the ReAct loop.

Progress half extracted from ``react_execution.py``: tracks which files the
code agent has read or edited, detects the current execution phase, and
renders the public progress summaries (code-mode and research-mode).
Trajectory half extracted from ``react_execution.py``: persists the beak
trajectory to the journal and throttles the planner's per-journal learning
(rules, memories, knowledge-graph refresh, recipe self-assessment) so they
don't run on every single turn. Leaf module: imports only from react_* leaf
modules and platform layers — never imports react_loop or react_execution.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from runtime.core.cerebrum.react_context import _estimate_tokens
from runtime.core.cerebrum.react_parsing import _parse_action
from runtime.core.cerebrum.react_types import ReActStep

_FILE_SKILLS = frozenset(
    {
        "read_file",
        "list_cwd",
        "edit_text_file",
        "write_text_file",
        "edit_file",
        "multi_edit_file",
        "create_file",
        "delete_file",
    }
)
_WRITE_SKILLS = frozenset(
    {
        "edit_text_file",
        "edit_file",
        "multi_edit_file",
        "write_text_file",
        "create_file",
        "delete_file",
    }
)
_PHASE_KEYWORDS = {
    "understand": {"read_file", "list_cwd", "recall"},
    "execute": {
        "edit_text_file",
        "edit_file",
        "multi_edit_file",
        "write_text_file",
        "create_file",
        "delete_file",
    },
    "verify": {"exec_shell", "run_command"},
}


def _update_working_set(
    working_set: dict[str, dict[str, Any]],
    step: ReActStep,
    current_phase: str,
) -> None:

    parsed = _parse_action(step.action) if step.action else None
    if not parsed:
        return
    skill_name = parsed[0]
    args = parsed[1] or {}
    if skill_name not in _FILE_SKILLS:
        return
    path = args.get("path") or args.get("file_path") or args.get("filepath")
    if not path or not isinstance(path, str):
        return
    relevance = "editing" if skill_name in _WRITE_SKILLS else "related"
    now = time.time()
    existing = working_set.get(path)
    if existing:
        if skill_name in _WRITE_SKILLS:
            existing["relevance"] = "editing"
            existing["last_modified_at"] = now
        else:
            existing["last_read_at"] = now
    else:
        working_set[path] = {
            "path": path,
            "last_read_at": now,
            "last_modified_at": now if skill_name in _WRITE_SKILLS else 0.0,
            "tokens_estimated": _estimate_tokens(step.observation) if step.observation else 0,
            "relevance": relevance,
        }


def _detect_phase(step: ReActStep, current_phase: str) -> str:
    action = step.action.lower() if step.action else ""
    for phase, skills in _PHASE_KEYWORDS.items():
        if any(s in action for s in skills):
            if phase == "verify" and current_phase == "execute":
                return "verify"
            if phase == "execute" and current_phase == "understand":
                return "execute"
            if phase == "execute":
                return "execute"
    return current_phase


def _build_progress_summary(
    steps: list[ReActStep],
    working_set: dict[str, dict[str, Any]],
    current_phase: str,
) -> str:
    if not steps:
        return ""
    phase_labels = {"understand": "补齐上下文", "execute": "处理线索", "verify": "确认结果"}
    phase_label = phase_labels.get(current_phase, current_phase)
    files_read = [
        p for p, f in working_set.items() if f.get("relevance") in ("related", "referenced")
    ]
    files_modified = [p for p, f in working_set.items() if f.get("relevance") == "editing"]
    parts = [phase_label]
    if files_read:
        parts.append(f"已查看 {', '.join(_public_progress_target(p) for p in files_read[:6])}")
    if files_modified:
        parts.append(f"已更新 {', '.join(_public_progress_target(p) for p in files_modified[:6])}")
    parts.append(f"第 {len(steps)} 轮")
    return " · ".join(part for part in parts if part)


def _public_progress_target(value: str) -> str:
    clean = re.sub(r"\s+", " ", str(value or "")).strip()
    if not clean:
        return ""
    parts = [part for part in re.split(r"[\\/]+", clean) if part]
    return parts[-1] if parts else clean


def _build_research_progress_summary(steps: list[ReActStep]) -> str:
    """Build a public, non-chain-of-thought progress summary for non-code ReAct."""
    if not steps:
        return ""
    latest = steps[-1]
    action = (latest.action or "").lower()
    searches = [step for step in steps if "web_search" in (step.action or "").lower()]
    if "web_search" in action:
        return f"已完成第 {len(searches)} 轮资料检索；正在收拢可用证据，继续补齐还不确定的缺口。"
    if "fetch_url" in action:
        return "已打开具体来源核对细节；接下来会把来源信息并入结论。"
    if "none" in action or "final" in action:
        return "资料检索已收敛，正在综合分析并生成最终回复。"
    return f"已完成 {len(steps)} 轮处理；正在根据上一轮结果调整下一步。"


_logger = logging.getLogger(__name__)

_KG_REFRESH_EVERY = 5
_KG_COUNTERS: dict[int, int] = {}

_RECIPE_REFRESH_EVERY = 5
_RECIPE_COUNTERS: dict[int, int] = {}


def _persist_react_trajectory(
    stack: Any,
    *,
    react_task_id: Any,
    beak_steps: list[Any],
    success: bool,
    disposition: str = "completed",
) -> None:
    if not beak_steps or react_task_id is None:
        return
    journal = getattr(stack, "journal", None)
    if journal is None or not hasattr(journal, "write_trajectory"):
        return

    try:
        from runtime.platform.models import (
            ArmId,
            CostEntry,
            Trajectory,
            TrajectoryOutcome,
        )
    except ImportError:
        return

    thread_id: str | None = None
    try:
        from runtime.platform.process.session import current_session

        _sess = current_session()
        thread_id = _sess.thread_id if _sess else None
    except Exception:  # noqa: BLE001 — thread tagging is best-effort
        thread_id = None

    try:
        traj = Trajectory(
            task_id=react_task_id,
            thread_id=thread_id,
            arm_id=ArmId("react_arm"),
            strategy_id="react_loop",
            steps=list(beak_steps),
            outcome=TrajectoryOutcome(
                success=success,
                cost=CostEntry(),
                disposition=disposition,
            ),
        )
        journal.write_trajectory(traj, actor="react_loop")
    except Exception as exc:  # noqa: BLE001
        _logger.debug("react_loop trajectory persist skipped: %s", exc)
        return

    planner = getattr(stack, "planner", None)
    if planner is None:
        return

    if not success:
        learn_rules = getattr(planner, "learn_from_journal", None)
        if learn_rules is not None:
            try:
                learn_rules(journal)
            except Exception as exc:  # noqa: BLE001
                _logger.debug(
                    "react_loop learn_from_journal skipped: %s",
                    exc,
                )

    learn_memories = getattr(planner, "learn_memories_from_journal", None)
    if learn_memories is not None:
        try:
            learn_memories(journal)
        except Exception as exc:  # noqa: BLE001
            _logger.debug(
                "react_loop learn_memories_from_journal skipped: %s",
                exc,
            )

    _react_kg_throttle(stack, journal, planner)
    _react_recipe_throttle(journal, planner)


def _react_kg_throttle(stack: Any, journal: Any, planner: Any) -> None:
    learn_kg = getattr(planner, "learn_kg_from_journal", None)
    if learn_kg is None:
        return
    key = id(journal)
    cnt = _KG_COUNTERS.get(key, 0) + 1
    if cnt < _KG_REFRESH_EVERY:
        _KG_COUNTERS[key] = cnt
        return
    _KG_COUNTERS[key] = 0
    try:
        learn_kg(journal)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("react_loop learn_kg_from_journal skipped: %s", exc)


def _reset_kg_throttle_for_tests() -> None:
    _KG_COUNTERS.clear()


def _react_recipe_throttle(journal: Any, planner: Any) -> None:
    """Refresh the planner's recipe self-assessment from accumulating
    experience, throttled like the KG refresh.

    Without this the recipe verdict (which drives 'prefer a stronger model' +
    the losing-recipe warning) is only ever set at startup and never reflects
    how the current prompt recipe is actually performing this session. Parallels
    the per-turn rules/memory/KG learning already wired here.
    """
    assess = getattr(planner, "assess_recipe_from_journal", None)
    if assess is None:
        return
    key = id(journal)
    cnt = _RECIPE_COUNTERS.get(key, 0) + 1
    if cnt < _RECIPE_REFRESH_EVERY:
        _RECIPE_COUNTERS[key] = cnt
        return
    _RECIPE_COUNTERS[key] = 0
    try:
        assess(journal)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("react_loop assess_recipe_from_journal skipped: %s", exc)


def _reset_recipe_throttle_for_tests() -> None:
    _RECIPE_COUNTERS.clear()
