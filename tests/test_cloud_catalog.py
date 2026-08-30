"""CloudCatalog(云商城插件/技能目录 + 内容包安装)单测。

覆盖:目录解析、内容包解包安全、install_skill 落地/幂等、install_plugin 落地 +
捆绑技能复制。内容包用内存 tar.gz 构造,不依赖公网。
"""

from __future__ import annotations

import io
import json
import tarfile

import pytest

from runtime.platform.plugins import cloud_catalog
from runtime.platform.plugins.cloud_catalog import CloudCatalog


def _make_skill_pack() -> bytes:
    """构造 echo-skills.tar.gz:skills/api-doc-gen/{SKILL.md, scripts/gen.py}。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in [
            ("skills/api-doc-gen/SKILL.md", b"# API Doc Gen\n"),
            ("skills/api-doc-gen/meta.json", b'{"name":"api-doc-gen"}\n'),
            ("skills/api-doc-gen/scripts/gen.py", b"print('hi')\n"),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _make_plugin_pack() -> bytes:
    """构造 echo-plugins.tar.gz:plugins/codex/figma + plugins/connector/wecom。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        entries = [
            (
                "plugins/codex/figma/.codex-plugin/plugin.json",
                b'{"name":"figma","version":"1.0.0"}\n',
            ),
            ("plugins/codex/figma/skills/figma-use/SKILL.md", b"# Figma Use\n"),
            (
                "plugins/connector/wecom/.echo-connector/manifest.json",
                b'{"schema":"echo.connector_package.v1","id":"wecom","version":"1.0.0"}\n',
            ),
            ("plugins/connector/wecom/cli.json", b'{"command":"wecom"}\n'),
            ("plugins/connector/wecom/skills/wecomcli-calendar/SKILL.md", b"# WeCom Calendar\n"),
        ]
        for name, content in entries:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class TestExtractMember:
    def test_extracts_under_prefix(self, tmp_path):
        pack = tmp_path / "pack.tar.gz"
        pack.write_bytes(_make_skill_pack())
        cat = CloudCatalog("skills", use_remote=False, use_cache=False)
        out = cat._extract_member(pack, "skills", tmp_path / "x", "api-doc-gen")
        assert out.name == "api-doc-gen"
        assert (out / "SKILL.md").exists()
        assert (out / "scripts" / "gen.py").exists()

    def test_normalizes_archive_file_modes(self, tmp_path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name, mode in (("run.sh", 0o4777), ("config.json", 0o666)):
                content = b"content"
                info = tarfile.TarInfo(f"skills/modes/{name}")
                info.mode = mode
                info.size = len(content)
                tf.addfile(info, io.BytesIO(content))
        pack = tmp_path / "modes.tar.gz"
        pack.write_bytes(buf.getvalue())

        out = CloudCatalog("skills", use_remote=False, use_cache=False)._extract_member(
            pack,
            "skills",
            tmp_path / "x",
            "modes",
        )

        assert out is not None
        assert (out / "run.sh").stat().st_mode & 0o7777 == 0o755
        assert (out / "config.json").stat().st_mode & 0o7777 == 0o644

    def test_missing_member_returns_none(self, tmp_path):
        pack = tmp_path / "pack.tar.gz"
        pack.write_bytes(_make_skill_pack())
        cat = CloudCatalog("skills", use_remote=False, use_cache=False)
        assert cat._extract_member(pack, "skills", tmp_path / "x", "nope") is None

    def test_rejects_path_traversal(self, tmp_path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            evil = "skills/evil/../../../outside.txt"
            info = tarfile.TarInfo(evil)
            data = b"boom"
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        pack = tmp_path / "evil.tar.gz"
        pack.write_bytes(buf.getvalue())
        cat = CloudCatalog("skills", use_remote=False, use_cache=False)
        try:
            cat._extract_member(pack, "skills", tmp_path / "x", "evil")
        except ValueError:
            return
        raise AssertionError("path traversal not rejected")

    def test_rejects_symlink_member(self, tmp_path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            link = tarfile.TarInfo("skills/evil/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../outside"
            tf.addfile(link)
        pack = tmp_path / "symlink.tar.gz"
        pack.write_bytes(buf.getvalue())

        cat = CloudCatalog("skills", use_remote=False, use_cache=False)
        with pytest.raises(ValueError, match="unsupported tar member"):
            cat._extract_member(pack, "skills", tmp_path / "x", "evil")

    def test_late_invalid_member_removes_partial_tree(self, tmp_path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            safe = b"partial"
            safe_info = tarfile.TarInfo("skills/evil/SAFE.txt")
            safe_info.size = len(safe)
            tf.addfile(safe_info, io.BytesIO(safe))
            link = tarfile.TarInfo("skills/evil/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../outside"
            tf.addfile(link)
        pack = tmp_path / "late-symlink.tar.gz"
        pack.write_bytes(buf.getvalue())

        cat = CloudCatalog("skills", use_remote=False, use_cache=False)
        out = tmp_path / "x" / "evil"

        with pytest.raises(ValueError, match="unsupported tar member"):
            cat._extract_member(pack, "skills", tmp_path / "x", "evil")
        assert not out.exists()


class TestArchiveDownload:
    def test_plugin_archive_allows_current_first_party_pack_with_bounded_headroom(
        self, tmp_path, monkeypatch
    ):
        observed: dict[str, int] = {}

        def _download(_url: str, *, timeout: float, max_bytes: int) -> bytes:
            assert timeout == 180
            observed["max_bytes"] = max_bytes
            return b"archive"

        monkeypatch.setattr(cloud_catalog, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(cloud_catalog, "fetch_public_https_bytes", _download)

        path = CloudCatalog("plugins", use_remote=False, use_cache=False)._archive_path()

        assert path.read_bytes() == b"archive"
        assert observed["max_bytes"] == 192 * 1024 * 1024
        assert observed["max_bytes"] < cloud_catalog._MAX_EXTRACTED_BYTES


class TestInstallSkill:
    def test_installs_to_target_and_is_idempotent(self, tmp_path, monkeypatch):
        pack = tmp_path / "pack.tar.gz"
        pack.write_bytes(_make_skill_pack())
        skills_root = tmp_path / "skills"
        cat = CloudCatalog("skills", use_remote=False, use_cache=False)
        monkeypatch.setattr(cat, "_archive_path", lambda: pack)
        res = cat.install_skill("api-doc-gen", skills_dir=skills_root)
        assert res["installed"] is True
        assert (skills_root / "api-doc-gen" / "SKILL.md").exists()
        # 幂等:二次安装返回 already_exists
        res2 = cat.install_skill("api-doc-gen", skills_dir=skills_root)
        assert res2["already_exists"] is True


class TestInstalledPlugins:
    def test_merges_cloud_dir_codex_cache_and_connector_state(self, tmp_path, monkeypatch):
        cat = CloudCatalog("plugins", use_remote=False, use_cache=False)
        # 云安装落点
        monkeypatch.setattr(
            "runtime.platform.plugins.cloud_catalog.CloudCatalog.PLUGIN_INSTALL_ROOT",
            tmp_path / "plugins",
        )
        (tmp_path / "plugins" / "connector" / "wecom").mkdir(parents=True)
        (tmp_path / "plugins" / "codex" / "figma").mkdir(parents=True)
        # codex 格式插件(echo 布局 <plugin>/.codex-plugin/plugin.json)
        cache = tmp_path / "codex-cache" / "sites"
        (cache / ".codex-plugin").mkdir(parents=True)
        (cache / ".codex-plugin" / "plugin.json").write_text('{"name":"sites"}', encoding="utf-8")
        monkeypatch.setattr(
            "runtime.platform.plugins.cloud_catalog.CloudCatalog.CODEX_CACHE_ROOT",
            tmp_path / "codex-cache",
        )
        # 连接器状态
        st = tmp_path / "connectors" / "state.json"
        st.parent.mkdir(parents=True)
        st.write_text(
            json.dumps(
                {
                    "github": {"id": "github", "installed": True},
                    "wecom": {"id": "wecom", "installed": False},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "runtime.platform.plugins.cloud_catalog.CloudCatalog.CONNECTOR_STATE_FILE",
            st,
        )
        got = cat.installed_plugins()
        assert "wecom" in got  # 云安装落点
        assert "figma" in got  # 云安装落点(codex)
        assert "sites" in got  # codex 缓存
        assert "github" in got  # 连接器状态 installed=true
        assert "tencent-docs" not in got  # 未安装的连接器不标
        assert got == sorted(got)

    def test_cloud_catalog_exposes_workbench_apps(self):
        cat = CloudCatalog("plugins", use_remote=False, use_cache=False)
        ids = {item["id"] for item in cat.items()}
        assert {
            "workbench_paper-trading",
            "workbench_design",
            "workbench_intelligence",
            "workbench_community",
        }.issubset(ids)

    def test_catalog_adds_honest_distribution_notes_without_claiming_original_authorship(self):
        cat = CloudCatalog("plugins", use_remote=False, use_cache=False)
        cat._store = {
            "items": [
                {
                    "id": "codex_documents",
                    "plugin": "documents",
                    "kind": "plugin",
                    "version": "2.0.0",
                    "author": "OpenAI",
                },
                {
                    "id": "wb_wecom",
                    "plugin": "wecom",
                    "kind": "connector",
                    "version": "1.0.0",
                    "author": "WorkBuddy",
                },
            ]
        }

        projected = {item["plugin"]: item for item in cat.items() if "plugin" in item}

        assert projected["documents"]["author"] == "OpenAI"
        assert projected["documents"]["release_summary"].startswith("2.0.0：由 Echo")
        assert projected["wecom"]["author"] == "WorkBuddy"
        assert projected["wecom"]["release_summary"].startswith("1.0.0：首次纳入")


class TestInstallPlugin:
    def test_connector_lands_and_copies_skills(self, tmp_path, monkeypatch):
        pack = tmp_path / "pack.tar.gz"
        pack.write_bytes(_make_plugin_pack())
        cat = CloudCatalog("plugins", use_remote=False, use_cache=False)
        cat._store = {
            "items": [
                {
                    "id": "wb_wecom",
                    "plugin": "wecom",
                    "kind": "connector",
                    "version": "1.0.0",
                }
            ]
        }
        monkeypatch.setattr(cat, "_archive_path", lambda: pack)
        monkeypatch.setattr(
            "runtime.platform.plugins.cloud_catalog.CloudCatalog.PLUGIN_INSTALL_ROOT",
            tmp_path / "plugins",
        )
        monkeypatch.setattr(
            "runtime.platform.plugins.cloud_catalog.CloudCatalog.CONNECTOR_STATE_FILE",
            tmp_path / "connectors" / "state.json",
        )
        monkeypatch.setattr(
            "runtime.platform.plugins.cloud_catalog.CloudCatalog.SKILLS_ROOT",
            tmp_path / "skills",
        )
        res = cat.install_plugin("wecom", plugin_kind="connector")
        assert res["installed"] is True
        plugin_dir = tmp_path / "plugins" / "connector" / "wecom"
        assert (plugin_dir / "cli.json").exists()
        # 捆绑技能复制到 ~/.echo/skills/<id>__<skill>
        copied = res["copied_skills"]
        assert copied == ["wecom__wecomcli-calendar"]
        assert (tmp_path / "skills" / "wecom__wecomcli-calendar" / "SKILL.md").exists()

    def test_codex_plugin_lands(self, tmp_path, monkeypatch):
        pack = tmp_path / "pack.tar.gz"
        pack.write_bytes(_make_plugin_pack())
        cat = CloudCatalog("plugins", use_remote=False, use_cache=False)
        cat._store = {
            "items": [
                {
                    "id": "codex_figma",
                    "plugin": "figma",
                    "kind": "plugin",
                    "version": "1.0.0",
                }
            ]
        }
        monkeypatch.setattr(cat, "_archive_path", lambda: pack)
        monkeypatch.setattr(
            "runtime.platform.plugins.cloud_catalog.CloudCatalog.PLUGIN_INSTALL_ROOT",
            tmp_path / "plugins",
        )
        monkeypatch.setattr(
            "runtime.platform.plugins.cloud_catalog.CloudCatalog.SKILLS_ROOT",
            tmp_path / "skills",
        )
        monkeypatch.setattr(
            "runtime.platform.plugins.cloud_catalog.CloudCatalog.CAPABILITY_STATE_FILE",
            tmp_path / "capabilities" / "state.json",
        )
        res = cat.install_plugin("figma", plugin_kind="codex")
        assert (tmp_path / "plugins" / "codex" / "figma" / ".codex-plugin" / "plugin.json").exists()
        assert res["kind"] == "codex"

    def test_uninstalls_only_mutable_package_and_copied_skills(self, tmp_path, monkeypatch):
        cat = CloudCatalog("plugins", use_remote=False, use_cache=False)
        plugins_root = tmp_path / "plugins"
        skills_root = tmp_path / "skills"
        target = plugins_root / "workbench" / "design"
        target.mkdir(parents=True)
        (target / "app.json").write_text("{}", encoding="utf-8")
        copied = skills_root / "design__helper"
        copied.mkdir(parents=True)
        (copied / "SKILL.md").write_text("# helper", encoding="utf-8")
        skills_root.mkdir(parents=True, exist_ok=True)
        (skills_root / "registry.json").write_text(
            json.dumps([{"name": "design__helper"}, {"name": "keep"}]),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "runtime.platform.plugins.cloud_catalog.CloudCatalog.PLUGIN_INSTALL_ROOT",
            plugins_root,
        )
        monkeypatch.setattr(
            "runtime.platform.plugins.cloud_catalog.CloudCatalog.SKILLS_ROOT",
            skills_root,
        )

        result = cat.uninstall_plugin("design", plugin_kind="workbench")

        assert result["uninstalled"] is True
        assert not target.exists()
        assert not copied.exists()
        registry = json.loads((skills_root / "registry.json").read_text("utf-8"))
        assert registry == [{"name": "keep"}]


class TestSyncCodexCache:
    def test_migrates_legacy_cache_to_echo_layout(self, tmp_path):
        from runtime.platform.plugins import codex_discovery

        legacy = tmp_path / "legacy-cache"
        # 旧布局:<family>/<plugin>/<version>/.codex-plugin/plugin.json(同插件多版本)
        v10 = legacy / "openai" / "figma" / "1.0.0"
        (v10 / ".codex-plugin").mkdir(parents=True)
        (v10 / ".codex-plugin" / "plugin.json").write_text(
            '{"name":"figma","version":"1.0.0"}', encoding="utf-8"
        )
        v11 = legacy / "openai" / "figma" / "1.1.0"
        (v11 / ".codex-plugin").mkdir(parents=True)
        (v11 / ".codex-plugin" / "plugin.json").write_text(
            '{"name":"figma","version":"1.1.0"}', encoding="utf-8"
        )
        dest = tmp_path / "echo-codex"
        n = codex_discovery.sync_codex_cache_to_echo(source=legacy, dest=dest)
        assert n == 1
        # 只保留最新版本,且为 echo 布局 <plugin>/
        assert (dest / "figma" / ".codex-plugin" / "plugin.json").exists()
        meta = json.loads((dest / "figma" / ".codex-plugin" / "plugin.json").read_text("utf-8"))
        assert meta["version"] == "1.1.0"
        # 幂等:重复同步不再复制
        n2 = codex_discovery.sync_codex_cache_to_echo(source=legacy, dest=dest)
        assert n2 == 0



