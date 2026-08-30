"""System-prompt size budget tests.

Polish guard: keep ``REACT_SYSTEM_PROMPT_BASE`` small so it cache-hits
on every turn regardless of mode-conditional appends. Mode-specific
content lives in conditional ``system_parts.append`` blocks in
react_loop, NOT in the base.

If you add genuinely-needed content to the base, raise the budget in
this test deliberately and document why.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_types import REACT_SYSTEM_PROMPT_BASE

# Anthropic prompt-cache hit requires a stable prefix; keeping the
# base small means a) cache stays warm regardless of mode, b) short
# chat turns don't pay for code-mode-specific prose. Current observed
# size is around 1700 chars; budget gives room to grow but flags
# bloat early.
BASE_PROMPT_BUDGET_CHARS = 2400


def test_base_prompt_within_budget() -> None:
    size = len(REACT_SYSTEM_PROMPT_BASE)
    assert size <= BASE_PROMPT_BUDGET_CHARS, (
        f"REACT_SYSTEM_PROMPT_BASE grew to {size} chars (budget "
        f"{BASE_PROMPT_BUDGET_CHARS}). Either move new content into "
        "a mode-conditional system_parts.append block in react_loop, "
        "or raise the budget here deliberately and document why."
    )


def test_base_prompt_has_no_mode_specific_sections() -> None:
    """Mode-specific sections (code mode, swarm mode, goal mode,
    research mode) should be conditional appends in react_loop, not
    in the base. The base is shared across every turn and any
    mode-specific text wastes prompt cache + tokens for the wrong
    turns.
    """
    forbidden = [
        # code-mode three-phase rules
        "理解阶段",
        "执行阶段",
        "验证阶段",
        # research/swarm bumps
        "max_iter 60",
        "max_iter 100",
        # planning_mode-specific instructions
        "PLANNING MODE",
    ]
    for marker in forbidden:
        assert marker not in REACT_SYSTEM_PROMPT_BASE, (
            f"Base prompt contains mode-specific marker {marker!r}; "
            "move it into the appropriate conditional append in "
            "runtime/core/cerebrum/react_loop.py."
        )


def test_base_prompt_keeps_protocol_essentials() -> None:
    """The base MUST contain the wire-protocol primitives the parser
    relies on; without these the model can't drive ReAct correctly."""
    essentials = [
        "Thought:",
        "Action:",
        "Observation:",
        "Final Answer:",
        "skill_name(",
    ]
    for marker in essentials:
        assert marker in REACT_SYSTEM_PROMPT_BASE, (
            f"Base prompt missing protocol essential {marker!r} — "
            "the ReAct parser depends on the model knowing this format."
        )
