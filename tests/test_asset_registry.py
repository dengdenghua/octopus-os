"""统一资产仓库(插件/技能/角色)测试 —— 纯 tmp 目录,不写真实 home。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from runtime.platform.assets import asset_registry as ar


def _mk_codex_plugin(root: Path, pid: str, name: str) -> None:
    pd = root / pid
    (pd / ".codex-plugin").mkdir(parents=True)
    (pd / "skills" / "ctrl").mkdir(parents=True)
    (pd / "skills" / "ctrl" / "SKILL.md").write_text(f"name: {name}\n", "utf-8")
    (pd / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": pid,
                "version": "1.2.3",
                "interface": {
                    "displayName": name,
                    "shortDescription": "codex plugin desc",
                },
            }
        ),
        "utf-8",
    )


def _mk_connector(root: Path, cid: str, name: str) -> None:
    (root / cid).mkdir(parents=True)
    (root / cid / "SKILL.md").write_text(f"# {name}\n", "utf-8")


def _mk_skill(root: Path, sid: str, display: str) -> None:
    d = root / sid
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {display}\n---\n", "utf-8")


def _mk_agent(root: Path, aid: str, desc: str) -> None:
    d = root / aid
    d.mkdir(parents=True)
    (d / "agent.md").write_text(desc, "utf-8")
    (d / "node_modules" / "x").mkdir(parents=True)
    (d / "node_modules" / "x" / "y.js").write_text("skip me", "utf-8")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    src = tmp_path / "src"
    for sub in [
        "codex_plugins",
        "connectors",
        "local_skills",
        "builtin_skills",
        "imported",
        "agents",
    ]:
        (src / sub).mkdir(parents=True)

    _mk_codex_plugin(src / "codex_plugins", "codex_a", "Codex A")
    _mk_codex_plugin(src / "codex_plugins", "codex_b", "Codex B")

    (src / "connectors" / "echo-manifest.json").write_text(
        json.dumps(
            {
                "connectors": [
                    {
                        "id": "wb_x",
                        "name": "WB X",
                        "name_zh": "连X",
                        "description_zh": "conn x",
                        "auth_mode": "oauth",
                        "mcp_servers": [{"name": "srv1"}],
                    },
                    {
                        "id": "wb_y",
                        "name": "WB Y",
                        "name_zh": "连Y",
                        "description_zh": "conn y",
                        "auth_mode": "token",
                        "mcp_servers": {"alpha": {}, "beta": {}},
                    },
                ]
            }
        ),
        "utf-8",
    )
    _mk_connector(src / "connectors", "wb_x", "WB X")
    _mk_connector(src / "connectors", "wb_y", "WB Y")

    _mk_skill(src / "local_skills", "writing", "Writing Pro")
    _mk_skill(src / "local_skills", "coding", "Coding Pro")
    _mk_skill(src / "builtin_skills", "builtin_a", "Builtin A")
    _mk_skill(src / "builtin_skills", "writing", "Writing Pro")  # dup id,builtin 应被去重

    _mk_agent(src / "agents", "local_alpha", "local agent alpha")
    _mk_agent(src / "agents", "local_beta", "local agent beta")
    (src / "agents" / "node_modules").mkdir(parents=True)  # 顶层 skip

    (src / "imported" / "ext1" / "skills").mkdir(parents=True)
    _mk_skill(src / "imported" / "ext1" / "skills", "imported_skill", "Imported Skill")

    (src / "expert-store.json").write_text(
        json.dumps(
            {
                "experts": [
                    {
                        "plugin": "wb_expert1",
                        "expertType": "agent",
                        "displayName": {"zh": "专家一"},
                        "description": {"zh": "专家一描述"},
                    },
                    {
                        "plugin": "wb_team1",
                        "expertType": "team",
                        "displayName": {"zh": "团一"},
                        "description": {"zh": "团一描述"},
                    },
                ]
            }
        ),
        "utf-8",
    )

    monkeypatch.setattr(ar, "CODEX_PLUGIN_ROOT", src / "codex_plugins")
    monkeypatch.setattr(ar, "CONNECTOR_ROOT", src / "connectors")
    monkeypatch.setattr(ar, "CONNECTOR_MANIFEST", src / "connectors" / "echo-manifest.json")
    monkeypatch.setattr(ar, "LOCAL_SKILLS", src / "local_skills")
    monkeypatch.setattr(ar, "BUILTIN_SKILLS", src / "builtin_skills")
    monkeypatch.setattr(ar, "IMPORTED_ROOT", src / "imported")
    monkeypatch.setattr(ar, "AGENTS_ROOT", src / "agents")
    monkeypatch.setattr(ar, "EXPERT_STORE", src / "expert-store.json")
    return {"src": src, "dest": tmp_path / "echo-assets"}


def test_sync_counts_and_index_structure(env: dict[str, Path]) -> None:
    result = ar.sync_assets(dest_root=env["dest"])
    assert result["counts"] == {"plugin": 4, "skill": 4, "agent": 3, "team": 1}
    result["counts"]["plugin"] = 0
    assert ar.summary(root=env["dest"])["counts"]["plugin"] == 4

    idx = json.loads((env["dest"] / "index.json").read_text("utf-8"))
    assert idx["schema"] == "echo.assets.v1"
    assets = idx["assets"]
    assert len(assets) == 12

    kinds = {a["kind"] for a in assets}
    assert kinds == {"plugin", "skill", "agent", "team"}
    sources = {a["source"] for a in assets}
    assert sources == {"codex", "workbuddy", "local", "builtin", "imported"}


def test_skill_dedup_local_wins(env: dict[str, Path]) -> None:
    ar.sync_assets(dest_root=env["dest"])
    skills = ar.list_assets(kind="skill", root=env["dest"])
    writing = [s for s in skills if s["id"] == "writing"]
    assert len(writing) == 1
    assert writing[0]["source"] == "local"  # 内置同名被去重,本地优先


def test_connector_mcp_and_auth(env: dict[str, Path]) -> None:
    ar.sync_assets(dest_root=env["dest"])
    conns = ar.list_assets(kind="plugin", source="workbuddy", root=env["dest"])
    assert len(conns) == 2
    by_id = {c["id"]: c for c in conns}
    assert by_id["wb_x"]["mcp_servers"] == ["srv1"]  # list 兼容
    assert by_id["wb_y"]["mcp_servers"] == ["alpha", "beta"]  # dict 兼容
    assert by_id["wb_x"]["auth_mode"] == "oauth"


def test_search_and_get(env: dict[str, Path]) -> None:
    ar.sync_assets(dest_root=env["dest"])
    hit = ar.list_assets(search="专家一", root=env["dest"])
    assert any(a["id"] == "wb_expert1" for a in hit)

    agent = ar.get_asset("agent", "local_alpha", root=env["dest"])
    assert agent is not None
    assert agent["source"] == "local"
    assert ar.get_asset("plugin", "nope", root=env["dest"]) is None


def test_sync_idempotent_and_light_copy(env: dict[str, Path]) -> None:
    r1 = ar.sync_assets(dest_root=env["dest"])
    agent_dest = env["dest"] / "agents" / "local_alpha"
    stamp = (agent_dest / "agent.md").stat().st_mtime_ns

    # 源新增一个文件后再 sync:已复制文件不应被覆盖
    (env["src"] / "agents" / "local_alpha" / "new.md").write_text("new", "utf-8")
    r2 = ar.sync_assets(dest_root=env["dest"])
    assert (agent_dest / "agent.md").stat().st_mtime_ns == stamp
    assert (agent_dest / "new.md").exists()  # 新增文件被补齐
    assert r2["counts"] == r1["counts"]

    # node_modules 不应进入快照
    assert not (agent_dest / "node_modules").exists()


def test_summary_none_before_sync(tmp_path: Path) -> None:
    assert ar.summary(root=tmp_path / "missing") is None
    assert ar.list_assets(root=tmp_path / "missing") == []


def test_index_cache_reuses_parse_and_invalidates_on_file_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    index_path = root / "index.json"

    def write_index(name: str) -> None:
        index_path.write_text(
            json.dumps(
                {
                    "schema": "echo.assets.v1",
                    "meta": {"title": name, "counts": {"skill": 1}},
                    "assets": [
                        {
                            "id": "writing",
                            "kind": "skill",
                            "source": "local",
                            "name": name,
                            "nested": {"value": "original"},
                        }
                    ],
                }
            ),
            "utf-8",
        )

    write_index("First")
    read_calls = 0
    real_read_json = ar._read_json

    def counted_read_json(path: Path) -> dict[str, object] | None:
        nonlocal read_calls
        read_calls += 1
        return real_read_json(path)

    monkeypatch.setattr(ar, "_read_json", counted_read_json)

    first = ar.list_assets(root=root)
    first[0]["nested"]["value"] = "caller mutation"
    assert ar.summary(root=root)["title"] == "First"
    assert ar.list_assets(root=root)[0]["nested"]["value"] == "original"
    assert read_calls == 1

    old_stat = index_path.stat()
    write_index("Updated")
    os.utime(
        index_path,
        ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 2_000_000_000),
    )

    assert ar.list_assets(root=root)[0]["name"] == "Updated"
    assert read_calls == 2


def test_flat_plugins_layout_and_collision_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    (src / "codex_plugins").mkdir(parents=True)
    (src / "connectors").mkdir(parents=True)
    (src / "local_skills").mkdir(parents=True)
    (src / "builtin_skills").mkdir(parents=True)
    (src / "imported").mkdir(parents=True)
    (src / "agents").mkdir(parents=True)

    _mk_codex_plugin(src / "codex_plugins", "github", "GitHub Codex")
    (src / "connectors" / "echo-manifest.json").write_text(
        json.dumps(
            {"connectors": [{"id": "github", "name": "GitHub WB", "name_zh": "GitHub 连接器"}]}
        ),
        "utf-8",
    )
    _mk_connector(src / "connectors", "github", "GitHub WB")

    monkeypatch.setattr(ar, "CODEX_PLUGIN_ROOT", src / "codex_plugins")
    monkeypatch.setattr(ar, "CONNECTOR_ROOT", src / "connectors")
    monkeypatch.setattr(ar, "CONNECTOR_MANIFEST", src / "connectors" / "echo-manifest.json")
    monkeypatch.setattr(ar, "LOCAL_SKILLS", src / "local_skills")
    monkeypatch.setattr(ar, "BUILTIN_SKILLS", src / "builtin_skills")
    monkeypatch.setattr(ar, "IMPORTED_ROOT", src / "imported")
    monkeypatch.setattr(ar, "AGENTS_ROOT", src / "agents")
    monkeypatch.setattr(ar, "EXPERT_STORE", src / "expert-missing.json")

    dest = tmp_path / "echo-assets"
    ar.sync_assets(dest_root=dest)

    # 平铺:所有插件在 plugins/ 下,不按 source 嵌套
    assert (dest / "plugins" / "github").is_dir()  # codex 先到,占 github
    assert (dest / "plugins" / "github-workbuddy").is_dir()  # 连接器后到,加 source 后缀
    assert not (dest / "plugins" / "codex").exists()
    assert not (dest / "plugins" / "workbuddy").exists()

    # index 里 dir 字段记录了真实目录
    idx = json.loads((dest / "index.json").read_text("utf-8"))
    dirs = {(a["id"], a["source"]): a["dir"] for a in idx["assets"] if a["kind"] == "plugin"}
    assert dirs[("github", "codex")] == "github"
    assert dirs[("github", "workbuddy")] == "github-workbuddy"


