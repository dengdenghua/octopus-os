"""Shared utility functions for evolution operator subsystems."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.safety.auth.scope import TenantScope
from runtime.safety.recovery.tenant_scope import (
    read_learning_events,
    read_learning_journal,
)

try:
    from fastapi.responses import PlainTextResponse

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    PlainTextResponse = None  # type: ignore[assignment,misc]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return None


def _iso(value: Any) -> str:
    dt = _as_dt(value)
    return dt.isoformat() if dt is not None else _utcnow().isoformat()


def _date_key(value: datetime) -> str:
    return value.astimezone(UTC).date().isoformat()


def _week_key(value: datetime) -> str:
    iso = value.astimezone(UTC).date().isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _bounded_score(value: float) -> float:
    return max(0.0, min(1.0, value))


class _ScopedJournalView:
    """Read-only ownership projection over a shared Journal.

    Existing evolution projections call ``read_all`` / ``read_by_type`` at
    several layers.  Passing this view lets those mature projections stay
    unchanged while enforcing one server-derived tenant boundary at the
    outer HTTP request.  ``scope=None`` intentionally means legacy-only, not
    global; only an explicit ``allow_cross_tenant`` scope reads every row.
    """

    def __init__(self, journal: Any, scope: TenantScope | None) -> None:
        self._journal = journal
        self.scope = scope

    def read_all(self, *, scope: TenantScope | None = None) -> list[Any]:
        del scope  # callers cannot override the request-owned boundary
        if self._journal is None:
            return []
        try:
            return read_learning_journal(self._journal, scope=self.scope)
        except (AttributeError, TypeError, OSError):
            return []

    def read_by_type(
        self,
        event_type: Any,
        *,
        scope: TenantScope | None = None,
    ) -> list[Any]:
        del scope  # callers cannot override the request-owned boundary
        if self._journal is None:
            return []
        try:
            return read_learning_events(self._journal, event_type, scope=self.scope)
        except (AttributeError, TypeError, OSError):
            return []


def _scoped_journal(journal: Any, scope: TenantScope | None) -> Any:
    """Return a fail-closed request projection for evolution read models."""

    return _ScopedJournalView(journal, scope)


def _journal_events(journal: Any) -> list[Any]:
    if journal is None:
        return []
    try:
        return list(journal.read_all())
    except (AttributeError, TypeError, OSError):
        return []


def _trajectory_rows(journal: Any) -> list[tuple[Any, Any]]:
    rows: list[tuple[Any, Any]] = []
    if journal is None:
        return rows
    try:
        events = list(journal.read_by_type("trajectory"))
    except (AttributeError, TypeError, OSError):
        events = [
            ev for ev in _journal_events(journal) if getattr(ev, "event_type", "") == "trajectory"
        ]
    for event in events:
        traj = getattr(event, "trajectory", None)
        if traj is not None:
            rows.append((event, traj))
    return rows


def _skill_step_rows(journal: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event, traj in _trajectory_rows(journal):
        task_id = str(getattr(traj, "task_id", None) or getattr(event, "task_id", "") or "")
        fallback_ts = (
            _as_dt(getattr(traj, "completed_at", None))
            or _as_dt(getattr(event, "ts", None))
            or _utcnow()
        )
        for step in getattr(traj, "steps", []) or []:
            action = getattr(step, "action", None)
            result = getattr(step, "result", None)
            skill_name = str(getattr(action, "sucker_id", "") or "").strip()
            if not skill_name:
                continue
            started = _as_dt(getattr(step, "ts", None)) or fallback_ts
            finished = _as_dt(getattr(result, "ts", None)) or fallback_ts
            duration_ms = max(0.0, (finished - started).total_seconds() * 1000)
            rows.append(
                {
                    "task_id": task_id,
                    "skill_name": skill_name,
                    "success": bool(getattr(step, "success", False)),
                    "ts": finished,
                    "duration_ms": duration_ms,
                }
            )
    return rows


def _registry_skill_names(registry: Any) -> list[str]:
    if registry is None:
        return []
    try:
        return sorted(str(name) for name in registry.all_names())
    except (AttributeError, TypeError, OSError):
        return []


def _registry_skill_is_auto(registry: Any, name: str) -> bool:
    if registry is None or not name:
        return False
    try:
        skill = registry.get(name)
    except (AttributeError, TypeError, OSError):
        return False
    affinity = getattr(skill, "affinity", []) or []
    trusted_source = str(getattr(skill, "trusted_source", "") or "")
    return "forged" in affinity or trusted_source.startswith("skill://forged/")


def _registry_has_skill(registry: Any, name: str) -> bool:
    if registry is None or not name:
        return False
    try:
        return bool(registry.has(name))
    except (AttributeError, TypeError, OSError):
        return False


def _stable_int_id(value: str) -> int:
    return int(hashlib.blake2b(value.encode("utf-8"), digest_size=4).hexdigest(), 16)


def _shorten_text(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _model_payload(value: Any) -> Any:
    if value is None:
        return None
    try:
        return value.model_dump(mode="json")
    except (AttributeError, TypeError, OSError):
        try:
            return value.dict()
        except (AttributeError, TypeError, OSError):
            return str(value)


def _token_usage_rows(journal: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if journal is None:
        return rows
    try:
        events = list(journal.read_by_type("token_usage"))
    except (AttributeError, TypeError, OSError):
        events = [
            event
            for event in _journal_events(journal)
            if getattr(event, "event_type", "") == "token_usage"
        ]
    for event in events:
        model = str(getattr(event, "model", "") or "").strip()
        task_id = str(getattr(event, "task_id", "") or "").strip()
        if not model or not task_id:
            continue
        rows.append(
            {
                "task_id": task_id,
                "model": model,
                "input_tokens": int(getattr(event, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(event, "output_tokens", 0) or 0),
                "cost_usd": float(getattr(event, "cost_usd", 0.0) or 0.0),
                "ts": _as_dt(getattr(event, "ts", None)) or _utcnow(),
            }
        )
    return rows


def _trajectory_outcomes_by_task(journal: Any) -> dict[str, bool]:
    outcomes: dict[str, tuple[datetime, bool]] = {}
    for event, traj in _trajectory_rows(journal):
        task_id = str(getattr(traj, "task_id", "") or getattr(event, "task_id", "") or "")
        if not task_id:
            continue
        ts = (
            _as_dt(getattr(traj, "completed_at", None))
            or _as_dt(getattr(event, "ts", None))
            or _utcnow()
        )
        success = bool(getattr(getattr(traj, "outcome", None), "success", False))
        existing = outcomes.get(task_id)
        if existing is None or ts >= existing[0]:
            outcomes[task_id] = (ts, success)
    return {task_id: success for task_id, (_ts, success) in outcomes.items()}


def _learn_from_intel_result(
    journal: Any,
    planner: Any,
    registry: Any,
    *,
    suppressed_names: set[str] | None = None,
    scope: TenantScope | None = None,
) -> dict[str, Any]:
    from .curriculum import (
        _curriculum_goal_rows,
    )
    from .framework_benchmarks import (
        _dispatch_snapshot,
        _framework_benchmark_rows,
    )
    from .mcp_ops import _mcp_proposal_rows
    from .protocol_drift import _protocol_drift_rows
    from .skill_forge import (
        _skill_candidate_to_proposal,
        _skill_forge_candidates,
    )

    errors: list[str] = []
    rules_learned = 0
    memories_stored = 0
    kg_triples = 0

    if planner is not None and journal is not None:
        rules_learned = _call_planner_learning_method(
            planner,
            "learn_from_journal",
            journal,
            errors,
        )
        memories_stored = _call_planner_learning_method(
            planner,
            "learn_memories_from_journal",
            journal,
            errors,
        )
        kg_triples = _call_planner_learning_method(
            planner,
            "learn_kg_from_journal",
            journal,
            errors,
        )
    elif planner is None:
        errors.append("planner_unavailable")

    kg = _knowledge_graph_overview(journal)
    skill_proposals = [
        _skill_candidate_to_proposal(candidate)
        for candidate in _skill_forge_candidates(
            journal,
            registry,
            suppressed_names=suppressed_names,
            scope=scope,
        )
    ]
    curriculum_goals = _curriculum_goal_rows(journal, status="pending")
    mcp_proposals = _mcp_proposal_rows(journal)
    protocol_drifts = _protocol_drift_rows(journal, acknowledged=False)
    framework_rows = _framework_benchmark_rows(journal)
    dispatch_rows = _dispatch_snapshot(journal)

    return {
        "ok": len(errors) == 0 or errors == ["planner_unavailable"],
        "skills_created": [],
        "skills_created_count": 0,
        "skill_proposals": [row["name"] for row in skill_proposals],
        "skill_proposals_count": len(skill_proposals),
        "entities_added": int(kg.get("entities", 0) or 0),
        "kg_triples": kg_triples or int(kg.get("relationships", 0) or 0),
        "memories_stored": memories_stored,
        "rules_learned": rules_learned,
        "curriculum_goals_count": len(curriculum_goals),
        "mcp_proposals_count": len(mcp_proposals),
        "protocol_drifts_count": len(protocol_drifts),
        "framework_benchmarks_count": len(framework_rows),
        "dispatch_tests_count": len(dispatch_rows),
        "planner_attached": planner is not None,
        "source": "planner" if planner is not None else "journal",
        "errors": errors,
    }


def _call_planner_learning_method(
    planner: Any,
    method_name: str,
    journal: Any,
    errors: list[str],
) -> int:
    method = getattr(planner, method_name, None)
    if not callable(method):
        return 0
    try:
        return int(method(journal) or 0)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{method_name}: {exc}")
        return 0


def _skill_performance_rows(journal: Any, registry: Any) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _skill_step_rows(journal):
        grouped[row["skill_name"]].append(row)

    for name in _registry_skill_names(registry):
        grouped.setdefault(name, [])

    out: list[dict[str, Any]] = []
    for name, rows in grouped.items():
        rows.sort(key=lambda r: r["ts"])
        usage = len(rows)
        successes = sum(1 for r in rows if r["success"])
        success_rate = successes / usage if usage else 0.0
        avg_duration = sum(float(r["duration_ms"]) for r in rows) / usage if usage else 0.0
        trend = _skill_trend(rows, success_rate)
        last_used = _iso(rows[-1]["ts"]) if rows else ""
        out.append(
            {
                "name": name,
                "usage_count": usage,
                "success_rate": success_rate,
                "avg_duration_ms": round(avg_duration, 2),
                "trend": trend,
                "last_used": last_used,
            }
        )

    out.sort(key=lambda r: (-int(r["usage_count"]), str(r["name"])))
    return out


def _skill_trend(rows: list[dict[str, Any]], success_rate: float) -> str:
    if len(rows) >= 4:
        mid = len(rows) // 2
        first = rows[:mid]
        second = rows[mid:]
        first_rate = sum(1 for r in first if r["success"]) / len(first)
        second_rate = sum(1 for r in second if r["success"]) / len(second)
        if second_rate - first_rate >= 0.15:
            return "improving"
        if first_rate - second_rate >= 0.15:
            return "declining"
    if len(rows) >= 3 and success_rate < 0.5:
        return "declining"
    if len(rows) >= 3 and success_rate >= 0.85:
        return "improving"
    return "stable"


def _section_line_count(section: str) -> int:
    return sum(1 for line in (section or "").splitlines() if line.lstrip().startswith("- ["))


def _learned_section_counts(planner: Any) -> dict[str, int]:
    if planner is None:
        return {"rules": 0, "memories": 0}
    return {
        "rules": _section_line_count(str(getattr(planner, "learned_rules_section", "") or "")),
        "memories": _section_line_count(
            str(getattr(planner, "learned_memories_section", "") or "")
        ),
    }


def _knowledge_graph_overview(journal: Any) -> dict[str, int]:
    try:
        from runtime.memory.knowledge_graph import KnowledgeGraph
        from runtime.safety.recovery import KGUpdater

        kg = KnowledgeGraph()
        KGUpdater(journal, kg).update()
        triples = kg.query()
        entities: set[str] = set()
        for triple in triples:
            entities.add(str(getattr(triple, "subject", "")))
            entities.add(str(getattr(triple, "object", "")))
        return {
            "entities": len({e for e in entities if e}),
            "relationships": len(triples),
            "communities": 0,
        }
    except (ImportError, AttributeError, TypeError, OSError):
        return {"entities": 0, "relationships": 0, "communities": 0}


def _csv_response(headers: list[str], rows: list[list[Any]]) -> Any:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


def _intelligence_store_snapshot() -> dict[str, Any]:
    path = Path(os.environ.get("ECHO_HOME", ".echo")) / "intelligence.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raw = {}

    subscriptions = [item for item in raw.get("subscriptions", []) if isinstance(item, dict)]
    reports = [item for item in raw.get("reports", []) if isinstance(item, dict)]
    enabled = [item for item in subscriptions if item.get("enabled") is not False]

    last_report_at = ""
    for report in reports:
        created_at = str(report.get("created_at") or "")
        if created_at > last_report_at:
            last_report_at = created_at

    return {
        "subscriptions": len(subscriptions),
        "enabled_subscriptions": len(enabled),
        "total_reports": len(reports),
        "last_report_at": last_report_at or None,
    }
