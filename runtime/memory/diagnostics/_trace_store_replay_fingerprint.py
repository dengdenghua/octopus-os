"""Stable fingerprinting for sanitized task-run replay steps."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def task_run_replay_fingerprint(steps: list[dict[str, Any]]) -> str:
    normalized: list[dict[str, Any]] = []
    for step in steps:
        kind = str(step.get("kind") or "")
        item: dict[str, Any] = {"kind": kind}
        if kind in {"tool_start", "tool_end"}:
            item.update(
                {
                    "tool": str(step.get("tool") or ""),
                    "status": str(step.get("status") or ""),
                    "is_error": bool(step.get("is_error")),
                    "input_preview": str(step.get("input_preview") or ""),
                    "output_preview": str(step.get("output_preview") or ""),
                }
            )
            raw_approval = step.get("approval")
            approval = raw_approval if isinstance(raw_approval, dict) else {}
            item["approval"] = {
                "decision": str(approval.get("decision") or ""),
                "risk_level": str(approval.get("risk_level") or ""),
            }
        elif kind == "task_start":
            item.update(
                {
                    "goal": str(step.get("goal") or ""),
                    "mode": str(step.get("mode") or ""),
                }
            )
        elif kind == "task_event":
            item.update(
                {
                    "event_type": str(step.get("event_type") or ""),
                    "status": str(step.get("status") or ""),
                    "reason": str(step.get("reason") or ""),
                }
            )
        normalized.append(item)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


__all__ = ["task_run_replay_fingerprint"]
