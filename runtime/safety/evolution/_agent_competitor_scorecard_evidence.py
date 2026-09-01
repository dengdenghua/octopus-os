from __future__ import annotations

from typing import Any


def _evidence_checklist_item(item: dict[str, Any]) -> dict[str, Any]:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    implementation = (
        evidence.get("implementation") if isinstance(evidence.get("implementation"), dict) else {}
    )
    tests = evidence.get("tests") if isinstance(evidence.get("tests"), dict) else {}
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "score": item.get("score"),
        "status": item.get("status"),
        "implementation": _path_checklist_summary(implementation),
        "tests": _path_checklist_summary(tests),
        "next_actions": list(item.get("next_actions") or []),
    }


def _path_checklist_summary(section: dict[str, Any]) -> dict[str, Any]:
    total = int(section.get("total") or 0)
    present = int(section.get("present") or 0)
    missing = [str(path) for path in section.get("missing", []) if path]
    return {
        "present": present,
        "total": total,
        "missing_count": len(missing),
        "missing": missing,
        "coverage": round(present / total, 3) if total > 0 else 0.0,
    }


def _evidence_readiness(evidence: list[dict[str, Any]]) -> float:
    if not evidence:
        return 0.0
    scores = [float(item.get("score") or 0.0) for item in evidence]
    return round(sum(scores) / len(scores), 3)
