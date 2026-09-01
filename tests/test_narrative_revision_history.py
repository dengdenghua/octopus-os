from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from runtime.execution.suckers.registry import SkillRegistry
from runtime.platform.plugins.bundled.narrative_studio import (
    API_PREFIX,
    NarrativeStudioPlugin,
)
from runtime.platform.plugins.bundled.narrative_studio.models import (
    ChapterCreate,
    ChapterUpdate,
    ProjectCreate,
)
from runtime.platform.plugins.bundled.narrative_studio.store import (
    NarrativeConflict,
    NarrativeStore,
    NarrativeStoreError,
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
    tmp_path: Path, *, principal: str | None = None
) -> tuple[NarrativeStudioPlugin, TestClient]:
    app = FastAPI()
    if principal:

        @app.middleware("http")
        async def authenticated_principal(request: Request, call_next):
            request.state.principal = {"id": principal}
            return await call_next(request)

    plugin = NarrativeStudioPlugin()
    plugin.on_load(
        ModuleContext(
            plugin_name="narrative_studio",
            plugin_dir=str(PLUGIN_DIR),
            manifest=None,
            fastapi_app=app,
            skill_registry=SkillRegistry(),
            config={"data_dir": str(tmp_path / "narrative")},
        )
    )
    return plugin, TestClient(app)


def _project(client: TestClient, project_id: str = "history") -> dict:
    response = client.post(
        f"{API_PREFIX}/projects",
        json={"id": project_id, "title": "History"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _chapter(
    client: TestClient,
    project_id: str = "history",
    chapter_id: str = "chapter-1",
    ordinal: int = 1,
) -> tuple[dict, object]:
    response = client.post(
        f"{API_PREFIX}/projects/{project_id}/chapters",
        json={
            "id": chapter_id,
            "branch_id": "main",
            "ordinal": ordinal,
            "title": f"Chapter {ordinal}",
            "body": "first draft",
        },
    )
    assert response.status_code == 201, response.text
    return response.json(), response


def test_chapter_history_etag_and_all_concurrency_inputs(tmp_path: Path) -> None:
    _plugin, client = _load(tmp_path, principal="writer-7")
    _project(client)
    chapter, created = _chapter(client)
    assert chapter["revision"] == 1
    assert created.headers["etag"] == '"1"'

    current = client.get(f"{API_PREFIX}/projects/history/chapters/chapter-1")
    assert current.status_code == 200
    assert current.headers["etag"] == '"1"'

    listing = client.get(f"{API_PREFIX}/projects/history/chapters/chapter-1/revisions").json()
    assert listing["total"] == 1
    assert "snapshot" not in listing["items"][0]
    assert listing["items"][0]["actor"] == "writer-7"
    assert listing["items"][0]["actor_source"] == "authenticated_principal"

    revision_one = client.get(f"{API_PREFIX}/projects/history/chapters/chapter-1/revisions/1")
    assert revision_one.headers["etag"] == '"1"'
    assert revision_one.json()["snapshot"]["body"] == "first draft"

    # If-Match has priority over a conflicting body compatibility value.
    second = client.put(
        f"{API_PREFIX}/projects/history/chapters/chapter-1",
        headers={"If-Match": '"1"'},
        json={"body": "second draft", "expected_revision": 99},
    )
    assert second.status_code == 200, second.text
    assert second.json()["revision"] == 2
    assert second.headers["etag"] == '"2"'

    # A stale header also wins over current query/body values and must not write r3.
    stale = client.put(
        f"{API_PREFIX}/projects/history/chapters/chapter-1?expected_revision=2",
        headers={"If-Match": '"1"'},
        json={"body": "must not persist", "expected_revision": 2},
    )
    assert stale.status_code == 409
    assert (
        client.get(f"{API_PREFIX}/projects/history/chapters/chapter-1").json()["body"]
        == "second draft"
    )

    weak = client.put(
        f"{API_PREFIX}/projects/history/chapters/chapter-1",
        headers={"If-Match": 'W/"2"'},
        json={"body": "third draft"},
    )
    assert weak.status_code == 200, weak.text
    assert weak.json()["revision"] == 3

    # Old clients remain compatible when no precondition is supplied.
    unconditional = client.put(
        f"{API_PREFIX}/projects/history/chapters/chapter-1",
        json={"body": "legacy client draft"},
    )
    assert unconditional.status_code == 200
    assert unconditional.json()["revision"] == 4

    malformed = client.put(
        f"{API_PREFIX}/projects/history/chapters/chapter-1",
        headers={"If-Match": '"4", "3"'},
        json={"body": "invalid etag"},
    )
    assert malformed.status_code == 400

    revisions = client.get(f"{API_PREFIX}/projects/history/chapters/chapter-1/revisions").json()
    assert [row["revision"] for row in revisions["items"]] == [4, 3, 2, 1]
    assert (
        client.get(f"{API_PREFIX}/projects/history/chapters/chapter-1/revisions/1").json()[
            "snapshot"
        ]["body"]
        == "first draft"
    )


def test_restore_creates_a_new_candidate_revision_and_preserves_old_snapshots(
    tmp_path: Path,
) -> None:
    _plugin, client = _load(tmp_path)
    _project(client)
    _chapter(client)
    updated = client.put(
        f"{API_PREFIX}/projects/history/chapters/chapter-1",
        json={"body": "second draft", "expected_revision": 1},
    )
    assert updated.status_code == 200

    restored = client.post(
        f"{API_PREFIX}/projects/history/chapters/chapter-1/revisions/1/restore",
        headers={"If-Match": '"2"'},
        json={"expected_revision": 999, "message": "return to opening"},
    )
    assert restored.status_code == 200, restored.text
    assert restored.headers["etag"] == '"3"'
    assert restored.json()["revision"] == 3
    assert restored.json()["body"] == "first draft"
    assert restored.json()["canon_status"] == "candidate"

    restore_entry = client.get(
        f"{API_PREFIX}/projects/history/chapters/chapter-1/revisions/3"
    ).json()
    assert restore_entry["operation"] == "restore"
    assert restore_entry["restored_from_revision"] == 1
    assert restore_entry["actor"] == "local"
    assert restore_entry["actor_source"] == "client_asserted"
    assert restore_entry["message"] == "return to opening"
    assert (
        client.get(f"{API_PREFIX}/projects/history/chapters/chapter-1/revisions/2").json()[
            "snapshot"
        ]["body"]
        == "second draft"
    )

    stale = client.post(
        f"{API_PREFIX}/projects/history/chapters/chapter-1/revisions/2/restore",
        json={"expected_revision": 2},
    )
    assert stale.status_code == 409
    strict = client.post(
        f"{API_PREFIX}/projects/history/chapters/chapter-1/revisions/1/restore",
        json={"unexpected": True},
    )
    assert strict.status_code == 422


def test_scene_history_is_parent_and_project_isolated(tmp_path: Path) -> None:
    _plugin, client = _load(tmp_path)
    _project(client)
    _chapter(client, chapter_id="chapter-1", ordinal=1)
    _chapter(client, chapter_id="chapter-2", ordinal=2)
    scene = client.post(
        f"{API_PREFIX}/projects/history/chapters/chapter-1/scenes",
        json={
            "id": "scene-1",
            "branch_id": "main",
            "ordinal": 1,
            "title": "Opening",
            "body": "scene one",
        },
    )
    assert scene.status_code == 201
    assert scene.headers["etag"] == '"1"'

    second = client.put(
        f"{API_PREFIX}/projects/history/chapters/chapter-1/scenes/scene-1",
        headers={"If-Match": '"1"'},
        json={"body": "scene two"},
    )
    assert second.status_code == 200
    assert second.json()["revision"] == 2
    detail = client.get(f"{API_PREFIX}/projects/history/chapters/chapter-1/scenes/scene-1")
    assert detail.status_code == 200
    assert detail.headers["etag"] == '"2"'

    wrong_parent = client.get(
        f"{API_PREFIX}/projects/history/chapters/chapter-2/scenes/scene-1/revisions"
    )
    assert wrong_parent.status_code == 404
    _project(client, "other")
    wrong_project = client.get(
        f"{API_PREFIX}/projects/other/chapters/chapter-1/scenes/scene-1/revisions"
    )
    assert wrong_project.status_code == 404

    restored = client.post(
        f"{API_PREFIX}/projects/history/chapters/chapter-1/scenes/scene-1/revisions/1/restore",
        headers={"If-Match": '"2"'},
        json={},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["revision"] == 3
    assert restored.json()["body"] == "scene one"


def test_legacy_revision_two_gets_truthful_baseline_without_fabricated_r1(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "data" / "projects" / "legacy"
    (project_dir / "chapters").mkdir(parents=True)
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "schema_version": "echo.narrative-studio.project.v2",
                "id": "legacy",
                "title": "Legacy",
                "default_branch_id": "main",
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "chapters" / "old-chapter.json").write_text(
        json.dumps(
            {
                "id": "old-chapter",
                "project_id": "legacy",
                "canon_status": "candidate",
                "revision": 2,
                "branch_id": "main",
                "ordinal": 1,
                "title": "Old",
                "body": "only revision two survived",
            }
        ),
        encoding="utf-8",
    )

    store = NarrativeStore(tmp_path / "data")
    assert store.get_chapter("legacy", "old-chapter").revision == 2
    rows = store.list_chapter_revisions("legacy", "old-chapter")
    assert [row.revision for row in rows] == [2]
    assert rows[0].history_origin == "legacy_baseline"
    assert rows[0].reconstructed is True
    assert rows[0].operation == "migrated"
    assert not (project_dir / "revisions" / "chapters" / "old-chapter" / "1.json").exists()
    with pytest.raises(NarrativeStoreError, match="revision not found"):
        store.get_chapter_revision("legacy", "old-chapter", 1)

    updated = store.update_chapter(
        "legacy",
        "old-chapter",
        ChapterUpdate(body="revision three"),
        expected_revision=2,
    )
    assert updated.revision == 3
    assert [row.revision for row in store.list_chapter_revisions("legacy", "old-chapter")] == [
        3,
        2,
    ]


def test_expected_revision_serializes_concurrent_writers(tmp_path: Path) -> None:
    store = NarrativeStore(tmp_path / "data")
    store.create_project(ProjectCreate(id="race", title="Race"))
    store.create_chapter(
        "race",
        ChapterCreate(
            id="chapter-1",
            branch_id="main",
            ordinal=1,
            title="Race",
            body="base",
        ),
    )

    def save(body: str):
        return store.update_chapter(
            "race",
            "chapter-1",
            ChapterUpdate(body=body),
            expected_revision=1,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save, body) for body in ("writer one", "writer two")]
    results = []
    errors = []
    for future in futures:
        try:
            results.append(future.result())
        except NarrativeConflict as exc:
            errors.append(exc)
    assert len(results) == 1
    assert len(errors) == 1
    assert results[0].revision == 2
    assert store.get_chapter("race", "chapter-1").revision == 2
    assert [row.revision for row in store.list_chapter_revisions("race", "chapter-1")] == [
        2,
        1,
    ]


def test_history_limits_and_failed_record_write_leave_no_partial_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    limited = NarrativeStore(
        tmp_path / "limited",
        history_max_revisions_per_record=1,
        history_max_entries_per_project=10,
    )
    limited.create_project(ProjectCreate(id="limited", title="Limited"))
    limited.create_chapter(
        "limited",
        ChapterCreate(id="chapter-1", branch_id="main", ordinal=1, title="One", body="one"),
    )
    with pytest.raises(NarrativeConflict, match="limit"):
        limited.update_chapter("limited", "chapter-1", ChapterUpdate(body="two"))
    assert limited.get_chapter("limited", "chapter-1").revision == 1

    tiny = NarrativeStore(tmp_path / "tiny", history_max_snapshot_bytes=1024)
    tiny.create_project(ProjectCreate(id="tiny", title="Tiny"))
    with pytest.raises(NarrativeStoreError, match="single-entry"):
        tiny.create_chapter(
            "tiny",
            ChapterCreate(
                id="large",
                branch_id="main",
                ordinal=1,
                title="Large",
                body="x" * 5000,
            ),
        )
    assert not (tmp_path / "tiny" / "projects" / "tiny" / "chapters" / "large.json").exists()

    atomic = NarrativeStore(tmp_path / "atomic")
    atomic.create_project(ProjectCreate(id="atomic", title="Atomic"))
    atomic.create_chapter(
        "atomic",
        ChapterCreate(id="chapter-1", branch_id="main", ordinal=1, title="One", body="one"),
    )
    record_path = tmp_path / "atomic" / "projects" / "atomic" / "chapters" / "chapter-1.json"
    revision_two_path = (
        tmp_path
        / "atomic"
        / "projects"
        / "atomic"
        / "revisions"
        / "chapters"
        / "chapter-1"
        / "2.json"
    )
    from runtime.platform.plugins.bundled.narrative_studio import store as store_module

    real_atomic_write = store_module._atomic_write_json

    def fail_current_record(path: Path, payload: dict) -> None:
        if path == record_path and payload.get("revision") == 2:
            raise OSError("simulated record write failure")
        real_atomic_write(path, payload)

    monkeypatch.setattr(store_module, "_atomic_write_json", fail_current_record)
    with pytest.raises(OSError, match="simulated"):
        atomic.update_chapter("atomic", "chapter-1", ChapterUpdate(body="two"))
    assert json.loads(record_path.read_text(encoding="utf-8"))["revision"] == 1
    assert not revision_two_path.exists()

