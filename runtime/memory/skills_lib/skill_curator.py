"""SkillCurator — lifecycle management for learned skills.

Skill lifecycle rules:
  - Skills unused for ``stale_days`` (default 30) → marked ``stale``
  - Skills unused for ``archive_days`` (default 90) → moved to archive dir
  - Every ``merge_interval_days`` (default 7) an LLM pass merges
    duplicate / near-duplicate skills and prunes dead ones.

Integration
-----------
The Curator is designed to run as a background task inside the
``deep_evolution`` cron loop. Wire it up with::

    from runtime.memory.skills_lib.skill_curator import SkillCurator
    curator = SkillCurator(agent_id="general")
    curator.run_pass()   # call from DeepEvolve or a cron job

Skill usage tracking
--------------------
Each skill file's YAML frontmatter is extended with three fields
that the Curator reads and writes:

    last_used_at: 2026-05-09T12:00:00
    use_count: 14
    status: active | stale | archived

``record_use(agent_id, name)`` is the hook that skill-apply code
calls to bump ``use_count`` and ``last_used_at``.

Archive layout
--------------
Archived skills are moved to ``agents/<agent_id>/skills/_archive/``
so they are not deleted and can be restored manually.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.skill_curator")

# ─── thresholds ────────────────────────────────────────────────
_DEFAULT_STALE_DAYS = 30
_DEFAULT_ARCHIVE_DAYS = 90
_DEFAULT_MERGE_INTERVAL_DAYS = 7

# ─── module-level LLM router (same wiring as skill_library) ────
_ROUTER: Any = None
_DEFAULT_MODEL: str | None = None
_LOCK = threading.Lock()


def set_curator_router(
    router: Any,
    *,
    default_model: str | None = None,
) -> None:
    """Install the LLM router used for the merge/prune pass.

    Call this at startup alongside ``set_skill_router``. Pass
    ``None`` to disable the LLM merge pass (usage-based pruning
    still runs without it).
    """
    global _ROUTER, _DEFAULT_MODEL
    with _LOCK:
        _ROUTER = router
        _DEFAULT_MODEL = default_model


# ─── path helpers ───────────────────────────────────────────────


def _project_root() -> Path:
    from runtime.platform.process.paths import project_root

    return project_root()


def _skills_dir(agent_id: str) -> Path:
    return _project_root() / "agents" / agent_id / "skills"


def _archive_dir(agent_id: str) -> Path:
    return _skills_dir(agent_id) / "_archive"


# ─── frontmatter helpers ────────────────────────────────────────


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for i, ln in enumerate(lines[1:], start=1):
        if ln.strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta: dict[str, str] = {}
    for raw in lines[1:end]:
        if ":" not in raw:
            continue
        k, v = raw.split(":", 1)
        meta[k.strip()] = v.strip()
    body = "\n".join(lines[end + 1 :]).lstrip()
    return meta, body


def _write_frontmatter(path: Path, meta: dict[str, str], body: str) -> None:
    """Rewrite the frontmatter of a skill file atomically."""
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    tmp.replace(path)


# ─── usage tracking ─────────────────────────────────────────────


def record_use(agent_id: str, name: str) -> None:
    """Bump ``use_count`` and ``last_used_at`` for a skill.

    Call this whenever ``apply_skill`` succeeds. Swallows all
    exceptions — tracking is best-effort.
    """
    if not agent_id or not name:
        return
    safe = re.sub(r"[^\w\-]", "-", name.lower())
    path = _skills_dir(agent_id) / f"{safe}.md"
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        count = int(meta.get("use_count", "0")) + 1
        meta["use_count"] = str(count)
        meta["last_used_at"] = datetime.now(UTC).isoformat()
        # Reset stale status on use.
        if meta.get("status") == "stale":
            meta["status"] = "active"
        _write_frontmatter(path, meta, body)
    except (OSError, TypeError, ValueError):  # noqa: BLE001
        pass


# ─── SkillCurator ───────────────────────────────────────────────


class SkillCurator:
    """Lifecycle manager for learned skills.

    Parameters
    ----------
    agent_id:
        The agent whose skill library to manage.
    stale_days:
        Days since last use before a skill is marked ``stale``.
    archive_days:
        Days since last use before a skill is archived (moved to
        ``_archive/``).
    merge_interval_days:
        Minimum days between LLM merge/prune passes. The pass is
        skipped if the last run was more recent than this.
    """

    def __init__(
        self,
        agent_id: str,
        *,
        stale_days: int = _DEFAULT_STALE_DAYS,
        archive_days: int = _DEFAULT_ARCHIVE_DAYS,
        merge_interval_days: int = _DEFAULT_MERGE_INTERVAL_DAYS,
    ) -> None:
        self.agent_id = agent_id
        self.stale_days = stale_days
        self.archive_days = archive_days
        self.merge_interval_days = merge_interval_days
        self._state_path = _skills_dir(agent_id) / "_curator_state.json"

    # ── public API ──────────────────────────────────────────────

    def run_pass(self) -> dict[str, Any]:
        """Run a full curator pass.

        Returns a summary dict with counts of skills processed,
        marked stale, archived, and whether the LLM merge pass ran.
        """
        sdir = _skills_dir(self.agent_id)
        if not sdir.exists():
            return {"skipped": True, "reason": "no skills dir"}

        now = datetime.now(UTC)
        stale_threshold = now - timedelta(days=self.stale_days)
        archive_threshold = now - timedelta(days=self.archive_days)

        stats: dict[str, Any] = {
            "agent_id": self.agent_id,
            "ran_at": now.isoformat(),
            "total": 0,
            "marked_stale": 0,
            "archived": 0,
            "merge_pass_ran": False,
            "errors": [],
        }

        for path in sorted(sdir.glob("*.md")):
            if path.name.startswith("_"):
                continue
            stats["total"] += 1
            try:
                self._process_skill(
                    path,
                    stale_threshold,
                    archive_threshold,
                    stats,
                )
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"{path.name}: {exc}")

        # LLM merge pass — only if router is wired and enough time
        # has passed since the last run.
        if self._should_run_merge_pass(now):
            try:
                merge_result = self._llm_merge_pass()
                stats["merge_pass_ran"] = True
                stats["merge_result"] = merge_result
                self._save_last_merge_ts(now)
            except Exception as exc:  # noqa: BLE001
                stats["merge_pass_ran"] = False
                stats["merge_error"] = str(exc)

        _LOG.info(
            "SkillCurator pass complete: %d total, %d stale, %d archived",
            stats["total"],
            stats["marked_stale"],
            stats["archived"],
        )
        return stats

    def list_stale(self) -> list[dict[str, Any]]:
        """Return metadata for all skills currently marked stale."""
        return self._list_by_status("stale")

    def list_archived(self) -> list[dict[str, Any]]:
        """Return metadata for all archived skills."""
        adir = _archive_dir(self.agent_id)
        if not adir.exists():
            return []
        out: list[dict[str, Any]] = []
        for p in sorted(adir.glob("*.md")):
            try:
                meta, _ = _parse_frontmatter(p.read_text(encoding="utf-8"))
                out.append(
                    {
                        "name": meta.get("name") or p.stem,
                        "description": meta.get("description", ""),
                        "last_used_at": meta.get("last_used_at", ""),
                        "use_count": int(meta.get("use_count", "0")),
                        "archived_at": meta.get("archived_at", ""),
                        "filename": p.name,
                    }
                )
            except (OSError, TypeError, ValueError):
                continue
        return out

    def restore(self, name: str) -> bool:
        """Move an archived skill back to the active skills dir."""
        safe = re.sub(r"[^\w\-]", "-", name.lower())
        src = _archive_dir(self.agent_id) / f"{safe}.md"
        if not src.is_file():
            return False
        dst = _skills_dir(self.agent_id) / f"{safe}.md"
        try:
            text = src.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(text)
            meta["status"] = "active"
            meta.pop("archived_at", None)
            _write_frontmatter(dst, meta, body)
            src.unlink()
            return True
        except (OSError, TypeError, ValueError):  # noqa: BLE001
            return False

    # ── internals ───────────────────────────────────────────────

    def _process_skill(
        self,
        path: Path,
        stale_threshold: datetime,
        archive_threshold: datetime,
        stats: dict[str, Any],
    ) -> None:
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)

        last_used_raw = meta.get("last_used_at") or meta.get("learned_at", "")
        last_used: datetime | None = None
        if last_used_raw:
            try:
                last_used = datetime.fromisoformat(last_used_raw)
                if last_used.tzinfo is None:
                    last_used = last_used.replace(tzinfo=UTC)
            except ValueError:  # noqa: BLE001 — tz parse fallthrough
                pass

        if last_used is None:
            # Never used — treat learned_at as last_used for threshold
            # purposes. If learned_at is also missing, skip.
            return

        current_status = meta.get("status", "active")

        if last_used < archive_threshold and current_status != "archived":
            # Archive it.
            adir = _archive_dir(self.agent_id)
            adir.mkdir(parents=True, exist_ok=True)
            meta["status"] = "archived"
            meta["archived_at"] = datetime.now(UTC).isoformat()
            dst = adir / path.name
            _write_frontmatter(dst, meta, body)
            path.unlink()
            stats["archived"] += 1

        elif last_used < stale_threshold and current_status == "active":
            meta["status"] = "stale"
            _write_frontmatter(path, meta, body)
            stats["marked_stale"] += 1

    def _list_by_status(self, status: str) -> list[dict[str, Any]]:
        sdir = _skills_dir(self.agent_id)
        if not sdir.exists():
            return []
        out: list[dict[str, Any]] = []
        for p in sorted(sdir.glob("*.md")):
            if p.name.startswith("_"):
                continue
            try:
                meta, _ = _parse_frontmatter(p.read_text(encoding="utf-8"))
                if meta.get("status") == status:
                    out.append(
                        {
                            "name": meta.get("name") or p.stem,
                            "description": meta.get("description", ""),
                            "last_used_at": meta.get("last_used_at", ""),
                            "use_count": int(meta.get("use_count", "0")),
                            "filename": p.name,
                        }
                    )
            except (OSError, TypeError, ValueError):
                continue
        return out

    def _should_run_merge_pass(self, now: datetime) -> bool:
        with _LOCK:
            if _ROUTER is None:
                return False
        if not self._state_path.exists():
            return True
        try:
            import json

            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            last_raw = state.get("last_merge_at", "")
            if not last_raw:
                return True
            last = datetime.fromisoformat(last_raw)
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            return (now - last).days >= self.merge_interval_days
        except (TypeError, ValueError, AttributeError):  # noqa: BLE001
            return True

    def _save_last_merge_ts(self, ts: datetime) -> None:
        import json

        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"last_merge_at": ts.isoformat()}),
            encoding="utf-8",
        )
        tmp.replace(self._state_path)

    def _llm_merge_pass(self) -> dict[str, Any]:
        """Ask the LLM to identify duplicate / redundant skills and
        suggest merges. Returns a summary dict.

        This is a best-effort advisory pass — the Curator does NOT
        automatically delete skills based on LLM output. It logs
        the suggestions so a human or a future automated pass can
        act on them.
        """
        with _LOCK:
            router = _ROUTER
            model = _DEFAULT_MODEL

        sdir = _skills_dir(self.agent_id)
        skills = []
        for p in sorted(sdir.glob("*.md")):
            if p.name.startswith("_"):
                continue
            try:
                meta, _ = _parse_frontmatter(p.read_text(encoding="utf-8"))
                skills.append(
                    {
                        "name": meta.get("name") or p.stem,
                        "description": meta.get("description", ""),
                        "use_count": meta.get("use_count", "0"),
                        "status": meta.get("status", "active"),
                    }
                )
            except (OSError, TypeError, ValueError):
                continue

        if not skills:
            return {"suggestions": [], "skill_count": 0}

        import json

        skill_list = "\n".join(
            f"- {s['name']} (uses={s['use_count']}, status={s['status']}): {s['description'][:80]}"
            for s in skills
        )
        prompt = (
            "You are a skill library curator. Review the following skill "
            "index and identify:\n"
            "1. Duplicate or near-duplicate skills that should be merged.\n"
            "2. Skills with 0 uses that are likely dead.\n\n"
            f"Skill index:\n{skill_list}\n\n"
            "Reply with a JSON object: "
            '{"merge_suggestions": [{"keep": "name", "remove": ["name"]}], '
            '"dead_skills": ["name"]}'
        )

        try:
            resp = router.complete(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                max_tokens=512,
            )
            raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
            # Extract JSON from response.
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                result = json.loads(m.group())
                return {
                    "suggestions": result.get("merge_suggestions", []),
                    "dead_skills": result.get("dead_skills", []),
                    "skill_count": len(skills),
                }
        except (ConnectionError, TimeoutError, json.JSONDecodeError, TypeError, ValueError) as exc:
            _LOG.warning("SkillCurator LLM merge pass failed: %s", exc)

        return {"suggestions": [], "dead_skills": [], "skill_count": len(skills)}


__all__ = [
    "SkillCurator",
    "record_use",
    "set_curator_router",
]
