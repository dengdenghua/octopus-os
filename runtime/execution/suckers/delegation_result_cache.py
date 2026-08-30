"""Spawn-level content-hash result cache · resume a graph without respawning.

The identity of a spawn is what it was asked to do, not when it was asked:
``(agent_id, resolved prompt, model tier, context digest)``. Hash those and a
re-declared node that would redo identical work can replay the recorded result
instead of spending a spawn.

Scope is deliberately narrow:

* Activation is EXPLICIT. A cache exists only when a caller (today:
  ``call_agent_graph`` with a ``resume_token``) creates one and hands it to the
  spawn path. Nothing else - ``run_orchestration``'s rounds, votes, retries -
  changes behaviour, because re-sampling with a fresh model draw is sometimes
  the point of a repeated prompt, and the runtime cannot tell that case apart
  from the wasteful one. Only a caller who knows it wants determinism opts in.
* Storage is in-memory and token-scoped. A token is a namespace with a FIFO
  shelf life; process restart forgets everything (resume across restarts would
  need journal persistence and is a different feature). The token is generated
  on the trusted side - a model cannot pre-seed a cache because it cannot
  predict the token.

The one rule that must never bend: **only completed, non-empty results enter
the store.** A failure, an empty output, or an interrupted spawn is exactly the
work a resume exists to REDO; caching any of them would pin one bad run onto
every future resume with the same token.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

# Context keys that differ per invocation without changing the work: closures
# (unstable reprs), ambient stacks, routing decisions made per-call, and
# per-spawn bookkeeping. Stripped before hashing so a resumed node's key
# matches the original's.
_VOLATILE_CONTEXT_KEYS = frozenset(
    {
        "event_emitter",
        "react_stack",
        "subagent_route_decision",
        "subagent_session_id",
        "subagent_report_delivery",
        "subagent_source_path",
        "subagent_scope",
        "caller_thread_id",
        "timeout_s",
        "skill_policy_sources",
        "skill_policy_reason_map",
        "dynamic_skill_grant_note",
    }
)

_MAX_ENTRIES_PER_TOKEN = 256
_MAX_TOKENS = 128
# Audit F-10: a resume token is valid for this long (absolute, since
# creation); after that load_spawn_cache treats it as expired so stale
# results are never replayed.
_TOKEN_TTL_S = 24 * 60 * 60


def _digest_input_files(paths: Any) -> str:
    """Content digest of the declared input files (audit F-05).

    A node that reads external files must declare them via ``input_files``
    so editing the files invalidates the cache key — otherwise a resume
    would replay a result computed against stale file contents. Files are
    hashed by content (sha256), directories recursively; a missing path is
    a stable marker (missing == missing), and an unreadable path degrades
    to a marker rather than crashing the key computation. Relative paths
    resolve against the process cwd.
    """
    from pathlib import Path

    norm = sorted({str(x) for x in (paths or []) if x is not None})
    parts: list[str] = []
    for raw in norm:
        p = Path(raw)
        if not p.is_absolute():
            p = Path.cwd() / p
        parts.append(_digest_one_path(p))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _digest_one_path(p: Any) -> str:

    try:
        if p.is_file():
            try:
                data = p.read_bytes()
            except OSError:
                return f"{p}:unreadable"
            return f"{p}:sha256:{hashlib.sha256(data).hexdigest()}"
        if p.is_dir():
            entries: list[str] = []
            try:
                children = sorted(p.rglob("*"), key=lambda c: str(c))
            except OSError:
                return f"{p}:unreadable_dir"
            for child in children:
                try:
                    if child.is_file():
                        entries.append(_digest_one_path(child))
                except OSError:
                    entries.append(f"{child}:unreadable")
            return f"{p}:dir:{'|'.join(entries)}"
    except OSError:
        return f"{p}:unreadable"
    return f"{p}:missing"


def _digest_context(context: dict[str, Any] | None) -> str:
    """Stable digest of the context fields that shape the work.

    Volatile keys are dropped first; the rest is canonical JSON. A value no
    JSON encoder accepts falls back to ``repr`` so the digest still terminates -
    such a value is by definition not content the caller controls, and its
    inclusion can only make the key more specific, never less.
    """
    ctx = {k: v for k, v in (context or {}).items() if k not in _VOLATILE_CONTEXT_KEYS}
    try:
        blob = json.dumps(ctx, sort_keys=True, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):  # pragma: no cover - default=repr covers it
        blob = repr(sorted(ctx.items(), key=lambda kv: str(kv[0])))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_spawn_cache_key(
    *,
    agent_id: str,
    prompt: str,
    cheap: bool = False,
    context: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    input_files: Any = None,
) -> str:
    """Content hash identifying one spawn's work.

    ``extra`` carries identity-bearing fields the caller knows about but that
    don't live in the context - e.g. a node's ``output_schema``, which changes
    what a valid reply looks like even though the prompt is unchanged.

    ``input_files`` (audit F-05): paths whose CONTENT the spawn reads.
    Declaring them folds a content digest into the key, so editing an input
    file invalidates the cached result instead of replaying stale output.
    Nodes that read external files must declare them; undeclared reads are
    the caller's responsibility (the cache stays opt-in at the token level).
    """
    material = json.dumps(
        {
            "agent_id": str(agent_id),
            "prompt": str(prompt),
            "cheap": bool(cheap),
            "context": _digest_context(context),
            "extra": extra or {},
            "input_files": _digest_input_files(input_files),
        },
        sort_keys=True,
        ensure_ascii=False,
        default=repr,
    )
    return "spawn:v1:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


# What is stored per entry: the success payload a caller needs to replay the
# node, and nothing that describes THIS run's plumbing (spec_index, retry
# flags, route decisions - all of it would be a lie about the replayed run).
_SNAPSHOT_FIELDS = ("agent_id", "codename", "output", "parsed", "schema_ok")


@dataclass
class SpawnResultCache:
    """One token's replay store. Thread-safe: parallel lanes put concurrently."""

    token: str
    owner: str | None = None
    created_at: float = field(default_factory=time.time)
    _entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            hit = self._entries.get(key)
            return dict(hit) if hit is not None else None

    def put(self, key: str, result: dict[str, Any]) -> bool:
        """Store a result. Returns False (and stores nothing) unless the result
        is a completed, non-empty success - the rule this cache exists under.

        Completion is judged by the fields that are actually present on an
        envelope entry, NOT by a ``success`` flag. ``_build_parallel_envelope``
        drops that flag from ``successes`` (membership in the list IS the
        success signal), so requiring it here made every real spawn unstorable
        while hand-built test envelopes carrying ``success: True`` passed - the
        cache looked correct and cached nothing in production.

        So: an explicit ``success: False`` still rejects, a missing flag is
        treated as "the caller already classified this as a success", and the
        partial/round-cap/converged markers reject regardless - those describe a
        spawn that stopped early, which is exactly what a resume must redo.
        """
        if not isinstance(result, dict):
            return False
        if result.get("success") is False:
            return False
        if any(
            result.get(marker)
            for marker in ("partial", "round_cap_exceeded", "converged_early", "error")
        ):
            return False
        if not str(result.get("output") or "").strip():
            return False
        snapshot = {k: result[k] for k in _SNAPSHOT_FIELDS if k in result}
        snapshot["success"] = True
        snapshot["_cached_at"] = time.time()
        with self._lock:
            if len(self._entries) < _MAX_ENTRIES_PER_TOKEN or key in self._entries:
                self._entries[key] = snapshot
                return True
            _log.warning(
                "spawn cache %s at capacity (%d entries) — evicting nothing, "
                "refusing new entry (audit F-10)",
                self.token[:8],
                _MAX_ENTRIES_PER_TOKEN,
            )
        return False

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_TOKEN_STORE: dict[str, SpawnResultCache] = {}
_STORE_LOCK = threading.Lock()


def _ambient_owner() -> str | None:
    """Best-effort caller identity for owner validation (audit F-10): the
    ambient session's thread id when one is bound, else None (no binding)."""
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
        if sess is not None:
            return str(getattr(sess, "thread_id", "") or "").strip() or None
    except Exception:  # noqa: BLE001 — owner binding is best-effort
        pass
    return None


def create_spawn_cache(token: str = "", owner: str | None = None) -> SpawnResultCache:
    """Create (and register) a fresh cache. Token generated when omitted.

    ``owner`` (audit F-10) binds the cache to a caller identity; resumes from
    a different owner are rejected by :func:`load_spawn_cache`. Defaults to
    the ambient session's thread id.
    """
    tok = str(token or "").strip() or f"src-{secrets.token_urlsafe(9)}"
    cache = SpawnResultCache(token=tok, owner=owner if owner is not None else _ambient_owner())
    with _STORE_LOCK:
        while len(_TOKEN_STORE) >= _MAX_TOKENS:
            evicted = _TOKEN_STORE.pop(next(iter(_TOKEN_STORE)))
            _log.warning(
                "spawn cache store at capacity (%d tokens) — evicting oldest token %s (audit F-10)",
                _MAX_TOKENS,
                evicted.token[:8],
            )
        _TOKEN_STORE[tok] = cache
    return cache


def load_spawn_cache(token: str, owner: str | None = None) -> SpawnResultCache | None:
    """Look up a previously issued cache. ``None`` for unknown/expired tokens or
    an owner mismatch — the caller decides whether that is an error (a resume
    with a typo'd token should fail loud, not silently re-run everything the
    caller thought was cached).

    Audit F-10: a token older than ``_TOKEN_TTL_S`` is expired (dropped +
    logged), and a cache bound to an owner refuses callers with a different
    owner.
    """
    tok = str(token or "").strip()
    with _STORE_LOCK:
        cache = _TOKEN_STORE.get(tok)
        if cache is None:
            return None
        if cache.created_at and (time.time() - cache.created_at) > _TOKEN_TTL_S:
            _TOKEN_STORE.pop(tok, None)
            _log.warning("spawn cache token %s expired (TTL %ds) — dropped", tok[:8], _TOKEN_TTL_S)
            return None
        if cache.owner is not None:
            caller = owner if owner is not None else _ambient_owner()
            if caller != cache.owner:
                _log.warning(
                    "spawn cache token %s owner mismatch (cache owner %r, caller %r) — refused",
                    tok[:8],
                    cache.owner,
                    caller,
                )
                return None
        return cache


def reset_spawn_cache_store() -> None:
    """Test seam: drop every token. Production code never needs this."""
    with _STORE_LOCK:
        _TOKEN_STORE.clear()
