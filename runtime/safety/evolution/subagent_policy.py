"""Operator-managed subagent routing policy.

Fitness is evidence; this file is the human decision layer. Operators can put a
subagent role on watch, retire it, or clear the manual policy. Routing consults
this policy before fitness so explicit retirements are enforced immediately.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from runtime.platform.io import atomic_write_json, read_json_with_backup
from runtime.platform.process.paths import app_paths

_SCHEMA = "echo.subagent_policy.v1"
PolicyAction = Literal["watch", "retire", "clear"]
PolicyStatus = Literal["watch", "retired"]


class SubagentPolicyStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else app_paths().subagent_policy_path

    def summary(self) -> dict[str, Any]:
        payload = self._read()
        policies = dict(payload.get("policies") or {})
        return {
            "schema": _SCHEMA,
            "policies": policies,
            "policy_count": len(policies),
            "retired_count": sum(
                1
                for item in policies.values()
                if isinstance(item, dict) and item.get("status") == "retired"
            ),
            "watch_count": sum(
                1
                for item in policies.values()
                if isinstance(item, dict) and item.get("status") == "watch"
            ),
            "lastUpdated": str(payload.get("lastUpdated") or ""),
        }

    def get(self, role: str) -> dict[str, Any] | None:
        role_id = _clean(role, limit=80)
        if not role_id:
            return None
        item = (self._read().get("policies") or {}).get(role_id)
        return dict(item) if isinstance(item, dict) else None

    def decide(
        self,
        role: str,
        *,
        action: str,
        reason: str = "",
        evidence_item_ids: list[str] | None = None,
        actor: str = "operator_panel",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        role_id = _clean(role, limit=80)
        if not role_id:
            raise ValueError("role is required")
        decision = _clean(action, limit=40).lower()
        if decision not in {"watch", "retire", "clear"}:
            raise ValueError("action must be one of: watch, retire, clear")

        payload = self._read()
        policies = dict(payload.get("policies") or {})
        history = list(payload.get("history") or [])
        now_text = (now or datetime.now(UTC)).isoformat()
        evidence = [
            item
            for item in (_clean(value, limit=120) for value in (evidence_item_ids or []))
            if item
        ][:20]
        record = {
            "role": role_id,
            "action": decision,
            "reason": _clean(reason, limit=800),
            "evidence_item_ids": evidence,
            "actor": _clean(actor, limit=120) or "operator_panel",
            "ts": now_text,
        }

        if decision == "clear":
            policies.pop(role_id, None)
            policy = None
        else:
            status: PolicyStatus = "retired" if decision == "retire" else "watch"
            policy = {
                "role": role_id,
                "status": status,
                "reason": record["reason"],
                "evidence_item_ids": evidence,
                "actor": record["actor"],
                "updated_at": now_text,
            }
            policies[role_id] = policy

        history.append(record)
        payload["policies"] = dict(sorted(policies.items()))
        payload["history"] = history[-500:]
        payload["lastUpdated"] = now_text
        self._write(payload)
        return {
            "schema": _SCHEMA,
            "role": role_id,
            "action": decision,
            "policy": policy,
            "summary": self.summary(),
        }

    def _read(self) -> dict[str, Any]:
        raw = read_json_with_backup(self.path, default=None)
        if not isinstance(raw, dict):
            return _empty_payload()
        return _normalize(raw)

    def _write(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.path, _normalize(payload))


def evaluate_agent_policy(
    assignments: Mapping[str, str],
    *,
    path: str | Path | None = None,
    store: SubagentPolicyStore | None = None,
) -> dict[str, Any]:
    """Report operator policies that affect a role->agent assignment map."""
    policy_store = store or SubagentPolicyStore(path)
    summary = policy_store.summary()
    policies = summary.get("policies") if isinstance(summary, dict) else {}
    policies = policies if isinstance(policies, dict) else {}
    retired: list[dict[str, Any]] = []
    watch: list[dict[str, Any]] = []
    for raw_role, raw_agent_id in sorted(assignments.items()):
        role = _clean(raw_role, limit=80)
        agent_id = _clean(raw_agent_id, limit=80)
        if not role or not agent_id:
            continue
        policy = policies.get(agent_id)
        if not isinstance(policy, dict):
            continue
        item = {
            "role": role,
            "agent_id": agent_id,
            "status": _clean(policy.get("status"), limit=40),
            "reason": _clean(policy.get("reason"), limit=800),
            "actor": _clean(policy.get("actor"), limit=120),
            "updated_at": _clean(policy.get("updated_at"), limit=80),
            "evidence_item_ids": [
                value
                for value in (
                    _clean(raw_id, limit=120) for raw_id in (policy.get("evidence_item_ids") or [])
                )
                if value
            ][:20],
        }
        if item["status"] == "retired":
            retired.append(item)
        elif item["status"] == "watch":
            watch.append(item)

    return {
        "status": "blocked" if retired else "watch" if watch else "clear",
        "blocked": bool(retired),
        "retired": retired,
        "watch": watch,
        "retired_count": len(retired),
        "watch_count": len(watch),
        "policy_count": summary.get("policy_count", 0),
        "lastUpdated": str(summary.get("lastUpdated") or ""),
    }


def _empty_payload() -> dict[str, Any]:
    return {
        "schema": _SCHEMA,
        "version": 1,
        "lastUpdated": "",
        "policies": {},
        "history": [],
    }


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    payload = _empty_payload()
    policies: dict[str, dict[str, Any]] = {}
    for role, item in (raw.get("policies") or {}).items():
        if not isinstance(item, dict):
            continue
        role_id = _clean(item.get("role") or role, limit=80)
        status = _clean(item.get("status"), limit=40)
        if not role_id or status not in {"watch", "retired"}:
            continue
        policies[role_id] = {
            "role": role_id,
            "status": status,
            "reason": _clean(item.get("reason"), limit=800),
            "evidence_item_ids": [
                value
                for value in (
                    _clean(raw_id, limit=120) for raw_id in (item.get("evidence_item_ids") or [])
                )
                if value
            ][:20],
            "actor": _clean(item.get("actor"), limit=120) or "operator_panel",
            "updated_at": _clean(item.get("updated_at"), limit=80),
        }
    history = [row for row in (raw.get("history") or []) if isinstance(row, dict)][-500:]
    payload.update(
        {
            "lastUpdated": _clean(raw.get("lastUpdated"), limit=80),
            "policies": dict(sorted(policies.items())),
            "history": history,
        }
    )
    return payload


def _clean(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip()


__all__ = ["SubagentPolicyStore", "evaluate_agent_policy"]
