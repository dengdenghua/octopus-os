"""
Integration tests for the ``meta`` endpoint group · feedback / skills /
auth providers.

Purpose
-------

Guard-rail coverage for endpoints about to be extracted out of the
2081-line ``runtime/platform/ui/app.py`` into their own router
(``runtime/sensing/siphon/meta_router.py``). Mirrors the pattern
established in ``test_app_config_endpoints.py``: lock current
behavior first, refactor second, verify the split is
behavior-preserving.

Coverage
--------

    POST /api/feedback                   · record 👍/👎 on reply
    GET  /api/feedback                   · admin dashboard read-back
    GET  /api/skills                     · registered skill list
    GET  /api/auth/providers             · configured login methods

Hermetic isolation
------------------

* ``chdir(tmp_path)`` isolates the ``Path("data/feedback.jsonl")``
  that the feedback handler appends to (otherwise real feedback
  would end up in repo-root ``data/``)
* No account or ``local_auth_config`` injected ·
  ``auth_providers`` returns an empty list · pinning the "nothing
  configured" behavior
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.arms.tool_registry import ToolRegistry
from runtime.execution.suckers.registry import Skill, SkillRegistry
from runtime.platform.ui.app import create_app
from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.meta_router import create_meta_router


def _skill_zip_bytes(skill_name: str = "demo_skill") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{skill_name}/SKILL.md", f"name: {skill_name}\n")
    return buf.getvalue()


@pytest.fixture
def isolated_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    # The agent-market install registry lives at ~/.echo/agents-installed.json
    # by default, which leaks state between test runs and across local
    # developer machines. Redirect it to tmp_path so each test sees a
    # clean install set.
    install_state = tmp_path / "agents-installed.json"
    monkeypatch.setattr(
        "runtime.sensing.gateway.agent_world_router._INSTALL_STATE",
        install_state,
    )
    yield tmp_path


@pytest.fixture
def client(isolated_cwd: Path) -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


class TestSecurityHeaders:
    def test_default_http_responses_block_sniffing_and_cross_origin_frames(
        self,
        client: TestClient,
    ) -> None:
        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert response.headers["x-frame-options"] == "SAMEORIGIN"
        assert response.headers["content-security-policy"] == "frame-ancestors 'self'"
        assert "strict-transport-security" not in response.headers

    def test_https_response_enables_hsts(self, isolated_cwd: Path) -> None:
        with TestClient(create_app(), base_url="https://testserver") as https_client:
            response = https_client.get("/api/health")

        assert response.status_code == 200
        assert response.headers["strict-transport-security"] == (
            "max-age=31536000; includeSubDomains"
        )


@pytest.fixture
def secured_meta_client(
    isolated_cwd: Path,
) -> Iterator[tuple[TestClient, dict[str, dict[str, str]], SkillRegistry]]:
    store = IdentityStore()
    store.add(Identity(actor_id="alice", roles=("admin",)), api_key_plaintext="sk-alice")
    store.add(Identity(actor_id="bob", roles=("user",)), api_key_plaintext="sk-bob")
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="demo_skill",
            description="Demo skill",
            trusted_source="skill://public/demo_skill",
            handler=lambda **_kw: {"ok": True},
        ),
        verify_tests=False,
    )

    app = FastAPI()
    app.include_router(
        create_meta_router(
            registry=registry,
            identity_store=store,
            require_auth=True,
        )
    )
    with TestClient(app) as test_client:
        yield (
            test_client,
            {
                "admin": {"Authorization": "Bearer sk-alice"},
                "user": {"Authorization": "Bearer sk-bob"},
            },
            registry,
        )


# ═══════════════════════════════════════════════════════════
# GET /api/auth/me
# ═══════════════════════════════════════════════════════════


def test_auth_me_uses_real_identity_store(secured_meta_client) -> None:
    client, headers, _registry = secured_meta_client

    response = client.get("/api/auth/me", headers=headers["admin"])

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "alice",
        "actor_id": "alice",
        "username": "alice",
        "roles": ["admin"],
        "permissions": [],
        "is_active": True,
    }
    assert client.get("/api/auth/me").status_code == 401


def test_auth_me_accepts_session_cookie_and_logout_expires_it(secured_meta_client) -> None:
    client, _, _registry = secured_meta_client

    client.cookies.set("echo_session", "sk-alice")
    assert client.get("/api/auth/me").status_code == 200

    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert "echo_session=" in cookie
    assert "Max-Age=0" in cookie


def test_auth_me_keeps_development_stub_when_auth_is_disabled(client) -> None:
    response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["user_id"] == "anonymous"


# ═══════════════════════════════════════════════════════════
# POST /api/feedback
# ═══════════════════════════════════════════════════════════


class TestFeedbackPost:
    def test_liked_feedback_persists_to_jsonl(
        self,
        client: TestClient,
        isolated_cwd: Path,
    ) -> None:
        r = client.post(
            "/api/feedback",
            json={
                "sentiment": "liked",
                "message_id": "m1",
                "thread_id": "t1",
                "agent_id": "coder",
                "content_preview": "a helpful reply",
            },
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        # File exists at documented path
        jsonl = isolated_cwd / "data" / "feedback.jsonl"
        assert jsonl.exists()
        lines = jsonl.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        import json

        rec = json.loads(lines[0])
        assert rec["sentiment"] == "liked"
        assert rec["message_id"] == "m1"
        assert rec["thread_id"] == "t1"
        assert rec["agent_id"] == "coder"
        assert "ts" in rec  # epoch seconds · not asserting exact value

    def test_disliked_with_reason(self, client: TestClient) -> None:
        r = client.post(
            "/api/feedback",
            json={"sentiment": "disliked", "reason": "hallucinated api"},
        )
        assert r.status_code == 200
        rec = r.json()["recorded"]
        assert rec["sentiment"] == "disliked"
        assert rec["reason"] == "hallucinated api"

    def test_invalid_sentiment_rejected(self, client: TestClient) -> None:
        r = client.post(
            "/api/feedback",
            json={"sentiment": "meh"},
        )
        assert r.status_code == 400

    def test_sentiment_required(self, client: TestClient) -> None:
        r = client.post("/api/feedback", json={})
        assert r.status_code == 400

    def test_content_preview_truncated(
        self,
        client: TestClient,
        isolated_cwd: Path,
    ) -> None:
        """Preview is capped at 400 chars — guards the feedback log
        from growing unbounded on huge reply texts."""
        big = "x" * 2000
        client.post(
            "/api/feedback",
            json={"sentiment": "liked", "content_preview": big},
        )
        import json

        jsonl = isolated_cwd / "data" / "feedback.jsonl"
        rec = json.loads(jsonl.read_text(encoding="utf-8").splitlines()[0])
        assert len(rec["content_preview"]) == 400


# ═══════════════════════════════════════════════════════════
# GET /api/feedback
# ═══════════════════════════════════════════════════════════


class TestFeedbackList:
    def test_requires_auth_when_no_credentials(self, client: TestClient) -> None:
        r = client.get("/api/feedback")
        assert r.status_code == 401

    def test_requires_admin_role(
        self,
        secured_meta_client: tuple[TestClient, dict[str, dict[str, str]], SkillRegistry],
    ) -> None:
        client, headers, _registry = secured_meta_client
        client.post("/api/feedback", json={"sentiment": "liked", "message_id": "m1"})

        r = client.get("/api/feedback", headers=headers["user"])
        assert r.status_code == 403

    def test_returns_entries_newest_first(
        self,
        secured_meta_client: tuple[TestClient, dict[str, dict[str, str]], SkillRegistry],
    ) -> None:
        client, headers, _registry = secured_meta_client
        for i in range(3):
            client.post(
                "/api/feedback",
                json={"sentiment": "liked", "message_id": f"m{i}"},
            )
        data = client.get("/api/feedback", headers=headers["admin"]).json()
        # Reverse order — newest first (m2, m1, m0)
        ids = [e["message_id"] for e in data["entries"]]
        assert ids == ["m2", "m1", "m0"]

    def test_limit_respected(
        self,
        secured_meta_client: tuple[TestClient, dict[str, dict[str, str]], SkillRegistry],
    ) -> None:
        client, headers, _registry = secured_meta_client
        for i in range(10):
            client.post(
                "/api/feedback",
                json={"sentiment": "liked", "message_id": f"m{i}"},
            )
        data = client.get("/api/feedback?limit=3", headers=headers["admin"]).json()
        assert len(data["entries"]) == 3

    def test_thread_id_filter(
        self,
        secured_meta_client: tuple[TestClient, dict[str, dict[str, str]], SkillRegistry],
    ) -> None:
        client, headers, _registry = secured_meta_client
        for i in range(5):
            client.post(
                "/api/feedback",
                json={
                    "sentiment": "liked",
                    "message_id": f"m{i}",
                    "thread_id": "t_a" if i < 2 else "t_b",
                },
            )
        data = client.get(
            "/api/feedback?thread_id=t_a",
            headers=headers["admin"],
        ).json()
        assert {e["message_id"] for e in data["entries"]} == {"m0", "m1"}


# ═══════════════════════════════════════════════════════════
# GET /api/skills
# ═══════════════════════════════════════════════════════════


class TestSkills:
    def test_returns_skill_list(self, client: TestClient) -> None:
        r = client.get("/api/skills")
        assert r.status_code == 200
        data = r.json()
        assert "skills" in data
        assert isinstance(data["skills"], list)

    def test_skill_entry_shape(self, client: TestClient) -> None:
        """Each entry must carry the documented fields — the
        frontend Skills page depends on these."""
        data = client.get("/api/skills").json()
        if not data["skills"]:
            pytest.skip("no skills registered in minimal create_app()")
        entry = data["skills"][0]
        required = {
            "name",
            "description",
            "affinity",
            "cost_profile",
            "trusted_source",
            "has_tests",
        }
        assert required.issubset(entry.keys()), (
            f"skill entry missing fields: {required - entry.keys()}"
        )

    def test_default_app_includes_file_backed_skill_library(
        self,
        client: TestClient,
    ) -> None:
        data = client.get("/api/skills").json()
        skills = {s["name"]: s for s in data["skills"]}

        assert "pdf" in skills
        pdf = skills["pdf"]
        assert pdf["trusted_source"] == "skill://all_skills/pdf"

    def test_plugin_dynamic_skills_are_hidden_from_global_catalog(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        registry = SkillRegistry()
        registry.register(
            Skill(
                name="plugin_skill",
                description="Plugin skill",
                trusted_source="skill://all_skills/plugin_skill",
                handler=lambda **_kw: {"ok": True},
            ),
            verify_tests=False,
        )
        registry.register(
            Skill(
                name="local_skill",
                description="Local skill",
                trusted_source="skill://public/local_skill",
                handler=lambda **_kw: {"ok": True},
            ),
            verify_tests=False,
        )
        monkeypatch.setattr(
            "runtime.sensing.gateway.meta_router._dynamic_plugin_skill_names",
            lambda: {"plugin_skill"},
        )
        app = FastAPI()
        app.include_router(create_meta_router(registry=registry))
        client = TestClient(app)
        data = client.get("/api/skills").json()
        names = {skill["name"] for skill in data["skills"]}

        assert "plugin_skill" not in names
        assert "local_skill" in names

    def test_skill_catalog_collapses_alias_child_and_duplicate_sources(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        registry = SkillRegistry()
        for name, source in (
            ("root_skill", "skill://all_skills/root-skill"),
            ("alias_skill", "skill://all_skills/root-skill#alias"),
            ("child_skill", "skill://all_skills/root-skill/child"),
            ("duplicate_root", "skill://all_skills/root-skill"),
            ("other_skill", "skill://all_skills/other-skill"),
        ):
            registry.register(
                Skill(
                    name=name,
                    description=name,
                    trusted_source=source,
                    handler=lambda **_kw: {"ok": True},
                ),
                verify_tests=False,
            )
        monkeypatch.setattr(
            "runtime.sensing.gateway.meta_router._dynamic_plugin_skill_names",
            lambda: set(),
        )
        app = FastAPI()
        app.include_router(create_meta_router(registry=registry))
        client = TestClient(app)
        data = client.get("/api/skills").json()
        names = {skill["name"] for skill in data["skills"]}

        assert "root_skill" in names
        assert "other_skill" in names
        assert "alias_skill" not in names
        assert "child_skill" not in names
        assert "duplicate_root" not in names

    def test_skill_catalog_skips_broken_registry_entries(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class RegistryWithBrokenEntry:
            def all_names(self) -> list[str]:
                return ["good_skill", "broken_skill"]

            def get(self, name: str) -> Skill:
                if name == "broken_skill":
                    raise RuntimeError("corrupt skill registration")
                return Skill(
                    name="good_skill",
                    description="Good skill",
                    trusted_source="skill://public/good_skill",
                    handler=lambda **_kw: {"ok": True},
                )

            def is_enabled(self, _name: str) -> bool:
                return True

        monkeypatch.setattr(
            "runtime.sensing.gateway.meta_router._dynamic_plugin_skill_names",
            lambda: set(),
        )
        app = FastAPI()
        app.include_router(create_meta_router(registry=RegistryWithBrokenEntry()))
        client = TestClient(app)

        r = client.get("/api/skills")
        assert r.status_code == 200
        names = {skill["name"] for skill in r.json()["skills"]}
        assert "good_skill" in names
        assert "broken_skill" not in names


class TestCapabilityCatalog:
    def test_capability_catalog_endpoint_merges_sources(
        self,
        tmp_path: Path,
    ) -> None:
        registry = SkillRegistry()
        registry.register(
            Skill(
                name="exec_shell",
                description="Execute shell commands.",
                affinity=["shell"],
                trusted_source="builtin://exec_shell",
                handler=lambda **_kw: {"ok": True},
            ),
            verify_tests=False,
        )
        skill_dir = tmp_path / "mobile-skills" / "tap"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: android.tap
description: Tap a coordinate on the Android screen.
parameters:
  - name: x
    type: integer
    required: true
---
""",
            encoding="utf-8",
        )
        tool_registry = ToolRegistry()
        app = FastAPI()
        app.include_router(
            create_meta_router(
                registry=registry,
                tool_registry=tool_registry,
                mobile_skills_root=tmp_path / "mobile-skills",
            )
        )
        client = TestClient(app)

        response = client.get(
            "/api/capability-catalog",
            params={"source": "mobile_mcp"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["schema"] == "echo.capability_catalog.v1"
        assert data["total"] == 1
        entry = data["capabilities"][0]
        assert entry["id"] == "mobile:android_tap"
        assert entry["canonical_name"] == "android.tap"
        assert entry["risk"]["level"] == "high"
        assert data["summary"]["by_source"]["runtime_skill"] == 1
        assert data["summary"]["by_source"]["mobile_mcp"] == 1


class TestPlugins:
    # Pin a plugin port that is actually committed to the repo
    # (.echo/plugins/codex/ whitelists product-design + remotion; the
    # other ~20 ports are gitignored local copies). Pinning "browser"
    # made these tests green only on dev machines and red on any clean
    # checkout / CI.
    def test_copied_codex_plugins_are_registered_for_frontend(
        self,
        client: TestClient,
    ) -> None:
        r = client.get("/api/plugins")
        assert r.status_code == 200
        plugins = r.json()
        if not plugins:
            pytest.skip("no codex plugins installed in test environment")

        by_id = {plugin["id"]: plugin for plugin in plugins}
        assert "product-design" in by_id
        plugin = by_id["product-design"]
        assert plugin["source"] == "codex"
        assert plugin["enabled"] is True
        assert plugin["state"] == "registered"
        assert isinstance(plugin["capabilities"], list)
        assert plugin["logo_url"].endswith("/logo.svg")
        assert plugin["icon_url"].endswith("/composerIcon.svg")

    def test_plugin_detail_and_capabilities(
        self,
        client: TestClient,
    ) -> None:
        r = client.get("/api/plugins")
        if not r.json():
            pytest.skip("no codex plugins installed in test environment")

        detail = client.get("/api/plugins/product-design")
        assert detail.status_code == 200
        assert detail.json()["id"] == "product-design"

        caps = client.get("/api/plugins/capabilities?type=codex")
        assert caps.status_code == 200
        assert any(cap["provider"] == "product-design" for cap in caps.json())

    def test_plugin_assets_are_served(
        self,
        client: TestClient,
    ) -> None:
        r = client.get("/api/plugins")
        if not r.json():
            pytest.skip("no codex plugins installed in test environment")

        detail = client.get("/api/plugins/product-design").json()
        logo_url = detail["logo_url"]
        assert logo_url

        asset = client.get(logo_url)
        assert asset.status_code == 200
        assert asset.headers["content-type"] == "image/svg+xml"
        assert asset.content.startswith(b"<svg")
        assert b"data:image/png;base64," in asset.content


# ═══════════════════════════════════════════════════════════
# GET /api/auth/providers
# ═══════════════════════════════════════════════════════════


class TestAgentMarket:
    def test_agency_agents_are_registry_only_not_listed_locally(
        self,
        client: TestClient,
    ) -> None:
        """Agency template catalog moved to the public registry (/api/registry/roles,
        304 role+twin-role assets — a superset of the old local templates); the local
        store search no longer surfaces it, only physically-installed agents (echo
        cast + system agents) remain local-default. Direct id lookup still resolves
        (see test_agency_agent_detail_uses_catalog_metadata) for install/uninstall."""
        r = client.get("/api/agent-market/store?search=Frontend%20Developer&limit=20")
        assert r.status_code == 200

        agents = r.json()["agents"]
        by_id = {agent["id"]: agent for agent in agents}
        assert "agency_engineering_frontend_developer" not in by_id

    def test_agency_agent_detail_uses_catalog_metadata(
        self,
        client: TestClient,
    ) -> None:
        r = client.get("/api/agent-market/store/agency_marketing_xiaohongshu_specialist")
        assert r.status_code == 200

        data = r.json()
        assert data["display_name"] == "Xiaohongshu Specialist"
        assert "agency-agents" in data["tags"]
        assert data["category"] == "creative"

    def test_financial_services_agents_are_not_preinstalled_in_local_catalog(
        self,
        client: TestClient,
    ) -> None:
        """Specialists stay addressable by market id but do not crowd the local roster."""
        r = client.get("/api/agent-market/store?search=Pitch%20Agent&limit=20")
        assert r.status_code == 200

        assert "financial_pitch_agent" not in {
            agent["id"] for agent in r.json()["agents"]
        }

    def test_financial_services_agent_detail_uses_catalog_metadata(
        self,
        client: TestClient,
    ) -> None:
        r = client.get("/api/agent-market/store/financial_kyc_screener")
        assert r.status_code == 200

        data = r.json()
        assert data["display_name"] == "KYC Screener"
        assert data["author"] == "anthropics/financial-services"
        assert data["category"] == "financial"
        assert "finance" in data["tags"]
        assert data["key_skills"] == ["kyc-doc-parse", "kyc-rules", "xlsx-author"]
        assert data["available_skills"] == data["key_skills"]

    def test_financial_services_templates_remain_available_for_on_demand_install(
        self,
        client: TestClient,
    ) -> None:
        """Dormant templates are installable without appearing as active local agents."""
        from runtime.sensing.gateway._agent_world_helpers import _template_by_id

        r = client.get("/api/agent-market/store?category=financial&limit=50")
        assert r.status_code == 200
        assert r.json()["agents"] == []

        expected = {
            "financial_earnings_reviewer",
            "financial_gl_reconciler",
            "financial_kyc_screener",
            "financial_market_researcher",
            "financial_meeting_prep_agent",
            "financial_model_builder",
            "financial_month_end_closer",
            "financial_pitch_agent",
            "financial_statement_auditor",
            "financial_valuation_reviewer",
        }
        assert all(_template_by_id(agent_id) is not None for agent_id in expected)

    def test_financial_services_install_carries_key_skills(
        self,
        tmp_path: Path,
    ) -> None:
        from runtime.sensing.gateway.agent_world_router import (
            _install_template_agent,
        )

        agent_dir = _install_template_agent(
            "financial_pitch_agent",
            tmp_path / "agents",
            skills_root=tmp_path / "skills",
        )
        assert agent_dir is not None

        profile = json.loads((agent_dir / "profile.jsonc").read_text(encoding="utf-8"))
        assert profile["capabilities"]["execution_backend"] == "codex_app_server"
        tool_registry = agent_dir / "agent-core" / "tool-registry.jsonc"
        data = tool_registry.read_text(encoding="utf-8")
        assert '"pitch-deck"' in data
        assert '"dcf-model"' in data
        assert (tmp_path / "skills" / "pitch-deck" / "SKILL.md").is_file()
        assert (tmp_path / "skills" / "dcf-model" / "SKILL.md").is_file()
        assert (tmp_path / "skills" / "pptx-author" / "SKILL.md").is_file()


class TestAuthProviders:
    def test_empty_when_no_provider_configured(
        self,
        client: TestClient,
    ) -> None:
        """Neither account nor local auth config injected →
        empty list. The frontend Login page hides all tabs in this
        case; adding a spurious default here would show a broken
        login form."""
        r = client.get("/api/auth/providers")
        assert r.status_code == 200
        assert r.json() == {"providers": []}

    def test_oct_provider_when_configured(
        self,
        isolated_cwd: Path,
    ) -> None:
        from runtime.adapters.integrations.oct.config import OctConfig

        cfg = OctConfig(
            enabled=True,
            jwt_secret="0123456789abcdef0123456789ABCDEF!",
        )
        app = create_app(oct_config=cfg)
        c = TestClient(app)
        data = c.get("/api/auth/providers").json()
        ids = {p["id"] for p in data["providers"]}
        assert "oct" in ids
        assert c.get("/api/auth/status").json()["enabled"] is True

    def test_local_provider_when_configured(
        self,
        isolated_cwd: Path,
    ) -> None:
        class _FakeLocal:
            enabled = True
            users = {"alice": "x"}  # password_required=True
            allow_any_username = True

        app = create_app(local_auth_config=_FakeLocal())
        c = TestClient(app)
        data = c.get("/api/auth/providers").json()
        ids = {p["id"] for p in data["providers"]}
        assert "local" in ids
        local = next(p for p in data["providers"] if p["id"] == "local")
        # password_required reflects whether users dict is non-empty
        assert local["password_required"] is True


class TestAdminMetaMutations:
    def test_skill_enable_disable_requires_admin(
        self,
        secured_meta_client: tuple[TestClient, dict[str, dict[str, str]], SkillRegistry],
    ) -> None:
        client, headers, registry = secured_meta_client

        assert client.post("/api/skills/demo_skill/disable").status_code == 401
        assert (
            client.post(
                "/api/skills/demo_skill/disable",
                headers=headers["user"],
            ).status_code
            == 403
        )

        disabled = client.post(
            "/api/skills/demo_skill/disable",
            headers=headers["admin"],
        )
        assert disabled.status_code == 200
        assert registry.is_enabled("demo_skill") is False

        enabled = client.post(
            "/api/skills/demo_skill/enable",
            headers=headers["admin"],
        )
        assert enabled.status_code == 200
        assert registry.is_enabled("demo_skill") is True

    def test_skill_market_toggle_requires_admin(
        self,
        secured_meta_client: tuple[TestClient, dict[str, dict[str, str]], SkillRegistry],
    ) -> None:
        client, headers, registry = secured_meta_client

        assert client.post("/api/skills-market/demo_skill/disable").status_code == 401
        assert (
            client.post(
                "/api/skills-market/demo_skill/disable",
                headers=headers["user"],
            ).status_code
            == 403
        )

        disabled = client.post(
            "/api/skills-market/demo_skill/disable",
            headers=headers["admin"],
        )
        assert disabled.status_code == 200
        assert registry.is_enabled("demo_skill") is False

        enabled = client.post(
            "/api/skills-market/demo_skill/enable",
            headers=headers["admin"],
        )
        assert enabled.status_code == 200
        assert registry.is_enabled("demo_skill") is True

    def test_skill_market_can_load_bundled_design_skill_on_demand(
        self,
        secured_meta_client: tuple[TestClient, dict[str, dict[str, str]], SkillRegistry],
    ) -> None:
        client, headers, registry = secured_meta_client

        response = client.post(
            "/api/skills-market/creative-3d-animation/enable",
            headers=headers["admin"],
        )

        assert response.status_code == 200
        assert registry.has("creative-3d-animation")
        assert registry.is_enabled("creative-3d-animation") is True

    def test_skill_uninstall_requires_admin(
        self,
        isolated_cwd: Path,
        secured_meta_client: tuple[TestClient, dict[str, dict[str, str]], SkillRegistry],
    ) -> None:
        client, headers, _registry = secured_meta_client
        skill_dir = isolated_cwd / "skills" / "public" / "demo_skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("name: demo\n", encoding="utf-8")

        assert client.delete("/api/skills/demo_skill/uninstall").status_code == 401
        assert (
            client.delete(
                "/api/skills/demo_skill/uninstall",
                headers=headers["user"],
            ).status_code
            == 403
        )

        removed = client.delete(
            "/api/skills/demo_skill/uninstall",
            headers=headers["admin"],
        )
        assert removed.status_code == 200
        assert skill_dir.exists() is False

    def test_skill_uninstall_rejects_symlink_without_removing_target(
        self,
        isolated_cwd: Path,
        secured_meta_client: tuple[TestClient, dict[str, dict[str, str]], SkillRegistry],
    ) -> None:
        client, headers, _registry = secured_meta_client
        public_root = isolated_cwd / "skills" / "public"
        public_root.mkdir(parents=True)
        outside = isolated_cwd / "outside-skill"
        outside.mkdir()
        (outside / "marker.txt").write_text("keep", encoding="utf-8")
        (public_root / "demo_skill").symlink_to(outside, target_is_directory=True)

        resp = client.delete(
            "/api/skills/demo_skill/uninstall",
            headers=headers["admin"],
        )

        assert resp.status_code == 409
        assert (outside / "marker.txt").read_text(encoding="utf-8") == "keep"

    def test_skill_install_rejects_existing_symlink_target(
        self,
        isolated_cwd: Path,
        secured_meta_client: tuple[TestClient, dict[str, dict[str, str]], SkillRegistry],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.safety.auth.url_guard import URLVerdict

        client, headers, _registry = secured_meta_client
        public_root = isolated_cwd / "skills" / "public"
        public_root.mkdir(parents=True)
        outside = isolated_cwd / "outside-skill"
        outside.mkdir()
        (outside / "marker.txt").write_text("keep", encoding="utf-8")
        (public_root / "demo_skill").symlink_to(outside, target_is_directory=True)
        archive = _skill_zip_bytes("demo_skill")

        monkeypatch.setattr(
            "runtime.safety.auth.url_guard.check_url",
            lambda url, **_kwargs: URLVerdict(True, url, resolved_ip="93.184.216.34"),
        )
        monkeypatch.setattr(
            "runtime.safety.auth.url_guard.safe_httpx_get",
            lambda url, **_kwargs: httpx.Response(
                200,
                content=archive,
                request=httpx.Request("GET", url),
            ),
        )

        resp = client.post(
            "/api/skills/install",
            json={"url": "https://example.com/demo-skill.zip", "name": "demo_skill"},
            headers=headers["admin"],
        )

        assert resp.status_code == 409
        assert (outside / "marker.txt").read_text(encoding="utf-8") == "keep"

    def test_capability_permission_update_requires_admin(
        self,
        secured_meta_client: tuple[TestClient, dict[str, dict[str, str]], SkillRegistry],
    ) -> None:
        client, headers, _registry = secured_meta_client

        assert (
            client.put(
                "/api/capability-permissions/not-a-group",
                json={"enabled": False},
            ).status_code
            == 401
        )
        assert (
            client.put(
                "/api/capability-permissions/not-a-group",
                json={"enabled": False},
                headers=headers["user"],
            ).status_code
            == 403
        )
        assert (
            client.put(
                "/api/capability-permissions/not-a-group",
                json={"enabled": False},
                headers=headers["admin"],
            ).status_code
            == 404
        )

    def test_skill_install_requires_admin_before_body_validation(
        self,
        secured_meta_client: tuple[TestClient, dict[str, dict[str, str]], SkillRegistry],
    ) -> None:
        client, headers, _registry = secured_meta_client

        assert client.post("/api/skills/install", json={}).status_code == 401
        assert (
            client.post(
                "/api/skills/install",
                json={},
                headers=headers["user"],
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/skills/install",
                json={},
                headers=headers["admin"],
            ).status_code
            == 400
        )

    def test_production_rejects_url_based_prompt_install_for_admin(
        self,
        secured_meta_client: tuple[TestClient, dict[str, dict[str, str]], SkillRegistry],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client, headers, _registry = secured_meta_client
        monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "production")

        response = client.post(
            "/api/skills/install",
            json={"url": "https://example.com/unsigned-skill.zip"},
            headers=headers["admin"],
        )

        assert response.status_code == 403
        assert "reviewed release artifact" in response.json()["detail"]
