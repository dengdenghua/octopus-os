"""Core types for the ReAct final-answer guard registry.

Extracted from ``react_guards.py`` (Wave 3, cluster 0) so that guard
clusters living below the registry (e.g. browser guards that take a
``GuardContext``) can depend on these types without importing the
registry module itself. Leaf module: depends only on dataclasses /
collections.abc / react_types — must never import react_guards.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from runtime.core.cerebrum.react_types import ReActStep


@dataclass
class GuardContext:
    """Everything a guard might need to evaluate a candidate final answer.

    Bundles the trajectory, the proposed final answer, and the loop-level
    flags that previously gated each guard inline (is_code_mode, the
    todo-protocol visibility pair, tool-availability flags, and the goal
    string for the inspection guards).
    """

    steps: list[ReActStep]
    final_answer: str
    is_code_mode: bool
    todo_protocol_required: bool = False
    todo_protocol_visible: bool = False
    file_inspection_tools_visible: bool = False
    tools_active: bool = False
    goal: str = ""
    browser_operation_mode: bool = False
    grounded_source_paths: frozenset[str] = frozenset()
    model: str = ""  # New: model name for model-aware guard routing
    # Tool observations fetched in EARLIER turns of the same thread (as
    # ``Observation:`` user messages in the assembled conversation history).
    # Research guards (fact/citation grounding) merge this into the evidence
    # stream so a figure sourced in a previous turn and reused here isn't
    # falsely flagged as fabricated — the guard must police fabrication, not
    # multi-turn research synthesis.
    prior_grounding_text: str = ""
    # Execution-environment health signal, computed live from the trajectory
    # (≥2 environmental tool failures — sandbox/network denials the model
    # cannot fix by retrying). When True, execution-evidence repair guards
    # downgrade to advisory so a degraded environment can't three-strike a
    # turn that physically cannot produce the required evidence.
    execution_degraded: bool = False


@dataclass(frozen=True)
class GuardSpec:
    """One registry entry: a guard plus its metadata.

    * ``label`` — the bracketed tag shown to the model ("secret-leak guard").
    * ``category`` — coarse grouping for telemetry / future enable flags
      ("security" / "verification" / "test-quality" / "code-smell" / "protocol").
    * ``invoke`` — takes a GuardContext, returns a guard message or None.
    * ``enabled`` — soft switch; disabled specs are skipped entirely.
    """

    label: str
    category: str
    invoke: Callable[[GuardContext], str | None]
    enabled: bool = True


def _spec_code_mode(
    label: str,
    category: str,
    fn: Callable[..., str | None],
) -> GuardSpec:
    """Build a GuardSpec for the common A-class guard signature
    ``fn(steps, final_answer, *, is_code_mode)`` that only runs in
    code mode."""

    def _invoke(ctx: GuardContext) -> str | None:
        if not ctx.is_code_mode:
            return None
        return fn(ctx.steps, ctx.final_answer, is_code_mode=ctx.is_code_mode)

    return GuardSpec(label=label, category=category, invoke=_invoke)


def _spec_security(
    label: str,
    category: str,
    fn: Callable[..., str | None],
) -> GuardSpec:
    """Build a GuardSpec for security gates that must run in every mode."""

    def _invoke(ctx: GuardContext) -> str | None:
        return fn(ctx.steps, ctx.final_answer, is_code_mode=ctx.is_code_mode)

    return GuardSpec(label=label, category=category, invoke=_invoke)
