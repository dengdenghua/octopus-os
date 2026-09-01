"""Verifier isolation · context-starved and read-only.

A voter exists to reach a verdict the lead cannot reach alone. Two things defeat
that, and each has its own test group here:

* **Caller context.** A voter that can read the lead's conversation, inherited
  memory, or its own prior turns is being handed the conclusion it was spawned
  to check. The ballot already carries the question and the artifact.
* **Write access.** A ballot returns a verdict, so a judge holding
  ``exec_shell`` / ``edit_file`` / ``git_commit`` can only do damage, and one
  that "fixes" what it was assessing has destroyed the independence being paid
  for. ``bb_write`` counts: a voter publishing to the turn blackboard can steer
  its fellow voters.

The gate lives in ``_call_agent_vote`` rather than at each caller because every
verifier lane funnels through it — ``run_orchestration``'s verify,
``verdict_repair``'s judge, and ``tournament``'s panel — so one switch covers
all three and a future caller inherits it.
"""

from __future__ import annotations

from typing import Any

import pytest

from runtime.execution.suckers import delegation_skills as ds


def _capture_vote_specs(monkeypatch: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake(specs: Any = None, context: Any = None, **_kw: Any) -> dict[str, Any]:
        seen["specs"] = list(specs or [])
        seen["context"] = context
        return {"ok": True, "successes": [], "failures": []}

    monkeypatch.setattr(ds, "_call_agent_parallel", fake)
    return seen


# ── the two flags reach the spawn ─────────────────────────────────


def test_vote_marks_its_lane_read_only(monkeypatch: Any) -> None:
    seen = _capture_vote_specs(monkeypatch)
    ds._call_agent_vote(question="is this real?", n=3)
    assert seen["context"]["tool_allowlist_read_only"] is True


def test_vote_starves_its_voters_of_caller_context(monkeypatch: Any) -> None:
    seen = _capture_vote_specs(monkeypatch)
    ds._call_agent_vote(question="is this real?", n=3)
    assert seen["context"]["subagent_policy_starve_context"] is True
    assert seen["context"]["share_history"] is False


def test_caller_context_is_preserved_not_replaced(monkeypatch: Any) -> None:
    """The judge context is additive: dropping the caller's routing/session
    state would break the spawn, not harden it.
    """
    seen = _capture_vote_specs(monkeypatch)
    ds._call_agent_vote(question="q", n=3, context={"model_name": "pinned-model"})
    assert seen["context"]["model_name"] == "pinned-model"


def test_a_model_cannot_clear_the_judge_flags() -> None:
    """Both keys must canonicalise into MODEL_PROTECTED_CONTEXT_PREFIXES, or a
    spawned agent could hand back a context that re-opens write access.
    """
    from runtime.safety.auth.arg_guard import is_model_protected_context_key

    assert is_model_protected_context_key("tool_allowlist_read_only") is True
    assert is_model_protected_context_key("subagent_policy_starve_context") is True


# ── read-only actually filters the tool surface ───────────────────


def _specs(*names: str) -> list[Any]:
    class _Spec:
        def __init__(self, name: str) -> None:
            self.name = name

    return [_Spec(n) for n in names]


def test_read_only_drops_shell_from_a_role_allowlist() -> None:
    """``debugger`` is in the voter rotation and its role allowlist carries
    ``exec_shell``. Without the intersection a voter could run shell.
    """
    from runtime.execution.suckers.layers import select_tool_specs

    all_specs = _specs("read_file", "exec_shell", "grep_text")
    out = {s.name for s in select_tool_specs(("read_file", "exec_shell", "grep_text"), all_specs)}
    assert "exec_shell" in out

    ro = {
        s.name
        for s in select_tool_specs(
            ("read_file", "exec_shell", "grep_text"), all_specs, read_only=True
        )
    }
    assert ro == {"read_file", "grep_text"}


def test_read_only_drops_bb_write_even_though_it_is_auto_added() -> None:
    """``select_tool_specs`` tops every lane up with ``bb_*``. A voter that can
    publish to the board its fellow voters read is not an independent voter, so
    the read-only pass must run AFTER that top-up.
    """
    from runtime.execution.suckers.layers import select_tool_specs

    all_specs = _specs("read_file", "bb_read", "bb_write", "bb_keys")
    plain = {s.name for s in select_tool_specs(("read_file",), all_specs)}
    assert "bb_write" in plain, "precondition: bb_* is auto-added"

    ro = {s.name for s in select_tool_specs(("read_file",), all_specs, read_only=True)}
    assert "bb_write" not in ro
    assert {"read_file", "bb_read", "bb_keys"} <= ro


def test_read_only_narrows_the_atomic_inherit_set() -> None:
    """``architect`` declares no allowlist, so it inherits ATOMIC_SKILL_NAMES —
    which includes memory writes like ``remember`` / ``diary_write``.
    """
    from runtime.execution.suckers.layers import select_tool_specs

    all_specs = _specs("read_file", "remember", "diary_write", "update_soul", "todo_write")
    plain = {s.name for s in select_tool_specs((), all_specs)}
    assert "remember" in plain, "precondition: memory writes are atomic"

    ro = {s.name for s in select_tool_specs((), all_specs, read_only=True)}
    assert ro == {"read_file"}


@pytest.mark.parametrize(
    "name",
    [
        "exec_shell",
        "write_text_file",
        "edit_file",
        "multi_edit_file",
        "append_text_file",
        "delete_file",
        "git_commit",
        "git_push",
        "git_checkout",
        "git_add",
        "propose_patch",
        "bb_write",
        "remember",
        "diary_write",
        "update_soul",
        "todo_write",
        "background_exec",
        "ipython",
        "call_agent",
        "call_agent_parallel",
    ],
)
def test_no_write_class_skill_is_read_only(name: str) -> None:
    """The allowlist is hand-maintained, so this is the regression net: adding a
    write skill to it must fail here rather than silently arm a judge.
    """
    from runtime.execution.suckers.layers import is_read_only_skill

    assert is_read_only_skill(name) is False, f"{name} must not be judge-reachable"


def test_read_only_still_leaves_a_judge_able_to_check_a_claim() -> None:
    """A verifier with no tools would just agree with whatever it was shown."""
    from runtime.execution.suckers.layers import is_read_only_skill

    for name in ("read_file", "grep_text", "glob_files", "git_diff", "bb_read"):
        assert is_read_only_skill(name) is True


# ── starvation actually removes the text from the prompt ───────────


class _FakeSession:
    thread_id = "t-1"

    def __init__(self) -> None:
        self.metadata = {
            "recent_messages": [
                {"type": "human", "content": "please confirm the bug is real"},
                {"type": "ai", "content": "LEAD-REASONING-MARKER: I think it is real"},
            ]
        }


def _role(**over: Any) -> Any:
    from runtime.execution.suckers.ephemeral_agents import EphemeralRoleDef

    base: dict[str, Any] = {
        "id": "reviewer",
        "display_name": "R",
        "description": "d",
        "system_prompt": "you judge",
        "share_context": True,
        "share_memory": True,
    }
    base.update(over)
    return EphemeralRoleDef(**base)


def test_starved_prompt_omits_the_leads_reasoning() -> None:
    from runtime.execution.suckers.ephemeral_agents import _compose_system_prompt

    session = _FakeSession()
    plain = _compose_system_prompt(_role(), session, context={})
    assert "LEAD-REASONING-MARKER" in plain, "precondition: context is normally shared"

    starved = _compose_system_prompt(
        _role(), session, context={"subagent_policy_starve_context": True}
    )
    assert "LEAD-REASONING-MARKER" not in starved
    assert "Caller conversation" not in starved


def test_starvation_overrides_the_role_definition() -> None:
    """A role whose own definition shares context must not re-open the door —
    the voter rotation picks roles by name, so the flag has to win.
    """
    from runtime.execution.suckers.ephemeral_agents import _compose_system_prompt

    starved = _compose_system_prompt(
        _role(share_context=True, share_memory=True),
        _FakeSession(),
        context={"subagent_policy_starve_context": True},
    )
    assert "Caller conversation" not in starved


def test_starvation_does_not_remove_the_roles_own_instructions() -> None:
    """Starving the judge of the lead's context must not starve it of its job."""
    from runtime.execution.suckers.ephemeral_agents import _compose_system_prompt

    starved = _compose_system_prompt(
        _role(system_prompt="JUDGE-INSTRUCTIONS"),
        _FakeSession(),
        context={"subagent_policy_starve_context": True},
    )
    assert "JUDGE-INSTRUCTIONS" in starved


# ── the advertised list matches what the runner will hand over ─────


def test_read_only_narrows_the_advertised_grant_note() -> None:
    """An agent told it has ``exec_shell`` and then handed no such tool burns a
    round finding out. The note and the enforcement must agree.
    """
    from runtime.execution.suckers import ephemeral_agents as ea

    captured: dict[str, Any] = {}

    def fake_runner(call: Any) -> str:
        captured["allowlist"] = list(call.context.get("tool_allowlist") or [])
        return "VERDICT: keep"

    prior = ea.get_ephemeral_role_runner()
    ea.set_ephemeral_role_runner(fake_runner)
    try:
        ea.run_ephemeral_definition(
            _role(tool_allowlist=("read_file", "exec_shell", "grep_text")),
            "judge it",
            context={"tool_allowlist_read_only": True},
        )
    finally:
        ea.set_ephemeral_role_runner(prior)

    assert "exec_shell" not in captured["allowlist"]
    assert "read_file" in captured["allowlist"]


def test_allow_all_does_not_hand_a_judge_the_whole_catalog() -> None:
    """``tool_allowlist_mode: all`` is the widest grant in the system. A
    read-only lane must collapse it to a concrete read-only set, not honour it.
    """
    from runtime.execution.suckers import ephemeral_agents as ea
    from runtime.execution.suckers.layers import is_read_only_skill

    captured: dict[str, Any] = {}

    def fake_runner(call: Any) -> str:
        captured["allowlist"] = list(call.context.get("tool_allowlist") or [])
        return "VERDICT: keep"

    prior = ea.get_ephemeral_role_runner()
    ea.set_ephemeral_role_runner(fake_runner)
    try:
        ea.run_ephemeral_definition(
            _role(tool_allowlist=()),
            "judge it",
            context={"tool_allowlist_read_only": True, "tool_allowlist_mode": "all"},
        )
    finally:
        ea.set_ephemeral_role_runner(prior)

    granted = captured["allowlist"]
    assert granted, "an empty list would mean 'atomic inherit', re-widening the lane"
    assert all(is_read_only_skill(n) for n in granted)

