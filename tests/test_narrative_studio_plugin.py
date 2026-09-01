from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.suckers.registry import SkillRegistry
from runtime.platform.plugins.bundled import narrative_studio
from runtime.platform.plugins.bundled.narrative_studio import (
    API_PREFIX,
    NarrativeStudioPlugin,
)
from runtime.platform.plugins.plugin_base import ModuleContext

PLUGIN_DIR = (
    Path(__file__).resolve().parents[1]
    / "runtime"
    / "platform"
    / "plugins"
    / "bundled"
    / "narrative_studio"
)


def _load(
    tmp_path: Path,
    *,
    echo_source_path: str = "",
    echo_max_bytes_per_file: int = 2 * 1024 * 1024,
) -> tuple[NarrativeStudioPlugin, TestClient, SkillRegistry]:
    app = FastAPI()
    registry = SkillRegistry()
    plugin = NarrativeStudioPlugin()
    plugin.on_load(
        ModuleContext(
            plugin_name="narrative_studio",
            plugin_dir=str(PLUGIN_DIR),
            manifest=None,
            fastapi_app=app,
            skill_registry=registry,
            config={
                "data_dir": str(tmp_path / "narrative"),
                "echo_source_path": echo_source_path,
                "echo_max_files": 100,
                "echo_max_chars_per_file": 5000,
                "echo_max_bytes_per_file": echo_max_bytes_per_file,
            },
        )
    )
    return plugin, TestClient(app), registry


def _create_project(client: TestClient, project_id: str = "echo-test") -> dict:
    response = client.post(
        f"{API_PREFIX}/projects",
        json={
            "id": project_id,
            "title": "陌生人的记忆",
            "premise": "A memory becomes evidence.",
            "language": "bilingual",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_data_dir_follows_desktop_root_and_preserves_legacy_source_projects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    shared_data = tmp_path / "shared-data"
    legacy_home = tmp_path / "legacy-home"
    legacy_data = legacy_home / ".echo" / "data" / "narrative-studio"
    legacy_data.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(legacy_home))
    monkeypatch.delenv("ECHO_DATA_DIR", raising=False)
    monkeypatch.delenv("ECHO_HOME", raising=False)
    monkeypatch.setattr(
        narrative_studio,
        "app_paths",
        lambda: SimpleNamespace(data_dir=shared_data),
    )

    assert NarrativeStudioPlugin._resolve_data_dir({}) == legacy_data

    monkeypatch.setenv("ECHO_DATA_DIR", str(shared_data))
    assert NarrativeStudioPlugin._resolve_data_dir({}) == shared_data / "narrative-studio"
    explicit = tmp_path / "explicit"
    assert NarrativeStudioPlugin._resolve_data_dir({"data_dir": str(explicit)}) == explicit


def test_candidate_story_vertical_slice_persists_and_updates_atomically(tmp_path: Path) -> None:
    plugin, client, _registry = _load(tmp_path)
    project = _create_project(client)
    assert project["canon_policy"] == "candidate_only"
    assert project["default_branch_id"] == "main"

    branch = client.post(
        f"{API_PREFIX}/projects/{project['id']}/branches",
        json={"id": "alternate", "name": "另一条回声", "base_branch_id": "main"},
    )
    assert branch.status_code == 201
    assert branch.json()["canon_status"] == "candidate"

    pack = client.post(
        f"{API_PREFIX}/projects/{project['id']}/world-packs",
        json={"id": "core-world", "name": "世界核心", "summary": "技术而非魔法"},
    )
    assert pack.status_code == 201
    assert pack.json()["canon_status"] == "candidate"

    chapter = client.post(
        f"{API_PREFIX}/projects/{project['id']}/chapters",
        json={
            "id": "chapter-1",
            "branch_id": "main",
            "ordinal": 1,
            "title": "被借走的手",
            "summary": "林乔在醒来后发现陌生程序记忆。",
            "body": "第一稿",
        },
    )
    assert chapter.status_code == 201
    assert chapter.json()["canon_status"] == "candidate"

    scene = client.post(
        f"{API_PREFIX}/projects/{project['id']}/chapters/chapter-1/scenes",
        json={
            "id": "scene-1",
            "branch_id": "main",
            "ordinal": 1,
            "title": "醒来",
            "goal": "确认身体归属",
            "conflict": "手记得她不认识的人",
            "body": "场景第一稿",
        },
    )
    assert scene.status_code == 201
    assert scene.json()["canon_status"] == "candidate"

    fact = client.post(
        f"{API_PREFIX}/projects/{project['id']}/facts",
        json={
            "id": "fact-memory-not-spirit",
            "subject": "Ghost",
            "predicate": "origin",
            "object": "uploaded memory, never a spirit",
            "scope": "world",
        },
    )
    assert fact.status_code == 201
    assert fact.json()["canon_status"] == "candidate"

    change = client.post(
        f"{API_PREFIX}/projects/{project['id']}/state-changes",
        json={
            "id": "change-linqiao-hand",
            "branch_id": "main",
            "chapter_id": "chapter-1",
            "scene_id": "scene-1",
            "entity_id": "lin-qiao",
            "field": "procedural_memory",
            "before": "absent",
            "after": "ren-vale-care-pattern",
            "reason": "Scene outcome",
        },
    )
    assert change.status_code == 201
    assert change.json()["canon_status"] == "candidate"

    saved = client.put(
        f"{API_PREFIX}/projects/{project['id']}/chapters/chapter-1",
        json={"body": "第二稿", "status": "review"},
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2
    assert saved.json()["body"] == "第二稿"
    assert saved.json()["canon_status"] == "candidate"

    saved_scene = client.put(
        f"{API_PREFIX}/projects/{project['id']}/chapters/chapter-1/scenes/scene-1",
        json={"outcome": "她保留了手中的陌生技能", "status": "candidate"},
    )
    assert saved_scene.status_code == 200
    assert saved_scene.json()["revision"] == 2

    detail = client.get(f"{API_PREFIX}/projects/{project['id']}").json()
    assert detail["counts"] == {
        "branches": 2,
        "chapters": 1,
        "facts": 1,
        "scenes": 1,
        "state_changes": 1,
        "world_packs": 1,
    }
    assert client.get(f"{API_PREFIX}/projects").json()["total"] == 1
    assert (
        client.get(f"{API_PREFIX}/projects/{project['id']}/chapters/chapter-1/scenes").json()[
            "total"
        ]
        == 1
    )

    chapter_path = (
        plugin.store.data_dir / "projects" / project["id"] / "chapters" / "chapter-1.json"
    )
    assert chapter_path.is_file()
    assert not list(plugin.store.data_dir.rglob("*.tmp"))


def test_echo_import_reads_real_collections_and_blocks_symlink_and_oversize(
    tmp_path: Path,
) -> None:
    source = tmp_path / "echo-universe-engine"
    for folder in ("bible", "characters", "factions", "locations", "stories", "timeline"):
        (source / folder).mkdir(parents=True)
    (source / "bible" / "rules.md").write_text("# Rules\nGhosts are data.", encoding="utf-8")
    (source / "characters" / "zero.md").write_text("# Zero", encoding="utf-8")
    (source / "factions" / "chaser.md").write_text("# CHASER", encoding="utf-8")
    (source / "locations" / "white-harbor.md").write_text("# White Harbor", encoding="utf-8")
    (source / "stories" / "episode-1.md").write_text("# Episode 1", encoding="utf-8")
    (source / "timeline" / "timeline.yaml").write_text("events: []", encoding="utf-8")
    (source / "bible" / "too-large.md").write_bytes(b"x" * 2048)
    secret = tmp_path / "outside-secret.md"
    secret.write_text("MUST NOT BE IMPORTED", encoding="utf-8")
    (source / "bible" / "linked-secret.md").symlink_to(secret)

    _plugin, client, _registry = _load(
        tmp_path,
        echo_source_path=str(source),
        echo_max_bytes_per_file=1024,
    )
    project = _create_project(client, "echo-import")
    status = client.get(f"{API_PREFIX}/status").json()
    assert status["echo"]["available"] is True
    # The external symlink is not counted as a supported source document.
    assert status["echo"]["inventory"]["bible"] == 2

    imported = client.post(
        f"{API_PREFIX}/projects/{project['id']}/imports/echo",
        json={"pack_name": "ECHO Snapshot", "include_content": False},
    )
    assert imported.status_code == 200
    payload = imported.json()
    assert payload["available"] is True
    assert payload["imported"] is True
    assert payload["world_pack"]["canon_status"] == "candidate"
    resources = payload["world_pack"]["resources"]
    paths = {item["relative_path"] for item in resources}
    assert "bible/rules.md" in paths
    assert "characters/zero.md" in paths
    assert "stories/episode-1.md" in paths
    assert "timeline/timeline.yaml" in paths
    assert "bible/linked-secret.md" not in paths
    assert "bible/too-large.md" not in paths
    assert all(item["excerpt"] == "" for item in resources)
    assert payload["skipped_oversize"] == 1
    assert payload["world_pack"]["metadata"]["skipped_oversize"] == 1

    imported_with_content = client.post(
        f"{API_PREFIX}/projects/{project['id']}/imports/echo",
        json={"pack_name": "ECHO Content Snapshot", "include_content": True},
    ).json()
    rule = next(
        item
        for item in imported_with_content["world_pack"]["resources"]
        if item["relative_path"] == "bible/rules.md"
    )
    assert "Ghosts are data" in rule["excerpt"]


def test_missing_echo_source_degrades_without_creating_pack(tmp_path: Path) -> None:
    _plugin, client, _registry = _load(tmp_path, echo_source_path=str(tmp_path / "does-not-exist"))
    project = _create_project(client, "missing-echo")
    response = client.post(
        f"{API_PREFIX}/projects/{project['id']}/imports/echo",
        json={},
    )
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["imported"] is False
    assert "does not exist" in response.json()["reason"]
    assert client.get(f"{API_PREFIX}/projects/{project['id']}/world-packs").json()["total"] == 0


def test_echo_source_can_be_discovered_from_environment(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "portable-echo"
    (source / "bible").mkdir(parents=True)
    (source / "bible" / "rules.md").write_text("# Rules", encoding="utf-8")
    monkeypatch.setenv("ECHO_UNIVERSE_ROOT", str(source))

    _plugin, client, _registry = _load(tmp_path)

    status = client.get(f"{API_PREFIX}/status").json()
    assert status["echo"]["available"] is True
    assert status["echo"]["source_root"] == str(source.resolve())


def test_agent_skills_are_candidate_only_and_no_promotion_surface(tmp_path: Path) -> None:
    _plugin, client, registry = _load(tmp_path)
    expected = {
        "narrative_studio.project_create",
        "narrative_studio.chapter_candidate",
        "narrative_studio.fact_candidate",
        "narrative_studio.state_change_candidate",
        "narrative_studio.echo_import_candidate",
    }
    assert expected.issubset(set(registry.all_names()))
    assert not any("promot" in name or "canon_write" in name for name in registry.all_names())

    created = registry.get("narrative_studio.project_create").handler(
        id="skill-project", title="Skill Story"
    )
    assert created["ok"] is True
    assert created["result"]["canon_policy"] == "candidate_only"

    chapter = registry.get("narrative_studio.chapter_candidate").handler(
        project_id="skill-project",
        branch_id="main",
        id="chapter-skill",
        ordinal=1,
        title="Candidate only",
        body="draft",
        canon_status="canonical",  # ignored; the skill has no canon write field
    )
    assert chapter["ok"] is True
    assert chapter["result"]["canon_status"] == "candidate"

    app_routes = [route.path for route in client.app.routes if hasattr(route, "path")]
    assert not any("promote" in path or "publish" in path for path in app_routes)
    page = client.get(f"{API_PREFIX}/page")
    assert page.status_code == 200
    assert "CANDIDATE ONLY" in page.text


def test_ids_cannot_escape_project_storage(tmp_path: Path) -> None:
    _plugin, client, _registry = _load(tmp_path)
    response = client.post(
        f"{API_PREFIX}/projects",
        json={"id": "../../escape", "title": "unsafe"},
    )
    assert response.status_code == 400
    assert not (tmp_path / "escape").exists()

