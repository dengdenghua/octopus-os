"""Echo Enterprise 企业服务 Arm 接入.

``echo-enterprise`` 是 Echo 家族的企业版后端 —— 独立 FastAPI
服务,管理项目 / 任务 / 审批 / 人员 / 文档。echo-agent 通过本
skill 调它的 HTTP API,不拥有企业数据本身。

依赖方向(见 docs/PM_INTERFACE.md):agent →(HTTP)→ enterprise
(企业管理面),enterprise →(HTTP)→ agent(AI 引擎),两条
独立链路。本 skill 走 agent → enterprise 方向,让 agent 能查项目
状态 / 提审批单 / 列人员。

服务不可达时自门控:返回清晰的可操作消息,绝不崩 turn。
零新依赖:urllib,与 storage_skills / echo_skills 同构。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase

_DEFAULT_URL = "http://127.0.0.1:3100"
_TIMEOUT_S = 10.0
_MAX_LIMIT = 100


def _base_url() -> str:
    raw = (os.environ.get("ECHO_ENTERPRISE_URL") or "").strip()
    return (raw or _DEFAULT_URL).rstrip("/")


def _api_token() -> str | None:
    return (os.environ.get("ECHO_ENTERPRISE_TOKEN") or "").strip() or None


def _request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = _TIMEOUT_S,
) -> dict[str, Any] | None:
    """一次 best-effort 调用。服务不可达 / 出错时返回 None,绝不抛。"""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    token = _api_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        _base_url() + path,
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 — audited HTTP endpoint
            body = resp.read().decode("utf-8", "replace")
        return json.loads(body) if body.strip() else {}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _unavailable() -> dict[str, Any]:
    return {
        "ok": False,
        "available": False,
        "message": (
            f"企业版服务(echo-enterprise)未运行或不可达({_base_url()})。"
            "请先启动 enterprise 后端服务后再试。"
        ),
    }


# ─── skill: enterprise_list_tasks ──────────────────────────


def _list_tasks(
    project_id: str = "",
    status: str = "",
    limit: int | str = 20,
    **kw: Any,
) -> dict[str, Any]:
    """查询指定项目的任务列表。"""
    project_id = str(project_id or kw.get("project") or "").strip()
    if not project_id:
        return {
            "ok": False,
            "available": True,
            "error": "project_id is required",
            "tasks": [],
            "count": 0,
        }
    try:
        n = max(1, min(_MAX_LIMIT, int(limit)))
    except (TypeError, ValueError):
        n = 20
    params = f"?skip=0&limit={n}"
    if status:
        params += f"&status={status}"
    resp = _request("GET", f"/projects/{project_id}/tasks{params}")
    if resp is None:
        return _unavailable()
    data = resp.get("data", []) if isinstance(resp, dict) else []
    return {
        "ok": True,
        "available": True,
        "project_id": project_id,
        "count": resp.get("total", len(data)) if isinstance(resp, dict) else len(data),
        "tasks": data,
    }


_ENTERPRISE_LIST_TASKS_DESC = (
    "List tasks in an Echo Enterprise project. Backed by the "
    "echo-enterprise backend (the enterprise management Arm).\n"
    "\n"
    "Use it when the user asks about project tasks ('项目进度怎样', "
    "'list tasks in project X', '哪些任务还没完成').\n"
    "\n"
    "Args: {project_id: string, status?: string, limit?: int 1-100}.\n"
    "Returns: {ok, available, project_id, count, tasks:[...]}."
)


# ─── skill: enterprise_list_approvals ──────────────────────


def _list_approvals(
    status: str = "",
    limit: int | str = 20,
    **kw: Any,
) -> dict[str, Any]:
    """查询审批单列表。"""
    try:
        n = max(1, min(_MAX_LIMIT, int(limit)))
    except (TypeError, ValueError):
        n = 20
    params = f"?skip=0&limit={n}"
    if status:
        params += f"&status={status}"
    resp = _request("GET", f"/approvals{params}")
    if resp is None:
        return _unavailable()
    data = resp.get("data", []) if isinstance(resp, dict) else []
    return {
        "ok": True,
        "available": True,
        "count": resp.get("total", len(data)) if isinstance(resp, dict) else len(data),
        "approvals": data,
    }


_ENTERPRISE_LIST_APPROVALS_DESC = (
    "List approval requests in Echo Enterprise. Backed by the "
    "echo-enterprise backend.\n"
    "\n"
    "Use it when the user asks about pending approvals ('有哪些待审批', "
    "'show me pending approvals', '审批状态').\n"
    "\n"
    "Args: {status?: string (pending/approved/rejected), limit?: int}.\n"
    "Returns: {ok, available, count, approvals:[...]}."
)


# ─── skill: enterprise_list_persons ─────────────────────────


def _list_persons(
    limit: int | str = 50,
    **kw: Any,
) -> dict[str, Any]:
    """查询企业人员列表。"""
    try:
        n = max(1, min(_MAX_LIMIT, int(limit)))
    except (TypeError, ValueError):
        n = 50
    resp = _request("GET", f"/persons?skip=0&limit={n}")
    if resp is None:
        return _unavailable()
    data = resp.get("data", []) if isinstance(resp, dict) else []
    return {
        "ok": True,
        "available": True,
        "count": resp.get("total", len(data)) if isinstance(resp, dict) else len(data),
        "persons": data,
    }


_ENTERPRISE_LIST_PERSONS_DESC = (
    "List persons (team members / stakeholders) in Echo Enterprise.\n"
    "\n"
    "Use it when the user asks '团队有谁', 'who is in the project', "
    "'list team members'.\n"
    "\n"
    "Args: {limit?: int 1-100 (default 50)}.\n"
    "Returns: {ok, available, count, persons:[...]}."
)


# ─── registration ──────────────────────────────────────────


def register_enterprise_skills(registry: SkillRegistry) -> int:
    """注册企业版 skill。始终注册;服务不可达时自报告。"""
    registry.register(
        Skill(
            name="enterprise_list_tasks",
            description=_ENTERPRISE_LIST_TASKS_DESC,
            affinity=["enterprise", "project", "task", "pm", "项目管理", "任务"],
            cost_profile="low",
            trusted_source="skill://public/enterprise_list_tasks",
            handler=_list_tasks,
            tests=[
                SkillTestCase(
                    name="missing_project_id_returns_error",
                    tier="golden",
                    args={"project_id": ""},
                    expect=SkillExpect(schema_keys=["ok", "available"]),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("ok") is False
                        and "required" in (r.get("error") or "")
                    ),
                ),
            ],
        ),
        replace=True,
    )
    registry.register(
        Skill(
            name="enterprise_list_approvals",
            description=_ENTERPRISE_LIST_APPROVALS_DESC,
            affinity=["enterprise", "approval", "workflow", "审批", "企业"],
            cost_profile="low",
            trusted_source="skill://public/enterprise_list_approvals",
            handler=_list_approvals,
            tests=[
                SkillTestCase(
                    name="returns_approvals_or_unavailable",
                    tier="golden",
                    args={},
                    expect=SkillExpect(schema_keys=["ok", "available"]),
                ),
            ],
        ),
        replace=True,
    )
    registry.register(
        Skill(
            name="enterprise_list_persons",
            description=_ENTERPRISE_LIST_PERSONS_DESC,
            affinity=["enterprise", "person", "team", "member", "人员", "团队"],
            cost_profile="low",
            trusted_source="skill://public/enterprise_list_persons",
            handler=_list_persons,
            tests=[
                SkillTestCase(
                    name="returns_persons_or_unavailable",
                    tier="golden",
                    args={},
                    expect=SkillExpect(schema_keys=["ok", "available"]),
                ),
            ],
        ),
        replace=True,
    )
    return 3
