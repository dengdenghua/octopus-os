"""
blackboard_skills · expose the turn-scoped shared dict as 3 skills.

Sister to ``memory_skills`` but different scope:
- ``remember`` / ``recall`` are CROSS-TURN per-agent memory (disk
  files in ``agents/<id>/agent-core/MEMORY.md``).
- ``bb_read`` / ``bb_write`` / ``bb_keys`` are WITHIN-TURN shared
  memory between lead + concurrent sub-agents (in-process dict).

The blackboard is the substrate that makes ``call_agent_parallel``
useful — without it, parallel sub-agents are mini silos. With it,
sub-agent A can drop a partial finding under key ``"competitor_a"``
and sub-agent B (running concurrently or right after) can see it
via ``bb_read("competitor_a")``.

All three skills resolve the active turn via ``current_session()``.
When invoked outside a Session (raw unit test), they return a clear
"no turn scope" error rather than silently dropping data.
"""

from __future__ import annotations

from typing import Any

from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase

# Max serialized value size the blackboard will accept. The board is meant
# for small coordination facts (decisions, status, owner); larger payloads
# must go to file artifacts and be referenced by path.
_MAX_BB_VALUE_BYTES: int = 8 * 1024


def _resolve_scope_id() -> str | None:
    """Resolve the blackboard's coordination scope.

    Precedence:
    1. ``blackboard_root_turn_id`` — the parent's turn id a threaded child
       carries so it keeps sharing the parent's board (the event bus uses the
       separate ``root_thread_id`` lineage root; conflating the two would
       split the board).
    2. ``root_thread_id`` — legacy threaded path that scopes the whole lineage
       under one root.
    3. ``turn_id`` — the existing turn-scoped behaviour for the normal
       shared-turn path.
    """
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
        if sess is None:
            return None
        meta = getattr(sess, "metadata", None) or {}
        if isinstance(meta, dict):
            for key in ("blackboard_root_turn_id", "root_thread_id"):
                val = meta.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        return getattr(sess, "turn_id", None)
    except Exception:  # noqa: BLE001
        return None


def _bb_write(
    key: str = "",
    value: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Write a value to the shared blackboard for this turn."""
    if not key:
        return {"ok": False, "error": "key is required"}
    turn_id = _resolve_scope_id()
    if not turn_id:
        return {
            "ok": False,
            "error": "no Session/scope active · blackboard is coordination-scoped",
        }
    from runtime.memory.runtime_state.blackboard import get_blackboard

    # Volume gate: the board is for small coordination facts. Large payloads
    # must go to file artifacts and be referenced by path (Kimi-style context
    # sharding / Claude-style file artifacts).
    size = _value_size(value)
    if size > _MAX_BB_VALUE_BYTES:
        return {
            "ok": False,
            "error": (
                f"value too large for blackboard ({size} bytes > "
                f"{_MAX_BB_VALUE_BYTES}) · use bb_save to store it as a file "
                f"artifact and reference the path instead"
            ),
            "key": key,
            "size_bytes": size,
        }

    bb = get_blackboard(turn_id)
    if bb is None:
        return {"ok": False, "error": "blackboard unavailable"}
    writer = _resolve_writer_id()
    allowed, reason = bb.can_write(key, writer)
    if not allowed:
        return {
            "ok": False,
            "key": key,
            "error": f"write denied · {reason}",
        }
    bb.write(key, value, writer=writer)
    audit = bb.audit()
    return {
        "ok": True,
        "key": key,
        "stored_at_turn": turn_id[:8],
        "writer": writer,
        "overwrite_count": audit.get("overwrite_count", 0),
        "contested": key in set(audit.get("contested_keys", [])),
    }


def _value_size(value: Any) -> int:
    import json as _json

    try:
        return len(_json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(value))


def _bb_read(key: str = "", **_kw: Any) -> dict[str, Any]:
    """Read a value from the shared blackboard for this turn."""
    if not key:
        return {"ok": False, "error": "key is required"}
    turn_id = _resolve_scope_id()
    if not turn_id:
        return {
            "ok": False,
            "error": "no Session/scope active · blackboard is coordination-scoped",
        }
    from runtime.memory.runtime_state.blackboard import get_blackboard

    bb = get_blackboard(turn_id)
    if bb is None:
        return {"ok": False, "found": False, "error": "blackboard unavailable"}
    val = bb.read(key, default=None)
    if val is None:
        return {"ok": True, "found": False, "key": key, "value": None}
    return {"ok": True, "found": True, "key": key, "value": val}


def _bb_keys(**_kw: Any) -> dict[str, Any]:
    """List all keys currently on the blackboard."""
    turn_id = _resolve_scope_id()
    if not turn_id:
        return {
            "ok": False,
            "keys": [],
            "count": 0,
            "error": "no Session/turn active · blackboard is turn-scoped",
        }
    from runtime.memory.runtime_state.blackboard import get_blackboard

    bb = get_blackboard(turn_id)
    if bb is None:
        return {
            "ok": False,
            "keys": [],
            "count": 0,
            "error": "blackboard unavailable",
        }
    audit = bb.audit()
    # Include claimed-but-not-yet-written slots so coordination slots are
    # visible to siblings from the moment they are reserved.
    keys = sorted(set(bb.keys()) | set(audit.get("claimed_keys", {})))
    return {"ok": True, "keys": keys, "count": len(keys), "audit": audit}


def _bb_claim(key: str = "", **_kw: Any) -> dict[str, Any]:
    """Claim a blackboard slot for the current writer."""
    if not key:
        return {"ok": False, "error": "key is required"}
    scope = _resolve_scope_id()
    if not scope:
        return {"ok": False, "error": "no Session/scope active · blackboard is coordination-scoped"}
    from runtime.memory.runtime_state.blackboard import get_blackboard

    bb = get_blackboard(scope)
    if bb is None:
        return {"ok": False, "error": "blackboard unavailable"}
    writer = _resolve_writer_id()
    ok, reason = bb.claim(key, writer)
    return {
        "ok": ok,
        "key": key,
        "writer": writer,
        "error": reason or None,
    }


def _bb_pin(key: str = "", **_kw: Any) -> dict[str, Any]:
    """Seal a blackboard key as an immutable snapshot. Writes are rejected."""
    if not key:
        return {"ok": False, "error": "key is required"}
    scope = _resolve_scope_id()
    if not scope:
        return {"ok": False, "error": "no Session/scope active · blackboard is coordination-scoped"}
    from runtime.memory.runtime_state.blackboard import get_blackboard

    bb = get_blackboard(scope)
    if bb is None:
        return {"ok": False, "error": "blackboard unavailable"}
    if bb.read(key, default=None) is None:
        return {"ok": False, "key": key, "error": "key not found · pin an existing value"}
    ok, reason = bb.pin(key)
    return {
        "ok": ok,
        "key": key,
        "error": reason or None,
    }


def _bb_save(key: str = "", value: Any = None, **_kw: Any) -> dict[str, Any]:
    """Persist a large value as a file artifact and return a reference."""
    import json as _json

    if not key:
        return {"ok": False, "error": "key is required"}
    scope = _resolve_scope_id()
    if not scope:
        return {"ok": False, "error": "no Session/scope active · blackboard is coordination-scoped"}
    try:
        data = _json.dumps({"key": key, "value": value}, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"ok": False, "error": "value is not JSON-serializable"}
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
        meta = getattr(sess, "metadata", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        workspace = meta.get("workspace_path") or None
        sub_thread = meta.get("thread_id") or scope
    except Exception:  # noqa: BLE001
        workspace = None
        sub_thread = scope
    from runtime.execution.subagents.artifacts import save_artifact

    ref = save_artifact(
        data,
        name=f"{key}.json",
        workspace_path=workspace,
        root_thread_id=scope,
        sub_thread_id=sub_thread,
    )
    if not ref.get("ok"):
        return ref
    return {
        "ok": True,
        "key": key,
        "artifact": {
            "path": ref["path"],
            "hash": ref["hash"],
            "size": ref["size"],
        },
        "size_bytes": ref["size"],
        "note": "large payload stored as a file artifact · reference the path, not the content",
    }


def _resolve_writer_id() -> str | None:
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
        if sess is None:
            return None
        return (
            getattr(sess, "agent_id", None)
            or getattr(sess, "actor", None)
            or getattr(sess, "thread_id", None)
        )
    except Exception:  # noqa: BLE001
        return None


def register_blackboard_skills(registry: SkillRegistry) -> int:
    """Register bb_read / bb_write / bb_keys. Returns count."""
    registry.register(
        Skill(
            name="bb_write",
            description=(
                "Write a value to the shared blackboard. The blackboard is "
                "an in-memory key/value store scoped to the current turn — "
                "lead agent + all concurrent sub-agents share it. Use it to "
                "pass findings between parallel workers (e.g. "
                "`bb_write(key='competitor_a', value={...})`). "
                "Args: {key: string, value: any JSON-serializable}. "
                "DON'T use for things that should persist across turns — "
                "use `remember` for that."
            ),
            affinity=["blackboard", "shared_state", "multi_agent"],
            cost_profile="low",
            trusted_source="skill://public/bb_write",
            handler=_bb_write,
            tests=[
                SkillTestCase(
                    name="empty_key_returns_error",
                    tier="golden",
                    args={"key": "", "value": "x"},
                    expect=SkillExpect(schema_keys=["ok", "error"]),
                    custom_predicate=lambda r: isinstance(r, dict) and r.get("ok") is False,
                ),
            ],
        )
    )

    registry.register(
        Skill(
            name="bb_read",
            description=(
                "Read a value from the shared blackboard. Returns "
                "{ok, found, key, value}. `found=False` means no entry "
                "exists for that key yet. Use this to check what other "
                "concurrent sub-agents (or the lead) have written. "
                "Args: {key: string}."
            ),
            affinity=["blackboard", "shared_state", "multi_agent"],
            cost_profile="low",
            trusted_source="skill://public/bb_read",
            handler=_bb_read,
            tests=[
                SkillTestCase(
                    name="empty_key_returns_error",
                    tier="golden",
                    args={"key": ""},
                    expect=SkillExpect(schema_keys=["ok", "error"]),
                    custom_predicate=lambda r: isinstance(r, dict) and r.get("ok") is False,
                ),
            ],
        )
    )

    registry.register(
        Skill(
            name="bb_keys",
            description=(
                "List all keys currently on the shared blackboard. Useful "
                "when you don't know what your concurrent siblings have "
                "written yet — call `bb_keys()` first, then `bb_read(k)` "
                "on the interesting ones. Returns {ok, keys, count}."
            ),
            affinity=["blackboard", "shared_state", "multi_agent"],
            cost_profile="low",
            trusted_source="skill://public/bb_keys",
            handler=_bb_keys,
            tests=[
                SkillTestCase(
                    name="returns_keys_list",
                    tier="golden",
                    args={},
                    expect=SkillExpect(schema_keys=["ok", "keys", "count"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="bb_claim",
            description=(
                "Claim a blackboard slot for the current agent. Once claimed, "
                "only the claiming agent can write that key — siblings can "
                "still read it. Use to avoid two parallel sub-agents stomping "
                "each other's coordination slot (e.g. `bb_claim(key='owner')`). "
                "Args: {key: string}."
            ),
            affinity=["blackboard", "shared_state", "multi_agent"],
            cost_profile="low",
            trusted_source="skill://public/bb_claim",
            handler=_bb_claim,
            tests=[
                SkillTestCase(
                    name="empty_key_returns_error",
                    tier="golden",
                    args={"key": ""},
                    expect=SkillExpect(schema_keys=["ok", "error"]),
                    custom_predicate=lambda r: isinstance(r, dict) and r.get("ok") is False,
                ),
            ],
        )
    )

    registry.register(
        Skill(
            name="bb_pin",
            description=(
                "Seal a blackboard key as an immutable snapshot. After pinning, "
                "no further writes to that key are accepted. Use to lock a final "
                "decision so parallel workers can't overwrite it. "
                "Args: {key: string}."
            ),
            affinity=["blackboard", "shared_state", "multi_agent"],
            cost_profile="low",
            trusted_source="skill://public/bb_pin",
            handler=_bb_pin,
            tests=[
                SkillTestCase(
                    name="empty_key_returns_error",
                    tier="golden",
                    args={"key": ""},
                    expect=SkillExpect(schema_keys=["ok", "error"]),
                    custom_predicate=lambda r: isinstance(r, dict) and r.get("ok") is False,
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="bb_save",
            description=(
                "Persist a LARGE value as a file artifact and return a "
                "reference (path + hash). Use this instead of bb_write when "
                "the payload is bigger than a small coordination fact — the "
                "blackboard is for tiny facts, big payloads live in files. "
                "Args: {key: string, value: any JSON-serializable}."
            ),
            affinity=["blackboard", "artifacts", "multi_agent"],
            cost_profile="low",
            trusted_source="skill://public/bb_save",
            handler=_bb_save,
            tests=[
                SkillTestCase(
                    name="empty_key_returns_error",
                    tier="golden",
                    args={"key": ""},
                    expect=SkillExpect(schema_keys=["ok", "error"]),
                    custom_predicate=lambda r: isinstance(r, dict) and r.get("ok") is False,
                ),
            ],
        )
    )
    return 6


__all__ = ["register_blackboard_skills"]
