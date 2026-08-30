"""audit.deep orchestration bus — grant, scrub, and progress streaming.

Three wires make the preset real end to end:
1. ``ultracode_token_budget()`` — the SERVER-side spawn-budget grant.
2. ``_apply_orchestration_grant`` — the gateway scrubs any client-supplied
   ``orchestration_token_budget`` (spawn-budget escalation) and grants the
   server value only for the ``audit.deep`` preset.
3. ``orchestration_progress_scope`` — ``run_orchestration`` phase lines
   stream to the client as thinking deltas instead of one opaque blob.
"""

from __future__ import annotations

from typing import Any

import pytest

from runtime.execution.suckers import delegation_budget as db
from runtime.execution.suckers import delegation_skills as ds
from runtime.sensing.gateway.realtime_react_stream import _apply_orchestration_grant


@pytest.fixture(autouse=True)
def _reset_budget_state():
    from runtime.execution.subagents.bridge import (
        set_sub_agent_runner,
        set_subagent_registry,
    )

    db._TURN_DELEGATIONS.clear()
    db._TURN_FAILED_FINGERPRINTS.clear()
    set_sub_agent_runner(None)
    set_subagent_registry(None)
    yield
    db._TURN_DELEGATIONS.clear()
    db._TURN_FAILED_FINGERPRINTS.clear()
    set_sub_agent_runner(None)
    set_subagent_registry(None)


# ── ultracode_token_budget resolution chain ──────────────────────


def test_ultracode_budget_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_ULTRACODE_TOKEN_BUDGET", raising=False)
    monkeypatch.delenv("ECHO_ORCH_TOKEN_BUDGET", raising=False)
    assert db.ultracode_token_budget() == db.ULTRACODE_TOKEN_BUDGET_DEFAULT


def test_ultracode_budget_operator_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_ULTRACODE_TOKEN_BUDGET", raising=False)
    monkeypatch.setenv("ECHO_ORCH_TOKEN_BUDGET", "77000")
    assert db.ultracode_token_budget() == 77000


def test_ultracode_budget_preset_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_ULTRACODE_TOKEN_BUDGET", "550000")
    monkeypatch.setenv("ECHO_ORCH_TOKEN_BUDGET", "77000")
    assert db.ultracode_token_budget() == 550000


def test_ultracode_budget_invalid_env_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_ULTRACODE_TOKEN_BUDGET", "not-a-number")
    monkeypatch.delenv("ECHO_ORCH_TOKEN_BUDGET", raising=False)
    assert db.ultracode_token_budget() == db.ULTRACODE_TOKEN_BUDGET_DEFAULT


# ── gateway grant + client scrub ─────────────────────────────────


def test_grant_scrubs_client_supplied_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    # A client smuggling the trusted key is a spawn-budget escalation —
    # it must vanish even when no preset is active.
    meta: dict[str, Any] = {"orchestration_token_budget": 10_000_000}
    _apply_orchestration_grant(meta)
    assert "orchestration_token_budget" not in meta


def test_grant_applied_for_deep_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_ULTRACODE_TOKEN_BUDGET", raising=False)
    monkeypatch.delenv("ECHO_ORCH_TOKEN_BUDGET", raising=False)
    meta: dict[str, Any] = {
        "workflow_preset": "audit.deep",
        # client tries to pick its own number — the server value wins
        "orchestration_token_budget": 10_000_000,
    }
    _apply_orchestration_grant(meta)
    assert meta["orchestration_token_budget"] == db.ULTRACODE_TOKEN_BUDGET_DEFAULT


def test_no_grant_for_other_presets() -> None:
    meta: dict[str, Any] = {"workflow_preset": "plan.mode"}
    _apply_orchestration_grant(meta)
    assert "orchestration_token_budget" not in meta


# ── progress streaming ───────────────────────────────────────────


def _fake_parallel_seq(rounds_outputs: list[list[str]]):
    calls = {"i": 0}

    def fake(specs: Any = None, **_kw: Any) -> dict[str, Any]:
        idx = calls["i"]
        calls["i"] += 1
        outs = rounds_outputs[idx] if idx < len(rounds_outputs) else []
        return {
            "ok": True,
            "successes": [{"output": o, "agent_id": "researcher"} for o in outs],
            "failures": [],
            "success_count": len(outs),
        }

    return fake


def test_progress_lines_stream_through_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds, "_call_agent_parallel", _fake_parallel_seq([["a\nb"], ["c"]]))
    lines: list[str] = []
    with ds.orchestration_progress_scope(lines.append):
        result = ds._run_orchestration(goal="g", n=1, rounds=2, patience=2, verify=False)
    assert result["ok"] is True
    joined = "\n".join(lines)
    assert "[orchestration] start" in joined
    assert "round 1/2" in joined
    assert "round 2/2" in joined
    assert "[orchestration] done" in joined


def test_no_emitter_outside_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds, "_call_agent_parallel", _fake_parallel_seq([["a"]]))
    # No scope installed — must run exactly as before, no error, no leak.
    result = ds._run_orchestration(goal="g", n=1, rounds=1, patience=0, verify=False)
    assert result["ok"] is True


def test_emitter_exception_never_breaks_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds, "_call_agent_parallel", _fake_parallel_seq([["a"]]))

    def _boom(_line: str) -> None:
        raise RuntimeError("emitter crashed")

    with ds.orchestration_progress_scope(_boom):
        result = ds._run_orchestration(goal="g", n=1, rounds=1, patience=0, verify=False)
    assert result["ok"] is True


# ── the operating contract in the preset prompt ──────────────────
#
# The bus wires above make fan-out POSSIBLE; the prompt is what makes it
# HAPPEN. An earlier revision phrased the trigger conditionally ("when
# independent sub-problems exist, fan out"), which left the call to a model
# that reliably judged its own task not to qualify — so the preset read as
# deep-thinking advice and produced single-agent runs on a runtime whose
# ceiling was in the hundreds. These pin the parts that carry that behaviour.


def _deep_prompt() -> str:
    from runtime.core.cerebrum._react_context_code import (
        _build_workflow_preset_prompt,
    )

    return _build_workflow_preset_prompt("audit.deep")


def test_deep_prompt_makes_orchestration_the_default() -> None:
    prompt = _deep_prompt()

    assert "默认就要编排" in prompt
    # The bar must be inverted: not "is this worth fanning out" but "is this
    # trivial enough to skip it".
    assert "这琐碎到不配扇出吗" in prompt
    assert "run_orchestration" in prompt


def test_deep_prompt_names_widths_the_code_actually_honours() -> None:
    """The prompt may not advertise a width the clamp silently eats.

    ``_run_orchestration`` clamps n to 1-6 and rounds to 1-5, so telling the
    model to pass n=12 would produce a value quietly reduced to 6 — the model
    would believe it fanned out twice as wide as it did.
    """
    prompt = _deep_prompt()

    assert "上限 6" in prompt, "must state the real n ceiling"
    assert "8-16" not in prompt, "must not advertise a width the clamp eats"
    # Over-wide work is covered by chaining orchestrations, not by a bigger n.
    assert "多次串联编排" in prompt


def test_deep_prompt_forbids_deferring_fan_out_behind_a_reading_phase() -> None:
    """The phase wording must not license an unbounded solo understanding phase.

    Measured, not theorised. A live run (deepseek-v4-flash, 30 iterations,
    ``run_orchestration`` present in a 102-tool spec) spent all 25 tool calls
    on atomic reads and spawned ZERO sub-agents while naming fan-out 13 times
    in its own reasoning. The deciding line it produced was a paraphrase of
    this very prompt: "按照深度指南，我应该先理解再扇出" — the earlier
    phase wording ("理解→设计→实现→审查") read as permission to finish
    understanding alone first, and understanding a real codebase never
    finishes inside one turn's budget. After this clause landed, the same
    configuration called ``run_orchestration(n=5, rounds=3)`` at iteration 4
    and produced 49 spawns.
    """
    prompt = _deep_prompt()

    assert "第一次编排就发生在理解阶段" in prompt
    # The excuse the model actually used has to be named as forbidden, not
    # merely left unmentioned.
    assert "先自己通读一遍再扇出" in prompt
    assert "当成禁止项" in prompt
    # And the reason the deferral is unnecessary: workers read files themselves.
    assert "子代理自带工具" in prompt


def test_deep_prompt_keeps_the_spawn_ceiling_operator_owned() -> None:
    """The preset steers WHAT and HOW WIDE, never how many spawns are allowed:
    a client picking the preset must not be able to raise its own ceiling."""
    prompt = _deep_prompt()

    assert "不自行抬高 spawn 上限" in prompt


def test_deep_prompt_degrades_when_skill_is_gated_out() -> None:
    prompt = _deep_prompt()

    assert "被网关裁掉" in prompt
    assert "不要去调一个不存在的工具" in prompt


def test_other_presets_do_not_inherit_the_fan_out_mandate() -> None:
    from runtime.core.cerebrum._react_context_code import (
        _build_workflow_preset_prompt as build,
    )

    for preset in (
        "plan.mode",
        "spec.mode",
        "goal.mode",
        "develop.iterate",
        "audit.review",
        "uxui.regression",
    ):
        body = build(preset)
        assert body, f"{preset} must still render a contract"
        assert "默认就要编排" not in body, f"{preset} must not mandate fan-out"
    assert build("nope") == ""


# ── the delegation block must not contradict the preset ──────────
#
# The preset prompt is only half the story. ``<agent-auto-delegation-guidance>``
# lands ~14 lines BELOW it in the same system prompt, and its default text used
# to open with "Current mode is single-agent Agent/ReAct", tell the model that
# simple or sequential work should be done alone, and cap fan-out at "exactly
# one call_agent_parallel batch for the current turn". A live run proved the
# nearest concrete instruction wins: preset present, tool in the spec, zero
# spawns. These pin the deep variant, and pin that the default variant is
# unchanged for every other turn.


class _PresetOnlyState:
    """The builder reads exactly one field, so don't fabricate the other 15.

    ``_AssemblyState`` needs a whole react turn's worth of required args
    (intent, stack, executor, budgets...). Constructing all of them here would
    couple this test to fields the function never touches.
    """

    def __init__(self, preset: str | None) -> None:
        self.workflow_preset_value = preset


def _delegation_block(preset: str | None) -> str:
    from runtime.core.cerebrum._react_prompt_assembly_guidance import (
        _build_auto_delegation_guidance,
    )

    return _build_auto_delegation_guidance(_PresetOnlyState(preset))  # type: ignore[arg-type]


def test_delegation_block_does_not_call_deep_single_agent() -> None:
    body = _delegation_block("audit.deep")

    assert "single agent" in body or "NOT a single agent" in body
    assert "Current mode is single-agent" not in body
    # The one-batch cap directly contradicts "chain multiple orchestrations".
    assert "exactly one" not in body
    # And the preset above stays authoritative on width.
    assert "authoritative" in body


def test_delegation_block_forbids_reading_first_under_deep() -> None:
    body = _delegation_block("audit.deep")

    assert "Fan out DURING the understanding phase" in body
    assert "Workers carry their own tools" in body
    # "do it yourself for simple work" is the default-variant licence that let
    # the model defer indefinitely; under deep mode it must be the exception.
    assert "Simple or sequential work: do it yourself" not in body


def test_default_delegation_block_is_unchanged_for_other_turns() -> None:
    for preset in (None, "", "audit.review", "plan.mode", "develop.iterate"):
        body = _delegation_block(preset)
        assert "Current mode is single-agent Agent/ReAct" in body, preset
        assert "Simple or sequential work: do it yourself" in body, preset
        assert "exactly one `call_agent_parallel` batch" in body, preset
        assert "NOT a single agent" not in body, preset


def test_both_delegation_variants_keep_the_synthesis_hygiene() -> None:
    """Widening the fan-out must not drop the "you still own the answer" rules."""
    for preset in ("audit.deep", "audit.review"):
        body = _delegation_block(preset)
        assert "synthesize the outputs" in body, preset
        assert "Never finish with raw worker logs" in body, preset
        assert "</agent-auto-delegation-guidance>" in body, preset

