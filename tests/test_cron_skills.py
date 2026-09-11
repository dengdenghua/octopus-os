"""Tests for the agent-self-schedule cron skills.

The skills must:
- reject empty / both / neither / bad-cron / past-fire-at args with
  ``error_type="invalid_argument"``
- write into the same on-disk format the settings UI reads (
  ``runtime.sensing.gateway.cron_router._read_cron_jobs``) so a UI
  list call sees model-scheduled tasks
- mark agent-created records with ``creator_actor="agent_self"``
- round-trip through ``list_scheduled_tasks`` / ``cancel_scheduled_task``
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.execution.suckers import cron_skills
from runtime.execution.suckers.cron_skills import (
    _cancel_scheduled_task,
    _list_scheduled_tasks,
    _schedule_task,
    register_cron_skills,
)
from runtime.execution.suckers.registry import SkillRegistry
from runtime.sensing.gateway import cron_router


@pytest.fixture
def cron_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``app_paths().cron_jobs_path`` into tmp_path for isolation."""
    target = tmp_path / "cron_jobs.json"
    fake = SimpleNamespace(cron_jobs_path=target)
    monkeypatch.setattr(cron_skills, "app_paths", lambda: fake)
    return target


def test_missing_prompt_returns_invalid_argument(cron_path: Path) -> None:
    result = _schedule_task(prompt="", cron_expression="0 * * * *")
    assert result["ok"] is False
    assert result["error_type"] == "invalid_argument"
    assert not cron_path.exists()


def test_both_cron_and_fire_at_returns_invalid_argument(cron_path: Path) -> None:
    future = (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat()
    result = _schedule_task(
        prompt="check deploy",
        cron_expression="0 * * * *",
        fire_at=future,
    )
    assert result["ok"] is False
    assert result["error_type"] == "invalid_argument"


def test_neither_cron_nor_fire_at_returns_invalid_argument(cron_path: Path) -> None:
    result = _schedule_task(prompt="check deploy")
    assert result["ok"] is False
    assert result["error_type"] == "invalid_argument"


def test_bad_cron_syntax_returns_invalid_argument(cron_path: Path) -> None:
    result = _schedule_task(prompt="x", cron_expression="not a cron")
    assert result["ok"] is False
    assert result["error_type"] == "invalid_argument"

    # 5 fields but out-of-range — still rejected by the parser.
    result2 = _schedule_task(prompt="x", cron_expression="99 * * * *")
    assert result2["ok"] is False
    assert result2["error_type"] == "invalid_argument"


def test_fire_at_in_past_returns_invalid_argument(cron_path: Path) -> None:
    past = (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat()
    result = _schedule_task(prompt="x", fire_at=past)
    assert result["ok"] is False
    assert result["error_type"] == "invalid_argument"


def test_fire_at_without_timezone_returns_invalid_argument(cron_path: Path) -> None:
    naive = "2099-01-01T00:00:00"  # No offset → must reject.
    result = _schedule_task(prompt="x", fire_at=naive)
    assert result["ok"] is False
    assert result["error_type"] == "invalid_argument"


def test_happy_path_cron_persists_and_lists(cron_path: Path) -> None:
    result = _schedule_task(
        prompt="check the deploy",
        cron_expression="0 9 * * *",
        name="check_deploy",
    )
    assert result["ok"] is True
    assert result["task_id"] == "check_deploy"
    assert result["recurring"] is True
    assert result["next_run_at"] is not None

    # Persisted in the same shape the settings UI reads.
    jobs = cron_router._read_cron_jobs(cron_path)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["name"] == "check_deploy"
    assert job["command"] == "check the deploy"
    assert job["cron_expression"] == "0 9 * * *"
    assert job["creator_actor"] == "agent_self"

    listing = _list_scheduled_tasks()
    assert listing["ok"] is True
    assert listing["count"] == 1
    task = listing["tasks"][0]
    assert task["task_id"] == "check_deploy"
    assert task["creator_actor"] == "agent_self"
    assert task["prompt"] == "check the deploy"
    assert task["recurring"] is True


def test_happy_path_fire_at_marks_recurring_false(cron_path: Path) -> None:
    future = datetime.now(tz=UTC) + timedelta(days=1)
    result = _schedule_task(
        prompt="ping after deploy",
        fire_at=future.isoformat(),
    )
    assert result["ok"] is True
    assert result["recurring"] is False
    assert result["task_id"].startswith("auto_")

    listing = _list_scheduled_tasks()
    task = listing["tasks"][0]
    assert task["recurring"] is False
    assert task["fire_at"] is not None


def test_cancel_removes_the_task(cron_path: Path) -> None:
    _schedule_task(
        prompt="recurring ping",
        cron_expression="*/5 * * * *",
        name="ping",
    )
    assert _list_scheduled_tasks()["count"] == 1

    cancelled = _cancel_scheduled_task(task_id="ping")
    assert cancelled["ok"] is True
    assert cancelled["deleted"] is True
    assert _list_scheduled_tasks()["count"] == 0


def test_cancel_missing_task_returns_not_found(cron_path: Path) -> None:
    result = _cancel_scheduled_task(task_id="nope")
    assert result["ok"] is False
    assert result["error_type"] == "not_found"


def test_cancel_empty_id_returns_invalid_argument(cron_path: Path) -> None:
    result = _cancel_scheduled_task(task_id="")
    assert result["ok"] is False
    assert result["error_type"] == "invalid_argument"


def test_register_cron_skills_returns_three(cron_path: Path) -> None:
    registry = SkillRegistry()
    count = register_cron_skills(registry)
    assert count == 3
    # Sanity: every registered skill is reachable by name.
    for name in ("schedule_task", "list_scheduled_tasks", "cancel_scheduled_task"):
        assert registry.get(name).name == name
