"""Cloud Expert Store(WorkBuddy 专家商城云端源)单测。

覆盖:本地镜像加载、列表/搜索/分类、详情、bundle 解压安全性、安装编排。
网络相关路径用 monkeypatch 短路,不依赖公网。
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from runtime.platform.plugins import cloud_expert_store as ces


def _make_store_json(expert_count: int = 3) -> dict:
    experts = []
    for i in range(expert_count):
        experts.append(
            {
                "id": f"Expert{i}",
                "plugin": f"expert-{i}",
                "expertType": "agent" if i % 2 == 0 else "team",
                "categoryId": "02-Engineering",
                "displayName": {"en": f"Expert {i}", "zh": f"专家{i}"},
                "profession": {"en": "Engineer", "zh": "工程师"},
                "description": {"en": "desc", "zh": "描述"},
                "tags": [{"en": "tag", "zh": "标签"}],
                "quickPrompts": [{"en": "q", "zh": "提示"}],
                "defaultInitPrompt": {"en": "p", "zh": "开场"},
                "avatar": "https://example.com/a.png",
                "promptFile": "/plugins/expert-0/agents/expert-0.md",
                "bundleUrl": "https://example.com/bundles/expert-0.tar.gz",
                "updatedAt": "2026-08-18T00:00:00Z",
            }
        )
    return {
        "meta": {"count": expert_count, "agentCount": 2, "teamCount": 1},
        "categories": [{"id": "02-Engineering", "name": {"en": "Engineering", "zh": "技术工程"}}],
        "experts": experts,
    }


def _write_mirror(store: dict) -> Path:
    # 构造一个假本地镜像:不落盘到真实仓库,而是 patch LOCAL_MIRROR
    return None  # noqa: BLE001


class TestCloudStoreLogic:
    def test_list_experts_uses_local_mirror(self, tmp_path, monkeypatch):
        store = _make_store_json(5)
        mirror = tmp_path / "expert-store.json"
        mirror.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(ces, "LOCAL_MIRROR", mirror)
        s = ces.CloudExpertStore(use_remote=False, use_cache=False)
        res = s.list_experts(limit=20)
        assert res["total"] == 5
        assert res["page_size"] == 20
        # 映射成 agent-market wire 形状
        a = res["agents"][0]
        assert a["source"] == "workbuddy-cloud"
        assert a["display_name"] == "专家0"
        assert a["category_id"] == "02-Engineering"
        assert a["is_team"] is False

    def test_search_filters(self, tmp_path, monkeypatch):
        store = _make_store_json(4)
        mirror = tmp_path / "expert-store.json"
        mirror.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(ces, "LOCAL_MIRROR", mirror)
        s = ces.CloudExpertStore(use_remote=False, use_cache=False)
        res = s.list_experts(search="专家3", limit=20)
        assert res["total"] == 1
        assert res["agents"][0]["id"] == "wb_expert-3"

    def test_category_filter_by_zh_name(self, tmp_path, monkeypatch):
        store = _make_store_json(3)
        mirror = tmp_path / "expert-store.json"
        mirror.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(ces, "LOCAL_MIRROR", mirror)
        s = ces.CloudExpertStore(use_remote=False, use_cache=False)
        res = s.list_experts(category="技术工程", limit=20)
        assert res["total"] == 3

    def test_team_flag(self, tmp_path, monkeypatch):
        store = _make_store_json(4)
        mirror = tmp_path / "expert-store.json"
        mirror.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(ces, "LOCAL_MIRROR", mirror)
        s = ces.CloudExpertStore(use_remote=False, use_cache=False)
        res = s.list_experts(limit=20)
        teams = [a for a in res["agents"] if a["is_team"]]
        assert len(teams) == 2

    def test_get_by_id(self, tmp_path, monkeypatch):
        store = _make_store_json(3)
        mirror = tmp_path / "expert-store.json"
        mirror.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(ces, "LOCAL_MIRROR", mirror)
        s = ces.CloudExpertStore(use_remote=False, use_cache=False)
        assert s.get("Expert1")["plugin"] == "expert-1"
        assert s.get("expert-2")["id"] == "Expert2"
        assert s.get("nope") is None


class TestBundleUnpack:
    def test_safe_extract_rejects_path_traversal(self, tmp_path):
        bundle = tmp_path / "evil.tar.gz"
        with tarfile.open(bundle, "w:gz") as tf:
            payload = b"evil"
            info = tarfile.TarInfo("../escape.txt")
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
        dest = tmp_path / "unpack"
        dest.mkdir()
        s = ces.CloudExpertStore(use_remote=False)
        try:
            s._unpack(bundle, dest)
            raised = False
        except ValueError:
            raised = True
        assert raised, "path traversal should be rejected"
        assert not (tmp_path / "escape.txt").exists()

    def test_unpack_valid_tarball(self, tmp_path):
        bundle = tmp_path / "ok.tar.gz"
        with tarfile.open(bundle, "w:gz") as tf:
            for name, data in (
                ("plugins/x/.codebuddy-plugin/plugin.json", b"{}"),
                ("plugins/x/agents/x.md", b"# x"),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        dest = tmp_path / "unpack"
        dest.mkdir()
        s = ces.CloudExpertStore(use_remote=False)
        out = s._unpack(bundle, dest)
        assert (out / "plugins/x/.codebuddy-plugin/plugin.json").exists()

    def test_rejects_symlink_before_writing_following_member(self, tmp_path):
        bundle = tmp_path / "symlink.tar.gz"
        with tarfile.open(bundle, "w:gz") as tf:
            link = tarfile.TarInfo("plugins/x/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../../outside"
            tf.addfile(link)
            payload = b"escape"
            file_info = tarfile.TarInfo("plugins/x/link/payload")
            file_info.size = len(payload)
            tf.addfile(file_info, io.BytesIO(payload))

        s = ces.CloudExpertStore(use_remote=False)
        with pytest.raises(ValueError, match="unsupported tar member"):
            s._unpack(bundle, tmp_path)
        assert not (tmp_path / "outside" / "payload").exists()

    def test_download_sanitizes_remote_plugin_filename(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ces, "fetch_public_https_bytes", lambda *a, **kw: b"bundle")

        out = ces.CloudExpertStore._download(
            "https://example.com/bundle.tar.gz",
            tmp_path,
            "../../escaped",
        )

        assert out.parent == tmp_path
        assert out.name == "escaped.tar.gz"
        assert out.read_bytes() == b"bundle"


class TestFindPackRoot:
    def test_finds_codebuddy_plugin_root(self, tmp_path):
        root = tmp_path / "unpack"
        (root / "plugins/x/.codebuddy-plugin").mkdir(parents=True)
        assert ces._find_pack_root(root) == root / "plugins/x"

    def test_falls_back_to_root(self, tmp_path):
        root = tmp_path / "unpack"
        root.mkdir()
        assert ces._find_pack_root(root) == root


class TestFindAgentName:
    def test_team_manifest_agent_name_beats_alphabetical_member(self, tmp_path):
        root = tmp_path / "stock-partner-team"
        agents = root / "agents"
        manifest = root / ".codebuddy-plugin" / "plugin.json"
        agents.mkdir(parents=True)
        manifest.parent.mkdir(parents=True)
        (agents / "contrarian-investor.md").write_text("# member", "utf-8")
        (agents / "stock-partner-lead.md").write_text("# lead", "utf-8")
        manifest.write_text(
            json.dumps(
                {
                    "agentName": "stock-partner-lead",
                    "teamInfo": {"leadAgent": "stock-partner-lead"},
                }
            ),
            "utf-8",
        )

        selected = ces._find_agent_name(
            root,
            {
                "plugin": "stock-partner-team",
                "promptFile": "/plugins/stock-partner-team/agents/stock-partner-lead.md",
            },
        )

        assert selected == "stock-partner-lead"

    def test_stock_partner_install_imports_lead_not_sorted_first_member(
        self, tmp_path, monkeypatch
    ):
        root = tmp_path / "stock-partner-team"
        agents = root / "agents"
        manifest = root / ".codebuddy-plugin" / "plugin.json"
        agents.mkdir(parents=True)
        manifest.parent.mkdir(parents=True)
        (agents / "contrarian-investor.md").write_text("# member", "utf-8")
        (agents / "stock-partner-lead.md").write_text("# lead", "utf-8")
        manifest.write_text(
            json.dumps(
                {
                    "agentName": "stock-partner-lead",
                    "teamInfo": {"leadAgent": "stock-partner-lead"},
                }
            ),
            "utf-8",
        )

        selected: list[str] = []

        class _Result:
            already_exists = False
            agent_id = "stock_partner_lead"
            agent_name = "Stock Partner Lead"
            agent_path = str(tmp_path / "installed" / agent_id)
            copied_skills: list[str] = []
            warnings: list[str] = []

        import runtime.execution.misc.agent_packs as agent_packs

        def fake_import(pack_root, agent_name, **_kwargs):
            assert pack_root == root
            selected.append(agent_name)
            return _Result()

        monkeypatch.setattr(agent_packs, "import_agent_from_pack", fake_import)
        store = ces.CloudExpertStore(use_remote=False, use_cache=False)
        monkeypatch.setattr(
            store,
            "get",
            lambda _expert_id: {
                "plugin": "stock-partner-team",
                "bundleUrl": "https://example.com/stock-partner-team.tar.gz",
                "promptFile": "/plugins/stock-partner-team/agents/stock-partner-lead.md",
            },
        )
        monkeypatch.setattr(store, "_download", lambda *_args: tmp_path / "bundle.tar.gz")
        monkeypatch.setattr(store, "_unpack", lambda *_args: root)

        result = store.install_expert(
            "wb_stock-partner-team",
            agents_root=tmp_path / "installed",
            skills_root=tmp_path / "skills",
        )

        assert selected == ["stock-partner-lead"]
        assert result["agent_id"] == "stock_partner_lead"

    def test_team_info_lead_is_used_when_agent_name_is_missing(self, tmp_path):
        root = tmp_path / "team"
        agents = root / "agents"
        manifest = root / ".codebuddy-plugin" / "plugin.json"
        agents.mkdir(parents=True)
        manifest.parent.mkdir(parents=True)
        (agents / "a-member.md").write_text("# member", "utf-8")
        (agents / "team-lead.md").write_text("# lead", "utf-8")
        manifest.write_text(
            json.dumps({"teamInfo": {"leadAgent": "team-lead"}}),
            "utf-8",
        )

        assert ces._find_agent_name(root, {"plugin": "team"}) == "team-lead"

    def test_catalog_prompt_file_precedes_alphabetical_fallback(self, tmp_path):
        root = tmp_path / "team"
        agents = root / "agents"
        agents.mkdir(parents=True)
        (agents / "a-member.md").write_text("# member", "utf-8")
        (agents / "team-lead.md").write_text("# lead", "utf-8")

        selected = ces._find_agent_name(
            root,
            {"promptFile": "/plugins/team/agents/team-lead.md"},
        )

        assert selected == "team-lead"


class TestWbPrefixAndInstalledDetection:
    """前端商城安装:wire id 带 wb_ 前缀,须可反查 + 与磁盘 slug 目录匹配。"""

    def test_get_with_wb_prefix(self, tmp_path, monkeypatch):
        store = _make_store_json(3)
        mirror = tmp_path / "expert-store.json"
        mirror.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(ces, "LOCAL_MIRROR", mirror)
        s = ces.CloudExpertStore(use_remote=False, use_cache=False)
        # 前端 list_experts 返回 id = wb_expert-1,安装时直接回传该 id
        listed = s.list_experts(limit=20)["agents"][1]
        assert listed["id"] == "wb_expert-1"
        assert s.get(listed["id"])["plugin"] == "expert-1"

    def test_is_installed_matches_slugged_agent_dir(self, tmp_path, monkeypatch):
        store = _make_store_json(3)
        mirror = tmp_path / "expert-store.json"
        mirror.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(ces, "LOCAL_MIRROR", mirror)
        # 模拟已经安装过:agents/expert_1(agent_packs slugified 名)
        agents_root = tmp_path / "agents"
        (agents_root / "expert_1").mkdir(parents=True)
        monkeypatch.setattr(ces, "default_agents_root", lambda: agents_root)
        s = ces.CloudExpertStore(use_remote=False, use_cache=False)
        by_id = {a["id"]: a for a in s.list_experts(limit=20)["agents"]}
        assert by_id["wb_expert-1"]["is_installed"] is True
        assert by_id["wb_expert-2"]["is_installed"] is False

    def test_install_expert_skips_when_slug_dir_exists(self, tmp_path, monkeypatch):
        store = _make_store_json(1)
        mirror = tmp_path / "expert-store.json"
        mirror.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(ces, "LOCAL_MIRROR", mirror)
        agents_root = tmp_path / "agents"
        (agents_root / "expert_0").mkdir(parents=True)
        s = ces.CloudExpertStore(use_remote=False, use_cache=False)
        res = s.install_expert(
            "wb_expert-0",
            agents_root=agents_root,
            skills_root=tmp_path / "skills",
        )
        assert res["installed"] is True
        assert res["already_exists"] is True
        assert res["agent_id"] == "wb_expert-0"


class TestCloudStoreRouterInstall:
    """前端商城安装走 HTTP 层:wb_ 前缀 id 必须能过(回归:此前 404)。"""

    def test_install_with_wb_prefix_via_router(self, tmp_path, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from runtime.sensing.gateway.agent_world_router import (
            create_agent_world_router,
        )

        store = _make_store_json(1)
        mirror = tmp_path / "expert-store.json"
        mirror.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(ces, "LOCAL_MIRROR", mirror)

        install_calls: list[str] = []

        class _FakeStore(ces.CloudExpertStore):
            def __init__(self, *a, **kw):
                super().__init__(use_remote=False, use_cache=False)

            def install_expert(self, expert_id, **kw):
                install_calls.append(expert_id)
                return {
                    "installed": True,
                    "already_exists": False,
                    "agent_id": f"wb_{expert_id}",
                    "agent_name": "X",
                    "agent_path": str(tmp_path / "agents" / "expert_0"),
                }

        monkeypatch.setattr(ces, "CloudExpertStore", _FakeStore)

        app = FastAPI()
        app.include_router(create_agent_world_router())
        client = TestClient(app)

        res = client.post("/api/agent-market/cloud/store/wb_expert-0/install")
        assert res.status_code == 200, res.text
        assert res.json()["installed"] is True
        assert install_calls == ["wb_expert-0"]

