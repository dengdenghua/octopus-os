"""Graceful-degradation contract for ``call_agent_parallel``.

When some sub-agents in a parallel fan-out fail (timeout, transport,
crash) the ones that succeeded must still surface to the lead. The
return envelope reports ``ok``/``successes``/``failures``/``partial``
plus a ``[partial-degradation]`` note so the lead can decide whether
to synthesise from partial data or escalate.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _reset_runner_and_budget():
    """Isolate every test from module-level state in the bridge and
    the per-turn delegation counter."""
    from runtime.execution.subagents.bridge import (
        set_sub_agent_runner,
        set_subagent_registry,
    )
    from runtime.execution.suckers.delegation_budget import (
        _TURN_DELEGATIONS,
        _TURN_FAILED_FINGERPRINTS,
    )

    set_sub_agent_runner(None)
    set_subagent_registry(None)
    _TURN_DELEGATIONS.clear()
    _TURN_FAILED_FINGERPRINTS.clear()
    yield
    set_sub_agent_runner(None)
    set_subagent_registry(None)
    _TURN_DELEGATIONS.clear()
    _TURN_FAILED_FINGERPRINTS.clear()


def _patch_bridge(monkeypatch, scripted: dict[str, Any]):
    """Replace ``call_subagent`` so each (agent_id, prompt) returns a
    canned result. ``scripted`` maps prompt → result dict."""

    def _fake_call_subagent(agent_id="", prompt="", **_kw):
        canned = scripted.get(prompt)
        if canned is None:
            return {
                "agent_id": agent_id,
                "output": f"echo:{prompt}",
                "success": True,
                "error": None,
            }
        # Always stamp the agent_id so the test doesn't have to
        # repeat it in every canned entry.
        out = dict(canned)
        out.setdefault("agent_id", agent_id)
        return out

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        _fake_call_subagent,
    )
    monkeypatch.setattr(
        "runtime.execution.subagents.bridge.call_subagent",
        _fake_call_subagent,
    )


def _specs(*prompts: str) -> list[dict[str, str]]:
    return [{"agent_id": "researcher", "prompt": p} for p in prompts]


def _ok(output: str) -> dict[str, Any]:
    return {"output": output, "success": True, "error": None}


def _fail(error: str, error_type: str | None = None) -> dict[str, Any]:
    r: dict[str, Any] = {"output": "", "success": False, "error": error}
    if error_type is not None:
        r["error_type"] = error_type
    return r


# ─────────────────────────────────────────────────────────────────────


def test_all_three_succeed(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    _patch_bridge(
        monkeypatch,
        {
            "p1": _ok("o1"),
            "p2": _ok("o2"),
            "p3": _ok("o3"),
        },
    )

    r = _call_agent_parallel(specs=_specs("p1", "p2", "p3"))

    assert r["ok"] is True
    assert r["partial"] is False
    assert r["success_count"] == 3
    assert r["total"] == 3
    assert r["failures"] == []
    assert r["notes"] == []
    assert len(r["successes"]) == 3
    assert {s["output"] for s in r["successes"]} == {"o1", "o2", "o3"}


def test_specs_json_string_is_accepted(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    _patch_bridge(
        monkeypatch,
        {
            "p1": _ok("o1"),
            "p2": _ok("o2"),
        },
    )

    r = _call_agent_parallel(specs=json.dumps(_specs("p1", "p2")))

    assert r["ok"] is True
    assert r["success_count"] == 2
    assert r["total"] == 2
    assert {s["output"] for s in r["successes"]} == {"o1", "o2"}


def test_specs_dict_wrapper_is_accepted(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    _patch_bridge(
        monkeypatch,
        {
            "p1": _ok("o1"),
            "p2": _ok("o2"),
        },
    )

    r = _call_agent_parallel(specs={"agents": _specs("p1", "p2")})

    assert r["ok"] is True
    assert r["success_count"] == 2
    assert r["total"] == 2


def test_parallel_timeout_string_is_coerced(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    captured: list[Any] = []

    def _fake_call_subagent(agent_id="", prompt="", **kw):
        captured.append(kw.get("timeout_s"))
        return {
            "agent_id": agent_id,
            "output": f"echo:{prompt}",
            "success": True,
            "error": None,
        }

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        _fake_call_subagent,
    )

    r = _call_agent_parallel(specs=_specs("p1"), timeout_s="600")

    assert r["ok"] is True
    assert captured == [600]


def test_parallel_spec_carries_dynamic_skill_grants(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    captured: list[dict[str, Any] | None] = []

    def _fake_call_subagent(agent_id="", prompt="", **kw):
        captured.append(kw.get("context"))
        return {
            "agent_id": agent_id,
            "output": f"echo:{prompt}",
            "success": True,
            "error": None,
        }

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        _fake_call_subagent,
    )

    r = _call_agent_parallel(
        specs=[
            {
                "agent_id": "researcher",
                "prompt": "Compare two vendors",
                "skill_packs": ["research", "files"],
                "skills": ["query_skill"],
                "plugins": ["browser"],
            }
        ]
    )

    assert r["ok"] is True
    assert len(captured) == 1
    ctx = captured[0] or {}
    grants = ctx.get("extra_tool_allowlist")
    assert "web_search" in grants
    assert "fetch_url" in grants
    assert "glob_files" in grants
    assert "query_skill" in grants
    assert "browser_state" in grants
    assert ctx["skill_pack_names"] == ["research", "files"]
    assert ctx["plugin_grants"] == ["browser"]


def test_agent_name_task_shape_is_accepted(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    seen: list[tuple[str, str]] = []

    def _fake_call_subagent(agent_id="", prompt="", **_kw):
        seen.append((agent_id, prompt))
        return {
            "agent_id": agent_id,
            "output": prompt,
            "success": True,
            "error": None,
        }

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        _fake_call_subagent,
    )

    r = _call_agent_parallel(
        specs=[
            {"agent_name": "Agent A", "task": "Task A"},
            {"agent_name": "Agent B", "task": "Task B"},
        ]
    )

    assert r["ok"] is True
    assert r["success_count"] == 2
    assert len(seen) == 2
    assert {agent_id for agent_id, _prompt in seen} == {"explorer"}
    assert any("Agent A" in prompt and "Task A" in prompt for _agent_id, prompt in seen)
    assert any("Agent B" in prompt and "Task B" in prompt for _agent_id, prompt in seen)


def test_prompt_only_spec_defaults_to_researcher(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    seen: list[str] = []

    def _fake_call_subagent(agent_id="", prompt="", **_kw):
        seen.append(agent_id)
        return {
            "agent_id": agent_id,
            "output": prompt,
            "success": True,
            "error": None,
        }

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        _fake_call_subagent,
    )

    r = _call_agent_parallel(specs=[{"instruction": "Summarize risk"}])

    assert r["ok"] is True
    assert seen == ["researcher"]


def test_all_three_fail(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    _patch_bridge(
        monkeypatch,
        {
            "p1": _fail("TimeoutError: subagent timed out", "timeout"),
            "p2": _fail("ConnectionError: refused", "transport"),
            "p3": _fail("RuntimeError: boom"),
        },
    )

    r = _call_agent_parallel(specs=_specs("p1", "p2", "p3"))

    assert r["ok"] is False
    assert r["partial"] is False  # only partial when SOME succeed
    assert r["success_count"] == 0
    assert r["successes"] == []
    assert len(r["failures"]) == 3
    assert r["total"] == 3


def test_two_of_three_succeed_marks_partial(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    _patch_bridge(
        monkeypatch,
        {
            "p1": _ok("alpha"),
            "p2": _fail("TimeoutError: subagent timed out after 30s", "timeout"),
            "p3": _ok("gamma"),
        },
    )

    r = _call_agent_parallel(specs=_specs("p1", "p2", "p3"))

    assert r["ok"] is True
    assert r["partial"] is True
    assert r["success_count"] == 2
    assert r["total"] == 3
    assert len(r["successes"]) == 2
    assert len(r["failures"]) == 1
    assert r["failures"][0]["error_type"] == "timeout"
    assert r["notes"], "expected a degradation note"
    note = r["notes"][0]
    assert "partial-degradation" in note
    assert "2/3" in note
    assert "timeout" in note


def test_one_of_three_succeeds(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    _patch_bridge(
        monkeypatch,
        {
            "p1": _ok("only-one"),
            "p2": _fail("ConnectionError: refused", "transport"),
            "p3": _fail("TimeoutError: timed out", "timeout"),
        },
    )

    r = _call_agent_parallel(specs=_specs("p1", "p2", "p3"))

    assert r["ok"] is True
    assert r["partial"] is True
    assert r["success_count"] == 1
    assert r["total"] == 3
    assert {f["error_type"] for f in r["failures"]} == {"transport", "timeout"}


def test_backward_compat_outputs_field_present(monkeypatch):
    """Legacy callers may read ``outputs`` (list of successful output
    strings, in result order). Keep emitting it."""
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    _patch_bridge(
        monkeypatch,
        {
            "a": _ok("A"),
            "b": _fail("boom"),
            "c": _ok("C"),
        },
    )

    r = _call_agent_parallel(specs=_specs("a", "b", "c"))

    assert "outputs" in r
    assert isinstance(r["outputs"], list)
    # only successful outputs, in completion order
    assert sorted(r["outputs"]) == ["A", "C"]
    # the legacy ``results`` / ``count`` shape is also still there
    assert "results" in r
    assert r["count"] == 3


def test_parallel_envelope_preserves_agent_telemetry_and_partial_output(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    _patch_bridge(
        monkeypatch,
        {
            "ok": {
                "agent_id": "researcher",
                "output": "finished synthesis",
                "success": True,
                "error": None,
                "iteration_count": 7,
                "duration_s": 12.5,
                "files_touched": ["reports/research.md"],
                "codename": "Spark-01",
                "avatar": ":search:",
            },
            "cap": {
                "agent_id": "reviewer",
                "output": "partial notes before cap",
                "success": False,
                "error": "ROUND_CAP_EXCEEDED",
                "error_type": "round_cap_exceeded",
                "round_cap_exceeded": True,
                "rounds_completed": 25,
            },
        },
    )

    r = _call_agent_parallel(
        specs=[
            {"agent_id": "researcher", "prompt": "ok"},
            {"agent_id": "reviewer", "prompt": "cap"},
        ]
    )

    assert r["ok"] is True
    assert r["partial"] is True
    assert r["successes"][0]["iteration_count"] == 7
    assert r["successes"][0]["duration_s"] == 12.5
    assert r["successes"][0]["files_touched"] == ["reports/research.md"]
    assert r["successes"][0]["codename"] == "Spark-01"
    assert r["failures"][0]["round_cap_exceeded"] is True
    assert r["failures"][0]["rounds_completed"] == 25
    assert r["failures"][0]["partial_output"] == "partial notes before cap"
    assert r["partial_outputs"] == [
        {
            "agent_id": "reviewer",
            "spec_index": 1,
            "task_label": "reviewer",
            "error": "ROUND_CAP_EXCEEDED",
            "error_type": "round_cap_exceeded",
            "output": "partial notes before cap",
        }
    ]


def test_error_type_passes_through_from_bridge(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    _patch_bridge(
        monkeypatch,
        {
            "p1": _fail("blew up", "custom_explosion"),
            "p2": _fail("network kaput", "transport"),
            "p3": _ok("kept-going"),
        },
    )

    r = _call_agent_parallel(specs=_specs("p1", "p2", "p3"))

    types = {f["agent_id"]: f["error_type"] for f in r["failures"]}
    # All failures came from the same agent_id ("researcher") in this
    # test setup — match by error string instead.
    by_error = {f["error"]: f["error_type"] for f in r["failures"]}
    assert by_error["blew up"] == "custom_explosion"
    assert by_error["network kaput"] == "transport"
    # touch ``types`` so the linter doesn't complain about an unused var
    assert types  # just non-empty


def test_notes_wording_lists_count_and_reasons(monkeypatch):
    """The synthetic note must mention success_count/total and the
    deduplicated set of failure reasons."""
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    _patch_bridge(
        monkeypatch,
        {
            "p1": _ok("ok-1"),
            "p2": _fail("TimeoutError: timed out", "timeout"),
            "p3": _fail("ConnectionError: refused", "transport"),
            "p4": _fail("TimeoutError: another timeout", "timeout"),
        },
    )

    r = _call_agent_parallel(specs=_specs("p1", "p2", "p3", "p4"))

    assert r["partial"] is True
    assert len(r["notes"]) == 1
    note = r["notes"][0]
    # 1/4 sub-agents completed; 3 failed (reasons: timeout, transport)
    pattern = re.compile(
        r"\[partial-degradation\] 1/4 sub-agents completed; "
        r"3 failed \(reasons: [a-z_, ]+\)"
    )
    assert pattern.search(note), f"note did not match expected shape: {note!r}"
    # both unique reasons must appear, dedup'd
    assert "timeout" in note
    assert "transport" in note
    # the note tells the agent what to do
    assert "Synthesise" in note or "synthesise" in note.lower()
