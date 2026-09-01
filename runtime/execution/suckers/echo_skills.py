"""ECHO Universe Engine 叙事 Ganglion 接入.

``echo-universe-engine`` 是 Echo 家族的叙事生成 Ganglion —— 独立
FastAPI 服务,管理角色 / 故事 / 事件 / 关系 / 阵营的 canon 与候选流。
echo-agent 通过本 skill 调它的 HTTP API,不拥有叙事索引本身。

服务不可达时自门控:返回清晰的可操作消息,绝不崩 turn。
零新依赖:urllib,与 storage_skills 同构。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase

_DEFAULT_URL = "https://universe.echo-age.com"
_TIMEOUT_S = 10.0
_MAX_LIMIT = 100


def _base_url() -> str:
    raw = (os.environ.get("ECHO_ECHO_URL") or "").strip()
    return (raw or _DEFAULT_URL).rstrip("/")


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
            f"ECHO 宇宙引擎(echo-universe-engine)未运行或不可达({_base_url()})。"
            "请先启动 echo-universe-engine 服务后再试。"
        ),
    }


# ─── skill: echo_query_characters ──────────────────────────


def _query_characters(
    **kw: Any,
) -> dict[str, Any]:
    """查询 ECHO canon 中的角色列表。"""
    resp = _request("GET", "/api/canon/characters")
    if resp is None:
        return _unavailable()
    characters = resp if isinstance(resp, list) else resp.get("characters", [])
    return {
        "ok": True,
        "available": True,
        "count": len(characters),
        "characters": characters[:_MAX_LIMIT],
    }


_ECHO_QUERY_CHARACTERS_DESC = (
    "Query the ECHO Universe canon for character cards (name, faction, "
    "role, status). Backed by the echo-universe-engine service (the "
    "narrative Ganglion), which owns the canon store.\n"
    "\n"
    "Use it when the user asks about ECHO universe characters ('有哪些角色', "
    "'tell me about Kane', '白幽灵小队有谁'). Do NOT use it for real-world "
    "people.\n"
    "\n"
    "Returns: {ok, available, count, characters:[...]}."
)


# ─── skill: echo_query_events ───────────────────────────────


def _query_events(
    limit: int | str = 20,
    **kw: Any,
) -> dict[str, Any]:
    """查询 ECHO 宇宙事件日志。"""
    try:
        n = max(1, min(_MAX_LIMIT, int(limit)))
    except (TypeError, ValueError):
        n = 20
    resp = _request("GET", f"/api/journal/events?limit={n}")
    if resp is None:
        return _unavailable()
    events = resp if isinstance(resp, list) else resp.get("events", [])
    return {
        "ok": True,
        "available": True,
        "count": len(events),
        "events": events,
    }


_ECHO_QUERY_EVENTS_DESC = (
    "Query the ECHO Universe journal for recent canon events (story beats, "
    "character changes, world shifts). Backed by echo-universe-engine.\n"
    "\n"
    "Use it when the user asks '最近发生了什么', 'what happened in the "
    "ECHO universe', or wants a timeline recap.\n"
    "\n"
    "Args: {limit?: int 1-100 (default 20)}.\n"
    "Returns: {ok, available, count, events:[...]}."
)


# ─── skill: echo_universe_feed ──────────────────────────────


def _universe_feed(
    user_id: str = "",
    **kw: Any,
) -> dict[str, Any]:
    """获取指定用户的 ECHO 宇宙动态流。"""
    user_id = str(user_id or kw.get("character_id") or "").strip()
    if not user_id:
        return {
            "ok": False,
            "available": True,
            "error": "user_id is required",
            "feed": [],
        }
    resp = _request("GET", f"/api/universe/feed/{user_id}")
    if resp is None:
        return _unavailable()
    return {
        "ok": True,
        "available": True,
        "user_id": user_id,
        "feed": resp,
    }


_ECHO_UNIVERSE_FEED_DESC = (
    "Get the ECHO Universe activity feed for a specific user/character "
    "(recent story beats, relationship changes, faction events that affect "
    "them). Backed by echo-universe-engine.\n"
    "\n"
    "Use it when the user asks '我的角色最近怎样了', 'what happened to "
    "Zero lately', or wants a personalized universe update.\n"
    "\n"
    "Args: {user_id: string (character id or user binding id)}.\n"
    "Returns: {ok, available, user_id, feed:{...}}."
)


# ─── skill: echo_run_story ──────────────────────────────────


def _run_story(
    **kw: Any,
) -> dict[str, Any]:
    """触发 ECHO 故事生成 agent。"""
    resp = _request("POST", "/api/agents/story/run")
    if resp is None:
        return _unavailable()
    return {
        "ok": True,
        "available": True,
        "result": resp,
    }


_ECHO_RUN_STORY_DESC = (
    "Trigger the ECHO Universe story-generation agent to produce a new "
    "story beat. Backed by echo-universe-engine.\n"
    "\n"
    "Use it when the user asks '生成一段故事', 'write the next ECHO "
    "episode', '推进剧情'. The generated beat goes through the canon "
    "candidate queue and may need promotion.\n"
    "\n"
    "Returns: {ok, available, result:{...}}."
)


# ─── registration ──────────────────────────────────────────


def register_echo_skills(registry: SkillRegistry) -> int:
    """注册 ECHO 宇宙引擎 skill。始终注册;服务不可达时自报告。"""
    registry.register(
        Skill(
            name="echo_query_characters",
            description=_ECHO_QUERY_CHARACTERS_DESC,
            affinity=["echo", "universe", "character", "canon", "narrative", "角色", "宇宙"],
            cost_profile="low",
            trusted_source="skill://public/echo_query_characters",
            handler=_query_characters,
            tests=[
                SkillTestCase(
                    name="missing_service_returns_unavailable",
                    tier="golden",
                    args={},
                    expect=SkillExpect(schema_keys=["ok", "available"]),
                    custom_predicate=lambda r: (
                        isinstance(r, dict) and r.get("ok") in (True, False) and "available" in r
                    ),
                ),
            ],
        ),
        replace=True,
    )
    registry.register(
        Skill(
            name="echo_query_events",
            description=_ECHO_QUERY_EVENTS_DESC,
            affinity=["echo", "universe", "event", "journal", "timeline", "事件", "日志"],
            cost_profile="low",
            trusted_source="skill://public/echo_query_events",
            handler=_query_events,
            tests=[
                SkillTestCase(
                    name="returns_events_or_unavailable",
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
            name="echo_universe_feed",
            description=_ECHO_UNIVERSE_FEED_DESC,
            affinity=["echo", "universe", "feed", "activity", "动态", "宇宙"],
            cost_profile="low",
            trusted_source="skill://public/echo_universe_feed",
            handler=_universe_feed,
            tests=[
                SkillTestCase(
                    name="missing_user_id_returns_error",
                    tier="golden",
                    args={"user_id": ""},
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
            name="echo_run_story",
            description=_ECHO_RUN_STORY_DESC,
            affinity=["echo", "universe", "story", "generate", "narrative", "故事", "生成"],
            cost_profile="mid",
            trusted_source="skill://public/echo_run_story",
            handler=_run_story,
            tests=[
                SkillTestCase(
                    name="returns_result_or_unavailable",
                    tier="golden",
                    args={},
                    expect=SkillExpect(schema_keys=["ok", "available"]),
                ),
            ],
        ),
        replace=True,
    )
    return 4
