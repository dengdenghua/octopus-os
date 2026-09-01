"""Tests for runtime.memory.skills_lib.ambient_suggestions + router."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory import ambient_suggestions as amb
from runtime.memory.skills_lib.ambient_suggestions import Suggestion
from runtime.platform import feature_flags as ff
from runtime.sensing.gateway.ambient_suggestions_router import (
    create_ambient_suggestions_router,
)


@pytest.fixture(autouse=True)
def _reset_flags(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    original = dict(ff._SPECS)
    yield
    ff._SPECS.clear()
    ff._SPECS.update(original)
    ff._SNAPSHOT = None
    ff._FILE_PATH = None


@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    return tmp_path / "ambient"


@pytest.fixture
def project(tmp_path: Path) -> str:
    """Use the tmp_path itself as the project root so each test
    gets its own bucket without real-repo bleed."""
    return str(tmp_path)


# ─── storage ────────────────────────────────────────────────


def test_read_empty_bucket_returns_stub(base_dir: Path, project: str) -> None:
    bucket = amb.read_bucket(project, base_dir=base_dir)
    assert bucket["suggestions"] == []
    assert bucket["project_root"].endswith(project.lstrip(os.sep))


def test_upsert_adds_new_suggestion(base_dir: Path, project: str) -> None:
    added = amb.upsert_many(
        project,
        [
            Suggestion(
                id="",
                project_root=project,
                title="Fix build",
                description="Tests fail on CI",
                prompt="please fix the broken CI build",
            )
        ],
        base_dir=base_dir,
    )
    assert added == 1
    bucket = amb.read_bucket(project, base_dir=base_dir)
    assert len(bucket["suggestions"]) == 1
    s = bucket["suggestions"][0]
    assert s["title"] == "Fix build"
    assert s["status"] == "pending"
    assert s["id"]  # UUID was assigned
    assert s["created_at"]


def test_upsert_dedupes_by_title(base_dir: Path, project: str) -> None:
    amb.upsert_many(
        project,
        [
            Suggestion(
                id="",
                project_root=project,
                title="Fix build",
                description="v1",
                prompt="prompt v1",
                source_turn_ids=["t1"],
            )
        ],
        base_dir=base_dir,
    )
    added = amb.upsert_many(
        project,
        [
            Suggestion(
                id="",
                project_root=project,
                title="  fix build  ",  # case + whitespace variant
                description="v2",
                prompt="prompt v2",
                source_turn_ids=["t2"],
            )
        ],
        base_dir=base_dir,
    )
    assert added == 0  # merged, not added
    bucket = amb.read_bucket(project, base_dir=base_dir)
    assert len(bucket["suggestions"]) == 1
    merged = bucket["suggestions"][0]
    assert merged["description"] == "v2"  # refreshed
    assert merged["prompt"] == "prompt v2"
    assert merged["source_turn_ids"] == ["t1", "t2"]


def test_upsert_keeps_same_title_separate_across_locales(
    base_dir: Path,
    project: str,
) -> None:
    first = Suggestion(
        id="",
        project_root=project,
        title="Review API",
        description="English",
        prompt="Review it",
        locale="en-US",
    )
    second = Suggestion(
        id="",
        project_root=project,
        title="Review API",
        description="中文",
        prompt="检查 API",
        locale="zh-CN",
    )
    assert amb.upsert_many(project, [first], base_dir=base_dir) == 1
    assert amb.upsert_many(project, [second], base_dir=base_dir) == 1
    assert len(amb.read_bucket(project, base_dir=base_dir)["suggestions"]) == 2
    zh_bucket = amb.read_bucket(project, base_dir=base_dir, locale="zh-CN")
    assert [s["description"] for s in zh_bucket["suggestions"]] == ["中文"]


def test_legacy_suggestion_defaults_to_english() -> None:
    suggestion = Suggestion.from_dict(
        {
            "id": "legacy",
            "project_root": "/p",
            "title": "Old English title",
            "prompt": "Continue",
        }
    )
    assert suggestion is not None
    assert suggestion.locale == "en-US"


def test_mark_status_updates_timestamp(
    base_dir: Path,
    project: str,
) -> None:
    amb.upsert_many(
        project,
        [
            Suggestion(
                id="",
                project_root=project,
                title="Do thing",
                description="",
                prompt="x",
            )
        ],
        base_dir=base_dir,
    )
    bucket = amb.read_bucket(project, base_dir=base_dir)
    sid = bucket["suggestions"][0]["id"]
    original_updated = bucket["suggestions"][0]["updated_at"]

    updated = amb.mark_status(project, sid, "dismissed", base_dir=base_dir)
    assert updated is not None
    assert updated["status"] == "dismissed"
    assert updated["updated_at"] != original_updated


def test_mark_status_rejects_invalid(base_dir: Path, project: str) -> None:
    with pytest.raises(ValueError):
        amb.mark_status(project, "any-id", "bogus", base_dir=base_dir)


def test_mark_status_returns_none_for_unknown_id(
    base_dir: Path,
    project: str,
) -> None:
    result = amb.mark_status(project, "nope", "dismissed", base_dir=base_dir)
    assert result is None


def test_clear_all_wipes(base_dir: Path, project: str) -> None:
    amb.upsert_many(
        project,
        [
            Suggestion(id="", project_root=project, title=f"t{i}", description="", prompt="p")
            for i in range(3)
        ],
        base_dir=base_dir,
    )
    removed = amb.clear(project, base_dir=base_dir)
    assert removed == 3
    assert amb.read_bucket(project, base_dir=base_dir)["suggestions"] == []


def test_clear_filtered_by_status(
    base_dir: Path,
    project: str,
) -> None:
    amb.upsert_many(
        project,
        [
            Suggestion(id="", project_root=project, title=f"t{i}", description="", prompt="p")
            for i in range(3)
        ],
        base_dir=base_dir,
    )
    bucket = amb.read_bucket(project, base_dir=base_dir)
    sid = bucket["suggestions"][0]["id"]
    amb.mark_status(project, sid, "dismissed", base_dir=base_dir)

    removed = amb.clear(project, base_dir=base_dir, only_status="dismissed")
    assert removed == 1
    remaining = amb.read_bucket(project, base_dir=base_dir)["suggestions"]
    assert len(remaining) == 2
    assert all(s["status"] == "pending" for s in remaining)


def test_projects_are_isolated(base_dir: Path, tmp_path: Path) -> None:
    p1 = str(tmp_path / "proj1")
    p2 = str(tmp_path / "proj2")
    (tmp_path / "proj1").mkdir()
    (tmp_path / "proj2").mkdir()
    amb.upsert_many(
        p1,
        [Suggestion(id="", project_root=p1, title="A", description="", prompt="x")],
        base_dir=base_dir,
    )
    amb.upsert_many(
        p2,
        [Suggestion(id="", project_root=p2, title="B", description="", prompt="y")],
        base_dir=base_dir,
    )
    assert len(amb.read_bucket(p1, base_dir=base_dir)["suggestions"]) == 1
    assert len(amb.read_bucket(p2, base_dir=base_dir)["suggestions"]) == 1
    assert {s["title"] for s in amb.read_bucket(p1, base_dir=base_dir)["suggestions"]} == {"A"}


# ─── generator (LLM mocked) ─────────────────────────────────


@pytest.fixture
def scored_turns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Seed turn_scoring with fake data for agent ``test-agent``.

    ``turn_scoring`` resolves its file path through a module-local
    ``_project_root`` helper (module path, not CWD), so we
    monkeypatch that function to point at ``tmp_path``.
    """
    monkeypatch.chdir(tmp_path)
    agents = tmp_path / "agents" / "test-agent" / "agent-core"
    agents.mkdir(parents=True)
    scores_path = agents / ".scores.jsonl"
    scores_path.write_text(
        "\n".join(
            [
                '{"ts": "2026-05-01T00:00:00Z", "agent_id": "test-agent", "score": 0.0, "reason": "tool_errors", "soul_hash": "a", "thread_id": "thread-1", "turn_id": "turn-1"}',
                '{"ts": "2026-05-02T00:00:00Z", "agent_id": "test-agent", "score": 0.5, "reason": "interrupted", "soul_hash": "a", "thread_id": "thread-2", "turn_id": "turn-2"}',
                '{"ts": "2026-05-03T00:00:00Z", "agent_id": "test-agent", "score": 1.0, "reason": "success", "soul_hash": "a", "thread_id": "thread-3", "turn_id": "turn-3"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    from runtime.memory import turn_scoring as _ts

    monkeypatch.setattr(_ts, "_project_root", lambda: tmp_path)
    return "test-agent"


def test_generate_with_no_scores_returns_error(
    base_dir: Path,
    project: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    result = amb.generate_suggestions(
        project,
        "new-agent-nothing-logged",
        base_dir=base_dir,
    )
    assert result["generated"] == 0
    assert result["added"] == 0
    assert "no scored turns" in (result["error"] or "")


def test_generate_merges_llm_output(
    base_dir: Path,
    project: str,
    scored_turns: str,
) -> None:
    fake_llm = (
        {
            "suggestions": [
                {
                    "title": "Fix tool_errors in CI path",
                    "description": "Several turns failed mid-tool-call.",
                    "prompt": "Investigate why the CI shell tool fails.",
                    "source_turn_ids": ["turn-1"],
                },
                {
                    "title": "Document interrupt recovery",
                    "description": "User hit cancel twice.",
                    "prompt": "Write docs on resuming interrupted plans.",
                    "source_turn_ids": ["turn-2"],
                },
            ]
        },
        {"model": "mock/cheap", "input_tokens": 100, "output_tokens": 50},
    )
    with patch(
        "runtime.memory.learning.deep_evolution._llm_call_json",
        return_value=fake_llm,
    ):
        result = amb.generate_suggestions(
            project,
            scored_turns,
            base_dir=base_dir,
        )
    assert result["error"] is None
    assert result["generated"] == 2
    assert result["added"] == 2
    assert result["model"] == "mock/cheap"
    titles = {s["title"] for s in result["suggestions"]}
    assert "Fix tool_errors in CI path" in titles


def test_generate_uses_requested_locale_in_prompt_and_bucket(
    base_dir: Path,
    project: str,
    scored_turns: str,
) -> None:
    fake_llm = (
        {
            "suggestions": [
                {
                    "title": "检查失败的构建",
                    "description": "最近的工具调用失败了。",
                    "prompt": "请检查并修复构建失败。",
                    "source_turn_ids": ["turn-1"],
                }
            ]
        },
        {"model": "mock/cheap"},
    )
    with patch(
        "runtime.memory.learning.deep_evolution._llm_call_json",
        return_value=fake_llm,
    ) as llm:
        result = amb.generate_suggestions(
            project,
            scored_turns,
            base_dir=base_dir,
            locale="zh-CN",
        )

    call = llm.call_args.kwargs
    assert "Simplified Chinese" in call["system"]
    assert "Response locale: zh-CN" in call["user"]
    assert result["suggestions"][0]["locale"] == "zh-CN"
    assert result["suggestions"][0]["title"] == "检查失败的构建"


def test_generate_handles_llm_failure(
    base_dir: Path,
    project: str,
    scored_turns: str,
) -> None:
    with patch(
        "runtime.memory.learning.deep_evolution._llm_call_json",
        return_value=(None, {"error": "router not wired"}),
    ):
        result = amb.generate_suggestions(
            project,
            scored_turns,
            base_dir=base_dir,
        )
    assert result["error"] == "router not wired"
    assert result["generated"] == 0


def test_generate_skips_candidates_missing_title_or_prompt(
    base_dir: Path,
    project: str,
    scored_turns: str,
) -> None:
    fake_llm = (
        {
            "suggestions": [
                {"title": "", "prompt": "x"},  # no title
                {"title": "Good one", "prompt": ""},  # no prompt
                {"title": "Keep me", "prompt": "do it"},
            ]
        },
        {"model": "mock/cheap"},
    )
    with patch(
        "runtime.memory.learning.deep_evolution._llm_call_json",
        return_value=fake_llm,
    ):
        result = amb.generate_suggestions(
            project,
            scored_turns,
            base_dir=base_dir,
        )
    assert result["generated"] == 1
    assert result["added"] == 1


# ─── router endpoints ──────────────────────────────────────


@pytest.fixture
def client(base_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(create_ambient_suggestions_router(base_dir=base_dir))
    return TestClient(app)


def test_get_returns_disabled_shape_when_flag_off(
    client: TestClient,
    project: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Default is now True, so "off" must be explicit.
    monkeypatch.setenv("ECHO_FF_UI_AMBIENT_SUGGESTIONS", "0")
    ff.reload()
    r = client.get("/api/ambient-suggestions", params={"project": project})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert r.json()["suggestions"] == []


def test_get_returns_real_bucket_when_flag_on(
    client: TestClient,
    base_dir: Path,
    project: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_AMBIENT_SUGGESTIONS", "1")
    ff.reload()
    amb.upsert_many(
        project,
        [Suggestion(id="", project_root=project, title="hi", description="", prompt="p")],
        base_dir=base_dir,
    )
    r = client.get("/api/ambient-suggestions", params={"project": project})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert len(body["suggestions"]) == 1
    assert body["suggestions"][0]["title"] == "hi"


def test_get_filters_suggestions_by_locale(
    client: TestClient,
    base_dir: Path,
    project: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_AMBIENT_SUGGESTIONS", "1")
    ff.reload()
    amb.upsert_many(
        project,
        [
            Suggestion(
                id="",
                project_root=project,
                title="English",
                description="",
                prompt="Continue",
                locale="en-US",
            ),
            Suggestion(
                id="",
                project_root=project,
                title="中文",
                description="",
                prompt="继续",
                locale="zh-CN",
            ),
        ],
        base_dir=base_dir,
    )

    r = client.get(
        "/api/ambient-suggestions",
        params={"project": project, "locale": "zh-CN"},
    )
    assert r.status_code == 200
    assert [s["title"] for s in r.json()["suggestions"]] == ["中文"]


def test_run_endpoint_is_403_when_flag_off(
    client: TestClient,
    project: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Default is now True, so "off" must be explicit.
    monkeypatch.setenv("ECHO_FF_UI_AMBIENT_SUGGESTIONS", "0")
    ff.reload()
    r = client.post(
        "/api/ambient-suggestions/run",
        json={"project": project, "agent_id": "a"},
    )
    assert r.status_code == 403
    assert "disabled" in r.json()["detail"]["error"]


def test_patch_404_when_suggestion_missing(
    client: TestClient,
    project: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_AMBIENT_SUGGESTIONS", "1")
    ff.reload()
    r = client.patch(
        "/api/ambient-suggestions/does-not-exist",
        json={"project": project, "status": "dismissed"},
    )
    assert r.status_code == 404


def test_patch_updates_status(
    client: TestClient,
    base_dir: Path,
    project: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_AMBIENT_SUGGESTIONS", "1")
    ff.reload()
    amb.upsert_many(
        project,
        [Suggestion(id="", project_root=project, title="x", description="", prompt="p")],
        base_dir=base_dir,
    )
    bucket = amb.read_bucket(project, base_dir=base_dir)
    sid = bucket["suggestions"][0]["id"]

    r = client.patch(
        f"/api/ambient-suggestions/{sid}",
        json={"project": project, "status": "accepted"},
    )
    assert r.status_code == 200
    assert r.json()["suggestion"]["status"] == "accepted"


def test_patch_rejects_bad_status(
    client: TestClient,
    project: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_AMBIENT_SUGGESTIONS", "1")
    ff.reload()
    r = client.patch(
        "/api/ambient-suggestions/some-id",
        json={"project": project, "status": "exploded"},
    )
    assert r.status_code == 400


def test_delete_filtered(
    client: TestClient,
    base_dir: Path,
    project: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_AMBIENT_SUGGESTIONS", "1")
    ff.reload()
    amb.upsert_many(
        project,
        [
            Suggestion(id="", project_root=project, title=f"t{i}", description="", prompt="p")
            for i in range(3)
        ],
        base_dir=base_dir,
    )
    bucket = amb.read_bucket(project, base_dir=base_dir)
    sid = bucket["suggestions"][0]["id"]
    amb.mark_status(project, sid, "dismissed", base_dir=base_dir)

    r = client.delete(
        "/api/ambient-suggestions",
        params={"project": project, "status": "dismissed"},
    )
    assert r.status_code == 200
    assert r.json()["removed"] == 1
    remaining = amb.read_bucket(project, base_dir=base_dir)["suggestions"]
    assert len(remaining) == 2


