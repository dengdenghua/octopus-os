from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.platform.io import TransactionalFileError, path_transaction
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path

_LOG = logging.getLogger("echo.turn_scoring")

# Filename for mutable per-agent quality state. Deployment-owned profiles stay
# read-only; production runtimes root this under ECHO_DATA_DIR/ECHO_HOME.
_SCORES_FILENAME = ".scores.jsonl"

# Keep the file bounded · drop oldest lines when over this. 5000
# turns is well over a year of casual use, plenty for any
# correlation analysis we do.
_MAX_LINES_KEEP: int = 5000

_SAFE_AGENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SCOPE_PARTITION_RE = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(slots=True)
class TurnScore:
    """One scored turn · serialized to a single jsonl line."""

    ts: str  # ISO-8601 wall time
    agent_id: str
    score: float  # 0.0 / 0.5 / 1.0
    reason: str  # short tag (e.g. "success", "tool_errors",
    # "interrupted", "no_reply")
    soul_hash: str  # 8-char MD5 of SOUL.md at score time
    rounds: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    thread_id: str = ""
    turn_id: str = ""
    # Added in the tenant-aware format.  Empty values are the legacy,
    # ownership-free namespace and remain readable only without a scope (or
    # by an explicitly cross-tenant control-plane caller).
    tenant_id: str = ""
    owner_actor_id: str = ""


# ═══════════════════════════════════════════════════════════
# Scoring
# ═══════════════════════════════════════════════════════════


def score_turn_outcome(
    *,
    has_final_reply: bool,
    tool_error_count: int = 0,
    rounds_used: int = 0,
    rounds_max: int = 30,
    interrupted: bool = False,
    duration_ms: int = 0,
    timeout_ms: int | None = None,
) -> tuple[float, str]:
    """Pure scoring function · no I/O · easy to unit test.

    Returns ``(score, reason)`` where reason is a short tag
    summarizing the dominant signal.

    Decision rules (first match wins):
        - interrupted/cancelled        → 0.0  "interrupted"
        - no final reply               → 0.0  "no_reply"
        - exceeded timeout             → 0.0  "timeout"
        - hit max rounds without reply → 0.0  "round_cap"
        - had tool errors > 0          → 0.5  "tool_errors"
        - rounds >= 80% of max         → 0.5  "near_round_cap"
        - else                         → 1.0  "success"
    """
    if interrupted:
        return (0.0, "interrupted")
    if not has_final_reply:
        if rounds_used >= rounds_max:
            return (0.0, "round_cap")
        return (0.0, "no_reply")
    if timeout_ms is not None and duration_ms >= timeout_ms:
        return (0.0, "timeout")
    if tool_error_count > 0:
        return (0.5, "tool_errors")
    if rounds_max > 0 and rounds_used >= int(rounds_max * 0.8):
        return (0.5, "near_round_cap")
    return (1.0, "success")


def _project_root() -> Path:
    from runtime.platform.process.paths import project_root

    return project_root()


def _score_storage_root() -> Path:
    """Resolve mutable score state without writing into packaged resources."""

    if os.environ.get("ECHO_DATA_DIR") or os.environ.get("ECHO_HOME"):
        from runtime.platform.process.paths import app_paths

        return app_paths().data_dir
    # Preserve the source-checkout layout for local development and existing
    # installations that have not configured an explicit runtime data root.
    return _project_root()


def is_safe_agent_id(agent_id: Any) -> bool:
    """Return whether ``agent_id`` is safe as an exact storage component."""

    return bool(_SAFE_AGENT_ID_RE.fullmatch(str(agent_id or "")))


def _scores_path(agent_id: str, scope: TenantScope | None = None) -> Path:
    """Return the legacy or opaque tenant-partitioned score path."""

    if not is_safe_agent_id(agent_id):
        raise ValueError("unsafe agent id for turn-score storage")
    base = _score_storage_root() / "agents" / agent_id / "agent-core" / _SCORES_FILENAME
    if scope is None or scope.allow_cross_tenant:
        return base
    return tenant_scoped_path(base, scope)


def _score_paths_for_read(agent_id: str, scope: TenantScope | None) -> list[Path]:
    """Resolve score files without ever enumerating raw tenant identities."""

    base = _scores_path(agent_id)
    if scope is None:
        return [base]
    if not scope.allow_cross_tenant:
        return [_scores_path(agent_id, scope)]

    paths = [base]
    tenants_dir = base.parent / "tenants"
    try:
        partitions = sorted(tenants_dir.iterdir())
    except OSError:
        partitions = []
    for partition in partitions:
        if not _SCOPE_PARTITION_RE.fullmatch(partition.name):
            continue
        candidate = partition / base.name
        # Tenant score partitions are regular runtime-owned files.  Do not
        # follow a symlink planted in the data directory during an admin-wide
        # read.
        if candidate.is_file() and not candidate.is_symlink():
            paths.append(candidate)
    return paths


def _soul_hash(agent_id: str) -> str:
    """8-char MD5 of the agent's current SOUL.md (or empty)."""
    from runtime.execution.agents.loader import default_agents_root

    soul = default_agents_root() / agent_id / "agent-core" / "SOUL.md"
    if not soul.exists():
        return ""
    try:
        return hashlib.md5(soul.read_bytes(), usedforsecurity=False).hexdigest()[:8]
    except Exception:  # noqa: BLE001
        return ""


def record_turn_score(
    *,
    agent_id: str,
    score: float,
    reason: str,
    rounds: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: int = 0,
    thread_id: str = "",
    turn_id: str = "",
    scope: TenantScope | None = None,
) -> Path | None:
    """Append a TurnScore to the agent's `.scores.jsonl`. Returns the
    path written, or None if the agent_id is empty / disk failure.

    Wrapped in best-effort try/except — scoring is a side-feature,
    a write failure must NEVER kill the user's turn.
    """
    if not agent_id or not is_safe_agent_id(agent_id):
        return None
    if scope is not None and scope.allow_cross_tenant:
        _LOG.warning("record_turn_score rejected cross-tenant write for %s", agent_id)
        return None
    path = _scores_path(agent_id, scope)
    try:
        ts = TurnScore(
            # Microseconds preserve append order for the common case where
            # several workers finish turns inside the same wall-clock second.
            ts=datetime.now().isoformat(timespec="microseconds"),
            agent_id=agent_id,
            score=float(score),
            reason=reason,
            soul_hash=_soul_hash(agent_id),
            rounds=int(rounds),
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            duration_ms=int(duration_ms),
            thread_id=thread_id or "",
            turn_id=turn_id or "",
            tenant_id=scope.tenant_id if scope is not None else "",
            owner_actor_id=scope.actor_id if scope is not None else "",
        )
        line = (json.dumps(asdict(ts), ensure_ascii=False) + "\n").encode("utf-8")
        # The sidecar lock is stable across processes.  Keep append, fsync and
        # optional trim inside one transaction so a Uvicorn worker cannot
        # append to the inode another worker is concurrently replacing.
        with path_transaction(path) as target:
            if turn_id:
                idempotency = _turn_id_state_locked(target, ts)
                if idempotency == "same":
                    # A previous attempt may have landed the complete row but
                    # reported an uncertain fsync failure. Re-sync it before
                    # acknowledging the retry as successful.
                    _sync_score_file_locked(target)
                    return path
                if idempotency == "conflict":
                    _LOG.warning(
                        "record_turn_score rejected conflicting turn_id for %s",
                        agent_id,
                    )
                    return None
            _append_score_line_locked(target, line)
            _trim_if_oversized_locked(target)
    except (OSError, TransactionalFileError, ValueError) as exc:
        _LOG.warning(
            "record_turn_score failed for %s: %s",
            agent_id,
            exc,
        )
        return None
    return path


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write while persisting turn score")
        view = view[written:]


def _fsync_directory(directory: Path) -> None:
    """Durably commit a file creation/replacement on POSIX."""

    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(directory, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_score_line_locked(path: Path, line: bytes) -> None:
    """Append one complete JSONL record and fsync it.

    ``path_transaction(path)`` must be held by the caller.  If an older
    process crashed after a partial append, insert a newline before the new
    record.  Readers will skip that malformed record, while the new record
    remains independently parseable and ownership-checked.
    """

    existed = path.exists()
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        size = os.fstat(fd).st_size
        prefix = b""
        if size:
            os.lseek(fd, -1, os.SEEK_END)
            if os.read(fd, 1) != b"\n":
                prefix = b"\n"
        _write_all(fd, prefix + line)
        os.fsync(fd)
    finally:
        os.close(fd)
    if not existed:
        _fsync_directory(path.parent)


def _idempotency_fingerprint(row: dict[str, Any]) -> tuple[Any, ...] | None:
    """Normalize the immutable score payload, excluding writer timestamp."""

    try:
        return (
            str(row.get("agent_id") or ""),
            float(row.get("score", 0.0)),
            str(row.get("reason") or ""),
            int(row.get("rounds", 0) or 0),
            int(row.get("input_tokens", 0) or 0),
            int(row.get("output_tokens", 0) or 0),
            int(row.get("duration_ms", 0) or 0),
            str(row.get("thread_id") or ""),
            str(row.get("turn_id") or ""),
            str(row.get("tenant_id") or ""),
            str(row.get("owner_actor_id") or ""),
        )
    except (TypeError, ValueError):
        return None


def _turn_id_state_locked(path: Path, proposed: TurnScore) -> str:
    """Return ``none``, ``same`` or ``conflict`` for a non-empty turn ID.

    The caller holds ``path_transaction``.  This scan is intentionally on the
    durable JSONL evidence rather than a process cache, so retries racing in
    separate workers remain exactly-once.
    """

    try:
        rows = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return "none"
    wanted = proposed.turn_id
    proposed_fingerprint = _idempotency_fingerprint(asdict(proposed))
    for raw in reversed(rows):
        try:
            row = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict) or str(row.get("turn_id") or "") != wanted:
            continue
        return (
            "same"
            if proposed_fingerprint is not None
            and _idempotency_fingerprint(row) == proposed_fingerprint
            else "conflict"
        )
    return "none"


def _sync_score_file_locked(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(path.parent)


def _trim_if_oversized_locked(path: Path) -> None:
    """If file > MAX_LINES, rewrite keeping only the most recent
    ``_MAX_LINES_KEEP`` lines. Caller holds ``path_transaction(path)``."""
    try:
        # Cheap line count without holding the whole file in memory:
        # only count if > some threshold byte size to avoid stat-then-
        # noop on every write.
        if path.stat().st_size < (_MAX_LINES_KEEP * 200):
            return
        lines = path.read_bytes().splitlines(keepends=True)
        if len(lines) <= _MAX_LINES_KEEP:
            return
        keep = b"".join(lines[-_MAX_LINES_KEEP:])
        fd, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            _write_all(fd, keep)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.replace(temporary, path)
            path.chmod(0o600)
            _fsync_directory(path.parent)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            if fd >= 0:
                os.close(fd)
    except OSError as exc:
        # Unlike the historical opportunistic trim, surface durability
        # failures to ``record_turn_score``.  The user turn still succeeds,
        # but the caller receives ``None`` instead of believing the quality
        # evidence was durably committed.
        raise TransactionalFileError("turn-score trim failed") from exc


# ═══════════════════════════════════════════════════════════
# Read / aggregate
# ═══════════════════════════════════════════════════════════


def read_recent_scores(
    agent_id: str,
    limit: int = 50,
    *,
    scope: TenantScope | None = None,
) -> list[TurnScore]:
    """Return up to ``limit`` most recent TurnScore entries
    (newest first) visible to ``scope``.

    Compatibility is deliberately one-way: no scope reads only the legacy
    ownership-free file; a normal scope reads only its opaque partition and
    verifies each row's exact tenant/owner pair; an explicit cross-tenant
    scope may aggregate legacy and tenant partitions for the management
    control plane.
    """
    if not agent_id or not is_safe_agent_id(agent_id) or limit <= 0:
        return []
    out: list[TurnScore] = []
    for path in _score_paths_for_read(agent_id, scope):
        try:
            with path_transaction(path) as target, target.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except (OSError, TransactionalFileError):
            continue
        for raw in reversed(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                d = json.loads(raw)
                if not isinstance(d, dict) or not _score_row_visible(d, scope):
                    continue
                row = TurnScore(
                    ts=str(d.get("ts", "")),
                    agent_id=str(d.get("agent_id", "")),
                    score=float(d.get("score", 0.0)),
                    reason=str(d.get("reason", "")),
                    soul_hash=str(d.get("soul_hash", "")),
                    rounds=int(d.get("rounds", 0) or 0),
                    input_tokens=int(d.get("input_tokens", 0) or 0),
                    output_tokens=int(d.get("output_tokens", 0) or 0),
                    duration_ms=int(d.get("duration_ms", 0) or 0),
                    thread_id=str(d.get("thread_id", "")),
                    turn_id=str(d.get("turn_id", "")),
                    tenant_id=str(d.get("tenant_id", "")),
                    owner_actor_id=str(d.get("owner_actor_id", "")),
                )
                # A tenant partition must never smuggle another agent's row
                # into an agent-specific fitness calculation.
                if row.agent_id != agent_id:
                    continue
                out.append(row)
            except (TypeError, ValueError, json.JSONDecodeError):
                # A crashed legacy append may leave one truncated/corrupt
                # JSONL row.  Skip only that row; every surviving row is still
                # exact-agent and exact-tenant checked before it is returned.
                continue
    # Each file is append-ordered, but an explicit admin aggregation spans
    # independently written files. ISO timestamps sort chronologically for
    # the writer's canonical format; the stable secondary keys make ties
    # deterministic without exposing identities in paths.
    out.sort(key=lambda row: (row.ts, row.turn_id, row.thread_id), reverse=True)
    return out[:limit]


def _score_row_visible(row: dict[str, Any], scope: TenantScope | None) -> bool:
    tenant_id = str(row.get("tenant_id") or "").strip()
    owner_actor_id = str(row.get("owner_actor_id") or "").strip()
    if bool(tenant_id) != bool(owner_actor_id):
        # Partially owned rows cannot safely belong to either namespace.
        return False
    if scope is None:
        return not tenant_id and not owner_actor_id
    if scope.allow_cross_tenant:
        return True
    return tenant_id == scope.tenant_id and owner_actor_id == scope.actor_id


def analyze_soul_impact(
    agent_id: str,
    *,
    window: int = 20,
    drop_threshold: float = 0.2,
    scope: TenantScope | None = None,
) -> dict[str, Any]:
    """Compare avg score before vs after the most recent SOUL change.

    Algorithm:
        - Read up to 2*window recent scores.
        - Find the position where ``soul_hash`` last changed.
        - Split: scores BEFORE that pivot vs AFTER.
        - Compute avg on each side · only with at least
          ``min(window, 5)`` samples per side, otherwise inconclusive.
        - If avg dropped by > drop_threshold → flag.

    Returns a dict suitable for direct skill output:

        {
          "ok": True,
          "verdict": "no_change" | "improved" | "regressed" |
                     "inconclusive",
          "before_avg": float | None,
          "after_avg": float | None,
          "delta": float | None,
          "before_n": int,
          "after_n": int,
          "current_soul_hash": str,
          "previous_soul_hash": str | None,
          "suggestion": str,
        }
    """
    scores = read_recent_scores(agent_id, limit=2 * window, scope=scope)
    if not scores:
        return {
            "ok": False,
            "verdict": "no_data",
            "suggestion": "no scores recorded yet · run a few turns first",
        }
    current_hash = scores[0].soul_hash
    # Find the index where soul_hash changed (going from newest to
    # oldest). All entries [0..pivot-1] are "after" (current SOUL),
    # entries [pivot..] are "before" (previous SOUL).
    pivot: int | None = None
    previous_hash: str | None = None
    for i, s in enumerate(scores):
        if s.soul_hash != current_hash:
            pivot = i
            previous_hash = s.soul_hash
            break

    if pivot is None:
        # No SOUL change in the recent window — nothing to compare.
        avg_now = sum(s.score for s in scores) / max(1, len(scores))
        return {
            "ok": True,
            "verdict": "no_change",
            "current_soul_hash": current_hash,
            "previous_soul_hash": None,
            "after_avg": round(avg_now, 3),
            "before_avg": None,
            "delta": None,
            "after_n": len(scores),
            "before_n": 0,
            "suggestion": (
                f"SOUL unchanged across last {len(scores)} turns · "
                f"avg score {avg_now:.2f}/1.0 · no action needed"
            ),
        }

    after = scores[:pivot]
    before = scores[pivot : pivot + window]  # cap at window
    min_samples = min(window, 5)
    if len(after) < min_samples or len(before) < min_samples:
        return {
            "ok": True,
            "verdict": "inconclusive",
            "current_soul_hash": current_hash,
            "previous_soul_hash": previous_hash,
            "after_avg": round(
                sum(s.score for s in after) / max(1, len(after)),
                3,
            )
            if after
            else None,
            "before_avg": round(
                sum(s.score for s in before) / max(1, len(before)),
                3,
            )
            if before
            else None,
            "delta": None,
            "after_n": len(after),
            "before_n": len(before),
            "suggestion": (
                f"need ≥ {min_samples} samples per side · "
                f"have before={len(before)}, after={len(after)} · "
                f"run more turns before judging the latest SOUL change"
            ),
        }

    avg_before = sum(s.score for s in before) / len(before)
    avg_after = sum(s.score for s in after) / len(after)
    delta = avg_after - avg_before

    if delta < -drop_threshold:
        verdict = "regressed"
        suggestion = (
            f"avg score dropped {abs(delta):.2f} after the SOUL "
            f"change ({avg_before:.2f} → {avg_after:.2f}) · "
            f"consider `revert_soul(steps_back=1, "
            f"reason='regression after lesson')`"
        )
    elif delta > drop_threshold:
        verdict = "improved"
        suggestion = (
            f"avg score rose {delta:+.2f} after the SOUL change "
            f"({avg_before:.2f} → {avg_after:.2f}) · keep the "
            f"current lessons"
        )
    else:
        verdict = "neutral"
        suggestion = (
            f"avg score barely changed ({avg_before:.2f} → "
            f"{avg_after:.2f}) · the latest lesson is not "
            f"clearly hurting or helping yet"
        )
    return {
        "ok": True,
        "verdict": verdict,
        "current_soul_hash": current_hash,
        "previous_soul_hash": previous_hash,
        "after_avg": round(avg_after, 3),
        "before_avg": round(avg_before, 3),
        "delta": round(delta, 3),
        "after_n": len(after),
        "before_n": len(before),
        "suggestion": suggestion,
    }


__all__ = [
    "TurnScore",
    "is_safe_agent_id",
    "score_turn_outcome",
    "record_turn_score",
    "read_recent_scores",
    "analyze_soul_impact",
]
