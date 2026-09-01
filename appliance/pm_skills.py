"""Echo OS appliance:企业版 PM 工具(D① 编程接入)。

让 OS 的对话 Agent 能通过工具在企业版(echo-enterprise)里列项目 / 建任务 /
列任务——PM 已从 OS 删除(交企业版),这里是 Agent 侧的「可编程接入」:
不打开 UI,直接调企业版的 PM API。

依赖方向(docs:echo-enterprise/docs/PM_INTERFACE.md):OS Agent →(HTTP)→
企业版 PM 服务。配置三项环境变量启用:
- ECHO_PM_URL    企业版地址(如 http://echo-enterprise:8100 或 NAS IP)
- ECHO_PM_TOKEN  企业版登录 JWT(Authorization: Bearer)
- ECHO_PM_TENANT 租户 ID(X-Tenant-ID;单租户可留空)

未配置 ECHO_PM_URL 时这些技能不注册(见 register_pm_skills 的门控)。
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit

_PROJECT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_ROLES = frozenset({"硬件", "固件", "软件", "结构", "AI", "测试", "PM", "供应链"})
_PRIORITIES = frozenset({"p0", "p1", "p2", "p3"})


def _failure(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    status: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "error": message,
        "code": code,
        "retryable": retryable,
    }
    if status is not None:
        result["status"] = status
    return result


def _pm_base() -> str:
    value = (os.environ.get("ECHO_PM_URL") or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("ECHO_PM_URL must be an HTTP(S) service URL without credentials")
    return value


def _pm_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("ECHO_PM_TOKEN")
    if token:
        if len(token) > 8192 or any(character < " " for character in token):
            raise ValueError("ECHO_PM_TOKEN is invalid")
        headers["Authorization"] = f"Bearer {token}"
    tenant = os.environ.get("ECHO_PM_TENANT")
    if tenant:
        if _PROJECT_ID.fullmatch(tenant) is None:
            raise ValueError("ECHO_PM_TENANT is invalid")
        headers["X-Tenant-ID"] = tenant
    return headers


def _pm_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        base = _pm_base()
        headers = _pm_headers()
    except ValueError:
        return _failure("invalid_configuration", "PM integration configuration is invalid")
    if not base:
        return _failure("not_configured", "PM integration is not configured")
    import httpx

    try:
        resp = httpx.request(
            method,
            f"{base}{path}",
            headers=headers,
            timeout=20.0,
            **kwargs,
        )
        resp.raise_for_status()
        return {"ok": True, "data": resp.json()}
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        return _failure(
            "upstream_http",
            "PM service rejected the request",
            retryable=status == 429 or status >= 500,
            status=status,
        )
    except httpx.RequestError:
        return _failure(
            "upstream_unavailable",
            "PM service is unavailable",
            retryable=True,
        )
    except ValueError:
        return _failure("invalid_response", "PM service returned invalid JSON")
    except Exception:  # noqa: BLE001 - never expose dependency or transport internals to Agent
        return _failure("internal_error", "PM integration failed safely")


# ── handlers ────────────────────────────────────────────────────
def _pm_list_projects(**_kw: Any) -> dict[str, Any]:
    return _pm_request("GET", "/api/v1/projects")


def _pm_list_tasks(project_id: str = "", **_kw: Any) -> dict[str, Any]:
    if not isinstance(project_id, str) or _PROJECT_ID.fullmatch(project_id) is None:
        return _failure("invalid_argument", "project_id is required and must be valid")
    return _pm_request("GET", f"/api/v1/projects/{project_id}/tasks")


def _pm_create_task(
    project_id: str = "",
    title: str = "",
    *,
    description: str = "",
    role: str = "PM",
    priority: str = "p2",
    **_kw: Any,
) -> dict[str, Any]:
    normalized_title = title.strip() if isinstance(title, str) else ""
    if (
        not isinstance(project_id, str)
        or _PROJECT_ID.fullmatch(project_id) is None
        or not normalized_title
        or len(normalized_title) > 256
        or any(character < " " for character in normalized_title)
        or not isinstance(description, str)
        or len(description) > 4096
        or not isinstance(role, str)
        or role not in _ROLES
        or not isinstance(priority, str)
        or priority not in _PRIORITIES
    ):
        return _failure("invalid_argument", "task fields are missing or invalid")
    return _pm_request(
        "POST",
        f"/api/v1/projects/{project_id}/tasks",
        json={
            "title": normalized_title,
            "description": description,
            "role": role,
            "priority": priority,
        },
    )


# ── registration ────────────────────────────────────────────────
def register_pm_skills(registry: Any) -> int:
    """配置了 ECHO_PM_URL 才注册;否则返回 0(不暴露 PM 工具)。"""
    if not (os.environ.get("ECHO_PM_URL") or "").strip():
        return 0
    _pm_base()

    # PM is an optional integration.  Do not import Agent's skill subsystem at
    # module load time, otherwise an unused optional domain could break Agent
    # startup.  Once explicitly configured, require a clear versioned contract.
    from appliance.agent_api.contract import require_agent_api_contract

    require_agent_api_contract(required_domains=("skills",), optional_domains=())
    from appliance.agent_api.skills import Skill, SkillExpect, SkillTestCase

    registry.register(
        Skill(
            name="pm_list_projects",
            description=(
                "用途: 列出企业版(项目管理)里的所有项目，返回 [{id, name, ...}]。\n"
                "何时用: 用户提到「项目管理/我的项目/有哪些项目」，或建任务前需先拿 project_id。\n"
                "参数: 无。\n"
                "示例: pm_list_projects({})"
            ),
            affinity=["web", "io", "pm"],
            cost_profile="low",
            trusted_source="skill://appliance/pm_list_projects",
            handler=_pm_list_projects,
            # 无必填参数,任何 golden 测试都会真打网络(不确定);留空,
            # 路由由 test_pm_skills.py 用 mock httpx 覆盖。
            tests=[],
        )
    )
    registry.register(
        Skill(
            name="pm_list_tasks",
            description=(
                "用途: 列出某个项目下的任务(含甘特/状态)，返回任务数组。\n"
                "何时用: 用户问某项目的进度/任务清单。先用 pm_list_projects 拿 project_id。\n"
                "参数: project_id (必填)。\n"
                '示例: pm_list_tasks({"project_id": "proj-123"})'
            ),
            affinity=["web", "io", "pm"],
            cost_profile="low",
            trusted_source="skill://appliance/pm_list_tasks",
            handler=_pm_list_tasks,
            tests=[
                SkillTestCase(
                    name="missing_project_id",
                    tier="golden",
                    args={"project_id": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="pm_create_task",
            description=(
                "用途: 在企业版某项目下新建一个任务(把对话里的待办/拆解结果落到项目管理)。\n"
                "何时用: 用户说「把这个建成任务/记到项目里」。先用 pm_list_projects 确定 project_id。\n"
                "参数: project_id (必填); title (必填); description; role (硬件/固件/软件/结构/AI/测试/PM/供应链, 默认 PM); priority (p0-p3, 默认 p2)。\n"
                '示例: pm_create_task({"project_id": "proj-123", "title": "完成 BOM 选型", "role": "硬件"})'
            ),
            affinity=["web", "io", "pm"],
            cost_profile="low",
            trusted_source="skill://appliance/pm_create_task",
            handler=_pm_create_task,
            tests=[
                SkillTestCase(
                    name="missing_args",
                    tier="golden",
                    args={"project_id": "p", "title": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    return 3
