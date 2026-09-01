"""Read-model builders for the evolution operator console.

The public FastAPI route declarations stay in :mod:`evolution_ops_router` so
their paths, signatures, ordering, and authorization wiring remain stable.
This module contains only the larger journal/planner projection routines.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from .evolution_ops import (
    _as_dt,
    _bounded_score,
    _date_key,
    _iso,
    _knowledge_graph_overview,
    _learned_section_counts,
    _registry_skill_is_auto,
    _registry_skill_names,
    _skill_performance_rows,
    _skill_step_rows,
    _trajectory_rows,
    _utcnow,
    _week_key,
)


def _intelligence_store_snapshot() -> dict[str, Any]:
    import json
    import os
    from pathlib import Path

    path = Path(os.environ.get("ECHO_HOME", ".echo")) / "intelligence.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
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


def evolution_overview_payload(
    journal: Any,
    registry: Any,
    planner: Any,
    *,
    include_global_intelligence: bool = True,
) -> dict[str, Any]:
    skill_perf = _skill_performance_rows(journal, registry)
    used_skill_rates = [
        float(row["success_rate"]) for row in skill_perf if int(row["usage_count"]) > 0
    ]
    avg_success = sum(used_skill_rates) / len(used_skill_rates) if used_skill_rates else 0.0

    skill_names = _registry_skill_names(registry)
    auto_skills = [name for name in skill_names if _registry_skill_is_auto(registry, name)]
    learned_counts = _learned_section_counts(planner)
    trajectory_rows = _trajectory_rows(journal)
    kg = _knowledge_graph_overview(journal)
    intelligence: dict[str, Any] = (
        _intelligence_store_snapshot()
        if include_global_intelligence
        else {
            "subscriptions": 0,
            "enabled_subscriptions": 0,
            "total_reports": 0,
            "last_report_at": None,
        }
    )

    learning_events = (
        len(trajectory_rows)
        + learned_counts["rules"]
        + learned_counts["memories"]
        + len(auto_skills)
        + int(intelligence["total_reports"])
    )
    improvement_score = _bounded_score(
        avg_success * 0.55
        + min(1.0, len(trajectory_rows) / 25) * 0.25
        + min(1.0, learned_counts["rules"] / 10) * 0.10
        + min(1.0, len(auto_skills) / 8) * 0.10
        + min(1.0, int(intelligence["total_reports"]) / 12) * 0.05
    )

    return {
        "skills": {
            "total": len(skill_names),
            "auto_extracted": len(auto_skills),
            "manual": max(0, len(skill_names) - len(auto_skills)),
            "avg_success_rate": avg_success,
        },
        "memory": {
            "total_facts": learned_counts["memories"],
            "categories": {
                "memories": learned_counts["memories"],
                "rules": learned_counts["rules"],
                "trajectories": len(trajectory_rows),
            },
        },
        "knowledge_graph": kg,
        "learning_events": learning_events,
        "improvement_score": improvement_score,
        "proactive_learning": {
            "enabled": int(intelligence["enabled_subscriptions"]) > 0,
            "is_running": False,
            "total_reports": intelligence["total_reports"],
            "subscriptions": intelligence["subscriptions"],
            "enabled_subscriptions": intelligence["enabled_subscriptions"],
            "last_report_at": intelligence["last_report_at"],
            "total_skills_created": len(auto_skills),
        },
        "source": "journal",
    }


def evolution_story_payload(
    journal: Any,
    registry: Any,
    planner: Any,
    thread_store: Any,
    *,
    limit: int,
) -> dict[str, Any]:
    """Build plain-language evidence for what the system actually learned."""

    def _section_items(value: Any) -> list[str]:
        items: list[str] = []
        for line in str(value or "").splitlines():
            stripped = line.lstrip()
            if stripped.startswith("- ["):
                items.append(stripped[2:].strip())
        return items

    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()

    def _learning_points(answer: str) -> list[str]:
        """Extract concise claims from a real answer without inventing a summary."""
        import re

        def _is_learning(value: str) -> bool:
            status_phrases = (
                "当前进度已暂停",
                "等待继续",
                "点击继续",
                "我不确定",
                "请补充信息",
                "请简要说明",
            )
            return not any(phrase in value for phrase in status_phrases)

        lines = [line.strip() for line in answer.splitlines()]
        points: list[str] = []
        in_conclusion = False
        for line in lines:
            if re.search(r"核心结论|主要结论|关键结论|结论摘要", line):
                in_conclusion = True
                continue
            if in_conclusion and line.startswith("##"):
                break
            if in_conclusion and re.match(r"^[-*]\s+", line):
                value = re.sub(r"^[-*]\s+", "", line).strip()
                value = re.sub(r"[*_`]+", "", value)
                if value and _is_learning(value):
                    points.append(value[:220])
            if len(points) >= 3:
                return points

        if points:
            return points

        for line in lines:
            if not line or line.startswith(("#", "---", "```", "|")):
                continue
            value = re.sub(r"^[-*\d.、)\s]+", "", line)
            value = re.sub(r"[*_`]+", "", value).strip()
            if len(value) < 12 or value.startswith(("文件位置", "报告完成日期")):
                continue
            if not _is_learning(value):
                continue
            points.append(value[:220])
            if len(points) >= 2:
                break
        return points

    rules = _section_items(getattr(planner, "learned_rules_section", ""))
    memories = _section_items(getattr(planner, "learned_memories_section", ""))
    auto_skills = [
        name for name in _registry_skill_names(registry) if _registry_skill_is_auto(registry, name)
    ]

    changes: list[dict[str, Any]] = []
    changes.extend(
        {
            "kind": "rule",
            "title": "Learned a safer recovery rule",
            "content": rule,
            "effect": "This rule is injected into future planning before tools run.",
        }
        for rule in rules
    )
    changes.extend(
        {
            "kind": "memory",
            "title": "Remembered a reusable pattern",
            "content": memory,
            "effect": "This memory is available when a similar task appears again.",
        }
        for memory in memories
    )
    for name in auto_skills:
        description = ""
        with suppress(AttributeError, KeyError, TypeError):
            description = str(getattr(registry.get(name), "description", "") or "")
        changes.append(
            {
                "kind": "skill",
                "title": name,
                "content": description or name,
                "effect": "The agent can call this learned skill in future tasks.",
            }
        )

    task_titles: dict[str, str] = {}
    if journal is not None:
        try:
            user_events = list(journal.read_by_type("user/message"))
        except (AttributeError, TypeError, OSError):
            user_events = []
        for event in user_events:
            task_id = str(getattr(event, "task_id", "") or "")
            text = str(getattr(event, "text", "") or "").strip()
            if task_id and text:
                task_titles[task_id] = text

    trajectory_rows = sorted(
        _trajectory_rows(journal),
        key=lambda row: _as_dt(getattr(row[1], "completed_at", None)) or _utcnow(),
        reverse=True,
    )
    observations: list[dict[str, Any]] = []
    seen_tasks: set[str] = set()
    for _event, trajectory in trajectory_rows:
        task_id = str(getattr(trajectory, "task_id", "") or "")
        if task_id in seen_tasks:
            continue
        seen_tasks.add(task_id)
        tools: list[str] = []
        for step in getattr(trajectory, "steps", []) or []:
            name = str(getattr(getattr(step, "action", None), "sucker_id", "") or "")
            if name and name not in tools:
                tools.append(name)
        outcome = getattr(trajectory, "outcome", None)
        disposition = str(getattr(outcome, "disposition", "") or "")
        success = bool(getattr(outcome, "success", False))
        observations.append(
            {
                "task_id": task_id,
                "thread_id": getattr(trajectory, "thread_id", None),
                "title": task_titles.get(task_id, ""),
                "timestamp": _iso(getattr(trajectory, "completed_at", None)),
                "status": disposition or ("completed" if success else "failed"),
                "success": success,
                "step_count": len(getattr(trajectory, "steps", []) or []),
                "tools": tools[:6],
                "learning_points": [],
            }
        )
        if len(observations) >= limit:
            break

    # Thread history is durable while some lightweight journal backends are
    # process-local. Use recent conversations as observation-only evidence
    # when trajectories are absent; they never count as learned changes.
    observed_thread_ids = {
        str(item.get("thread_id") or "") for item in observations if item.get("thread_id")
    }
    if len(observations) < limit and thread_store is not None:
        try:
            recent_threads = list(thread_store.search(limit=limit * 3, sort_by="updated_at") or [])
        except (AttributeError, TypeError, OSError):
            recent_threads = []
        if recent_threads is None:
            recent_threads = []
        for thread in recent_threads:
            thread_id = str(thread.get("thread_id") or "")
            if not thread_id or thread_id in observed_thread_ids:
                continue
            values: dict[str, Any] = (
                thread.get("values") if isinstance(thread.get("values"), dict) else {}
            )
            raw_messages = values.get("messages")
            messages: list[Any] = raw_messages if isinstance(raw_messages, list) else []
            human_text = ""
            latest_answer = ""
            tool_count = 0
            for message in messages:
                if not isinstance(message, dict):
                    continue
                if not human_text and message.get("type") == "human":
                    content = message.get("content")
                    human_text = str(content if isinstance(content, str) else "").strip()
                if message.get("type") == "ai":
                    extra = message.get("additional_kwargs")
                    kind = extra.get("message_kind") if isinstance(extra, dict) else None
                    if kind == "answer":
                        answer_text = _message_text(message.get("content"))
                        if answer_text:
                            latest_answer = answer_text
                calls = message.get("tool_calls")
                if isinstance(calls, list):
                    tool_count += len(calls)
            title = str(values.get("title") or "").strip()
            if title.lower() in {"new chat", "untitled", "新对话", "未命名"}:
                title = ""
            if not title:
                title = human_text
            if not title and not messages:
                continue
            status = str(thread.get("status") or "idle")
            observations.append(
                {
                    "task_id": thread_id,
                    "thread_id": thread_id,
                    "title": title,
                    "timestamp": str(thread.get("updated_at") or thread.get("created_at") or ""),
                    "status": status,
                    "success": status not in {"error", "failed", "cancelled"},
                    "step_count": tool_count,
                    "tools": [],
                    "learning_points": _learning_points(latest_answer),
                }
            )
            observed_thread_ids.add(thread_id)
            if len(observations) >= limit:
                break

    return {
        "has_real_change": bool(changes),
        "observed_task_count": len(observations),
        "durable_change_count": len(changes),
        "rule_count": len(rules),
        "memory_count": len(memories),
        "skill_count": len(auto_skills),
        "changes": changes,
        "observations": observations,
    }


def evolution_memory_growth_payload(
    journal: Any,
    registry: Any,
    planner: Any,
    *,
    days: int,
) -> list[dict[str, Any]]:
    from collections import defaultdict
    from datetime import timedelta

    by_day: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "fact": 0,
            "preference": 0,
            "learned_skill": 0,
            "relationship": 0,
        }
    )
    cutoff = _utcnow() - timedelta(days=days)
    for item in _skill_step_rows(journal):
        ts = item["ts"]
        if ts and ts >= cutoff:
            bucket = by_day[_date_key(ts)]
            if _registry_skill_is_auto(registry, item["skill_name"]):
                bucket["learned_skill"] += 1
            else:
                bucket["fact"] += 1

    learned = _learned_section_counts(planner)
    if learned["memories"] > 0:
        by_day[_date_key(_utcnow())]["fact"] += learned["memories"]
    if learned["rules"] > 0:
        by_day[_date_key(_utcnow())]["relationship"] += learned["rules"]

    return [{"date": day, **counts} for day, counts in sorted(by_day.items())]


def evolution_learning_curve_payload(journal: Any, *, weeks: int) -> list[dict[str, Any]]:
    from collections import defaultdict
    from datetime import timedelta

    cutoff = _utcnow() - timedelta(weeks=weeks)
    buckets: dict[str, list[Any]] = defaultdict(list)
    for _event, traj in _trajectory_rows(journal):
        completed = _as_dt(getattr(traj, "completed_at", None))
        if completed is None or completed < cutoff:
            continue
        buckets[_week_key(completed)].append(traj)

    rows: list[dict[str, Any]] = []
    for week, trajs in sorted(buckets.items()):
        if not trajs:
            continue
        success_rate = sum(1 for t in trajs if getattr(t.outcome, "success", False)) / len(trajs)
        durations = [
            max(
                0.0,
                (
                    (_as_dt(getattr(t, "completed_at", None)) or _utcnow())
                    - (_as_dt(getattr(t, "started_at", None)) or _utcnow())
                ).total_seconds()
                * 1000,
            )
            for t in trajs
        ]
        skill_count = sum(len(getattr(t, "steps", []) or []) for t in trajs)
        rows.append(
            {
                "week": week,
                "success_rate": success_rate,
                "avg_duration_ms": (sum(durations) / len(durations) if durations else 0),
                "skills_used": skill_count,
            }
        )
    return rows


def evolution_recommendations_payload(
    journal: Any,
    registry: Any,
    planner: Any,
) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for row in _skill_performance_rows(journal, registry):
        usage = int(row["usage_count"])
        rate = float(row["success_rate"])
        if usage >= 3 and rate < 0.6:
            recommendations.append(
                {
                    "type": "declining_skill",
                    "title": f"Review {row['name']}",
                    "description": (
                        f"{row['name']} succeeded on {rate:.0%} of "
                        f"{usage} recent calls. Check arguments, permissions, "
                        "or add a narrower recovery rule."
                    ),
                    "severity": "warning" if rate >= 0.35 else "critical",
                    "action_label": "Inspect failures",
                    "meta": {"skill_name": row["name"], "usage_count": usage},
                }
            )

    learned = _learned_section_counts(planner)
    if learned["rules"] == 0 and len(_trajectory_rows(journal)) >= 3:
        recommendations.append(
            {
                "type": "extraction_opportunity",
                "title": "Run reflection",
                "description": (
                    "Recent trajectories exist, but no learned mitigation "
                    "rules are active yet. Run the reflection pass to extract "
                    "repeatable lessons."
                ),
                "severity": "info",
                "action_label": "Reflect",
                "meta": {"trajectory_count": len(_trajectory_rows(journal))},
            }
        )
    return recommendations[:12]


__all__ = [
    "evolution_learning_curve_payload",
    "evolution_memory_growth_payload",
    "evolution_overview_payload",
    "evolution_recommendations_payload",
    "evolution_story_payload",
]
