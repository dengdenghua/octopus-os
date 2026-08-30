from __future__ import annotations

import json
import threading
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import app_paths

SCHEMA = "echo.auto_verifier_metrics.v1"
DECISION_SCHEMA = "echo.auto_verifier_decision.v1"
BATCH_SCHEMA = "echo.auto_verifier_batch.v1"
REPAIR_ATTEMPT_SCHEMA = "echo.verification_repair_attempt.v1"
DRIFT_REPAIR_QUEUE_SCHEMA = "echo.verifier_drift_repair_queue.v1"
DRIFT_REPAIR_ITEM_SCHEMA = "echo.verifier_drift_repair_route.v1"
_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class AutoVerifierMetric:
    ts: str
    command: str
    family: str
    kind: str
    ok: bool
    exit_code: int | None
    duration_ms: int
    target: str = ""
    reason: str = ""


def record_auto_verifier_metric(
    *,
    command: str,
    kind: str,
    ok: bool,
    exit_code: int | None,
    duration_ms: int,
    target: str = "",
    reason: str = "",
    path: str | Path | None = None,
) -> None:
    metric = AutoVerifierMetric(
        ts=datetime.now(UTC).isoformat(),
        command=command,
        family=command_family(command),
        kind=kind,
        ok=bool(ok),
        exit_code=exit_code,
        duration_ms=max(0, int(duration_ms)),
        target=target,
        reason=reason,
    )
    target_path = Path(path) if path is not None else app_paths().auto_verifier_metrics_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(metric), ensure_ascii=False) + "\n"
    with _LOCK, target_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def summarize_auto_verifier_metrics(
    *,
    path: str | Path | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    records = _read_metrics(path=path, limit=limit)
    by_family: dict[str, list[AutoVerifierMetric]] = defaultdict(list)
    for record in records:
        by_family[record.family].append(record)

    families = [
        _family_summary(family, rows)
        for family, rows in sorted(by_family.items(), key=lambda item: item[0])
    ]
    total = len(records)
    passed = sum(1 for record in records if record.ok)
    return {
        "schema": SCHEMA,
        "total": total,
        "pass_count": passed,
        "fail_count": total - passed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "avg_duration_ms": _avg(record.duration_ms for record in records),
        "families": families,
        "alerts": _drift_alerts(families),
        "recent_decisions": recent_auto_verifier_decisions(limit=20),
        "recent_batches": recent_auto_verifier_batches(limit=20),
        "recent_repair_attempts": recent_auto_verifier_repair_attempts(limit=20),
        "top_failures": [
            {"command": command, "count": count}
            for command, count in Counter(
                record.command for record in records if not record.ok
            ).most_common(5)
        ],
    }


def queue_verifier_drift_backlog(
    *,
    metrics_path: str | Path | None = None,
    review_queue_path: str | Path | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    report = summarize_auto_verifier_metrics(path=metrics_path, limit=limit)
    alerts = [alert for alert in report.get("alerts") or [] if isinstance(alert, dict)]
    if not alerts:
        return {
            "schema": DRIFT_REPAIR_QUEUE_SCHEMA,
            "created": 0,
            "updated": 0,
            "alerts": [],
            "items": [],
            "summary": {
                "total": report.get("total", 0),
                "fail_count": report.get("fail_count", 0),
                "pass_rate": report.get("pass_rate", 0.0),
            },
        }

    from runtime.memory.learning.review_queue import ReviewQueue

    queue = ReviewQueue(
        Path(review_queue_path) if review_queue_path is not None else app_paths().review_queue_path,
    )
    created = 0
    updated = 0
    items: list[dict[str, Any]] = []
    for alert in alerts:
        result = queue.upsert_item(
            source="auto_verifier_metrics",
            source_kind="repair_route",
            candidate_kind=_drift_candidate_kind(alert),
            priority=_drift_priority(alert),
            target_bucket="experiment_backlog",
            title=_drift_title(alert),
            text=_drift_text(alert),
            metadata={
                "schema": DRIFT_REPAIR_ITEM_SCHEMA,
                "alert": alert,
                "repair_route": _drift_repair_route(alert),
                "metrics": {
                    "schema": SCHEMA,
                    "window_limit": limit,
                    "total": report.get("total", 0),
                    "fail_count": report.get("fail_count", 0),
                    "pass_rate": report.get("pass_rate", 0.0),
                },
            },
            tags=[
                "auto_verifier",
                "verifier_drift",
                "repair_route",
                str(alert.get("family") or "other"),
            ],
        )
        created += int(result.get("created") or 0)
        updated += int(result.get("updated") or 0)
        items.extend(result.get("items") or [])
    return {
        "schema": DRIFT_REPAIR_QUEUE_SCHEMA,
        "created": created,
        "updated": updated,
        "alerts": alerts,
        "items": items,
        "summary": {
            "total": report.get("total", 0),
            "fail_count": report.get("fail_count", 0),
            "pass_rate": report.get("pass_rate", 0.0),
        },
    }


def _drift_alerts(families: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for family in families:
        total = int(family.get("total") or 0)
        fail_count = int(family.get("fail_count") or 0)
        pass_rate = float(family.get("pass_rate") or 0.0)
        if total < 3 or fail_count < 2 or pass_rate >= 0.7:
            continue
        severity = "critical" if pass_rate < 0.5 else "warning"
        top_command = ""
        commands = family.get("commands")
        if isinstance(commands, list) and commands:
            first = commands[0]
            if isinstance(first, dict):
                top_command = str(first.get("command") or "")
        name = str(family.get("family") or "other")
        alerts.append(
            {
                "family": name,
                "severity": severity,
                "total": total,
                "fail_count": fail_count,
                "pass_rate": round(pass_rate, 3),
                "latest_ts": str(family.get("latest_ts") or ""),
                "top_command": top_command,
                "message": (
                    f"{name} verifier family is drifting: {fail_count}/{total} recent runs failed"
                ),
            }
        )
    return alerts


def _drift_candidate_kind(alert: dict[str, Any]) -> str:
    family = str(alert.get("family") or "other").strip() or "other"
    return f"verifier_drift:{family}"


def _drift_priority(alert: dict[str, Any]) -> str:
    return "P0" if str(alert.get("severity") or "") == "critical" else "P1"


def _drift_title(alert: dict[str, Any]) -> str:
    family = str(alert.get("family") or "other").strip() or "other"
    return f"Repair verifier drift: {family}"


def _drift_text(alert: dict[str, Any]) -> str:
    message = str(alert.get("message") or "").strip()
    command = str(alert.get("top_command") or "").strip()
    pass_rate = float(alert.get("pass_rate") or 0.0)
    fail_count = int(alert.get("fail_count") or 0)
    total = int(alert.get("total") or 0)
    lines = [
        message or f"Verifier family {alert.get('family') or 'other'} is drifting.",
        f"Recent pass rate: {pass_rate:.3f} ({fail_count}/{total} failures).",
    ]
    if command:
        lines.append(f"Top failing command: {command}")
    lines.append(
        "Route this to a repair experiment before the verifier family is trusted again.",
    )
    return "\n".join(lines)


def _drift_repair_route(alert: dict[str, Any]) -> dict[str, Any]:
    family = str(alert.get("family") or "other").strip() or "other"
    return {
        "schema": "echo.verifier_drift_repair_route.plan.v1",
        "family": family,
        "route": "verification_repair",
        "required_evidence": [
            "reproduce the failing verifier command",
            "classify environment noise versus real regression",
            "attach passing rerun evidence before promotion",
        ],
        "suggested_command": str(alert.get("top_command") or ""),
        "blocks_auto_promotion": True,
    }


def rank_verification_commands(
    commands: list[dict[str, Any]],
    *,
    path: str | Path | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    records = _read_metrics(path=path, limit=limit)
    quality = _family_quality(records)

    def sort_key(indexed: tuple[int, dict[str, Any]]) -> tuple[float, float, float, int]:
        index, command = indexed
        priority = int(command.get("priority") or 99)
        family = command_family(str(command.get("command") or ""))
        stats = quality.get(
            family,
            {
                "pass_rate": 0.5,
                "avg_duration_ms": 999999.0,
                "priority_penalty": 0.0,
            },
        )
        return (
            priority + float(stats["priority_penalty"]),
            -float(stats["pass_rate"]),
            float(stats["avg_duration_ms"]),
            index,
        )

    return [
        command
        for _index, command in sorted(
            list(enumerate(commands)),
            key=sort_key,
        )
    ]


def explain_verification_ranking(
    commands: list[dict[str, Any]],
    *,
    path: str | Path | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    records = _read_metrics(path=path, limit=limit)
    quality = _family_quality(records)

    def sort_key(indexed: tuple[int, dict[str, Any]]) -> tuple[float, float, float, int]:
        index, command = indexed
        stats = _command_stats(command, quality)
        return (
            int(command.get("priority") or 99) + float(stats["priority_penalty"]),
            -float(stats["pass_rate"]),
            float(stats["avg_duration_ms"]),
            index,
        )

    ranked: list[dict[str, Any]] = []
    for rank, (index, command) in enumerate(
        sorted(list(enumerate(commands)), key=sort_key),
        start=1,
    ):
        stats = _command_stats(command, quality)
        ranked.append(
            {
                "rank": rank,
                "command": str(command.get("command") or ""),
                "kind": str(command.get("kind") or "manual"),
                "priority": int(command.get("priority") or 99),
                "family": stats["family"],
                "history_count": stats["count"],
                "pass_rate": round(float(stats["pass_rate"]), 3),
                "avg_duration_ms": float(stats["avg_duration_ms"]),
                "reason": _ranking_reason(command, stats),
                "original_index": index,
            }
        )
    return ranked


def record_auto_verifier_decision(
    *,
    candidates: list[dict[str, Any]],
    selected_command: str,
    path: str | Path | None = None,
) -> None:
    decision_path = Path(path) if path is not None else app_paths().auto_verifier_decisions_path
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": DECISION_SCHEMA,
        "ts": datetime.now(UTC).isoformat(),
        "selected_command": selected_command,
        "candidates": candidates,
    }
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with _LOCK, decision_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def record_auto_verifier_batch(
    *,
    candidate_count: int,
    commands: list[str],
    passed_count: int,
    stop_reason: str,
    path: str | Path | None = None,
) -> None:
    decision_path = Path(path) if path is not None else app_paths().auto_verifier_decisions_path
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": BATCH_SCHEMA,
        "ts": datetime.now(UTC).isoformat(),
        "candidate_count": max(0, int(candidate_count)),
        "attempted_count": len(commands),
        "passed_count": max(0, int(passed_count)),
        "commands": commands,
        "complete": bool(commands) and passed_count == len(commands) and stop_reason != "failed",
        "stop_reason": stop_reason,
    }
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with _LOCK, decision_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def record_auto_verifier_repair_attempt(
    *,
    attempt: int,
    max_attempts: int,
    status: str,
    failed_commands: list[str],
    fresh_evidence_commands: list[str] | None = None,
    path: str | Path | None = None,
) -> None:
    decision_path = Path(path) if path is not None else app_paths().auto_verifier_decisions_path
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": REPAIR_ATTEMPT_SCHEMA,
        "ts": datetime.now(UTC).isoformat(),
        "attempt": max(1, int(attempt)),
        "max_attempts": max(1, int(max_attempts)),
        "status": str(status or "unknown"),
        "failed_commands": [str(command) for command in failed_commands if str(command).strip()],
        "fresh_evidence_commands": [
            str(command) for command in (fresh_evidence_commands or []) if str(command).strip()
        ],
    }
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with _LOCK, decision_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def recent_auto_verifier_decisions(
    *,
    path: str | Path | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    decision_path = Path(path) if path is not None else app_paths().auto_verifier_decisions_path
    if not decision_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with _LOCK, decision_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict) and raw.get("schema") == DECISION_SCHEMA:
                rows.append(raw)
    return rows[-max(1, int(limit)) :]


def recent_auto_verifier_batches(
    *,
    path: str | Path | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    decision_path = Path(path) if path is not None else app_paths().auto_verifier_decisions_path
    if not decision_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with _LOCK, decision_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict) and raw.get("schema") == BATCH_SCHEMA:
                rows.append(raw)
    return rows[-max(1, int(limit)) :]


def recent_auto_verifier_repair_attempts(
    *,
    path: str | Path | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    decision_path = Path(path) if path is not None else app_paths().auto_verifier_decisions_path
    if not decision_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with _LOCK, decision_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict) and raw.get("schema") == REPAIR_ATTEMPT_SCHEMA:
                rows.append(raw)
    return rows[-max(1, int(limit)) :]


def command_family(command: str) -> str:
    text = " ".join(command.strip().split())
    if text.startswith("python -m ruff ") or " python -m ruff " in f" {text} ":
        return "ruff"
    if text.startswith("python -m pytest ") or text == "python -m pytest":
        return "pytest"
    if text.startswith("cd ") and "&& python -m pytest" in text:
        return "pytest"
    if text.startswith("pnpm check") or text.endswith("&& pnpm check"):
        return "pnpm_check"
    if "pnpm vitest run" in text:
        return "pnpm_vitest"
    return "other"


def _read_metrics(
    *,
    path: str | Path | None,
    limit: int,
) -> list[AutoVerifierMetric]:
    target_path = Path(path) if path is not None else app_paths().auto_verifier_metrics_path
    if not target_path.is_file():
        return []
    rows: list[AutoVerifierMetric] = []
    with _LOCK, target_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    continue
                rows.append(
                    AutoVerifierMetric(
                        ts=str(raw.get("ts") or ""),
                        command=str(raw.get("command") or ""),
                        family=str(
                            raw.get("family") or command_family(str(raw.get("command") or ""))
                        ),
                        kind=str(raw.get("kind") or "manual"),
                        ok=bool(raw.get("ok")),
                        exit_code=(
                            int(raw["exit_code"]) if raw.get("exit_code") is not None else None
                        ),
                        duration_ms=int(raw.get("duration_ms") or 0),
                        target=str(raw.get("target") or ""),
                        reason=str(raw.get("reason") or ""),
                    )
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
    return rows[-max(1, int(limit)) :]


def _family_summary(family: str, rows: list[AutoVerifierMetric]) -> dict[str, Any]:
    passed = sum(1 for record in rows if record.ok)
    return {
        "family": family,
        "total": len(rows),
        "pass_count": passed,
        "fail_count": len(rows) - passed,
        "pass_rate": round(passed / len(rows), 3) if rows else 0.0,
        "avg_duration_ms": _avg(record.duration_ms for record in rows),
        "latest_ts": rows[-1].ts if rows else "",
        "commands": [
            {"command": command, "count": count}
            for command, count in Counter(record.command for record in rows).most_common(5)
        ],
    }


def _family_quality(records: list[AutoVerifierMetric]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[AutoVerifierMetric]] = defaultdict(list)
    for record in records:
        grouped[record.family].append(record)
    out: dict[str, dict[str, float]] = {}
    for family, rows in grouped.items():
        passed = sum(1 for record in rows if record.ok)
        # Laplace smoothing keeps one lucky pass or fail from dominating
        # a brand-new command family.
        out[family] = {
            "pass_rate": (passed + 1) / (len(rows) + 2),
            "avg_duration_ms": _avg(record.duration_ms for record in rows),
            "count": float(len(rows)),
            "pass_count": float(passed),
            "fail_count": float(len(rows) - passed),
            "priority_penalty": _drift_priority_penalty(rows),
        }
    return out


def _drift_priority_penalty(rows: list[AutoVerifierMetric]) -> float:
    total = len(rows)
    if total < 3:
        return 0.0
    passed = sum(1 for record in rows if record.ok)
    fail_count = total - passed
    raw_pass_rate = passed / total if total else 0.0
    if fail_count < 2 or raw_pass_rate >= 0.7:
        return 0.0
    return 2.0 if raw_pass_rate < 0.5 else 1.0


def _command_stats(
    command: dict[str, Any],
    quality: dict[str, dict[str, float]],
) -> dict[str, float | str]:
    family = command_family(str(command.get("command") or ""))
    stats = quality.get(family)
    if stats is None:
        return {
            "family": family,
            "pass_rate": 0.5,
            "avg_duration_ms": 999999.0,
            "count": 0.0,
            "pass_count": 0.0,
            "fail_count": 0.0,
            "priority_penalty": 0.0,
        }
    return {"family": family, **stats}


def _ranking_reason(command: dict[str, Any], stats: dict[str, float | str]) -> str:
    priority = int(command.get("priority") or 99)
    family = str(stats["family"])
    count = int(float(stats["count"]))
    if count <= 0:
        return f"priority={priority}; no history for {family}, using neutral score"
    penalty = float(stats["priority_penalty"])
    drift = f", drift_penalty={penalty:.1f}" if penalty > 0 else ""
    return (
        f"priority={priority}; {family} history count={count}, "
        f"smoothed pass_rate={float(stats['pass_rate']):.3f}, "
        f"avg_duration_ms={float(stats['avg_duration_ms']):.1f}{drift}"
    )


def _avg(values: Any) -> float:
    nums = [int(value) for value in values]
    return round(sum(nums) / len(nums), 1) if nums else 0.0


__all__ = [
    "SCHEMA",
    "DECISION_SCHEMA",
    "BATCH_SCHEMA",
    "REPAIR_ATTEMPT_SCHEMA",
    "AutoVerifierMetric",
    "command_family",
    "queue_verifier_drift_backlog",
    "explain_verification_ranking",
    "rank_verification_commands",
    "recent_auto_verifier_batches",
    "recent_auto_verifier_decisions",
    "recent_auto_verifier_repair_attempts",
    "record_auto_verifier_batch",
    "record_auto_verifier_decision",
    "record_auto_verifier_metric",
    "record_auto_verifier_repair_attempt",
    "summarize_auto_verifier_metrics",
]
