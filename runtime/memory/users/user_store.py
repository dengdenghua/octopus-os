"""Local user memory store used by chat and the settings UI.

The store is deliberately simple: a single JSON file under ``data/`` with
manual facts and lightweight text search. It only persists memories that are
explicitly provided by the user or by an API caller.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.platform.io import atomic_write_json
from runtime.platform.process.paths import app_paths
from runtime.safety.auth.scope import TenantScope

DEFAULT_MAX_FACTS = 500
HARD_MAX_FACTS = 2_000
DEFAULT_DEBOUNCE_SECONDS = 5
MAX_DEBOUNCE_SECONDS = 3_600
DEFAULT_MAX_INJECTION_TOKENS = 2_000
HARD_MAX_INJECTION_TOKENS = 32_000
MAX_FACT_CONTENT_CHARS = 500
MAX_SECTION_SUMMARY_CHARS = 4_000
MAX_LABEL_CHARS = 80
MAX_SCOPE_VALUE_CHARS = 120


def _scope_suffix(scope: TenantScope) -> str:
    return hashlib.sha256(f"{scope.tenant_id}:{scope.actor_id}".encode()).hexdigest()[:32]


def _memory_path(scope: TenantScope | None = None) -> Path:
    base = app_paths().user_memory_path
    if scope is None or scope.allow_cross_tenant:
        return base
    return base.parent / "tenants" / _scope_suffix(scope) / "memory.json"


def _config_path(scope: TenantScope | None = None) -> Path:
    base = app_paths().user_memory_config_path
    if scope is None or scope.allow_cross_tenant:
        return base
    return base.parent / "tenants" / _scope_suffix(scope) / "memory-config.json"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def empty_memory() -> dict[str, Any]:
    now = now_iso()
    return {
        "version": "1",
        "lastUpdated": now,
        "user": {
            "workContext": {"summary": "", "updatedAt": ""},
            "personalContext": {"summary": "", "updatedAt": ""},
            "topOfMind": {"summary": "", "updatedAt": ""},
        },
        "history": {
            "recentMonths": {"summary": "", "updatedAt": ""},
            "earlierContext": {"summary": "", "updatedAt": ""},
            "longTermBackground": {"summary": "", "updatedAt": ""},
        },
        "facts": [],
    }


def read_memory(scope: TenantScope | None = None) -> dict[str, Any]:
    path = _memory_path(scope)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_memory()
    except (TypeError, ValueError):
        return empty_memory()
    return normalize_memory(raw, scope=scope)


def write_memory(memory: dict[str, Any], *, scope: TenantScope | None = None) -> dict[str, Any]:
    normalized = normalize_memory(memory, scope=scope)
    normalized["lastUpdated"] = now_iso()
    path = _memory_path(scope)
    atomic_write_json(path, normalized)
    return normalized


def add_fact(
    content: str,
    *,
    category: str = "profile",
    confidence: float = 0.85,
    source: str = "chat",
    scope: str = "global",
    agent_id: str | None = None,
    project: str | None = None,
    owner: str = "local-user",
    visibility: str = "private",
    team_id: str | None = None,
    allowed_users: list[str] | None = None,
    allowed_roles: list[str] | None = None,
    allowed_agents: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    tenant_scope: TenantScope | None = None,
) -> dict[str, Any] | None:
    if not read_config(tenant_scope).get("enabled", True):
        return None
    content = _clean_text(content)
    if not content:
        return None
    memory = read_memory(tenant_scope)
    facts = list(memory.get("facts") or [])
    max_facts = int(read_config(tenant_scope).get("max_facts") or DEFAULT_MAX_FACTS)
    scope = _normalize_scope(scope, agent_id=agent_id, project=project)
    clean_agent = _clean_scope_value(agent_id)
    clean_project = _clean_scope_value(project)
    key = content.casefold()
    for fact in facts:
        if (
            str(fact.get("content") or "").strip().casefold() == key
            and str(fact.get("scope") or "global") == scope
            and str(fact.get("agent_id") or "") == clean_agent
            and str(fact.get("project") or "") == clean_project
        ):
            return fact
    fact = {
        "id": uuid4().hex,
        "content": content,
        "category": _clean_label(category or "profile", fallback="profile"),
        "confidence": max(0.0, min(1.0, _coerce_float(confidence, 0.85))),
        "createdAt": now_iso(),
        "source": _clean_label(source or "chat", fallback="chat"),
        "scope": scope,
        "agent_id": clean_agent,
        "project": clean_project,
        "asset_type": "atom",
        "layer": "L1",
        "title": _clean_label(title or content[:MAX_LABEL_CHARS], fallback="Memory"),
        "tags": _clean_string_list(tags or [category]),
        "owner": (tenant_scope.actor_id if tenant_scope is not None else _clean_scope_value(owner))
        or "local-user",
        "tenant_id": tenant_scope.tenant_id if tenant_scope is not None else "",
        "visibility": _normalize_choice(
            visibility, {"private", "team", "restricted", "agent"}, "private"
        ),
        "status": "active",
        "asset_version": 1,
        "team_id": _clean_scope_value(team_id),
        "allowed_users": _clean_string_list(allowed_users),
        "allowed_roles": _clean_string_list(allowed_roles),
        "allowed_agents": _clean_string_list(allowed_agents),
        "provenance": _normalize_provenance(provenance, fallback_source=source),
    }
    memory["facts"] = [*facts, fact][-max_facts:]
    write_memory(memory, scope=tenant_scope)
    return fact


def _tokenize(text: str) -> list[str]:
    """中文按字符，英文按空格，统一小写。"""
    text = text.lower()
    tokens: list[str] = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or ch.isalnum():
            tokens.append(ch)
        else:
            tokens.append(" ")
    return [t for t in "".join(tokens).split() if t]


def _cosine_similarity(q: list[str], d: list[str]) -> float:
    """词频向量余弦相似度。"""
    from collections import Counter

    if not q or not d:
        return 0.0
    cq = Counter(q)
    cd = Counter(d)
    vocab = set(cq.keys()) | set(cd.keys())
    dot = sum(cq.get(t, 0) * cd.get(t, 0) for t in vocab)
    norm_q = sum(v * v for v in cq.values()) ** 0.5
    norm_d = sum(v * v for v in cd.values()) ** 0.5
    if norm_q == 0 or norm_d == 0:
        return 0.0
    return dot / (norm_q * norm_d)


def search_facts(
    query: str,
    *,
    limit: int = 20,
    agent_id: str | None = None,
    project: str | None = None,
    include_global: bool = True,
    semantic: bool = False,
    scope: TenantScope | None = None,
    viewer: MemoryViewer | None = None,
) -> list[dict[str, Any]]:
    query = _clean_text(query).casefold()
    if not query:
        return []
    terms = [term for term in query.split() if term]
    scored: list[tuple[float, dict[str, Any]]] = []
    query_tokens = _tokenize(query) if semantic else []
    if scope is None and viewer is not None:
        # 多用户共享读取：聚合该租户下全部 actor 分区的记忆，再按查看者
        # 身份过滤（否则 team 共享记忆物理躺在 owner 自己的分区里，其他
        # 成员永远读不到——那"共享上下文"就只是字段，不是闭环）。
        facts_source: list[dict[str, Any]] = _facts_for_viewer(viewer)
    else:
        facts_source = read_memory(scope).get("facts", [])
    for fact in facts_source:
        if not isinstance(fact, dict):
            continue
        if viewer is not None and not fact_visible_to(fact, viewer):
            continue
        if not _fact_in_scope(
            fact,
            agent_id=agent_id,
            project=project,
            include_global=include_global,
        ):
            continue
        content = str(fact.get("content") or "")
        category = str(fact.get("category") or "")
        haystack = f"{content} {category}".casefold()
        if query in haystack:
            score = 1.0
        elif semantic and query_tokens:
            doc_tokens = _tokenize(haystack)
            score = _cosine_similarity(query_tokens, doc_tokens)
            if score <= 0:
                continue
        else:
            hits = sum(1 for term in terms if term in haystack)
            if hits <= 0:
                continue
            score = hits / max(1, len(terms))
        scored.append((score, fact))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [fact for _, fact in scored[:limit]]


def relevant_memory_texts(
    query: str,
    *,
    limit: int = 8,
    agent_id: str | None = None,
    project: str | None = None,
    scope: TenantScope | None = None,
    viewer: MemoryViewer | None = None,
) -> list[str]:
    settings = read_config(scope)
    if not settings.get("enabled", True) or not settings.get("injection_enabled", True):
        return []
    return [
        str(fact.get("content") or "").strip()
        for fact in search_facts(
            query,
            limit=limit,
            agent_id=agent_id,
            project=project,
            scope=scope,
            viewer=viewer,
        )
        if str(fact.get("content") or "").strip()
    ]


def default_config(scope: TenantScope | None = None) -> dict[str, Any]:
    return {
        "enabled": True,
        "storage_path": str(_memory_path(scope)),
        "auto_capture_enabled": True,
        "debounce_seconds": DEFAULT_DEBOUNCE_SECONDS,
        "max_facts": DEFAULT_MAX_FACTS,
        "fact_confidence_threshold": 0.5,
        "injection_enabled": True,
        "max_injection_tokens": DEFAULT_MAX_INJECTION_TOKENS,
    }


def read_config(scope: TenantScope | None = None) -> dict[str, Any]:
    path = _config_path(scope)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default_config(scope)
    except (TypeError, ValueError):
        return default_config(scope)
    if not isinstance(raw, dict):
        return default_config(scope)
    config = default_config(scope)
    config.update(
        {
            "enabled": bool(raw.get("enabled", config["enabled"])),
            "storage_path": str(_memory_path(scope)),
            "auto_capture_enabled": bool(
                raw.get("auto_capture_enabled", config["auto_capture_enabled"])
            ),
            "debounce_seconds": _coerce_int(
                raw.get("debounce_seconds"),
                int(config["debounce_seconds"]),
            ),
            "max_facts": _coerce_int(raw.get("max_facts"), int(config["max_facts"])),
            "fact_confidence_threshold": _coerce_float(
                raw.get("fact_confidence_threshold"),
                float(config["fact_confidence_threshold"]),
            ),
            "injection_enabled": bool(raw.get("injection_enabled", config["injection_enabled"])),
            "max_injection_tokens": _coerce_int(
                raw.get("max_injection_tokens"),
                int(config["max_injection_tokens"]),
            ),
        }
    )
    return read_config_from_raw(config, scope=scope)


def write_config(patch: dict[str, Any], *, scope: TenantScope | None = None) -> dict[str, Any]:
    config = read_config(scope)
    for key in (
        "enabled",
        "auto_capture_enabled",
        "injection_enabled",
        "debounce_seconds",
        "max_facts",
        "fact_confidence_threshold",
        "max_injection_tokens",
    ):
        if key in patch:
            config[key] = patch[key]
    normalized = read_config_from_raw(config, scope=scope)
    path = _config_path(scope)
    atomic_write_json(path, normalized)
    return normalized


def read_config_from_raw(
    raw: dict[str, Any], *, scope: TenantScope | None = None
) -> dict[str, Any]:
    config = default_config(scope)
    config.update(raw)
    config["enabled"] = bool(config.get("enabled", True))
    config["auto_capture_enabled"] = bool(config.get("auto_capture_enabled", True))
    config["injection_enabled"] = bool(config.get("injection_enabled", True))
    config["storage_path"] = str(_memory_path(scope))
    config["debounce_seconds"] = max(
        0,
        min(
            MAX_DEBOUNCE_SECONDS,
            _coerce_int(config.get("debounce_seconds"), DEFAULT_DEBOUNCE_SECONDS),
        ),
    )
    config["max_facts"] = max(
        1,
        min(HARD_MAX_FACTS, _coerce_int(config.get("max_facts"), DEFAULT_MAX_FACTS)),
    )
    config["fact_confidence_threshold"] = max(
        0.0,
        min(
            1.0,
            _coerce_float(config.get("fact_confidence_threshold"), 0.5),
        ),
    )
    config["max_injection_tokens"] = max(
        0,
        min(
            HARD_MAX_INJECTION_TOKENS,
            _coerce_int(
                config.get("max_injection_tokens"),
                DEFAULT_MAX_INJECTION_TOKENS,
            ),
        ),
    )
    return config


def normalize_memory(raw: Any, *, scope: TenantScope | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    base = empty_memory()
    last_updated = str(raw.get("lastUpdated") or base["lastUpdated"])
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    history = raw.get("history") if isinstance(raw.get("history"), dict) else {}
    facts: list[dict[str, Any]] = []
    for item in raw.get("facts") or []:
        if not isinstance(item, dict):
            continue
        content = _clean_text(item.get("content") or "")
        if not content:
            continue
        try:
            confidence = float(item.get("confidence", 0.8))
        except (TypeError, ValueError):
            confidence = 0.8
        facts.append(
            {
                "id": str(item.get("id") or uuid4().hex),
                "content": content,
                "category": _clean_label(item.get("category") or "context", fallback="context"),
                "confidence": max(0.0, min(1.0, confidence)),
                "createdAt": str(item.get("createdAt") or last_updated),
                "updatedAt": str(item.get("updatedAt") or item.get("createdAt") or last_updated),
                "source": _clean_label(item.get("source") or "manual", fallback="manual"),
                "scope": _normalize_scope(
                    item.get("scope") or "global",
                    agent_id=item.get("agent_id"),
                    project=item.get("project"),
                ),
                "agent_id": _clean_scope_value(item.get("agent_id")),
                "project": _clean_scope_value(item.get("project")),
                "asset_type": _normalize_choice(
                    item.get("asset_type"),
                    {
                        "conversation",
                        "atom",
                        "scenario",
                        "persona",
                        "skill",
                        "wiki",
                        "code_graph",
                        "media",
                    },
                    "atom",
                ),
                "layer": _normalize_choice(
                    str(item.get("layer") or "L1").upper(),
                    {"L0", "L1", "L2", "L3"},
                    "L1",
                ),
                "title": _clean_label(
                    item.get("title") or content[:MAX_LABEL_CHARS],
                    fallback="Memory",
                ),
                "tags": _clean_string_list(item.get("tags") or [item.get("category")]),
                "owner": (
                    scope.actor_id if scope is not None else _clean_scope_value(item.get("owner"))
                )
                or "local-user",
                "tenant_id": scope.tenant_id
                if scope is not None
                else _clean_scope_value(item.get("tenant_id")),
                "visibility": _normalize_choice(
                    item.get("visibility"),
                    {"private", "team", "restricted", "agent"},
                    "private",
                ),
                "status": _normalize_choice(
                    item.get("status"),
                    {"draft", "active", "archived", "rejected"},
                    "active",
                ),
                "asset_version": max(1, _coerce_int(item.get("asset_version"), 1)),
                "team_id": _clean_scope_value(item.get("team_id")),
                "allowed_users": _clean_string_list(item.get("allowed_users")),
                "allowed_roles": _clean_string_list(item.get("allowed_roles")),
                "allowed_agents": _clean_string_list(item.get("allowed_agents")),
                "provenance": _normalize_provenance(
                    item.get("provenance"),
                    fallback_source=item.get("source") or "manual",
                ),
            }
        )
    base.update(
        {
            "version": str(raw.get("version") or "1"),
            "lastUpdated": last_updated,
            "user": {
                "workContext": _section(user.get("workContext"), last_updated),
                "personalContext": _section(user.get("personalContext"), last_updated),
                "topOfMind": _section(user.get("topOfMind"), last_updated),
            },
            "history": {
                "recentMonths": _section(history.get("recentMonths"), last_updated),
                "earlierContext": _section(history.get("earlierContext"), last_updated),
                "longTermBackground": _section(history.get("longTermBackground"), last_updated),
            },
            "facts": facts[-_configured_max_facts(scope) :],
        }
    )
    return base


def _section(value: Any, fallback_updated_at: str) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            "summary": _clean_section_summary(value.get("summary") or ""),
            "updatedAt": str(value.get("updatedAt") or fallback_updated_at or ""),
        }
    if isinstance(value, str):
        return {
            "summary": _clean_section_summary(value),
            "updatedAt": fallback_updated_at or "",
        }
    return {"summary": "", "updatedAt": fallback_updated_at or ""}


def _clean_text(value: Any) -> str:
    text = " ".join(str(value).split()).strip(" .。")
    return text[:MAX_FACT_CONTENT_CHARS].rstrip()


def _clean_scope_value(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()[:MAX_SCOPE_VALUE_CHARS]


def _clean_section_summary(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()[:MAX_SECTION_SUMMARY_CHARS].rstrip()


def _clean_label(value: Any, *, fallback: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:MAX_LABEL_CHARS].rstrip() or fallback


def _clean_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple, set)):
        return []
    cleaned = [_clean_scope_value(item) for item in value]
    return list(dict.fromkeys(item for item in cleaned if item))


def _normalize_choice(value: Any, allowed: set[str], fallback: str) -> str:
    clean = _clean_label(value, fallback=fallback)
    return clean if clean in allowed else fallback


def _normalize_provenance(value: Any, *, fallback_source: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "source_type": _clean_label(
            raw.get("source_type") or fallback_source,
            fallback="manual",
        ),
        "source_id": _clean_scope_value(raw.get("source_id")),
        "source_uri": str(raw.get("source_uri") or "").strip()[:500],
        "captured_at": str(raw.get("captured_at") or "").strip()[:80],
        "parent_ids": _clean_string_list(raw.get("parent_ids")),
        "evidence": _clean_section_summary(raw.get("evidence")),
    }


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _configured_max_facts(scope: TenantScope | None = None) -> int:
    try:
        return int(read_config(scope).get("max_facts") or DEFAULT_MAX_FACTS)
    except (TypeError, ValueError):
        return DEFAULT_MAX_FACTS


def _normalize_scope(
    value: Any,
    *,
    agent_id: Any = None,
    project: Any = None,
) -> str:
    raw = str(value or "global").strip().lower()
    if raw not in {"global", "agent", "project"}:
        raw = "global"
    if raw == "project" and not _clean_scope_value(project):
        raw = "agent" if _clean_scope_value(agent_id) else "global"
    if raw == "agent" and not _clean_scope_value(agent_id):
        raw = "global"
    return raw


def _fact_in_scope(
    fact: dict[str, Any],
    *,
    agent_id: str | None,
    project: str | None,
    include_global: bool,
) -> bool:
    scope = str(fact.get("scope") or "global")
    if scope == "global":
        return include_global
    clean_agent = _clean_scope_value(agent_id)
    clean_project = _clean_scope_value(project)
    if scope == "agent":
        return bool(clean_agent) and str(fact.get("agent_id") or "") == clean_agent
    if scope == "project":
        return bool(clean_project) and str(fact.get("project") or "") == clean_project
    return include_global


# ── 多用户共享上下文与身份隔离 · 记忆可见性执行层 ─────────────────────────
#
# fact 自始就携带可见性元数据（visibility / team_id / allowed_users /
# allowed_roles / allowed_agents / owner / tenant_id），但此前的搜索过滤
# （``_fact_in_scope``）只按 agent/project/global 维度做范围过滤，完全忽略
# 这些身份字段——"我的记忆"与"团队共享记忆"没有执行边界。本层把
# ``visibility`` 语义变成可判定、可单测的纯函数：显式传入查看者
# （``MemoryViewer``）时，搜索 / 注入只返回该查看者有权可见的记忆。
#
# 向后兼容：``viewer=None``（既有调用路径）行为不变，不做身份过滤；
# 显式提供 ``viewer`` 时按 tenant + visibility + ACL 字段执行隔离。


@dataclass(frozen=True)
class MemoryViewer:
    """查看者身份：actor + 租户 + 团队归属 + 角色。

    ``team_ids`` 为查看者所属协作团队（对应 fact 的 ``team_id``）；
    ``roles`` 为查看者在组织/团队中的角色（对应 fact 的 ``allowed_roles``）；
    ``is_admin`` 为组织管理员——可审计 team / restricted 记忆（仍不可读
    他人 private 记忆，private 只属于 owner）。
    """

    actor_id: str
    tenant_id: str = ""
    team_ids: frozenset[str] = field(default_factory=frozenset)
    roles: frozenset[str] = field(default_factory=frozenset)
    is_admin: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MemoryViewer:
        return cls(
            actor_id=str(raw.get("actor_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            team_ids=frozenset(str(t) for t in (raw.get("team_ids") or []) if t),
            roles=frozenset(str(r) for r in (raw.get("roles") or []) if r),
            is_admin=bool(raw.get("is_admin", False)),
        )


def fact_visible_to(fact: dict[str, Any], viewer: MemoryViewer | None) -> bool:
    """判定一条 fact 是否对 ``viewer`` 可见（纯函数，绝不抛出）。

    规则（默认最小暴露）：

    * ``viewer`` 为 None——视为不启用身份过滤，返回 True（旧路径语义）；
    * 租户不一致（fact.tenant_id 非空且 != viewer.tenant_id）——不可见；
    * owner 本人——总是可见（private 也只属于 owner）；
    * ``private``——仅 owner；
    * ``team``——查看者属于 fact.team_id 所指团队（或组织管理员）；
    * ``restricted``——命中 allowed_users / allowed_roles / allowed_agents
      任一（或组织管理员）；
    * ``agent``——绑定的 agent 身份（fact.agent_id）或 allowed_agents 命中；
    * 其它/缺失 visibility——按 private 保守处理（仅 owner）。
    """
    if viewer is None:
        return True
    try:
        fact_owner = str(fact.get("owner") or "").strip()
        tenant_id = str(fact.get("tenant_id") or "").strip()
        if tenant_id and tenant_id != viewer.tenant_id:
            return False
        if fact_owner and fact_owner == viewer.actor_id:
            return True
        visibility = str(fact.get("visibility") or "private").strip().lower()
        if visibility == "private":
            return False
        if visibility == "team":
            team_id = str(fact.get("team_id") or "").strip()
            return bool(team_id) and (team_id in viewer.team_ids or viewer.is_admin)
        if visibility == "agent":
            agent_id = str(fact.get("agent_id") or "").strip()
            if agent_id and agent_id == viewer.actor_id:
                return True
            return viewer.actor_id in _clean_string_list(fact.get("allowed_agents"))
        if visibility == "restricted":
            if viewer.is_admin:
                return True
            if viewer.actor_id in _clean_string_list(fact.get("allowed_users")):
                return True
            allowed_roles = frozenset(_clean_string_list(fact.get("allowed_roles")))
            if allowed_roles and allowed_roles & viewer.roles:
                return True
            return viewer.actor_id in _clean_string_list(fact.get("allowed_agents"))
        return False
    except Exception:  # noqa: BLE001 - visibility check must never break memory read
        return False


def visible_facts_for_viewer(
    viewer: MemoryViewer,
    *,
    limit: int = 50,
    scope: TenantScope | None = None,
) -> list[dict[str, Any]]:
    """查看者可读的全部 fact（无查询词版本，供团队共享上下文注入）。

    按 visibility + tenant + ACL 过滤，返回原 fact dict（调用方决定
    如何投影进提示词）。与 ``search_facts`` 一致：scope 为空时聚合
    读取该租户全部分区。
    """
    source = _facts_for_viewer(viewer) if scope is None else read_memory(scope).get("facts", [])
    facts: list[dict[str, Any]] = []
    for fact in source:
        if not isinstance(fact, dict):
            continue
        if fact_visible_to(fact, viewer):
            facts.append(fact)
        if len(facts) >= max(1, limit):
            break
    return facts


def _memory_paths_for_tenant(tenant_id: str) -> list[Path]:
    """该租户涉及的全部记忆文件：默认文件 + ``data/tenants/*`` 分区。

    分区目录名是 ``tenant:actor`` 的哈希，无法从文件名反推租户，因此
    多用户共享读取扫描全部分区、按 fact 自带的 ``tenant_id`` 过滤。
    """
    paths: list[Path] = [app_paths().user_memory_path]
    if not tenant_id:
        return paths
    tenants_dir = app_paths().user_memory_path.parent / "tenants"
    if not tenants_dir.is_dir():
        return paths
    for partition in sorted(tenants_dir.iterdir()):
        if not partition.is_dir():
            continue
        paths.append(partition / "memory.json")
    return paths


def _facts_from_memory_file(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    return [fact for fact in normalize_memory(raw).get("facts") or [] if isinstance(fact, dict)]


def _facts_for_viewer(viewer: MemoryViewer) -> list[dict[str, Any]]:
    """聚合读取查看者租户下全部记忆 fact（供身份过滤与团队共享注入）。"""
    facts: list[dict[str, Any]] = []
    for path in _memory_paths_for_tenant(viewer.tenant_id):
        for fact in _facts_from_memory_file(path):
            # 分区哈希不可反推租户：以 fact 自带的 tenant_id 为准；legacy
            # fact（tenant_id 为空）在本地单租户模式下视为同一空间。
            tenant_id = str(fact.get("tenant_id") or "").strip()
            if tenant_id and viewer.tenant_id and tenant_id != viewer.tenant_id:
                continue
            facts.append(fact)
    return facts
