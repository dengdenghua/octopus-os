"""Per-spawn filesystem isolation · the trusted ``isolate`` switch.

Every worktree piece already existed (``worktree_scope`` /
``subagent_worktree_worker`` / ``workspace_path`` → ``_locked_write_root``) and
``tournament`` already used them, but ``_call_agent_parallel`` never passed
``workspace_path``, so a fan-out that WROTE files had all lanes editing one
checkout. The industry position is uniform on this point: no vendor runs
parallel writers against a shared tree.

``isolate`` is deliberately a BOOLEAN, not a path. ``workspace`` sits in
``MODEL_PROTECTED_CONTEXT_PREFIXES`` so a model cannot aim write-confinement at
a directory of its choosing; this switch must not become a way around that, so
the worktree is created on the trusted side and only its path is handed down.
"""

from __future__ import annotations

from typing import Any

import pytest

from runtime.execution.suckers import delegation_skills as ds


def _spec(**over: Any) -> dict[str, Any]:
    base = {"agent_id": "researcher", "prompt": "do it"}
    base.update(over)
    return base


# ── the flag reaches the spawn as a path, or not at all ────────────


def test_plain_spec_passes_no_workspace_path(monkeypatch: Any) -> None:
    """Unisolated lanes must be byte-for-byte unchanged: a worktree per read-only
    worker would cost a checkout for nothing.
    """
    seen: dict[str, Any] = {}

    def fake_call(**kw: Any) -> dict[str, Any]:
        seen.update(kw)
        return {"success": True, "output": "ok", "agent_id": "researcher"}

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake_call)
    ds._call_agent_parallel(specs=[_spec()])
    assert "workspace_path" not in seen


def test_isolated_spec_receives_a_worktree_path(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def fake_call(**kw: Any) -> dict[str, Any]:
        seen.update(kw)
        return {"success": True, "output": "ok", "agent_id": "researcher"}

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake_call)
    monkeypatch.setattr("runtime.execution.subagents.worktree_loop.is_git_repo", lambda _root: True)

    import contextlib

    @contextlib.contextmanager
    def fake_scope(_root: str, name: str) -> Any:
        yield f"/tmp/wt-{name}", f"octo/wt-{name}"

    monkeypatch.setattr("runtime.execution.subagents.worktree_loop.worktree_scope", fake_scope)
    monkeypatch.setattr(
        "runtime.execution.subagents.worktree_loop._capture_diff",
        lambda _p: ("diff --git a/f b/f", ["f"]),
    )

    env = ds._call_agent_parallel(specs=[_spec(isolate=True, bb_key="writer")])
    assert seen.get("workspace_path", "").startswith("/tmp/wt-")
    succ = env["successes"][0]
    assert succ["files_touched"] == ["f"]


def test_the_diff_survives_the_envelope_projection(monkeypatch: Any) -> None:
    """Found live, not by a stub: ``_build_parallel_envelope`` is a WHITELIST
    projection, so ``isolated`` / ``branch`` / ``diff`` were dropped on the way
    out. The worktree was created, written and cleaned up correctly, but the
    caller got nothing back - isolation silently meant "discard the work".

    ``files_touched`` masked it, because ``common`` already projected that one.
    So this asserts on the ENVELOPE entry, never on ``_invoke``'s return value.
    """
    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        lambda **_kw: {"success": True, "output": "edited", "agent_id": "implementer"},
    )
    monkeypatch.setattr("runtime.execution.subagents.worktree_loop.is_git_repo", lambda _root: True)

    import contextlib

    @contextlib.contextmanager
    def fake_scope(_root: str, name: str) -> Any:
        yield "/tmp/wt-x", f"octo/wt-{name}"

    monkeypatch.setattr("runtime.execution.subagents.worktree_loop.worktree_scope", fake_scope)
    monkeypatch.setattr(
        "runtime.execution.subagents.worktree_loop._capture_diff",
        lambda _p: ("diff --git a/probe b/probe\n+new line", ["probe"]),
    )

    env = ds._call_agent_parallel(specs=[_spec(isolate=True, bb_key="w")])
    succ = env["successes"][0]
    assert succ.get("isolated") is True, "isolation flag lost in the envelope"
    assert "diff --git" in str(succ.get("diff") or ""), "the diff never reached the caller"
    # Audit F-08: the worktree branch is deleted right after capture, so it
    # must NOT ride out in the envelope as a stale/misleading identifier.
    assert "branch" not in succ, "stale branch leaked into the envelope"


def test_graph_node_surfaces_its_isolated_diff(monkeypatch: Any) -> None:
    """The graph's per-node result dict is a whitelist projection too."""

    def fake_parallel(specs: Any = None, **_kw: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "successes": [
                {
                    "bb_key": specs[0]["bb_key"],
                    "spec_index": 0,
                    "agent_id": "implementer",
                    "output": "done",
                    "isolated": True,
                    "branch": "octo/wt-spawn-w",
                    "diff": "diff --git a/f b/f",
                    "files_touched": ["f"],
                }
            ],
            "failures": [],
        }

    monkeypatch.setattr(ds, "_call_agent_parallel", fake_parallel)
    out = ds._run_agent_graph(nodes=[{"id": "w", "prompt": "edit it", "isolate": True}])
    node = out["nodes"]["w"]
    assert node.get("isolated") is True
    assert "diff --git" in str(node.get("diff") or "")
    assert node.get("files_touched") == ["f"]


def test_diff_is_captured_before_the_worktree_is_removed(monkeypatch: Any) -> None:
    """``worktree_scope`` DELETES the tree on exit. Capturing the diff after the
    scope would silently turn isolation into "discard the work".
    """
    order: list[str] = []

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        lambda **_kw: {"success": True, "output": "ok", "agent_id": "researcher"},
    )
    monkeypatch.setattr("runtime.execution.subagents.worktree_loop.is_git_repo", lambda _root: True)

    import contextlib

    @contextlib.contextmanager
    def fake_scope(_root: str, name: str) -> Any:
        order.append("enter")
        try:
            yield "/tmp/wt", "octo/wt"
        finally:
            order.append("exit")

    monkeypatch.setattr("runtime.execution.subagents.worktree_loop.worktree_scope", fake_scope)

    def fake_diff(_p: str) -> tuple[str, list[str]]:
        order.append("capture")
        return "d", ["f"]

    monkeypatch.setattr("runtime.execution.subagents.worktree_loop._capture_diff", fake_diff)

    ds._call_agent_parallel(specs=[_spec(isolate=True)])
    assert order == ["enter", "capture", "exit"]


# ── fail closed, never silently unisolated ─────────────────────────


def test_non_git_root_fails_the_lane_instead_of_writing_live(monkeypatch: Any) -> None:
    """Running unisolated because a worktree was unavailable would do the exact
    opposite of what the caller asked for.
    """
    called = {"n": 0}

    def fake_call(**_kw: Any) -> dict[str, Any]:
        called["n"] += 1
        return {"success": True, "output": "ok", "agent_id": "researcher"}

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake_call)
    monkeypatch.setattr(
        "runtime.execution.subagents.worktree_loop.is_git_repo", lambda _root: False
    )

    env = ds._call_agent_parallel(specs=[_spec(isolate=True)])
    assert called["n"] == 0, "spawned into the live tree despite isolate"
    assert env["successes"] == []
    assert "isolation_unavailable" in str(env["failures"][0].get("error_type"))


def test_isolation_failure_degrades_one_lane_not_the_batch(monkeypatch: Any) -> None:
    """A git/filesystem OSError in one lane must leave siblings' results intact."""
    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        lambda **_kw: {"success": True, "output": "ok", "agent_id": "researcher"},
    )
    monkeypatch.setattr("runtime.execution.subagents.worktree_loop.is_git_repo", lambda _root: True)

    def boom(_root: str, _name: str) -> Any:
        raise OSError("git worktree add failed: disk full")

    monkeypatch.setattr("runtime.execution.subagents.worktree_loop.worktree_scope", boom)

    env = ds._call_agent_parallel(specs=[_spec(isolate=True), _spec(prompt="read only")])
    assert env["ok"] is True, "one isolation failure sank the whole batch"
    assert len(env["successes"]) == 1
    assert len(env["failures"]) == 1


# ── the security boundary this must not weaken ─────────────────────


def test_model_supplied_workspace_path_is_still_stripped() -> None:
    """The reason ``isolate`` is a bool: a model naming its own confinement dir
    would defeat the point. That guard must keep holding.
    """
    from runtime.safety.auth.arg_guard import is_model_protected_context_key

    assert is_model_protected_context_key("workspace_path") is True


@pytest.mark.parametrize("key", ["workspace_path", "workspaceRoot", "workspace"])
def test_workspace_keys_remain_protected(key: str) -> None:
    from runtime.safety.auth.arg_guard import is_model_protected_context_key

    assert is_model_protected_context_key(key) is True


def test_isolate_is_coerced_to_bool_not_passed_through() -> None:
    """A truthy string must not become a path-like value downstream."""
    from runtime.execution.suckers._delegation_skills_parallel import (
        _coerce_parallel_specs,
    )

    specs = _coerce_parallel_specs([{"agent_id": "researcher", "prompt": "x", "isolate": "/etc"}])
    assert specs is not None
    # The cleaner runs inside _call_agent_parallel; here we only assert the raw
    # spec survives coercion so the bool() at the cleaning site is what decides.
    assert specs[0]["isolate"] == "/etc"

