"""Real executor checks for server-owned workspace vs service path namespaces."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import FileOpEvent, InMemoryJournal
from runtime.platform.models import ArmId, Budget, BudgetLimits, SkillId, TaskId
from runtime.platform.process.session import Session, session_scope
from runtime.safety.auth import TrustEngine


def _stack(handler, *, resolution="workspace", name="synthetic_service_read", affinity=None):
    skill = Skill(
        name=name, trusted_source="builtin://synthetic-scope", handler=handler,
        affinity=affinity or ["file", "read"], path_resolution=resolution,
    )
    registry = SkillRegistry()
    registry.register(skill, verify_tests=False)
    journal = InMemoryJournal()
    executor = ToolExecutor(registry, TrustEngine(unknown_policy="allow"), journal=journal)
    return executor, journal, skill


def _execute(executor, skill, session, args, *, task_id=None, step_id=1):
    task_id = task_id or TaskId(uuid4())
    with session_scope(session):
        return executor.execute_step(
            step_id=step_id, node_id="scope-fixture", sucker_id=SkillId(skill.name),
            args=args, caller="react_loop", task_id=task_id, arm_id=ArmId("scope-arm"),
            budget=Budget(task_id=task_id, limits=BudgetLimits(tokens=10000, usd=1.0)),
        )


@pytest.fixture
def session(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return Session(metadata={"mode": "code", "workspace_path": str(workspace)})


@pytest.mark.parametrize("resolution", ["workspace", "service"])
def test_relative_path_and_session_injection_follow_registration(session, resolution):
    seen = []

    def read(path: str, session=None):
        seen.append((path, session))
        return {"path": path}

    executor, _, skill = _stack(read, resolution=resolution)
    step = _execute(executor, skill, session, {"path": "Invoices"})
    assert step.success
    expected = "Invoices" if resolution == "service" else str(Path(session.metadata["workspace_path"]) / "Invoices")
    assert seen == [(expected, session)]


@pytest.mark.parametrize("args", [{}, {"root": ".", "path": "."}, {"root": "", "path": ""}])
def test_service_defaults_and_logical_roots_are_not_host_injected(session, args):
    seen = []

    def read(root=".", path=".", sandbox_dir=None):
        seen.append({"root": root, "path": path, "sandbox_dir": sandbox_dir})
        return {"ok": True}

    executor, _, skill = _stack(read, resolution="service")
    assert _execute(executor, skill, session, args).success
    assert seen == [{"root": args.get("root", "."), "path": args.get("path", "."), "sandbox_dir": None}]


def test_default_workspace_root_and_sandbox_injection_are_preserved(session):
    seen = []

    def read(root=".", path=".", sandbox_dir=None):
        seen.append((root, path, sandbox_dir))
        return {"ok": True}

    executor, _, skill = _stack(read)
    assert _execute(executor, skill, session, {}).success
    workspace = session.metadata["workspace_path"]
    assert seen == [(workspace, workspace, workspace)]


def test_workspace_image_paths_are_confined_to_the_server_owned_read_scope(session, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    seen = []

    def inspect(directory=".", image_path="", image_paths=None):
        seen.append((directory, image_path, image_paths))
        return {"ok": True}

    executor, _, skill = _stack(inspect, name="image_scope_fixture")
    step = _execute(
        executor,
        skill,
        session,
        {
            "directory": str(outside),
            "image_path": str(outside / "secret.jpg"),
            "image_paths": [str(outside / "other.jpg")],
        },
    )

    assert not step.success
    assert step.result.error_type == "PermissionError"
    assert seen == []


def test_workspace_image_paths_are_resolved_relative_to_the_active_workspace(session):
    seen = []

    def inspect(directory=".", image_path="", image_paths=None):
        seen.append((directory, image_path, image_paths))
        return {"ok": True}

    executor, _, skill = _stack(inspect, name="image_scope_fixture_relative")
    assert _execute(
        executor,
        skill,
        session,
        {"image_path": "photo.jpg", "image_paths": ["a.jpg", "nested/b.jpg"]},
    ).success
    workspace = Path(session.metadata["workspace_path"])
    assert seen == [
        (
            str(workspace),
            str(workspace / "photo.jpg"),
            [str(workspace / "a.jpg"), str(workspace / "nested/b.jpg")],
        )
    ]


@pytest.mark.parametrize("resolution,forged", [("workspace", "service"), ("service", "workspace")])
def test_model_argument_cannot_select_path_namespace(session, resolution, forged):
    seen = []

    def read(path: str, **kwargs):
        seen.append(path)
        return {"ok": True}

    executor, _, skill = _stack(read, resolution=resolution)
    assert _execute(executor, skill, session, {"path": "Invoices", "path_resolution": forged}).success
    expected = "Invoices" if resolution == "service" else str(Path(session.metadata["workspace_path"]) / "Invoices")
    assert seen == [expected]
    assert executor.registry.get(skill.name).path_resolution == resolution


def test_skill_metadata_is_frozen_validated_and_survives_copy_registration():
    skill = Skill(name="service_fixture", trusted_source="builtin://fixture", handler=lambda: None, path_resolution="service")
    with pytest.raises(ValidationError):
        skill.path_resolution = "workspace"
    with pytest.raises(ValidationError):
        Skill(name="bad", trusted_source="builtin://fixture", handler=lambda: None, path_resolution="model")
    clone = skill.model_copy(update={"description": "updated server description"}, deep=True)
    restored = Skill(**clone.model_dump())
    registry = SkillRegistry()
    registry.register(restored, verify_tests=False)
    assert registry.get(skill.name).path_resolution == "service"
    assert restored.handler is skill.handler
    default = Skill(name="default", trusted_source="builtin://fixture", handler=lambda: None)
    assert default.path_resolution == "workspace"


def test_service_write_has_no_host_capture_file_lease_or_rollback_but_keeps_effect_receipt(session, tmp_path, monkeypatch):
    import runtime.execution.tool_engine.executor as module

    monkeypatch.chdir(tmp_path)
    (tmp_path / "Invoices").write_text("synthetic unrelated host object", encoding="utf-8")
    seen = []

    def write(path: str):
        seen.append(path)
        return {"path": path, "content": "service result", "ok": True}

    def unexpected_host_access(*args, **kwargs):
        pytest.fail("service path must not trigger host file observation or leasing")

    monkeypatch.setattr(module, "_try_read_pre_content", unexpected_host_access)
    monkeypatch.setattr(module, "acquire_file_write_lease", unexpected_host_access)
    executor, journal, skill = _stack(write, resolution="service", affinity=["file", "write"])
    task_id = TaskId(uuid4())
    first = _execute(executor, skill, session, {"path": "Invoices"}, task_id=task_id)
    repeated = _execute(executor, skill, session, {"path": "Invoices"}, task_id=task_id)
    assert first.success and repeated.success
    assert seen == ["Invoices"]  # Side-effect receipt still prevents duplicate execution.
    events = journal.read_all()
    assert not any(isinstance(event, FileOpEvent) for event in events)
    assert any(event.event_type == "step" for event in events)
    assert any(event.event_type == "tool_effect_intent" for event in events)
    assert first.result.effect_receipt["state"] == "committed"
    assert first.result.effect_receipt["sealed"] is True
    assert repeated.result.effect_receipt["state"] == "replayed"
    assert "diff_preview" not in first.result.output
    assert (tmp_path / "Invoices").read_text(encoding="utf-8") == "synthetic unrelated host object"


def test_service_read_does_not_grant_read_before_write_for_host_file(session):
    executor, _, skill = _stack(lambda path: {"content": "service text"}, resolution="service", name="read_file")
    assert _execute(executor, skill, session, {"path": "Invoices"}).success
    assert "_read_file_paths_this_turn" not in session.metadata


def test_service_write_cannot_borrow_workspace_file_allowlist(session):
    session.metadata["allowed_write_paths"] = ["Invoices"]
    seen = []
    executor, _, skill = _stack(lambda path: seen.append(path), resolution="service", affinity=["file", "write"])
    step = _execute(executor, skill, session, {"path": "Invoices"})
    assert not step.success
    assert step.result.error_type == "PermissionError"
    assert "workspace write allowlist cannot" in str(step.result.output)
    assert seen == []


def test_service_write_is_still_blocked_in_plan_mode(session):
    session.metadata["mode"] = "plan"
    seen = []
    executor, _, skill = _stack(lambda path: seen.append(path), resolution="service", affinity=["file", "write"])
    step = _execute(executor, skill, session, {"path": "Invoices"})
    assert not step.success
    assert seen == []


def test_service_scope_does_not_skip_handler_authorization_or_absolute_path_rejection(session):
    def read(path: str):
        if Path(path).is_absolute() or path != "Invoices":
            raise PermissionError("service path is not authorized")
        return {"ok": True}

    executor, _, skill = _stack(read, resolution="service")
    step = _execute(executor, skill, session, {"path": session.metadata["workspace_path"]})
    assert not step.success
    assert step.result.error_type == "PermissionError"


def test_service_scope_preserves_registry_tenant_visibility(session):
    executor, _, skill = _stack(lambda path: {"ok": True}, resolution="service")
    executor.registry.register(skill.model_copy(update={"tenant_id": "tenant-b"}), replace=True, verify_tests=False)
    session.metadata["tenant_id"] = "tenant-a"
    from runtime.execution.suckers.registry import SkillNotFound

    with pytest.raises(SkillNotFound):
        _execute(executor, skill, session, {"path": "Invoices"})


def test_service_scope_preserves_exact_task_capability_denial(session):
    seen = []
    executor, _, skill = _stack(lambda path: seen.append(path), resolution="service")
    session.metadata["task_capability_manifest"] = {"allowed_skill_ids": []}
    step = _execute(executor, skill, session, {"path": "Invoices"})
    assert not step.success
    assert "task capability skill disabled" in " ".join(step.result.stderr_tags)
    assert seen == []


def test_service_scope_preserves_explicit_sandbox_escape_denial(session, tmp_path):
    seen = []

    def read(path, sandbox_dir=None):
        seen.append(path)
        return {"ok": True}

    executor, _, skill = _stack(read, resolution="service")
    step = _execute(executor, skill, session, {"path": "Invoices", "sandbox_dir": str(tmp_path / "outside")})
    assert not step.success
    assert step.result.error_type == "PermissionError"
    assert seen == []
