from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

_LOG = logging.getLogger


def _publish_skill_event(rec: SkillUsageRecord) -> None:
    from runtime.platform.process.eventbus import SkillUsed, publish_event

    publish_event(
        SkillUsed(
            event_type="skill.used",
            skill_name=rec.skill_name,
            success=rec.success,
            duration_sec=rec.duration_sec,
            agent_id=rec.agent_id,
        ),
        logger=_LOG,
    )


_LOG = logging.getLogger("echo.evolution.skill_usage")


@dataclass
class SkillUsageRecord:
    skill_name: str
    ts: str
    success: bool
    duration_sec: float
    agent_id: str = ""
    error_type: str | None = None


@dataclass
class SkillUsageStats:
    skill_name: str
    total_calls: int
    success_count: int
    failure_count: int
    success_rate: float
    avg_duration: float
    last_called: str


class SkillUsageTracker:
    def __init__(self, log_path: str = "data/skill_usage.jsonl") -> None:
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[SkillUsageRecord] = []
        self._lock = threading.Lock()

    def record(
        self,
        skill_name: str,
        success: bool,
        duration_sec: float = 0.0,
        *,
        agent_id: str = "",
        error_type: str | None = None,
    ) -> SkillUsageRecord:
        rec = SkillUsageRecord(
            skill_name=skill_name,
            ts=datetime.now().isoformat(timespec="seconds"),
            success=success,
            duration_sec=duration_sec,
            agent_id=agent_id,
            error_type=error_type,
        )
        with self._lock:
            self._records.append(rec)
        self._persist(rec)
        _publish_skill_event(rec)
        return rec

    def stats(self, skill_name: str | None = None) -> list[SkillUsageStats]:
        with self._lock:
            records = self._records
        if skill_name:
            records = [r for r in records if r.skill_name == skill_name]

        grouped: dict[str, list[SkillUsageRecord]] = {}
        for r in records:
            grouped.setdefault(r.skill_name, []).append(r)

        result: list[SkillUsageStats] = []
        for name, recs in grouped.items():
            total = len(recs)
            successes = sum(1 for r in recs if r.success)
            durations = [r.duration_sec for r in recs if r.duration_sec > 0]
            result.append(
                SkillUsageStats(
                    skill_name=name,
                    total_calls=total,
                    success_count=successes,
                    failure_count=total - successes,
                    success_rate=round(successes / max(1, total), 3),
                    avg_duration=round(sum(durations) / max(1, len(durations)), 3)
                    if durations
                    else 0.0,
                    last_called=recs[-1].ts if recs else "",
                )
            )
        return sorted(result, key=lambda s: s.total_calls, reverse=True)

    def top_skills(self, limit: int = 10) -> list[SkillUsageStats]:
        return self.stats()[:limit]

    def least_reliable(self, min_calls: int = 3) -> list[SkillUsageStats]:
        all_stats = self.stats()
        return [s for s in all_stats if s.total_calls >= min_calls and s.success_rate < 0.5]

    def _persist(self, rec: SkillUsageRecord) -> None:
        try:
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(rec), ensure_ascii=False, default=str) + "\n")
        except OSError:  # noqa: BLE001 — usage log write best-effort
            pass


__all__ = ["SkillUsageRecord", "SkillUsageStats", "SkillUsageTracker"]
