"""Runtime-side auto-verification salvage for final-answer guard deadlocks.

When the model keeps emitting plain-text final answers that a
verification guard rejects (e.g. the language-mismatch guard: python
edits were never verified by pytest / ruff / py_compile), the ReAct loop
previously hard-stopped with ``guard_impasse`` after three rejections —
even when the missing check is a cheap, side-effect-free command the
runtime can execute itself. This module builds a synthetic
``exec_shell`` step for those cases so the loop runs the check through
its normal dispatch instead of deadlocking on a satisfiable guard.

Only python ``py_compile`` is auto-executed today: it is pure syntax
verification, requires no dependencies, and cannot mutate the
workspace. It runs through the ``run_tests`` skill (registered in every
executor surface) with an explicit command, rather than ``exec_shell``
— the headless CLI registry has no shell tool, so an ``exec_shell``
salvage would deadlock there. Other languages / verifiers still
require the model to act.
"""

from __future__ import annotations

import json
import logging
import shlex
import sys

from runtime.core.cerebrum.react_types import ReActStep

_logger = logging.getLogger(__name__)

# Guard labels whose rejection can be satisfied by a runtime-side check.
# The language-mismatch guard's own suggestion for python is
# ``pytest / ruff / py_compile``; ``py_compile`` is the safe minimal
# subset the runtime may run on the model's behalf.
_AUTO_VERIFY_GUARD_LABELS: frozenset[str] = frozenset({"language-verification guard"})


def _recent_python_write_paths(steps: list[ReActStep]) -> list[str]:
    """Return unique .py paths written in the guard's recent-write window."""
    from runtime.core.cerebrum._react_parsing_core import _is_code_write_step
    from runtime.core.cerebrum.react_verification_guards import (
        _LANG_MISMATCH_LOOKBACK,
        _action_path,
    )

    window = steps[-_LANG_MISMATCH_LOOKBACK:] if steps else []
    paths: list[str] = []
    seen: set[str] = set()
    for step in window:
        if not _is_code_write_step(step):
            continue
        path = _action_path(step)
        if not path or path in seen:
            continue
        seen.add(path)
        if path.lower().endswith(".py"):
            paths.append(path)
    return paths


def _build_py_compile_step(
    paths: list[str],
    *,
    iteration: int,
    cwd: str,
) -> ReActStep:
    """Build a synthetic ``run_tests`` step running ``py_compile``."""
    command = f"{shlex.quote(sys.executable)} -m py_compile " + " ".join(
        shlex.quote(path) for path in paths
    )
    action = f"run_tests({json.dumps({'command': command, 'cwd': cwd}, ensure_ascii=False)})"
    return ReActStep(
        iteration=iteration,
        thought=(
            "[runtime auto-verification] The final answer was rejected because python "
            f"edits ({', '.join(paths)}) were never verified by a matching verifier. "
            "Running py_compile directly to satisfy the language-verification guard."
        ),
        public_update="[runtime] 自动执行缺失的 python 语法验证 (py_compile)。",
        action=action,
        actions=[action],
    )


def _try_auto_verification_salvage(
    label: str,
    steps: list[ReActStep],
    *,
    iteration: int,
    cwd: str | None = None,
) -> ReActStep | None:
    """Return a runtime-executed verification step, or ``None``.

    ``None`` means either the guard has no cheap runtime-side remedy
    (the loop should keep its existing hard-stop behaviour) or there is
    nothing verifiable in the recent window.
    """
    if label not in _AUTO_VERIFY_GUARD_LABELS:
        return None
    if not cwd:
        return None
    paths = _recent_python_write_paths(steps)
    if not paths:
        return None
    step = _build_py_compile_step(paths, iteration=iteration, cwd=cwd)
    _logger.info(
        "react_auto_verify: salvaging %s with py_compile on %s",
        label,
        ", ".join(paths),
    )
    return step
