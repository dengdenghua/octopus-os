"""Document rollback must restore the actual file and preserve later user edits."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.suckers.builtins import _read_file
from runtime.execution.suckers.write_skills import register_write_skills
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import InMemoryJournal
from runtime.memory.runtime_state.file_transactions import apply_file_rollback_ledger
from runtime.platform.models import ArmId, Budget, BudgetLimits, SkillId, TaskId
from runtime.platform.process.session import Session, session_scope
from runtime.safety.auth import TrustEngine


@pytest.fixture
def documents(tmp_path, monkeypatch):
    workspace, launcher = tmp_path / "documents", tmp_path / "launcher"
    workspace.mkdir()
    launcher.mkdir()
    monkeypatch.chdir(launcher)
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    registry = SkillRegistry()
    register_write_skills(registry)
    registry.register(
        Skill(
            name="read_file",
            description="Read the selected document",
            affinity=["file", "read"],
            trusted_source="builtin://read_file",
            handler=_read_file,
        ),
        verify_tests=False,
    )
    journal = InMemoryJournal()
    executor = ToolExecutor(
        registry=registry, immunity=TrustEngine(unknown_policy="allow"), journal=journal
    )
    session = Session(
        thread_id="document-thread",
        turn_id="document-turn",
        metadata={"workspace_path": str(workspace), "mode": "code"},
    )
    task_id = TaskId(uuid4())
    counter = 0

    def execute(name, **args):
        nonlocal counter
        counter += 1
        with session_scope(session):
            step = executor.execute_step(
                step_id=counter,
                node_id="document-step",
                sucker_id=SkillId(name),
                args=args,
                caller="document-regression",
                task_id=task_id,
                arm_id=ArmId("document-agent"),
                budget=Budget(task_id=task_id, limits=BudgetLimits(tokens=10_000, usd=0.1)),
            )
            assert step.result.status == "success", (step.result.error_type, step.result.output)
            return step

    return workspace, registry, journal, execute


def test_relative_document_edit_undo_restores_existing_file(documents):
    root, _registry, journal, execute = documents
    original = root / "invoice-notes.txt"
    original.write_bytes(b"original invoice notes\n")
    assert execute("read_file", path="invoice-notes.txt").result.status == "success"
    assert (
        execute(
            "write_text_file", path="invoice-notes.txt", content="organized notes\n", overwrite=True
        ).result.status
        == "success"
    )

    result = apply_file_rollback_ledger(journal.read_by_type("file_op"), project_root=root)

    assert result.applied == 1 and result.failed == result.skipped == 0
    assert original.read_bytes() == b"original invoice notes\n"


def test_append_undo_hashes_the_complete_result(documents):
    root, _registry, journal, execute = documents
    original = root / "invoice-notes.txt"
    original.write_bytes(b"original invoice notes\n")
    assert execute("read_file", path=str(original)).result.status == "success"
    assert (
        execute("append_text_file", path=str(original), content="added category\n").result.status
        == "success"
    )

    result = apply_file_rollback_ledger(journal.read_by_type("file_op"), project_root=root)

    assert result.applied == 1 and result.failed == result.skipped == 0
    assert original.read_bytes() == b"original invoice notes\n"


def test_undo_preserves_original_line_endings(documents):
    root, _registry, journal, execute = documents
    original = root / "invoice-notes.txt"
    original.write_bytes(b"first line\r\nsecond line\r\n")
    assert execute("read_file", path=str(original)).result.status == "success"
    assert (
        execute(
            "write_text_file", path=str(original), content="updated\n", overwrite=True
        ).result.status
        == "success"
    )

    result = apply_file_rollback_ledger(journal.read_by_type("file_op"), project_root=root)

    assert result.applied == 1 and result.failed == result.skipped == 0
    assert original.read_bytes() == b"first line\r\nsecond line\r\n"


def test_delete_undo_does_not_overwrite_recreated_document(documents):
    root, registry, journal, execute = documents
    original = root / "invoice-notes.txt"
    original.write_bytes(b"old invoice notes\n")

    def delete_document(path: str):
        Path(path).unlink()
        return {"path": path}

    registry.register(
        Skill(
            name="delete_document_fixture",
            description="Delete a synthetic document",
            affinity=["file", "delete"],
            trusted_source="builtin://document-fixture",
            handler=delete_document,
        ),
        verify_tests=False,
    )
    assert execute("read_file", path=str(original)).result.status == "success"
    assert execute("delete_document_fixture", path=str(original)).result.status == "success"
    original.write_bytes(b"new independently created invoice notes\n")

    result = apply_file_rollback_ledger(journal.read_by_type("file_op"), project_root=root)

    assert result.applied == 0 and result.skipped == 1
    assert original.read_bytes() == b"new independently created invoice notes\n"


def test_preview_simulates_two_edits_to_the_same_document(documents):
    root, _registry, journal, execute = documents
    target = root / "notes.txt"
    target.write_bytes(b"original\n")
    execute("read_file", path=str(target))
    execute("append_text_file", path=str(target), content="category\n")
    execute("read_file", path=str(target))
    execute("append_text_file", path=str(target), content="reviewed\n")
    final = target.read_bytes()
    events = journal.read_by_type("file_op")

    preview = apply_file_rollback_ledger(events, project_root=root, dry_run=True)

    assert (preview.applied, preview.skipped, preview.failed) == (2, 0, 0)
    assert target.read_bytes() == final
    applied = apply_file_rollback_ledger(events, project_root=root)
    assert (applied.applied, applied.skipped, applied.failed) == (2, 0, 0)
    assert target.read_bytes() == b"original\n"


def _rollback_event(path, before, after, **overrides):
    import hashlib

    rollback = {
        "reversible": True,
        "action": "write",
        "path": str(path),
        "content": before,
        "expected_current_sha256": hashlib.sha256(after.encode()).hexdigest(),
        "hash_mode": "bytes-v1",
        "expected_current_exists": True,
        **overrides,
    }
    return SimpleNamespace(
        event_type="file_op", event_id=str(uuid4()), path=str(path), rollback=rollback
    )


@pytest.mark.parametrize("dry_run", [False, True])
def test_conflict_blocks_older_undo_but_allows_independent_document(tmp_path, dry_run):
    target, other = tmp_path / "notes.txt", tmp_path / "other.txt"
    events = [
        _rollback_event(target, "original", "intermediate"),
        _rollback_event(other, "other original", "other edited"),
        _rollback_event(target, "intermediate", "final"),
    ]
    # A subsequent independent edit happens to match an older step's output.
    target.write_bytes(b"intermediate")
    other.write_bytes(b"other edited")

    result = apply_file_rollback_ledger(events, project_root=tmp_path, dry_run=dry_run)

    assert (result.applied, result.skipped, result.failed) == (1, 2, 0)
    assert target.read_bytes() == b"intermediate"
    assert other.read_bytes() == (b"other edited" if dry_run else b"other original")


@pytest.mark.parametrize("dry_run", [False, True])
def test_nonreversible_latest_step_blocks_older_undo(tmp_path, dry_run):
    target = tmp_path / "notes.txt"
    target.write_bytes(b"edited")
    events = [
        _rollback_event(target, "original", "edited"),
        _rollback_event(target, "edited", "edited", reversible=False, reason="no_snapshot"),
    ]
    result = apply_file_rollback_ledger(events, project_root=tmp_path, dry_run=dry_run)
    assert result.applied == 0 and result.skipped == 2
    assert target.read_bytes() == b"edited"


@pytest.mark.parametrize("dry_run", [False, True])
def test_missing_content_is_not_counted_as_reversible(tmp_path, dry_run):
    target = tmp_path / "notes.txt"
    target.write_bytes(b"edited")
    event = _rollback_event(target, None, "edited")
    result = apply_file_rollback_ledger([event], project_root=tmp_path, dry_run=dry_run)
    assert result.applied == 0 and result.failed + result.skipped == 1
    assert target.read_bytes() == b"edited"


@pytest.mark.parametrize("dry_run", [False, True])
def test_deleting_created_file_requires_a_content_identity(tmp_path, dry_run):
    target = tmp_path / "notes.txt"
    target.write_bytes(b"user content")
    event = _rollback_event(target, None, "", action="delete", expected_current_sha256="")
    result = apply_file_rollback_ledger([event], project_root=tmp_path, dry_run=dry_run)
    assert result.applied == 0 and result.failed + result.skipped == 1
    assert target.read_bytes() == b"user content"


def test_newline_only_external_change_prevents_undo(documents):
    root, _registry, journal, execute = documents
    target = root / "notes.txt"
    target.write_bytes(b"original\r\n")
    execute("read_file", path=str(target))
    execute("write_text_file", path=str(target), content="edited\n", overwrite=True)
    target.write_bytes(b"edited\r\n")
    result = apply_file_rollback_ledger(journal.read_by_type("file_op"), project_root=root)
    assert result.applied == 0 and result.skipped == 1
    assert target.read_bytes() == b"edited\r\n"


@pytest.mark.parametrize("dry_run", [False, True])
def test_replaced_path_link_is_not_followed(tmp_path, dry_run):
    target, other = tmp_path / "notes.txt", tmp_path / "unrelated.txt"
    other.write_bytes(b"edited")
    try:
        target.symlink_to(other)
    except OSError:
        pytest.skip("symlink privilege unavailable on this host")
    event = _rollback_event(target, "original", "edited")
    result = apply_file_rollback_ledger([event], project_root=tmp_path, dry_run=dry_run)
    assert result.applied == 0
    assert other.read_bytes() == b"edited" and target.is_symlink()


def test_post_hook_edit_is_not_enrolled_as_the_tools_postimage(documents, monkeypatch):
    root, _registry, journal, execute = documents
    target = root / "notes.txt"
    target.write_bytes(b"original\n")
    execute("read_file", path=str(target))

    def later_edit(**_kwargs):
        target.write_bytes(b"independent later edit\n")
        return SimpleNamespace(modified_output=None)

    monkeypatch.setattr("runtime.safety.hooks.runner.dispatch_post_tool", later_edit)
    execute("write_text_file", path=str(target), content="tool edit\n", overwrite=True)
    result = apply_file_rollback_ledger(journal.read_by_type("file_op"), project_root=root)

    assert (result.applied, result.skipped) == (0, 1)
    assert target.read_bytes() == b"independent later edit\n"


def test_post_hook_redaction_covers_the_inline_diff(documents, monkeypatch):
    root, _registry, _journal, execute = documents
    target = root / "notes.txt"
    target.write_bytes(b"sensitive old document\n")
    execute("read_file", path=str(target))
    seen_outputs = []

    def scrub_output(**kwargs):
        seen_outputs.append(dict(kwargs["output"]))
        return SimpleNamespace(modified_output={"message": "document updated"})

    monkeypatch.setattr("runtime.safety.hooks.runner.dispatch_post_tool", scrub_output)
    monkeypatch.setattr(
        "runtime.safety.hooks.tool_edge_hooks.post_write_diagnostics",
        lambda *_args, **_kwargs: "source diagnostic: sensitive old document",
    )
    step = execute("write_text_file", path=str(target), content="updated\n", overwrite=True)

    assert "sensitive old document" not in str(step.result.output)
    assert "document updated" in str(step.result.output)
    assert "diff_preview" in seen_outputs[0]
    assert "post_diagnostics" in seen_outputs[0]
    assert len(seen_outputs) == 1
    assert "post_hook_rewrote" in step.result.stderr_tags


def test_inline_diff_keeps_post_write_diagnostics(documents, monkeypatch):
    root, _registry, _journal, execute = documents
    target = root / "notes.txt"
    target.write_bytes(b"original\n")
    execute("read_file", path=str(target))
    monkeypatch.setattr(
        "runtime.safety.hooks.tool_edge_hooks.post_write_diagnostics",
        lambda *_args, **_kwargs: "focused diagnostic result",
    )
    monkeypatch.setattr(
        "runtime.safety.hooks.tool_edge_hooks.post_write_regression_matrix",
        lambda *_args, **_kwargs: "focused verification commands",
    )
    step = execute("write_text_file", path=str(target), content="updated\n", overwrite=True)

    assert "diff_preview" in step.result.output
    assert "focused diagnostic result" in str(step.result.output)
    assert "focused verification commands" in str(step.result.output)


def test_delete_label_does_not_advertise_unobserved_file_removal(documents):
    root, registry, journal, execute = documents
    target = root / "notes.txt"
    target.write_bytes(b"original\n")
    registry.register(
        Skill(
            name="delete_without_removal_fixture",
            description="Synthetic handler that reports success without deleting",
            affinity=["file", "delete"],
            trusted_source="builtin://document-fixture",
            handler=lambda path: {"path": path},
        ),
        verify_tests=False,
    )
    execute("read_file", path=str(target))
    execute("delete_without_removal_fixture", path=str(target))
    target.unlink()  # A later independent deletion must not become this tool's undo.
    result = apply_file_rollback_ledger(journal.read_by_type("file_op"), project_root=root)

    assert result.applied == 0 and result.skipped == 1
    assert not target.exists()


@pytest.mark.parametrize("uncertain", [False, True])
def test_io_failure_reports_commit_and_evidence_without_continuing_older_undo(
    tmp_path, monkeypatch, uncertain
):
    target = tmp_path / "notes.txt"
    target.write_bytes(b"final")
    retained = tmp_path / ".rollback-retained"
    retained.write_bytes(b"final")

    def interrupted_restore(target, content, **_kwargs):
        error = OSError("synthetic failure after the commit decision")
        if uncertain:
            error.rollback_commit_uncertain = True
        else:
            target.write_bytes(content.encode())
            error.rollback_committed = True
        error.rollback_backup_path = retained
        error.rollback_evidence_private = False
        raise error

    monkeypatch.setattr(
        "runtime.memory.runtime_state._file_rollback_io.atomic_restore_text", interrupted_restore
    )
    events = [
        _rollback_event(target, "original", "intermediate"),
        _rollback_event(target, "intermediate", "final"),
    ]
    result = apply_file_rollback_ledger(events, project_root=tmp_path)

    assert (result.applied, result.failed, result.skipped) == (0, 1, 1)
    assert target.read_bytes() == (b"final" if uncertain else b"intermediate")
    outcome = result.to_dict()["outcomes"][0]
    assert outcome["committed"] is (None if uncertain else True)
    assert outcome["status"] == ("uncertain" if uncertain else "failed")
    assert outcome["evidence_private"] is False
    assert outcome["recovery_paths"] == [str(retained)]
    assert result.outcomes[1].reason == "newer_operation_blocked"


def test_postimage_observation_does_not_turn_a_completed_handler_into_a_timeout(
    documents, monkeypatch
):
    import time

    from runtime.execution.tool_engine import executor as executor_module

    root, registry, journal, execute = documents
    target = root / "notes.txt"
    target.write_bytes(b"original\n")
    handler_calls = []

    def update(path):
        Path(path).write_bytes(b"updated\n")
        handler_calls.append("returned")
        return {"path": path}

    registry.register(
        Skill(
            name="update_timed_document_fixture",
            description="Update a synthetic document quickly",
            affinity=["file", "write"],
            trusted_source="builtin://document-fixture",
            handler=update,
            timeout_s=0.1,
        ),
        verify_tests=False,
    )
    execute("read_file", path=str(target))
    original_reader = executor_module._try_read_pre_content
    reads = 0

    def delayed_observation(path):
        nonlocal reads
        reads += 1
        if reads == 2:
            time.sleep(0.2)
        return original_reader(path)

    monkeypatch.setattr(executor_module, "_try_read_pre_content", delayed_observation)
    execute("update_timed_document_fixture", path=str(target))

    assert handler_calls == ["returned"]
    assert len(journal.read_by_type("file_op")) == 1
    result = apply_file_rollback_ledger(journal.read_by_type("file_op"), project_root=root)
    assert result.applied == 1 and target.read_bytes() == b"original\n"
