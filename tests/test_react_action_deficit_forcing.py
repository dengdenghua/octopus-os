"""A prose-only round must force a tool call on the next decode.

Regression cover for trn_c2fbddce247b4164 / trn_2f015724ea194bfd: both turns
produced zero commandExecution items across several rounds while the model
narrated "I'll inspect X next", then terminated via the guard impasse. Prompt
reminders are advice; ``require_tool_use`` removes prose-only as an option for
one round, which is the only intervention the model cannot talk itself out of.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_loop_state import _LoopState
from runtime.core.cerebrum.react_phase_6c import _next_zero_action_rounds
from runtime.core.cerebrum.react_types import ReActStep
from runtime.platform.models.llm import Message, ModelRequest, ToolSpec


def _step(**kw: object) -> ReActStep:
    kw.setdefault("iteration", 1)
    kw.setdefault("thought", "t")
    return ReActStep(**kw)  # type: ignore[arg-type]


def test_state_defaults_to_no_deficit() -> None:
    assert _LoopState().zero_action_rounds == 0


def test_request_defaults_to_not_forcing() -> None:
    req = ModelRequest(model="m", messages=[Message(role="user", content="hi")])
    assert req.require_tool_use is False


def test_openai_router_maps_forcing_to_tool_choice_required() -> None:
    """The field only matters if it reaches the wire as tool_choice."""
    import inspect

    from runtime.sensing.model_router import openai_router

    src = inspect.getsource(openai_router)
    assert '"required" if request.require_tool_use else "auto"' in src


def test_forcing_condition_requires_a_prior_zero_action_round() -> None:
    """Mirrors the predicate used when building the loop's ModelRequest."""

    def forces(*, native: bool, converging: object, recovery: bool, deficit: int) -> bool:
        return native and converging is None and not recovery and deficit > 0

    # The observed failure: native, not converging, one prose-only round.
    assert forces(native=True, converging=None, recovery=False, deficit=1) is True
    # First round of a turn must stay unconstrained.
    assert forces(native=True, converging=None, recovery=False, deficit=0) is False
    # A closing round is supposed to be prose.
    assert forces(native=True, converging=True, recovery=False, deficit=3) is False
    # Length-limit recovery has its own targeted prompt; do not double up.
    assert forces(native=True, converging=None, recovery=True, deficit=3) is False
    # Text-protocol models have no tool_choice to set.
    assert forces(native=False, converging=None, recovery=False, deficit=3) is False


def test_prose_only_round_increments_the_deficit() -> None:
    assert _next_zero_action_rounds(0, step=None, maybe_final=None, final_answer_emitted=False) == 1
    assert _next_zero_action_rounds(2, step=None, maybe_final=None, final_answer_emitted=False) == 3


def test_executed_action_resets_the_deficit() -> None:
    step = _step(action='read_file({"path": "x"})')
    assert _next_zero_action_rounds(3, step=step, maybe_final=None, final_answer_emitted=False) == 0


def test_failed_action_still_resets_the_deficit() -> None:
    """Engaging with a tool and failing is not an action deficit."""
    step = _step(action='edit_file({"old": "a"})', observation="(参数校验失败)")
    assert _next_zero_action_rounds(2, step=step, maybe_final=None, final_answer_emitted=False) == 0


def test_blank_action_counts_as_no_action() -> None:
    for blank in ("", "   ", "\n"):
        step = _step(action=blank)
        got = _next_zero_action_rounds(1, step=step, maybe_final=None, final_answer_emitted=False)
        assert got == 2, repr(blank)


def test_concluding_the_turn_resets_the_deficit() -> None:
    """A genuine prose answer must never be punished as a deficit."""
    assert (
        _next_zero_action_rounds(4, step=None, maybe_final="done", final_answer_emitted=False) == 0
    )
    assert _next_zero_action_rounds(4, step=None, maybe_final=None, final_answer_emitted=True) == 0


def test_request_accepts_forcing_with_tools() -> None:
    req = ModelRequest(
        model="m",
        messages=[Message(role="user", content="fix it")],
        tools=[ToolSpec(name="edit_file", description="edit", input_schema={"type": "object"})],
        require_tool_use=True,
    )
    assert req.require_tool_use is True
    assert req.tools[0].name == "edit_file"


# ── zero-action protocol reminder ──────────────────────────────────────────


def test_plain_narration_gets_the_protocol_reminder() -> None:
    """GLM-5.3 shape: intent prose with no Update: line and no Action anchor.

    Regression for the "光说不做" turns (e.g. tI7UmTEB5PEOz7YZNtOMWQ,
    2026-08-17 01:26Z): iter 1 narrated "我来查一下…", got no corrective
    observation (the old nudge required an Update: line), repeated the
    narration on iter 2, and the turn ended with zero tool executions.
    """
    from runtime.core.cerebrum.react_phase_6c import _zero_action_protocol_reminder

    step = _step(action="", public_update="", thought="我来查一下工作区状态和最近提交")
    reminder = _zero_action_protocol_reminder(step, consecutive_format_violations=1)
    assert reminder is not None
    assert reminder.startswith("[protocol-reminder]")
    assert "Action:" in reminder
    assert "narrated" in reminder


def test_update_only_round_keeps_its_reminder() -> None:
    """Kimi K3 shape: Update published, Action missing."""
    from runtime.core.cerebrum.react_phase_6c import _zero_action_protocol_reminder

    step = _step(action="", public_update="正在检查 git 状态")
    reminder = _zero_action_protocol_reminder(step, consecutive_format_violations=1)
    assert reminder is not None
    assert "published an Update" in reminder


def test_no_reminder_for_first_round_or_acted_steps() -> None:
    """Round 0 narration is normal prelude; acted steps need no reminder."""
    from runtime.core.cerebrum.react_phase_6c import _zero_action_protocol_reminder

    # No violation counted yet: narrating before any strike stays unjudged.
    assert _zero_action_protocol_reminder(_step(action=""), 0) is None
    # An executed action needs no reminder.
    acted = _step(action='read_file({"path": "x"})')
    assert _zero_action_protocol_reminder(acted, 2) is None
    # An existing observation (another guard already spoke) is not overwritten.
    observed = _step(action="", observation="[other-guard] already injected")
    assert _zero_action_protocol_reminder(observed, 1) is None

