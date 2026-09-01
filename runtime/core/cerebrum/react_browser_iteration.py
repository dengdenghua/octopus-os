"""Browser-surface gating and per-task iteration limits for the ReAct loop.

Extracted from ``react_loop.py`` (Wave 1 of the split documented in
``docs/design/react-loop-split-plan.md``). Decides when local browser tools
must be dependency-activated for a turn, and sizes the iteration budget for
browser-operation, narrow-research, and code-implementation turns.
"""

from __future__ import annotations

import logging
import re
from typing import Any

_logger = logging.getLogger(__name__)


def _ensure_browser_operation_skills(executor: Any) -> int:
    """Enable the local browser group only for an explicit Browser surface.

    Local configurations may intentionally disable general web skills. That
    must not also remove localhost UI automation from a turn the user opened
    on the Browser surface. Registration remains dependency-gated and URL
    safety still requires explicit private-address permission.
    """
    registry = getattr(executor, "registry", None)
    if registry is None:
        return 0
    try:
        if registry.has("browser_navigate"):
            return 0
        from runtime.execution.suckers.browser_skills import register_browser_skills

        return int(register_browser_skills(registry, verify_tests=False))
    except (AttributeError, ImportError, TypeError, ValueError):
        _logger.debug("explicit browser skill activation failed", exc_info=True)
        return 0


def _browser_operation_requested(user_context: Any) -> bool:
    """Return whether the turn requires dependency-gated local browser tools.

    Code-mode UI regression needs those tools too, but remains distinct from
    browser-only operation mode so it keeps coding iteration limits and final
    guards.  This predicate is intentionally about tool registration, not the
    work-surface semantics used later in the loop.
    """
    if not isinstance(user_context, dict):
        return False
    metadata = user_context.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    surface = (
        str(user_context.get("browser_surface") or metadata.get("browser_surface") or "")
        .strip()
        .lower()
    )
    runtime_surfaces = user_context.get("runtime_surfaces") or metadata.get("runtime_surfaces")
    surface_names = (
        {str(item).strip().lower() for item in runtime_surfaces}
        if isinstance(runtime_surfaces, list)
        else set()
    )
    return bool(
        user_context.get("browser_operation_mode")
        or metadata.get("browser_operation_mode")
        or user_context.get("browser_regression_enabled")
        or metadata.get("browser_regression_enabled")
        or user_context.get("chrome_operation_mode")
        or metadata.get("chrome_operation_mode")
        or surface in {"browser", "chrome"}
        or {"browser", "chrome"} & surface_names
    )


def _browser_task_iteration_limit(
    max_iterations: int,
    *,
    browser_operation_mode: bool,
) -> int:
    """Give explicit Browser turns enough rounds for stateful UI flows.

    A 30-round floor still auto-pauses at round 27, which is too early for
    ordinary form workflows that must navigate, fill native controls, upload,
    submit, wait for a delayed frame, and inspect confirmation state.  Keep
    explicit browser turns aligned with implementation turns at 60 rounds so
    the checkpoint guard remains a last resort rather than interrupting the
    final submit/verify pair.
    """
    if browser_operation_mode:
        return max(60, max_iterations)
    return max_iterations


def _narrow_research_iteration_limit(goal: str, max_iterations: int) -> int:
    """Keep single-source fact lookups from inheriting deep-research budgets."""
    text = " ".join(str(goal or "").strip().split()).lower()
    source_marker = bool(
        re.search(r"(?:一个|1\s*个)\s*(?:官方|可靠)?\s*(?:来源|网页|页面)", text)
        or re.search(r"\b(?:one|single)\s+(?:official\s+)?source\b", text)
    )
    concise_marker = bool(
        re.search(r"(?:一句|一段|简短|一句话|结论)", text)
        or re.search(r"\b(?:one sentence|brief|concise|short conclusion)\b", text)
    )
    if source_marker and concise_marker:
        return min(max_iterations, 8)
    return max_iterations


def _code_task_iteration_limit(
    goal: str,
    max_iterations: int,
    *,
    is_code_mode: bool,
) -> int:
    """Give real implementation turns enough room for edits plus verification.

    Small explicit caps (used by tests, smoke runs, and callers that really want
    a short turn) remain authoritative.  The ordinary realtime default is 30;
    cross-cutting changes routinely consume half of that on inspection and
    checklist receipts before the first regression test is written.
    """

    if not is_code_mode or max_iterations < 15 or max_iterations >= 60:
        return max_iterations
    lowered = str(goal or "").lower()
    mutation_markers = (
        "implement",
        "change",
        "modify",
        "rename",
        "update",
        "create",
        "patch",
        "fix",
        "build",
        "migrate",
        "refactor",
        "实现",
        "修改",
        "改动",
        "重命名",
        "更新",
        "创建",
        "新增",
        "修复",
        "构建",
        "迁移",
        "重构",
    )
    return 60 if any(marker in lowered for marker in mutation_markers) else max_iterations
