"""
memory_skills · per-agent long-term memory / user-profile / diary skills.

Each skill resolves the "current agent" via the Session ContextVar
(`runtime.platform.process.session.current_session`). Without an active agent
session the skills raise — they can't know where to write.

Layout (per agent):

    agents/<agent_id>/agent-core/
        MEMORY.md           · long-term facts (remember / recall)
        USER.md             · user profile (note_user)
        SOUL.md             · agent persona + lessons (update_soul)
        diary/YYYY-MM-DD.md · daily diary (diary_write)

The four "write" skills target different scopes — pick the right
one based on WHAT you're recording:

    remember     · facts about THE WORLD or THE PROJECT (what's
                   the deadline, what's the API key location, etc).
    note_user    · facts about THE USER (likes terse answers,
                   prefers Chinese, uses vim, etc).
    update_soul  · LESSONS the agent learned about ITSELF (this
                   approach failed last time, that workaround
                   actually works, this skill has a quirky arg
                   format). The next session inherits these via
                   SOUL.md → system prompt.
    diary_write  · narrative log of "what happened today" — not
                   for retrieval, just for human inspection.
"""

from __future__ import annotations

# ╔════════════════════════════════════════════════════════════════════════╗
# ║ memory_skills.py · navigation map.                                      ║
# ║                                                                        ║
# ║   §1 path resolution helpers (agent_core_dir, scope) ~L56              ║
# ║   §2 _append_memory_line + _iso_now                  ~L116             ║
# ║   §3 remember / recall (private impls)               ~L147             ║
# ║   §4 remember / recall (public, scope-aware)         ~L197             ║
# ║   §5 note_user (user-profile trait recording)        ~L253             ║
# ║   §6 SOUL snapshots (history/list/snapshot)          ~L285             ║
# ║   §7 update_soul (with auto-regression check)        ~L330             ║
# ║   §8 list_soul_history + recall_scores               ~L416             ║
# ║   §9 analyze_soul_impact + auto_regression_check     ~L460             ║
# ║   §10 deep_reflect + deep_evolve                     ~L559             ║
# ║   §11 revert_soul (rollback to snapshot)             ~L608             ║
# ║   §12 diary_write (human-readable narrative log)     ~L666             ║
# ║   §13 register_memory_skills → _memory_skills_handlers (extracted)     ║
# ╚════════════════════════════════════════════════════════════════════════╝
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.memory.runtime_state.scope_paths import (
    normalize_memory_scope,
    scoped_memory_path,
    visible_memory_tier_paths,
)

# ═══════════════════════════════════════════════════════════
# Path resolution
# ═══════════════════════════════════════════════════════════

# Project root is the parent of `runtime/` — this file lives at
# runtime/execution/suckers/memory_skills.py so parents[3] = repo root.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _agent_core_dir() -> Path:
    """Return `agents/<agent_id>/agent-core/` for the current session.

    Raises RuntimeError if no Session / no agent is active.
    """
    from runtime.platform.process.session import current_session

    sess = current_session()
    if sess is None or sess.agent is None:
        raise RuntimeError(
            "memory skills require an active agent session (no current_session or no agent bound)"
        )
    agent_id = sess.agent.agent_id
    if not agent_id:
        raise RuntimeError("memory skills: current agent has no agent_id")
    return _PROJECT_ROOT / "agents" / agent_id / "agent-core"


def _current_turn_score_scope() -> Any:
    """Return the exact score namespace bound to the active Session.

    A missing Session is already rejected by ``_agent_core_dir``.  A Session
    without ownership is the local legacy namespace; a partially populated or
    inconsistent authenticated Session raises instead of leaking legacy or a
    neighbouring tenant's quality history.
    """

    from runtime.platform.process.session import current_session
    from runtime.safety.recovery.tenant_scope import trusted_scope_from_session

    return trusted_scope_from_session(current_session())


def _current_agent_and_metadata() -> tuple[str, dict[str, Any]]:
    from runtime.platform.process.session import current_session

    sess = current_session()
    if sess is None or sess.agent is None:
        raise RuntimeError(
            "memory skills require an active agent session (no current_session or no agent bound)"
        )
    agent_id = sess.agent.agent_id
    if not agent_id:
        raise RuntimeError("memory skills: current agent has no agent_id")
    metadata = dict(sess.metadata or {})
    return agent_id, metadata


def _memory_path_for_scope(scope: str | None = None) -> tuple[str, Path]:
    agent_id, metadata = _current_agent_and_metadata()
    normalized = normalize_memory_scope(scope)
    path = scoped_memory_path(
        normalized,
        repo_root=_PROJECT_ROOT,
        agent_id=agent_id,
        metadata=metadata,
    )
    return normalized, path


def _visible_memory_paths(scope: str | None = None) -> list[tuple[str, Path]]:
    agent_id, metadata = _current_agent_and_metadata()
    normalized = normalize_memory_scope(scope)
    if normalized == "all":
        return visible_memory_tier_paths(
            repo_root=_PROJECT_ROOT,
            agent_id=agent_id,
            metadata=metadata,
        )
    label, path = _memory_path_for_scope(normalized)
    return [(label, path)]


def _append_memory_line(
    path: Path,
    *,
    fact: str,
    tags: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if "_No memories yet._" in existing:
            cleaned = (
                "\n".join(
                    ln for ln in existing.splitlines() if ln.strip() != "_No memories yet._"
                ).rstrip()
                + "\n"
            )
            path.write_text(cleaned, encoding="utf-8")

    tag_str = ",".join(str(t) for t in tags)
    line = f"- [{_iso_now()} · {tag_str}] {fact}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ═══════════════════════════════════════════════════════════
# Handlers
# ═══════════════════════════════════════════════════════════


def _remember(fact: str, tags: list[str] | None = None, **_kw: Any) -> dict[str, Any]:
    """Append a fact line to MEMORY.md.

    On the first real write, strip the ``_No memories yet._`` placeholder
    line so the file cleanly transitions from "scaffold" to "live". Keep
    the header comment (starts with ``#`` / ``<!--``) intact so the file
    still reads like a document.
    """
    tags = tags or []
    core = _agent_core_dir()
    core.mkdir(parents=True, exist_ok=True)
    path = core / "MEMORY.md"

    # First-real-write cleanup · drop the "_No memories yet._" placeholder
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if "_No memories yet._" in existing:
            cleaned = (
                "\n".join(
                    ln for ln in existing.splitlines() if ln.strip() != "_No memories yet._"
                ).rstrip()
                + "\n"
            )
            path.write_text(cleaned, encoding="utf-8")

    tag_str = ",".join(str(t) for t in tags)
    line = f"- [{_iso_now()} · {tag_str}] {fact}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    return {"ok": True, "path": str(path), "appended": fact}


def _recall(query: str = "", limit: int = 20, **_kw: Any) -> dict[str, Any]:
    """Read MEMORY.md · return last N lines or substring matches."""
    core = _agent_core_dir()
    path = core / "MEMORY.md"
    if not path.exists():
        return {"ok": True, "entries": [], "count": 0}

    text = path.read_text(encoding="utf-8")
    lines = [ln.rstrip("\n") for ln in text.splitlines() if ln.strip()]

    if query:
        q = query.lower()
        matches = [ln for ln in lines if q in ln.lower()]
        entries = matches[:limit]
    else:
        entries = lines[-limit:] if limit > 0 else []

    return {"ok": True, "entries": entries, "count": len(entries)}


def _remember(
    fact: str,
    tags: list[str] | None = None,
    scope: str = "agent",
    **_kw: Any,
) -> dict[str, Any]:
    """Append a fact line to a scoped MEMORY.md."""
    tags = tags or []
    resolved_scope, path = _memory_path_for_scope(scope)
    _append_memory_line(path, fact=fact, tags=tags)
    return {
        "ok": True,
        "scope": resolved_scope,
        "path": str(path),
        "appended": fact,
    }


def _recall(
    query: str = "",
    limit: int = 20,
    scope: str = "all",
    **_kw: Any,
) -> dict[str, Any]:
    """Read visible scoped memory tiers and return matching lines."""
    entries: list[str] = []
    paths: list[str] = []
    q = (query or "").lower()
    max_entries = max(0, int(limit) if limit else 20)

    for tier_name, path in _visible_memory_paths(scope):
        if not path.exists():
            continue
        paths.append(str(path))
        text = path.read_text(encoding="utf-8")
        lines = [ln.rstrip("\n") for ln in text.splitlines() if ln.strip()]
        if q:
            tier_entries = [ln for ln in lines if q in ln.lower()]
        else:
            tier_entries = lines[-max_entries:] if max_entries > 0 else []
        entries.extend(f"[{tier_name}] {line}" for line in tier_entries)

    if max_entries > 0 and len(entries) > max_entries:
        entries = entries[-max_entries:]
    return {
        "ok": True,
        "scope": normalize_memory_scope(scope),
        "paths": paths,
        "entries": entries,
        "count": len(entries),
    }


_OBSERVATIONS_HEADER = "## Observations"


def _note_user(trait: str, **_kw: Any) -> dict[str, Any]:
    """Append a trait line under `## Observations` in USER.md."""
    core = _agent_core_dir()
    core.mkdir(parents=True, exist_ok=True)
    path = core / "USER.md"

    entry = f"- [{_iso_now()}] {trait}"

    if not path.exists():
        path.write_text(
            f"{_OBSERVATIONS_HEADER}\n{entry}\n",
            encoding="utf-8",
        )
        return {"ok": True, "path": str(path)}

    text = path.read_text(encoding="utf-8")
    if _OBSERVATIONS_HEADER not in text:
        # Append a new section at the end
        sep = "" if text.endswith("\n") else "\n"
        new_text = f"{text}{sep}\n{_OBSERVATIONS_HEADER}\n{entry}\n"
        path.write_text(new_text, encoding="utf-8")
    else:
        # Append line at end of file (section already present).
        sep = "" if text.endswith("\n") else "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{sep}{entry}\n")
    return {"ok": True, "path": str(path)}


_LESSONS_HEADER = "## Lessons Learned"


def _soul_history_dir() -> Path:
    """Per-agent ``.soul_history/`` snapshot directory."""
    return _agent_core_dir() / ".soul_history"


def _snapshot_soul(reason: str = "") -> Path | None:
    """Write the current SOUL.md to a timestamped snapshot.

    Returns the snapshot path, or None if there was nothing to
    snapshot (no SOUL.md exists yet). Snapshots are how
    ``revert_soul`` rolls back · without them every ``update_soul``
    is destructive. Called automatically before every successful
    write. Safe to call repeatedly — generates a unique filename
    per call (millisecond precision).
    """
    core = _agent_core_dir()
    src = core / "SOUL.md"
    if not src.exists():
        return None
    hist = _soul_history_dir()
    hist.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]  # ms precision
    slug = ""
    if reason:
        # Sanitise reason for filename (alnum/dash only, max 30 chars).
        slug = "-" + "".join(c if c.isalnum() else "-" for c in reason)[:30].strip("-")
    snap = hist / f"{ts}{slug}.md"
    snap.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return snap


def _list_soul_snapshots() -> list[Path]:
    """Return snapshots newest-first."""
    hist = _soul_history_dir()
    if not hist.exists():
        return []
    return sorted(
        (p for p in hist.glob("*.md") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _update_soul(
    lesson: str = "",
    tag: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    """Append a self-learned lesson to the current agent's SOUL.md.

    Lessons accumulate under a ``## Lessons Learned`` section that's
    auto-loaded into the agent's system prompt on next session boot.
    The dedupe check is exact-match within the section so the same
    lesson written twice doesn't bloat the file. Tag is optional and
    appears as a bracketed prefix.

    Every successful write FIRST snapshots the existing SOUL.md to
    ``.soul_history/<timestamp>.md`` so the change can be rolled
    back via ``revert_soul``. This is the "anti-self-harm"
    counterpart to the MiniMax-style "agent rewrites its own
    scaffold" — without versioning a bad lesson would silently
    poison every future turn.

    Use sparingly · every lesson costs context tokens forever after,
    so only durable insights belong here, not one-off observations
    (those go in `diary_write` or `remember`).
    """
    lesson = (lesson or "").strip()
    if not lesson:
        return {"ok": False, "error": "lesson required"}
    tag = (tag or "").strip()

    core = _agent_core_dir()
    core.mkdir(parents=True, exist_ok=True)
    path = core / "SOUL.md"

    line = f"- [{_iso_now()}{f' · {tag}' if tag else ''}] {lesson}"

    if not path.exists():
        # Fresh SOUL.md — write a minimal scaffold + the section.
        # No snapshot needed (nothing to roll back to yet).
        path.write_text(
            "# Soul\n\n_This file is yours to evolve. As you learn "
            "who you are, update it via the `update_soul` skill._\n"
            f"\n{_LESSONS_HEADER}\n{line}\n",
            encoding="utf-8",
        )
        return {"ok": True, "path": str(path), "appended": lesson}

    text = path.read_text(encoding="utf-8")

    # Dedupe · exact-match only. We don't try semantic dedupe ·
    # that's the agent's job before deciding to call this skill.
    if lesson in text:
        return {
            "ok": True,
            "path": str(path),
            "skipped": "lesson already recorded",
        }

    # Snapshot BEFORE the destructive change · so revert_soul can
    # restore this exact state. Failure to snapshot blocks the
    # write — better to refuse the lesson than to lose history.
    try:
        snap = _snapshot_soul(reason=tag or "lesson")
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"snapshot failed: {type(exc).__name__}: {exc}",
        }

    if _LESSONS_HEADER in text:
        # Append at end of file (section already exists).
        sep = "" if text.endswith("\n") else "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{sep}{line}\n")
    else:
        # First lesson · add the section header.
        sep = "" if text.endswith("\n") else "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{sep}\n{_LESSONS_HEADER}\n{line}\n")
    return {
        "ok": True,
        "path": str(path),
        "appended": lesson,
        "snapshot": str(snap) if snap else None,
    }


def _list_soul_history(limit: int = 10, **_kw: Any) -> dict[str, Any]:
    """List the most recent SOUL.md snapshots (newest first).

    Each entry: ``{timestamp, filename, size_bytes}``. Used by the
    agent (or the operator) to inspect what changed and pick a
    revert target.
    """
    snaps = _list_soul_snapshots()[: max(1, int(limit) if limit else 10)]
    out: list[dict[str, Any]] = []
    for s in snaps:
        try:
            stat = s.stat()
            out.append(
                {
                    "filename": s.name,
                    "size_bytes": stat.st_size,
                    "mtime_iso": datetime.fromtimestamp(
                        stat.st_mtime,
                    ).isoformat(timespec="seconds"),
                }
            )
        except Exception:  # noqa: BLE001
            continue
    return {"ok": True, "count": len(out), "snapshots": out}


def _recall_scores(limit: int = 20, **_kw: Any) -> dict[str, Any]:
    """Return the agent's recent per-turn scores (newest first)."""
    # Resolve current agent_id from Session — same gate the other
    # memory skills use, so unit tests / no-session callers get a
    # clean RuntimeError instead of writing into a wrong path.
    core = _agent_core_dir()
    agent_id = core.parent.name  # agents/<id>/agent-core → <id>
    from dataclasses import asdict

    from runtime.memory.learning.turn_scoring import read_recent_scores

    rows = read_recent_scores(
        agent_id,
        limit=max(1, int(limit) if limit else 20),
        scope=_current_turn_score_scope(),
    )
    return {
        "ok": True,
        "count": len(rows),
        "scores": [asdict(r) for r in rows],
    }


def _analyze_soul_impact(
    window: int = 20,
    drop_threshold: float = 0.2,
    **_kw: Any,
) -> dict[str, Any]:
    """Run the heuristic before-vs-after check on recent scores."""
    core = _agent_core_dir()
    agent_id = core.parent.name
    from runtime.memory.learning.turn_scoring import analyze_soul_impact

    return analyze_soul_impact(
        agent_id,
        window=max(1, int(window) if window else 20),
        drop_threshold=float(drop_threshold) if drop_threshold else 0.2,
        scope=_current_turn_score_scope(),
    )


def _auto_regression_check(
    window: int = 20,
    drop_threshold: float = 0.2,
    min_samples: int = 5,
    dry_run: bool = False,
    _scope: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Auto-revert SOUL if the most recent lesson caused a score drop.

    The "anti-self-harm" counterpart to ``deep_evolve(dry_run=False)``:
    apply path adds a lesson, this path checks if the lesson actually
    helped and rolls back if it hurt.

    Flow:
        1. Call ``analyze_soul_impact`` with the same window/threshold.
        2. If verdict == "regressed" AND after_n >= min_samples:
           call ``_revert_soul(steps_back=1, reason=…)`` — unless
           ``dry_run=True`` in which case only report the intent.
        3. All other verdicts → no action.

    Returns:
        {
          "ok": True,
          "action": "reverted" | "would_revert" | "no_action",
          "analysis": {...analyze_soul_impact output...},
          "revert_result": {...} | None,
        }

    Design:
        Atomic (0-token) · safe by default (dry_run=False triggers real
        revert — the analyze step requires ≥5 post-change samples, so
        it's rate-limited by real usage rather than a fresh-apply
        panic). The 5-sample floor is what keeps this from auto-reverting
        after one bad turn.
    """
    core = _agent_core_dir()
    agent_id = core.parent.name
    from runtime.memory.learning.turn_scoring import analyze_soul_impact

    scope = _current_turn_score_scope() if _scope is None else _scope
    res = analyze_soul_impact(
        agent_id,
        window=max(1, int(window) if window else 20),
        drop_threshold=float(drop_threshold) if drop_threshold else 0.2,
        scope=scope,
    )
    analysis = dict(res)
    action = "no_action"
    revert_result: dict[str, Any] | None = None

    verdict = res.get("verdict")
    after_n = int(res.get("after_n") or 0)
    delta = res.get("delta")

    # Coerce dry_run · LLMs sometimes pass strings.
    if isinstance(dry_run, str):
        dry_run = dry_run.lower() in ("true", "yes", "1")

    should_revert = (
        verdict == "regressed"
        and after_n >= int(min_samples)
        and isinstance(delta, (int, float))
        and delta < -float(drop_threshold)
    )

    if should_revert:
        reason = (
            f"auto-revert · delta={delta:+.2f} over {after_n} turns (≥ {drop_threshold} threshold)"
        )[:80]
        if dry_run:
            action = "would_revert"
            revert_result = {"dry_run": True, "planned_reason": reason}
        else:
            try:
                revert_result = _revert_soul(steps_back=1, reason=reason)
                action = "reverted"
            except Exception as exc:  # noqa: BLE001
                action = "revert_failed"
                revert_result = {
                    "error": f"{type(exc).__name__}: {exc}",
                }

    return {
        "ok": True,
        "action": action,
        "analysis": analysis,
        "revert_result": revert_result,
        "dry_run": bool(dry_run),
        "min_samples": int(min_samples),
    }


def _deep_reflect(
    window: int = 20,
    model: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    """LLM-judged review of recent turns (B2 · ~2-3¢ haiku call)."""
    core = _agent_core_dir()
    agent_id = core.parent.name
    from runtime.memory.learning.deep_evolution import deep_reflect

    return deep_reflect(
        agent_id=agent_id,
        window=max(1, int(window) if window else 20),
        model=(model or None),
        scope=_current_turn_score_scope(),
    )


def _deep_evolve(
    window: int = 20,
    candidates_per_round: int = 3,
    max_rounds: int = 1,
    dry_run: bool = True,
    model: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    """MiniMax-style autonomous self-improvement loop (B3 · expensive).

    Default ``dry_run=True`` returns a preview only. Pass
    ``dry_run=False`` to register a typed evolution candidate; it still
    requires structured shadow review and staged canary before SOUL changes.
    """
    core = _agent_core_dir()
    agent_id = core.parent.name
    from runtime.memory.learning.deep_evolution import deep_evolve

    # Coerce dry_run · LLMs sometimes pass strings.
    if isinstance(dry_run, str):
        dry_run = dry_run.lower() not in ("false", "no", "0", "")
    return deep_evolve(
        agent_id=agent_id,
        window=max(1, int(window) if window else 20),
        candidates_per_round=max(
            1,
            min(5, int(candidates_per_round) if candidates_per_round else 3),
        ),
        max_rounds=max(1, min(10, int(max_rounds) if max_rounds else 1)),
        dry_run=bool(dry_run),
        model=(model or None),
        scope=_current_turn_score_scope(),
    )


def _revert_soul(
    steps_back: int = 1,
    reason: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    """Roll the agent's SOUL.md back N snapshots.

    Args:
        steps_back: how many snapshots to step back (default 1 ·
            "undo last change"). 2 = undo last two writes.
        reason: free-text why · stored in a snapshot of the CURRENT
            state before the rollback so this revert is itself
            reversible. Convention: write what went wrong (e.g.
            "lesson caused over-cautious behavior").

    Restores SOUL.md from the Nth-newest snapshot. The current
    state gets snapshotted first under reason "pre-revert" so the
    operator can re-apply if the rollback was a mistake.
    """
    steps_back = max(1, int(steps_back) if steps_back else 1)
    reason = (reason or "").strip() or "manual"

    snaps = _list_soul_snapshots()
    if not snaps:
        return {
            "ok": False,
            "error": "no snapshots available · update_soul has never run",
        }
    if steps_back > len(snaps):
        return {
            "ok": False,
            "error": (f"can't step back {steps_back} · only {len(snaps)} snapshots exist"),
        }
    target = snaps[steps_back - 1]

    # Snapshot the CURRENT state first so this revert is reversible.
    try:
        pre_revert = _snapshot_soul(reason=f"pre-revert-{reason}")
    except Exception:  # noqa: BLE001
        pre_revert = None

    core = _agent_core_dir()
    soul_path = core / "SOUL.md"
    soul_path.write_text(
        target.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "restored_from": target.name,
        "pre_revert_snapshot": pre_revert.name if pre_revert else None,
        "reason": reason,
    }


def _diary_write(entry: str, **_kw: Any) -> dict[str, Any]:
    """Append a timestamped entry to today's diary file."""
    core = _agent_core_dir()
    diary_dir = core / "diary"
    diary_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    path = diary_dir / f"{now.strftime('%Y-%m-%d')}.md"
    ts = now.strftime("%H:%M:%S")

    block = f"## {ts}\n\n{entry}\n"
    if path.exists() and path.stat().st_size > 0:
        existing = path.read_text(encoding="utf-8")
        sep = "" if existing.endswith("\n") else "\n"
        block = f"{sep}\n{block}"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(block)

    return {"ok": True, "path": str(path)}


# ═══════════════════════════════════════════════════════════
# Registrar · moved to _memory_skills_handlers to keep this file
# under 1000 lines.  Re-exported below so public callers are
# unaffected.  The import MUST come after all handler definitions
# above so the submodule can resolve them at load time.
# ═══════════════════════════════════════════════════════════
from ._memory_skills_handlers import register_memory_skills  # noqa: E402  (after defs)

__all__ = ["register_memory_skills"]
