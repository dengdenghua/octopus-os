"""MCP subsystem for evolution operators."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .utils import (
    _as_dt,
    _iso,
    _journal_events,
    _shorten_text,
    _stable_int_id,
    _trajectory_rows,
    _utcnow,
)

_MCP_CAPABILITY_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "server_name": "page-agent",
        "suggested_cmd": "npx -y @page-agent/mcp",
        "keywords": (
            "browser",
            "chrome",
            "dom",
            "webpage",
            "click",
            "screenshot",
            "ui automation",
            "page agent",
            "playwright",
        ),
        "description": (
            "Browser GUI automation via Alibaba Page Agent. Useful when "
            "failures indicate missing page navigation, clicking, or DOM "
            "inspection capabilities."
        ),
        "risk_level": "high",
    },
    {
        "server_name": "github",
        "suggested_cmd": "npx -y @modelcontextprotocol/server-github",
        "keywords": (
            "github",
            "pull request",
            "issue",
            "repository",
            "repo",
            "branch",
            "commit",
        ),
        "description": (
            "GitHub MCP access for issues, pull requests, repository search, "
            "and code review workflows. Requires a scoped GitHub token."
        ),
        "risk_level": "high",
    },
    {
        "server_name": "filesystem",
        "suggested_cmd": "npx -y @modelcontextprotocol/server-filesystem",
        "keywords": ("filesystem", "file system", "directory access", "local files"),
        "description": (
            "Filesystem MCP access for project directories. Vet carefully "
            "because it exposes local file reads and writes."
        ),
        "risk_level": "high",
    },
    {
        "server_name": "postgres",
        "suggested_cmd": "npx -y @modelcontextprotocol/server-postgres",
        "keywords": ("postgres", "postgresql", "sql", "database", "db"),
        "description": (
            "Postgres MCP access for schema inspection and SQL queries. "
            "Requires database credentials and read/write scope review."
        ),
        "risk_level": "high",
    },
    {
        "server_name": "slack",
        "suggested_cmd": "npx -y @modelcontextprotocol/server-slack",
        "keywords": ("slack", "channel", "workspace message"),
        "description": (
            "Slack MCP access for channels and messages. Requires workspace "
            "authorization and privacy review."
        ),
        "risk_level": "high",
    },
    {
        "server_name": "notion",
        "suggested_cmd": "npx -y @modelcontextprotocol/server-notion",
        "keywords": ("notion", "workspace doc", "knowledge base"),
        "description": (
            "Notion MCP access for workspace pages and docs. Requires a scoped integration token."
        ),
        "risk_level": "medium",
    },
)

_MCP_MISSING_CAPABILITY_MARKERS: tuple[str, ...] = (
    "skillnotfound",
    "no skill named",
    "tool_missing",
    "capability_missing",
    "missing capability",
    "missing tool",
    "mcp",
    "not configured",
    "not available",
    "unsupported",
)


def _mcp_proposal_rows(journal: Any) -> list[dict[str, Any]]:
    decisions = _mcp_proposal_decision_map(journal)
    clusters: dict[str, dict[str, Any]] = {}

    for event, traj in _trajectory_rows(journal):
        fallback_ts = (
            _as_dt(getattr(traj, "completed_at", None))
            or _as_dt(getattr(event, "ts", None))
            or _utcnow()
        )
        task_id = str(getattr(traj, "task_id", "") or "")
        for step in getattr(traj, "steps", []) or []:
            if bool(getattr(step, "success", False)):
                continue
            text = _mcp_failure_text(step)
            if not text:
                continue
            lowered = text.lower()
            if not any(marker in lowered for marker in _MCP_MISSING_CAPABILITY_MARKERS):
                continue
            for spec in _MCP_CAPABILITY_CATALOG:
                if not any(keyword in lowered for keyword in spec["keywords"]):
                    continue
                server_name = str(spec["server_name"])
                cluster = clusters.setdefault(
                    server_name,
                    {
                        "spec": spec,
                        "task_ids": set(),
                        "examples": [],
                        "last_seen": fallback_ts,
                    },
                )
                if task_id:
                    cluster["task_ids"].add(task_id)
                else:
                    cluster["task_ids"].add(f"event:{len(cluster['task_ids'])}")
                if len(cluster["examples"]) < 3:
                    cluster["examples"].append(_shorten_text(text, 180))
                if fallback_ts > cluster["last_seen"]:
                    cluster["last_seen"] = fallback_ts

    rows: list[dict[str, Any]] = []
    for server_name, cluster in clusters.items():
        spec = cluster["spec"]
        decision = decisions.get(server_name, {})
        status = str(decision.get("status") or "pending_vet")
        failure_count = len(cluster["task_ids"])
        description = f"{spec['description']} Observed {failure_count} related failure(s)."
        rows.append(
            {
                "id": _stable_int_id(f"mcp:{server_name}"),
                "server_name": server_name,
                "created_at": _iso(cluster["last_seen"]),
                "suggested_cmd": spec["suggested_cmd"],
                "description": description,
                "status": status,
                "risk_level": spec["risk_level"],
                "failure_count": failure_count,
                "examples": cluster["examples"],
            }
        )

    rows.sort(
        key=lambda row: (
            _mcp_status_rank(str(row["status"])),
            -int(row["failure_count"]),
            str(row["server_name"]),
        )
    )
    return rows[:50]


def _mcp_failure_text(step: Any) -> str:
    action = getattr(step, "action", None)
    result = getattr(step, "result", None)
    parts: list[str] = []
    parts.append(str(getattr(action, "sucker_id", "") or ""))
    import contextlib

    with contextlib.suppress(Exception):
        parts.append(str(getattr(action, "args", {}) or {}))
    for attr in ("status", "error_type", "output"):
        parts.append(str(getattr(result, attr, "") or ""))
    with contextlib.suppress(Exception):
        parts.extend(str(tag) for tag in (getattr(result, "stderr_tags", []) or []))
    return " ".join(part for part in parts if part).strip()


def _mcp_status_rank(status: str) -> int:
    return {
        "pending_vet": 0,
        "vetted": 1,
        "install_requested": 2,
        "installed": 3,
        "rejected": 4,
    }.get(status, 9)


def _mcp_proposal_decision_map(journal: Any) -> dict[str, dict[str, Any]]:
    decisions: dict[str, tuple[datetime, dict[str, Any]]] = {}
    if journal is None:
        return {}
    try:
        events = list(journal.read_by_type("mcp_proposal_decision"))
    except (AttributeError, TypeError, OSError):
        events = [
            event
            for event in _journal_events(journal)
            if getattr(event, "event_type", "") == "mcp_proposal_decision"
        ]
    for event in events:
        server_name = str(getattr(event, "server_name", "") or "").strip()
        status = str(getattr(event, "status", "") or "").strip()
        if not server_name or not status:
            continue
        ts = _as_dt(getattr(event, "ts", None)) or _utcnow()
        payload = {
            "status": status,
            "reason": getattr(event, "reason", ""),
        }
        existing = decisions.get(server_name)
        if existing is None or ts >= existing[0]:
            decisions[server_name] = (ts, payload)
    return {key: payload for key, (_ts, payload) in decisions.items()}


def _write_mcp_proposal_decision(
    journal: Any,
    *,
    server_name: str,
    status: str,
    reason: str = "",
    details: dict[str, Any] | None = None,
) -> bool:
    if journal is None:
        return False
    try:
        if hasattr(journal, "write_mcp_proposal_decision"):
            journal.write_mcp_proposal_decision(
                server_name=server_name,
                status=status,
                reason=reason,
                details=details or {},
            )
        else:
            from runtime.memory.journal import McpProposalDecisionEvent

            journal.write(
                McpProposalDecisionEvent(
                    server_name=server_name,
                    status=status,
                    reason=reason,
                    details=details or {},
                )
            )
        return True
    except (AttributeError, TypeError, OSError):
        return False
