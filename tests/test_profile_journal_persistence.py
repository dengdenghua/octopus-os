"""Profile-selected journals must survive restart in the existing state root."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from runtime.memory.journal import InMemoryJournal, JSONLJournal
from runtime.memory.journal.journal_context import journal_context
from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config, load_from_yaml
from runtime.platform.models import TaskId
from runtime.platform.process.paths import app_paths
from runtime.platform.ui.state import AppState
from runtime.safety.auth.scope import TenantScope

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ECHO_DATA_DIR", raising=False)
    monkeypatch.delenv("ECHO_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    # These tests exercise persistence and real AppState binding, with no
    # plugin registration, external MCP startup, or model requests.
    monkeypatch.setattr("runtime.execution.all_skills.register_all", lambda registry: None)
    monkeypatch.setattr("runtime.execution.all_skills.register_local", lambda registry: None)


def _config(template: Path = ROOT / "config.example.yaml") -> AgentConfig:
    return load_from_yaml(template).model_copy(
        update={"planner": PlannerConfig(type="static"), "enable_web_skills": False}
    )


def test_source_profile_uses_one_project_journal_from_nested_workdirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "source"
    (repo / "runtime").mkdir(parents=True)
    (repo / "frontend").mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='echo-os'\n", encoding="utf-8")
    config = _config()

    monkeypatch.chdir(repo)
    first = build_from_config(config)
    monkeypatch.chdir(repo / "frontend")
    second = build_from_config(config)

    assert isinstance(first.journal, JSONLJournal)
    assert first.journal._path == second.journal._path == repo / "data" / "events.jsonl"
    assert first.journal._path.is_absolute()


@pytest.mark.parametrize("profile", ["desktop", "native", "appliance"])
def test_installed_profiles_resolve_journal_beside_runtime_state(
    profile: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = tmp_path / profile / "private-state"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data))
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "unused-home"))
    template = ROOT / "config.example.yaml"
    if profile == "desktop":
        # Electron replaces the auth placeholder before Python reads this
        # template. Materialize a test-only value without touching a profile.
        template = tmp_path / "desktop-config.yaml"
        template.write_text(
            (ROOT / "packaging/desktop/config.desktop.yaml")
            .read_text(encoding="utf-8")
            .replace("__ECHO_DESKTOP_ACCOUNT_JWT_SECRET__", f"Fixture-{uuid4().hex}"),
            encoding="utf-8",
        )
    elif profile == "native":
        # Mirror the native bundle's actual extends layout.
        template = tmp_path / "bundle" / "native-config.yaml"
        resources = template.parent / "resources"
        resources.mkdir(parents=True)
        (resources / "config.example.yaml").write_bytes((ROOT / "config.example.yaml").read_bytes())
        template.write_bytes((ROOT / "deploy/agent/echo-agent-native.yaml").read_bytes())

    stack = build_from_config(_config(template))

    assert isinstance(stack.journal, JSONLJournal)
    assert stack.journal._path == data / "events.jsonl"
    assert stack.journal._path.parent == app_paths().agent_trace_path.parent
    assert stack.journal._path.parent == app_paths().task_runs_path.parent


def test_auto_journal_follows_home_when_data_override_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime_home = tmp_path / "user-profile"
    monkeypatch.setenv("ECHO_HOME", str(runtime_home))

    stack = build_from_config(_config())

    assert stack.journal._path == runtime_home / "data" / "events.jsonl"


@pytest.mark.parametrize("config", [AgentConfig(), AgentConfig(journal_file=None)])
def test_library_default_and_explicit_null_remain_in_memory(
    config: AgentConfig, tmp_path: Path
) -> None:
    stack = build_from_config(config)
    state = AppState(
        journal=stack.journal,
        registry=stack.registry,
        trace_store_path=tmp_path / "agent_trace.sqlite",
    )

    assert isinstance(stack.journal, InMemoryJournal)
    assert state.trace_store is None
    assert not (tmp_path / "data/events.jsonl").exists()


def test_explicit_journal_path_is_preserved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "custom-audit" / "operator-selected.jsonl"
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "other-data"))
    stack = build_from_config(AgentConfig(journal_file=str(path)))

    assert isinstance(stack.journal, JSONLJournal)
    assert stack.journal._path == path


def test_profile_restart_restores_scoped_checkpoints_and_attaches_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = tmp_path / "private-profile" / "data"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data))
    config = _config()
    first_stack = build_from_config(config)
    first = AppState(
        journal=first_stack.journal,
        registry=first_stack.registry,
        trace_store_path=app_paths().agent_trace_path,
    )
    assert first.trace_store is not None
    task_id = TaskId(uuid4())
    alice = TenantScope(tenant_id="household", actor_id="local:alice")
    bob = TenantScope(tenant_id="household", actor_id="local:bob")
    with journal_context(
        tenant_id=alice.tenant_id,
        owner_actor_id=alice.actor_id,
        conversation_id="alice-thread",
        agent_id="assistant",
    ):
        first.journal.write_react_checkpoint(
            task_id,
            iteration_completed=10,
            max_iterations=30,
            messages_snapshot=[{"role": "user", "content": "Continue the document task"}],
            steps_snapshot=[],
            current_phase="verify",
        )
    first.trace_store.close()

    # Rebuild from disk after changing the cwd, just as a new launcher can.
    next_cwd = tmp_path / "different-working-directory"
    next_cwd.mkdir()
    monkeypatch.chdir(next_cwd)
    restarted_stack = build_from_config(config)
    restarted = AppState(
        journal=restarted_stack.journal,
        registry=restarted_stack.registry,
        trace_store_path=app_paths().agent_trace_path,
    )
    try:
        assert restarted.journal_path == data / "events.jsonl"
        checkpoints = [
            event
            for event in restarted.journal.read_all(scope=alice)
            if event.event_type == "react_checkpoint"
        ]
        assert len(checkpoints) == 1
        assert checkpoints[0].conversation_id == "alice-thread"
        assert checkpoints[0].iteration_completed == 10
        assert restarted.journal.read_all(scope=bob) == []
        assert restarted.trace_store is not None
        trace = restarted.trace_store.latest_checkpoint(task_id=str(task_id), scope=alice)
        assert trace is not None
        assert trace["thread_id"] == "alice-thread"
        assert trace["state"]["iteration_completed"] == 10
        assert restarted.trace_store.latest_checkpoint(task_id=str(task_id), scope=bob) is None
    finally:
        if restarted.trace_store is not None:
            restarted.trace_store.close()
