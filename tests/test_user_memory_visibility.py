"""Tests for the multi-user memory visibility layer (P1 · 多用户助手).

Covers:
  1. ``fact_visible_to`` 判定矩阵：tenant 隔离 / private / team / restricted / agent
  2. owner 本人与管理员边界（管理员可审计 team/restricted，不可读他人 private）
  3. ``search_facts`` / ``relevant_memory_texts`` 携带 viewer 时按身份过滤
  4. ``visible_facts_for_viewer`` 团队共享上下文注入
  5. 向后兼容：viewer=None 保持既有行为；junk fact 永不泄露
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.memory.users import user_store
from runtime.memory.users.user_store import (
    MemoryViewer,
    fact_visible_to,
    relevant_memory_texts,
    search_facts,
    visible_facts_for_viewer,
)
from runtime.safety.auth.scope import TenantScope


@pytest.fixture
def memory_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    return data_dir


def _viewer(
    *,
    actor_id: str = "alice",
    tenant_id: str = "t1",
    team_ids: frozenset[str] = frozenset(),
    roles: frozenset[str] = frozenset(),
    is_admin: bool = False,
) -> MemoryViewer:
    return MemoryViewer(
        actor_id=actor_id,
        tenant_id=tenant_id,
        team_ids=team_ids,
        roles=roles,
        is_admin=is_admin,
    )


def _fact(
    *,
    content: str = "fact",
    owner: str = "alice",
    tenant_id: str = "t1",
    visibility: str = "private",
    team_id: str = "",
    allowed_users: list[str] | None = None,
    allowed_roles: list[str] | None = None,
    allowed_agents: list[str] | None = None,
    agent_id: str = "",
) -> dict:
    return {
        "id": content[:8],
        "content": content,
        "category": "profile",
        "confidence": 0.9,
        "createdAt": "2026-01-01T00:00:00+00:00",
        "source": "chat",
        "scope": "global",
        "agent_id": agent_id,
        "project": "",
        "owner": owner,
        "tenant_id": tenant_id,
        "visibility": visibility,
        "team_id": team_id,
        "allowed_users": allowed_users or [],
        "allowed_roles": allowed_roles or [],
        "allowed_agents": allowed_agents or [],
    }


# ── fact_visible_to 判定矩阵 ────────────────────────────────────────────────


def test_visible_to_none_viewer_is_legacy_pass_through() -> None:
    assert fact_visible_to(_fact(), None) is True


@pytest.mark.parametrize(
    ("fact", "viewer", "expected"),
    [
        # tenant 隔离
        (_fact(tenant_id="t1"), _viewer(tenant_id="t2"), False),
        (_fact(tenant_id=""), _viewer(tenant_id="t1"), True),  # legacy fact 本地模式视为同租户
        # owner 本人总可见（private 也只属于 owner）
        (_fact(visibility="private"), _viewer(actor_id="alice"), True),
        (_fact(visibility="private"), _viewer(actor_id="bob"), False),
        # team：同团队 / 管理员 / 团队外
        (
            _fact(visibility="team", team_id="room-a"),
            _viewer(actor_id="bob", team_ids=frozenset({"room-a"})),
            True,
        ),
        (
            _fact(visibility="team", team_id="room-a"),
            _viewer(actor_id="bob", team_ids=frozenset({"room-b"})),
            False,
        ),
        (_fact(visibility="team", team_id="room-a"), _viewer(actor_id="bob", is_admin=True), True),
        (
            _fact(visibility="team", team_id=""),
            _viewer(actor_id="bob", team_ids=frozenset({"room-a"})),
            False,
        ),
        # restricted：allowed_users / allowed_roles / 管理员
        (
            _fact(visibility="restricted", allowed_users=["bob"]),
            _viewer(actor_id="bob"),
            True,
        ),
        (
            _fact(visibility="restricted", allowed_roles=["finance"]),
            _viewer(actor_id="bob", roles=frozenset({"finance"})),
            True,
        ),
        (
            _fact(visibility="restricted", allowed_roles=["finance"]),
            _viewer(actor_id="bob", roles=frozenset({"eng"})),
            False,
        ),
        (
            _fact(visibility="restricted", allowed_users=["carol"]),
            _viewer(actor_id="bob", is_admin=True),
            True,
        ),
        (_fact(visibility="restricted"), _viewer(actor_id="bob"), False),
        # agent：绑定 agent / allowed_agents / 他人
        (_fact(visibility="agent", agent_id="agent-7"), _viewer(actor_id="agent-7"), True),
        (
            _fact(visibility="agent", agent_id="agent-7", allowed_agents=["agent-8"]),
            _viewer(actor_id="agent-8"),
            True,
        ),
        (_fact(visibility="agent", agent_id="agent-7"), _viewer(actor_id="bob"), False),
    ],
)
def test_fact_visible_to_matrix(fact: dict, viewer: MemoryViewer, expected: bool) -> None:
    assert fact_visible_to(fact, viewer) is expected


def test_owner_outranks_every_visibility() -> None:
    for visibility in ("private", "team", "restricted", "agent"):
        assert fact_visible_to(_fact(visibility=visibility), _viewer(actor_id="alice")) is True


def test_admin_cannot_read_others_private() -> None:
    assert (
        fact_visible_to(_fact(visibility="private"), _viewer(actor_id="bob", is_admin=True))
        is False
    )


def test_junk_fact_never_visible() -> None:
    assert fact_visible_to({}, _viewer(actor_id="bob")) is False
    assert fact_visible_to({"owner": "bob"}, _viewer(actor_id="bob")) is True  # owner 仍可见
    assert fact_visible_to(None, _viewer(actor_id="bob")) is False  # type: ignore[arg-type]


def test_memory_viewer_from_dict() -> None:
    viewer = MemoryViewer.from_dict(
        {
            "actor_id": "bob",
            "tenant_id": "t2",
            "team_ids": ["a", "b"],
            "roles": ["eng"],
            "is_admin": True,
        }
    )
    assert viewer.actor_id == "bob"
    assert viewer.team_ids == frozenset({"a", "b"})
    assert viewer.roles == frozenset({"eng"})
    assert viewer.is_admin is True


# ── search / inject 携带 viewer 的身份隔离 ───────────────────────────────────


def test_search_facts_filters_by_viewer(memory_home: Path) -> None:
    user_store.add_fact(
        "alice 的私有记忆",
        category="profile",
        tenant_scope=TenantScope("t1", "alice"),
    )
    user_store.add_fact(
        "团队共享的发布日历",
        category="profile",
        tenant_scope=TenantScope("t1", "alice"),
        visibility="team",
        team_id="room-a",
    )
    user_store.add_fact(
        "bob 的私有记忆",
        category="profile",
        tenant_scope=TenantScope("t1", "bob"),
    )

    alice = _viewer(actor_id="alice", team_ids=frozenset({"room-a"}))
    bob = _viewer(actor_id="bob", team_ids=frozenset({"room-b"}))

    assert any("团队共享的发布日历" in f["content"] for f in search_facts("日历", viewer=alice))
    # bob 不在 room-a：团队记忆不可见
    assert search_facts("日历", viewer=bob) == []
    # bob 看不到 alice 的 private，但能看到自己的
    assert not any("alice 的私有记忆" in f["content"] for f in search_facts("私有记忆", viewer=bob))
    assert any("bob 的私有记忆" in f["content"] for f in search_facts("私有记忆", viewer=bob))


def test_search_facts_tenant_isolation(memory_home: Path) -> None:
    user_store.add_fact("t1 的秘密", category="profile", tenant_scope=TenantScope("t1", "alice"))
    viewer_t2 = _viewer(actor_id="alice", tenant_id="t2")
    assert search_facts("秘密", viewer=viewer_t2) == []


def test_search_facts_without_viewer_keeps_legacy_behavior(memory_home: Path) -> None:
    user_store.add_fact("传统可见记忆", category="profile")  # 无 tenant_scope → 默认文件
    # 不传 viewer：既有行为，不做身份过滤
    assert any("传统可见记忆" in f["content"] for f in search_facts("传统可见"))


def test_relevant_memory_texts_passes_viewer(memory_home: Path) -> None:
    user_store.add_fact("alice 的排期", category="profile", tenant_scope=TenantScope("t1", "alice"))
    user_store.add_fact(
        "团队排期 7 月",
        category="profile",
        tenant_scope=TenantScope("t1", "alice"),
        visibility="team",
        team_id="room-a",
    )
    texts = relevant_memory_texts(
        "排期",
        limit=8,
        viewer=_viewer(actor_id="bob", team_ids=frozenset({"room-a"})),
    )
    assert "团队排期 7 月" in texts
    assert "alice 的排期" not in texts


def test_visible_facts_for_viewer_returns_shared_context(memory_home: Path) -> None:
    user_store.add_fact("A 私有", category="profile", tenant_scope=TenantScope("t1", "alice"))
    user_store.add_fact(
        "A 团队共享",
        category="profile",
        tenant_scope=TenantScope("t1", "alice"),
        visibility="team",
        team_id="room-a",
    )
    user_store.add_fact(
        "给 admin 看",
        category="profile",
        tenant_scope=TenantScope("t1", "alice"),
        visibility="restricted",
        allowed_users=["admin-user"],
    )
    contents = [
        f["content"]
        for f in visible_facts_for_viewer(_viewer(actor_id="bob", team_ids=frozenset({"room-a"})))
    ]
    assert "A 团队共享" in contents
    assert "A 私有" not in contents
    assert "给 admin 看" not in contents

    admin_contents = [
        f["content"]
        for f in visible_facts_for_viewer(_viewer(actor_id="admin-user", is_admin=True))
    ]
    assert "给 admin 看" in admin_contents

