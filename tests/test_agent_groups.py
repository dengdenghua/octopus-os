"""Implementation note."""

from __future__ import annotations

import pytest
from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.agents import (
    Agent,
    AgentGroup,
    AgentGroupNotFound,
    AgentGroupRegistry,
    AgentRegistry,
    effective_groups_for_agent,
    make_all_agent_presets,
)
from runtime.execution.arms.base import ArmPool, Worker
from runtime.platform.models import ArmId


class _FakeExecutor:
    journal = None


def _rt():
    return GraphRuntime(executor=_FakeExecutor(), journal=None)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestAgentGroupDataclass:
    def test_defaults(self):
        g = AgentGroup(group_id="x")
        assert g.group_id == "x"
        assert g.display_name == ""
        assert g.members == []
        assert g.created_at is not None


# ═══════════════════════════════════════════════════════════
# Registry CRUD
# ═══════════════════════════════════════════════════════════


class TestRegistryCRUD:
    def test_create_and_get(self):
        r = AgentGroupRegistry()
        r.create(
            AgentGroup(
                group_id="ecom",
                display_name="Ecom",
                members=["vibe_selling", "ecommerce_mind"],
            )
        )
        g = r.get("ecom")
        assert g.display_name == "Ecom"
        assert g.members == ["vibe_selling", "ecommerce_mind"]

    def test_create_deduplicates_members(self):
        r = AgentGroupRegistry()
        r.create(AgentGroup(group_id="x", members=["a", "a", "b"]))
        assert r.get("x").members == ["a", "b"]

    def test_create_rejects_empty_id(self):
        r = AgentGroupRegistry()
        with pytest.raises(ValueError):
            r.create(AgentGroup(group_id=""))

    def test_create_rejects_duplicate(self):
        r = AgentGroupRegistry()
        r.create(AgentGroup(group_id="x"))
        with pytest.raises(ValueError, match="duplicate"):
            r.create(AgentGroup(group_id="x"))

    def test_update_partial(self):
        r = AgentGroupRegistry()
        r.create(AgentGroup(group_id="x", display_name="old", description="d"))
        new = r.update("x", display_name="new")
        assert new.display_name == "new"
        assert new.description == "d"  # Implementation note.

    def test_update_unknown_raises(self):
        r = AgentGroupRegistry()
        with pytest.raises(AgentGroupNotFound):
            r.update("ghost", display_name="x")

    def test_remove(self):
        r = AgentGroupRegistry()
        r.create(AgentGroup(group_id="x"))
        assert r.remove("x") is True
        assert r.remove("x") is False
        assert not r.has("x")

    def test_list_all_stable_order(self):
        r = AgentGroupRegistry()
        r.create(AgentGroup(group_id="z"))
        r.create(AgentGroup(group_id="a"))
        r.create(AgentGroup(group_id="m"))
        ids = [g.group_id for g in r.list_all()]
        assert ids == ["a", "m", "z"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMembership:
    def test_add_member_new(self):
        r = AgentGroupRegistry()
        r.create(AgentGroup(group_id="x"))
        assert r.add_member("x", "alice") is True
        assert r.get("x").members == ["alice"]

    def test_add_member_duplicate(self):
        r = AgentGroupRegistry()
        r.create(AgentGroup(group_id="x", members=["alice"]))
        assert r.add_member("x", "alice") is False

    def test_add_member_empty_agent_rejected(self):
        r = AgentGroupRegistry()
        r.create(AgentGroup(group_id="x"))
        with pytest.raises(ValueError):
            r.add_member("x", "")

    def test_add_member_unknown_group(self):
        r = AgentGroupRegistry()
        with pytest.raises(AgentGroupNotFound):
            r.add_member("ghost", "alice")

    def test_remove_member_present(self):
        r = AgentGroupRegistry()
        r.create(AgentGroup(group_id="x", members=["a", "b"]))
        assert r.remove_member("x", "a") is True
        assert r.get("x").members == ["b"]

    def test_remove_member_absent(self):
        r = AgentGroupRegistry()
        r.create(AgentGroup(group_id="x"))
        assert r.remove_member("x", "alice") is False

    def test_groups_for_agent(self):
        r = AgentGroupRegistry()
        r.create(AgentGroup(group_id="team_a", members=["alice", "bob"]))
        r.create(AgentGroup(group_id="team_b", members=["bob", "carol"]))
        assert r.groups_for_agent("alice") == ["team_a"]
        assert r.groups_for_agent("bob") == ["team_a", "team_b"]
        assert r.groups_for_agent("dave") == []


# ═══════════════════════════════════════════════════════════
# effective_groups_for_agent
# ═══════════════════════════════════════════════════════════


class TestEffectiveGroups:
    def test_static_only(self):
        result = effective_groups_for_agent(
            agent_id="alice",
            static_groups=["team_a", "team_b"],
        )
        assert result == ["team_a", "team_b"]

    def test_dynamic_only(self):
        r = AgentGroupRegistry()
        r.create(AgentGroup(group_id="team_a", members=["alice"]))
        result = effective_groups_for_agent(
            agent_id="alice",
            static_groups=[],
            registry=r,
        )
        assert result == ["team_a"]

    def test_union(self):
        r = AgentGroupRegistry()
        r.create(AgentGroup(group_id="team_dynamic", members=["alice"]))
        result = effective_groups_for_agent(
            agent_id="alice",
            static_groups=["team_static"],
            registry=r,
        )
        assert result == ["team_dynamic", "team_static"]

    def test_dedup_when_both_sources_overlap(self):
        r = AgentGroupRegistry()
        r.create(AgentGroup(group_id="shared", members=["alice"]))
        result = effective_groups_for_agent(
            agent_id="alice",
            static_groups=["shared", "static_only"],
            registry=r,
        )
        assert result == ["shared", "static_only"]


# ═══════════════════════════════════════════════════════════
# HTTP router
# ═══════════════════════════════════════════════════════════


fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from runtime.sensing.gateway.agents_router import create_agents_router  # noqa: E402


def _build_app(
    *,
    agents: AgentRegistry | None = None,
    groups: AgentGroupRegistry | None = None,
) -> FastAPI:
    agents = agents or AgentRegistry()
    app = FastAPI()
    app.include_router(
        create_agents_router(
            registry=agents,
            group_registry=groups,
        )
    )
    return app


@pytest.fixture
def agents_reg() -> AgentRegistry:
    r = AgentRegistry()
    r.register_all(make_all_agent_presets(_rt()))
    return r


class TestGroupHTTPCRUD:
    def test_list_empty(self):
        app = _build_app(groups=AgentGroupRegistry())
        r = TestClient(app).get("/api/groups")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_and_list(self, agents_reg):
        groups = AgentGroupRegistry()
        app = _build_app(agents=agents_reg, groups=groups)
        client = TestClient(app)
        r = client.post(
            "/api/groups",
            json={
                "group_id": "ecom",
                "display_name": "Ecom Team",
                "description": "E-commerce specialists",
                "members": ["vibe_selling", "ecommerce_mind"],
            },
        )
        assert r.status_code == 201
        data = r.json()
        assert data["group_id"] == "ecom"
        assert data["members"] == ["vibe_selling", "ecommerce_mind"]

        lst = client.get("/api/groups").json()
        assert len(lst) == 1
        assert lst[0]["group_id"] == "ecom"

    def test_create_duplicate_409(self, agents_reg):
        groups = AgentGroupRegistry()
        groups.create(AgentGroup(group_id="ecom"))
        app = _build_app(agents=agents_reg, groups=groups)
        r = TestClient(app).post(
            "/api/groups",
            json={"group_id": "ecom"},
        )
        assert r.status_code == 409

    def test_create_empty_id_400(self, agents_reg):
        groups = AgentGroupRegistry()
        app = _build_app(agents=agents_reg, groups=groups)
        r = TestClient(app).post(
            "/api/groups",
            json={"group_id": ""},
        )
        assert r.status_code == 400

    def test_get_detail(self, agents_reg):
        groups = AgentGroupRegistry()
        groups.create(
            AgentGroup(
                group_id="ecom",
                display_name="Ecom",
                members=["vibe_selling"],
            )
        )
        app = _build_app(agents=agents_reg, groups=groups)
        r = TestClient(app).get("/api/groups/ecom")
        assert r.status_code == 200
        assert r.json()["display_name"] == "Ecom"

    def test_get_not_found_404(self, agents_reg):
        app = _build_app(agents=agents_reg, groups=AgentGroupRegistry())
        r = TestClient(app).get("/api/groups/ghost")
        assert r.status_code == 404

    def test_put_update(self, agents_reg):
        groups = AgentGroupRegistry()
        groups.create(
            AgentGroup(
                group_id="ecom",
                display_name="old",
                description="keep",
            )
        )
        app = _build_app(agents=agents_reg, groups=groups)
        r = TestClient(app).put(
            "/api/groups/ecom",
            json={"display_name": "new"},
        )
        assert r.status_code == 200
        assert r.json()["display_name"] == "new"
        assert r.json()["description"] == "keep"

    def test_put_unknown_404(self, agents_reg):
        app = _build_app(agents=agents_reg, groups=AgentGroupRegistry())
        r = TestClient(app).put(
            "/api/groups/ghost",
            json={"display_name": "x"},
        )
        assert r.status_code == 404

    def test_delete(self, agents_reg):
        groups = AgentGroupRegistry()
        groups.create(AgentGroup(group_id="ecom"))
        app = _build_app(agents=agents_reg, groups=groups)
        r = TestClient(app).delete("/api/groups/ecom")
        assert r.status_code == 204
        assert not groups.has("ecom")

    def test_delete_unknown_404(self, agents_reg):
        app = _build_app(agents=agents_reg, groups=AgentGroupRegistry())
        r = TestClient(app).delete("/api/groups/ghost")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMemberHTTP:
    def test_add_member_happy(self, agents_reg):
        groups = AgentGroupRegistry()
        groups.create(AgentGroup(group_id="ecom"))
        app = _build_app(agents=agents_reg, groups=groups)
        r = TestClient(app).post(
            "/api/groups/ecom/members/vibe_selling",
        )
        assert r.status_code == 200
        data = r.json()
        assert data["added"] is True
        assert "vibe_selling" in groups.get("ecom").members

    def test_add_member_idempotent(self, agents_reg):
        groups = AgentGroupRegistry()
        groups.create(AgentGroup(group_id="ecom", members=["vibe_selling"]))
        app = _build_app(agents=agents_reg, groups=groups)
        r = TestClient(app).post(
            "/api/groups/ecom/members/vibe_selling",
        )
        assert r.status_code == 200
        assert r.json()["added"] is False

    def test_add_unknown_agent_404(self, agents_reg):
        groups = AgentGroupRegistry()
        groups.create(AgentGroup(group_id="ecom"))
        app = _build_app(agents=agents_reg, groups=groups)
        r = TestClient(app).post(
            "/api/groups/ecom/members/ghost_agent",
        )
        assert r.status_code == 404

    def test_add_member_unknown_group_404(self, agents_reg):
        app = _build_app(agents=agents_reg, groups=AgentGroupRegistry())
        r = TestClient(app).post(
            "/api/groups/ghost/members/vibe_selling",
        )
        assert r.status_code == 404

    def test_remove_member_happy(self, agents_reg):
        groups = AgentGroupRegistry()
        groups.create(AgentGroup(group_id="ecom", members=["vibe_selling"]))
        app = _build_app(agents=agents_reg, groups=groups)
        r = TestClient(app).delete(
            "/api/groups/ecom/members/vibe_selling",
        )
        assert r.status_code == 200
        assert r.json()["removed"] is True

    def test_remove_member_absent(self, agents_reg):
        groups = AgentGroupRegistry()
        groups.create(AgentGroup(group_id="ecom"))
        app = _build_app(agents=agents_reg, groups=groups)
        r = TestClient(app).delete(
            "/api/groups/ecom/members/vibe_selling",
        )
        assert r.status_code == 200
        assert r.json()["removed"] is False


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestByAgent:
    def test_union_static_and_dynamic(self):
        # Implementation note.
        rt = _rt()
        arm = Worker(
            arm_id=ArmId("a"),
            affinity=[],
            allowed_skills=[],
            runtime=rt,
        )
        ar = AgentRegistry()
        ar.register(
            Agent(
                agent_id="alice",
                display_name="A",
                description="",
                soul="",
                arms=ArmPool([arm]),
                groups=["static_one"],
            )
        )
        groups = AgentGroupRegistry()
        groups.create(AgentGroup(group_id="dynamic_one", members=["alice"]))

        app = _build_app(agents=ar, groups=groups)
        r = TestClient(app).get("/api/groups/by-agent/alice")
        assert r.status_code == 200
        data = r.json()
        assert data["groups"] == ["dynamic_one", "static_one"]
        assert data["static"] == ["static_one"]
        assert data["dynamic"] == ["dynamic_one"]

    def test_unknown_agent_still_returns_dynamic_only(self):
        """Implementation note."""
        groups = AgentGroupRegistry()
        groups.create(AgentGroup(group_id="orphan_group", members=["ghost"]))
        app = _build_app(agents=AgentRegistry(), groups=groups)
        r = TestClient(app).get("/api/groups/by-agent/ghost")
        assert r.status_code == 200
        assert r.json()["groups"] == ["orphan_group"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestDisabled:
    def test_group_endpoints_not_mounted_without_registry(self, agents_reg):
        app = _build_app(agents=agents_reg, groups=None)
        r = TestClient(app).get("/api/groups")
        # Implementation note.
        assert r.status_code == 404
