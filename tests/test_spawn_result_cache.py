"""Spawn-level content-hash resume for ``call_agent_graph``.

The identity of a spawn is what it was asked to do - ``(agent_id, resolved
prompt, model tier, context digest)`` - hashed so an unchanged node replays
its recorded result instead of respawning.

Two rules the tests pin hardest:

* **Only completed, non-empty results are ever stored.** A failure, an empty
  output, or a never-finished spawn is exactly the work a resume exists to
  redo; caching any of them would pin one bad run onto every future resume.
* **Downstream identity is inherited through the resolved prompt.** A node's
  prompt embeds its upstream outputs, so a changed upstream shifts every
  dependent's key - the diamond below asserts the propagation explicitly.
"""

from __future__ import annotations

from typing import Any

import pytest

from runtime.execution.suckers import delegation_skills as ds
from runtime.execution.suckers.delegation_result_cache import (
    SpawnResultCache,
    compute_spawn_cache_key,
    reset_spawn_cache_store,
)

_DIAMOND = [
    {"id": "l", "prompt": "left facts"},
    {"id": "r", "prompt": "right facts"},
    {
        "id": "fold",
        "prompt": "reconcile L with R: {l.output} || {r.output}",
        "depends_on": ["l", "r"],
    },
]


class _FakeParallel:
    """Counts spawns; returns one success per spec, output derived from prompt.

    Fail-on-demand: ``fail_ids`` / ``empty_ids`` match on bb_key (node id).
    """

    def __init__(self) -> None:
        self.spawned: list[dict[str, Any]] = []
        self.fail_ids: set[str] = set()
        self.empty_ids: set[str] = set()

    def __call__(self, specs: Any = None, **_kw: Any) -> dict[str, Any]:
        successes: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for i, s in enumerate(list(specs or [])):
            key = str(s.get("bb_key") or "")
            if key in self.fail_ids:
                failures.append(
                    {
                        "bb_key": key,
                        "spec_index": i,
                        "agent_id": s.get("agent_id"),
                        "output": "",
                        "success": False,
                        "error": "boom",
                    }
                )
                continue
            out = "" if key in self.empty_ids else f"OUT[{s['prompt']}]"
            successes.append(
                {
                    "bb_key": key,
                    "spec_index": i,
                    "agent_id": s.get("agent_id"),
                    "output": out,
                    "success": True,
                }
            )
            self.spawned.append(s)
        return {"ok": bool(successes), "successes": successes, "failures": failures}


@pytest.fixture(autouse=True)
def _clean_store():
    reset_spawn_cache_store()
    yield
    reset_spawn_cache_store()


# ── the headline acceptance: replay, exact-respawn, redo ───────────


def test_same_graph_and_token_respawns_nothing(monkeypatch: Any) -> None:
    fake = _FakeParallel()
    monkeypatch.setattr(ds, "_call_agent_parallel", fake)

    first = ds._run_agent_graph(nodes=_DIAMOND)
    token = first["resume_token"]
    assert token
    assert len(fake.spawned) == 3

    second = ds._run_agent_graph(nodes=_DIAMOND, resume_token=token)
    assert len(fake.spawned) == 3, "resume must not spawn for unchanged nodes"
    assert sorted(second["replayed"]) == ["fold", "l", "r"]
    assert second["nodes"]["fold"]["output"] == first["nodes"]["fold"]["output"]
    assert second["nodes"]["l"]["replayed"] is True


def test_changing_only_the_last_node_respawns_exactly_one(monkeypatch: Any) -> None:
    fake = _FakeParallel()
    monkeypatch.setattr(ds, "_call_agent_parallel", fake)

    token = ds._run_agent_graph(nodes=_DIAMOND)["resume_token"]
    changed = [dict(n) for n in _DIAMOND]
    changed[-1]["prompt"] = "reconcile differently: {l.output} || {r.output}"
    fake.spawned.clear()

    second = ds._run_agent_graph(nodes=changed, resume_token=token)
    assert len(fake.spawned) == 1, "only the changed node should respawn"
    assert second["replayed"] == ["l", "r"]
    assert "differently" in fake.spawned[-1]["prompt"]


def test_changed_upstream_forces_the_downstream_to_respawn(monkeypatch: Any) -> None:
    fake = _FakeParallel()
    monkeypatch.setattr(ds, "_call_agent_parallel", fake)

    token = ds._run_agent_graph(nodes=_DIAMOND)["resume_token"]
    changed = [dict(n) for n in _DIAMOND]
    changed[0]["prompt"] = "left facts, revised"
    fake.spawned.clear()

    second = ds._run_agent_graph(nodes=changed, resume_token=token)
    # l changed -> its key moves; fold embeds {l.output} -> its resolved prompt
    # moves too. Only r replays.
    assert sorted(second["replayed"]) == ["r"]
    assert len(fake.spawned) == 2


def test_replayed_outputs_feed_downstream_resolution(monkeypatch: Any) -> None:
    """Resume is not just bookkeeping: a replayed upstream's recorded output is
    what the respawned downstream's prompt is resolved against.
    """
    fake = _FakeParallel()
    monkeypatch.setattr(ds, "_call_agent_parallel", fake)

    first = ds._run_agent_graph(nodes=_DIAMOND)
    token = first["resume_token"]
    left_output = first["nodes"]["l"]["output"]
    changed = [dict(n) for n in _DIAMOND]
    changed[-1]["prompt"] = "fold v2: {l.output} || {r.output}"

    ds._run_agent_graph(nodes=changed, resume_token=token)
    assert left_output in fake.spawned[-1]["prompt"]


# ── nothing incomplete ever enters the store ───────────────────────


def test_failed_node_is_not_cached_and_reruns(monkeypatch: Any) -> None:
    fake = _FakeParallel()
    fake.fail_ids = {"l"}
    monkeypatch.setattr(ds, "_call_agent_parallel", fake)

    first = ds._run_agent_graph(nodes=_DIAMOND)
    assert first["nodes"]["l"]["ok"] is False
    token = first["resume_token"]

    fake.fail_ids.clear()
    fake.spawned.clear()
    second = ds._run_agent_graph(nodes=_DIAMOND, resume_token=token)
    # l failed before -> re-runs; r succeeded -> replays; fold was skipped
    # upstream-failure -> re-runs.
    assert sorted(second["replayed"]) == ["r"]
    assert {s["bb_key"] for s in fake.spawned} == {"fold", "l"}


def test_empty_success_is_not_cached_and_reruns(monkeypatch: Any) -> None:
    fake = _FakeParallel()
    fake.empty_ids = {"l"}
    monkeypatch.setattr(ds, "_call_agent_parallel", fake)

    token = ds._run_agent_graph(nodes=_DIAMOND)["resume_token"]
    fake.empty_ids.clear()
    fake.spawned.clear()

    second = ds._run_agent_graph(nodes=_DIAMOND, resume_token=token)
    assert "l" not in second["replayed"]
    assert any(s["bb_key"] == "l" for s in fake.spawned)


def test_put_refuses_failures_and_empty_output() -> None:
    cache = SpawnResultCache(token="t")
    assert cache.put("k", {"success": False, "output": "text"}) is False
    assert cache.put("k", {"success": True, "output": ""}) is False
    assert cache.put("k", {"success": True, "output": "  \n"}) is False
    assert cache.get("k") is None
    assert cache.put("k", {"success": True, "output": "real", "parsed": {"a": 1}}) is True
    hit = cache.get("k")
    assert hit is not None and hit["output"] == "real" and hit["parsed"] == {"a": 1}


def test_put_accepts_the_real_envelope_shape_without_a_success_flag() -> None:
    """Regression for a live-only defect: ``_build_parallel_envelope`` DROPS
    ``success`` from ``successes`` entries (membership in that list is the
    success signal). ``put`` used to require the flag, so every real spawn was
    unstorable while hand-built test envelopes carrying ``success: True``
    passed - the cache looked correct and cached nothing in production.
    """
    cache = SpawnResultCache(token="t")
    envelope_entry = {
        "agent_id": "researcher",
        "codename": "Scout",
        "output": "real finding",
        "bb_key": "a",
        "spec_index": 0,
        "duration_s": 1.2,
        "partial": False,
        "round_cap_exceeded": False,
        # no "success" key at all - this is the production shape
    }
    assert cache.put("k", envelope_entry) is True
    hit = cache.get("k")
    assert hit is not None and hit["output"] == "real finding"


@pytest.mark.parametrize("marker", ["partial", "round_cap_exceeded", "converged_early", "error"])
def test_put_refuses_a_spawn_that_stopped_early(marker: str) -> None:
    """A spawn that hit its round cap or converged incomplete produced text, but
    not FINISHED text. Caching it would pin a truncated answer onto every
    future resume - the exact thing a resume exists to redo.
    """
    cache = SpawnResultCache(token="t")
    entry = {"agent_id": "researcher", "output": "half an answer", marker: True}
    assert cache.put("k", entry) is False
    assert cache.get("k") is None


def test_real_parallel_path_stores_and_replays(monkeypatch: Any) -> None:
    """End-to-end through the REAL ``_call_agent_parallel``, stubbing only
    ``call_subagent``. The 12 tests above all stub the whole parallel call, so
    none of them exercise the envelope shape the cache is actually handed -
    which is how the production miss survived a green suite.
    """
    spawns = {"n": 0}

    def fake_sub(agent_id: str = "", prompt: str = "", **_kw: Any) -> dict[str, Any]:
        spawns["n"] += 1
        return {"agent_id": agent_id, "output": f"OUT-{prompt[:16]}", "success": True}

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake_sub)

    nodes = [
        {"id": "a", "prompt": "alpha work"},
        {"id": "b", "prompt": "use {a.output}", "depends_on": ["a"]},
    ]
    first = ds._run_agent_graph(nodes=nodes)
    assert spawns["n"] == 2

    spawns["n"] = 0
    second = ds._run_agent_graph(nodes=nodes, resume_token=first["resume_token"])
    assert spawns["n"] == 0, "real envelope did not reach the cache"
    assert sorted(second["replayed"]) == ["a", "b"]

    spawns["n"] = 0
    changed = [dict(nodes[0]), {**nodes[1], "prompt": "use {a.output} differently"}]
    third = ds._run_agent_graph(nodes=changed, resume_token=first["resume_token"])
    assert spawns["n"] == 1
    assert third["replayed"] == ["a"]


# ── token lifecycle ────────────────────────────────────────────────


def test_unknown_token_fails_loud_before_spawning(monkeypatch: Any) -> None:
    fake = _FakeParallel()
    monkeypatch.setattr(ds, "_call_agent_parallel", fake)
    out = ds._run_agent_graph(nodes=_DIAMOND, resume_token="typo-token")
    assert out["ok"] is False
    assert "resume_token" in (out.get("error") or "")
    assert fake.spawned == []


def test_two_tokens_do_not_share_entries(monkeypatch: Any) -> None:
    fake = _FakeParallel()
    monkeypatch.setattr(ds, "_call_agent_parallel", fake)

    ds._run_agent_graph(nodes=_DIAMOND)
    cold = ds._run_agent_graph(nodes=_DIAMOND)  # fresh token: cold run
    assert len(fake.spawned) == 6
    assert cold["replayed"] == []


# ── key hygiene ────────────────────────────────────────────────────


def test_key_is_stable_across_volatile_context_noise() -> None:
    base = {"model_name": "m1", "system_addendum": "focus"}
    noisy = {
        **base,
        "event_emitter": lambda _e: None,
        "react_stack": object(),
        "subagent_route_decision": {"action": "allow", "when": "now"},
    }
    assert compute_spawn_cache_key(
        agent_id="r", prompt="p", context=base
    ) == compute_spawn_cache_key(agent_id="r", prompt="p", context=noisy)


def test_key_moves_on_any_identity_bearing_input() -> None:
    k = compute_spawn_cache_key(agent_id="r", prompt="p", context={"model_name": "m"})
    assert k != compute_spawn_cache_key(agent_id="o", prompt="p", context={"model_name": "m"})
    assert k != compute_spawn_cache_key(agent_id="r", prompt="q", context={"model_name": "m"})
    assert k != compute_spawn_cache_key(agent_id="r", prompt="p", context={"model_name": "m2"})
    assert k != compute_spawn_cache_key(
        agent_id="r", prompt="p", cheap=True, context={"model_name": "m"}
    )
    assert k != compute_spawn_cache_key(
        agent_id="r", prompt="p", context={"model_name": "m"}, extra={"output_schema": {"a": 1}}
    )


def test_isolated_nodes_never_replay(monkeypatch: Any) -> None:
    """An isolated node's product is a diff against a worktree that is deleted
    on exit - replaying it would hand back a diff with no branch behind it.
    """
    fake = _FakeParallel()
    monkeypatch.setattr(ds, "_call_agent_parallel", fake)

    graph = [dict(n) for n in _DIAMOND]
    graph[0]["isolate"] = True
    token = ds._run_agent_graph(nodes=graph)["resume_token"]
    fake.spawned.clear()

    second = ds._run_agent_graph(nodes=graph, resume_token=token)
    assert "l" not in second["replayed"]
    assert any(s["bb_key"] == "l" for s in fake.spawned)


# ═══════════════════════════════════════════════════════════
# Audit F-05: declared input-file content folds into the cache key
# ═══════════════════════════════════════════════════════════


def test_key_moves_when_declared_input_file_changes(tmp_path: Any) -> None:
    f = tmp_path / "data.txt"
    f.write_text("v1", encoding="utf-8")
    k1 = compute_spawn_cache_key(agent_id="r", prompt="p", input_files=[str(f)])
    f.write_text("v2", encoding="utf-8")
    k2 = compute_spawn_cache_key(agent_id="r", prompt="p", input_files=[str(f)])
    assert k1 != k2  # modified input file invalidates the key


def test_key_stable_for_unchanged_declared_input(tmp_path: Any) -> None:
    f = tmp_path / "data.txt"
    f.write_text("v1", encoding="utf-8")
    k1 = compute_spawn_cache_key(agent_id="r", prompt="p", input_files=[str(f)])
    k2 = compute_spawn_cache_key(agent_id="r", prompt="p", input_files=[str(f)])
    assert k1 == k2


def test_key_without_input_files_is_unchanged_semantics() -> None:
    """Not declaring input_files keeps the old key shape (no digest)."""
    k1 = compute_spawn_cache_key(agent_id="r", prompt="p")
    k2 = compute_spawn_cache_key(agent_id="r", prompt="p")
    assert k1 == k2


def test_missing_and_unreadable_inputs_are_stable(tmp_path: Any) -> None:
    missing = tmp_path / "nope.txt"
    k1 = compute_spawn_cache_key(agent_id="r", prompt="p", input_files=[str(missing)])
    k2 = compute_spawn_cache_key(agent_id="r", prompt="p", input_files=[str(missing)])
    assert k1 == k2


def test_declared_directory_digest_tracks_content(tmp_path: Any) -> None:
    d = tmp_path / "inputs"
    d.mkdir()
    (d / "a.txt").write_text("a1", encoding="utf-8")
    k1 = compute_spawn_cache_key(agent_id="r", prompt="p", input_files=[str(d)])
    (d / "a.txt").write_text("a2", encoding="utf-8")
    k2 = compute_spawn_cache_key(agent_id="r", prompt="p", input_files=[str(d)])
    assert k1 != k2


# ═══════════════════════════════════════════════════════════
# Audit F-10: TTL, audible eviction, owner validation
# ═══════════════════════════════════════════════════════════


def test_expired_token_is_dropped() -> None:
    from runtime.execution.suckers.delegation_result_cache import (
        _TOKEN_TTL_S,
        create_spawn_cache,
        load_spawn_cache,
        reset_spawn_cache_store,
    )

    reset_spawn_cache_store()
    try:
        cache = create_spawn_cache(owner=None)
        assert load_spawn_cache(cache.token) is cache
        cache.created_at -= _TOKEN_TTL_S + 1  # backdate past the TTL
        assert load_spawn_cache(cache.token) is None  # expired -> invalidated
    finally:
        reset_spawn_cache_store()


def test_owner_validation() -> None:
    from runtime.execution.suckers.delegation_result_cache import (
        create_spawn_cache,
        load_spawn_cache,
        reset_spawn_cache_store,
    )

    reset_spawn_cache_store()
    try:
        cache = create_spawn_cache(owner="alice")
        assert load_spawn_cache(cache.token, owner="alice") is cache
        assert load_spawn_cache(cache.token, owner="bob") is None  # owner mismatch
    finally:
        reset_spawn_cache_store()


def test_store_capacity_evicts_oldest_token_audibly() -> None:
    from runtime.execution.suckers.delegation_result_cache import (
        _MAX_TOKENS,
        create_spawn_cache,
        load_spawn_cache,
        reset_spawn_cache_store,
    )

    reset_spawn_cache_store()
    try:
        first = create_spawn_cache(owner=None)
        for _ in range(_MAX_TOKENS):
            create_spawn_cache(owner=None)
        assert load_spawn_cache(first.token) is None  # oldest evicted (FIFO)
    finally:
        reset_spawn_cache_store()


def test_entry_capacity_refuses_and_keeps_old() -> None:
    from runtime.execution.suckers.delegation_result_cache import (
        _MAX_ENTRIES_PER_TOKEN,
        SpawnResultCache,
    )

    cache = SpawnResultCache(token="t-cap")
    result = {"success": True, "output": "x", "agent_id": "r"}
    for i in range(_MAX_ENTRIES_PER_TOKEN):
        assert cache.put(f"k{i}", result) is True
    # At capacity, a NEW key is refused but existing entries stay readable.
    assert cache.put("k-extra", result) is False
    assert cache.get("k0") is not None
    assert cache.get("k-extra") is None

