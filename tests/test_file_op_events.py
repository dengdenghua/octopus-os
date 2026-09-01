"""Implementation note."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.execution.misc.file_write_leases import authorize_file_write_handoff
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import FileOpEvent, InMemoryJournal
from runtime.memory.runtime_state.file_transactions import apply_file_rollback_ledger
from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
    SkillId,
    TaskId,
)
from runtime.platform.process.session import Session, session_scope
from runtime.platform.ui.app import create_app
from runtime.safety.auth import TrustEngine
from runtime.sensing.gateway.observability_router import create_observability_router
from runtime.sensing.gateway.streaming_journal import StreamingJournal


@pytest.fixture
def isolated_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    yield tmp_path


# Implementation note.


def _make_executor(
    *,
    file_write_handler,
    other_handler=None,
) -> tuple[ToolExecutor, InMemoryJournal, SkillRegistry]:
    reg = SkillRegistry()
    reg.register(
        Skill(
            name="write_text_file",
            description="write",
            trusted_source="builtin://write_text_file",
            affinity=["file", "write"],
            handler=file_write_handler,
        ),
        verify_tests=False,
    )
    if other_handler is not None:
        reg.register(
            Skill(
                name="echo_text",
                description="no file",
                trusted_source="builtin://echo_text",
                affinity=["compute"],
                handler=other_handler,
            ),
            verify_tests=False,
        )
    journal = InMemoryJournal()
    executor = ToolExecutor(
        registry=reg,
        immunity=TrustEngine(unknown_policy="allow"),
        journal=journal,
    )
    return executor, journal, reg


def _mk_budget() -> Budget:
    return Budget(
        task_id=TaskId(uuid4()),
        limits=BudgetLimits(tokens=10_000, usd=0.10),
    )


class TestBeakAutoEmitsFileOp:
    def test_successful_file_write_emits_file_op(self) -> None:
        def _write(path: str, content: str) -> dict:
            return {"bytes_written": len(content.encode("utf-8"))}

        executor, journal, _ = _make_executor(file_write_handler=_write)
        executor.execute_step(
            step_id=1,
            node_id="n0",
            sucker_id=SkillId("write_text_file"),
            args={"path": "hello.txt", "content": "hi"},
            caller="t",
            task_id=TaskId(uuid4()),
            arm_id=ArmId("a1"),
            budget=_mk_budget(),
        )
        file_ops = [e for e in journal.read_all() if isinstance(e, FileOpEvent)]
        assert len(file_ops) == 1
        ev = file_ops[0]
        assert ev.path == "hello.txt"
        assert ev.action == "write"
        assert ev.sucker_id == "write_text_file"
        assert ev.new_size == 2  # "hi" utf8

    def test_non_file_skill_does_not_emit(self) -> None:
        def _write(**kw) -> None:
            return None  # noqa: ANN003, ARG001

        def _echo(**kw) -> str:
            return "x"  # noqa: ANN003, ARG001

        executor, journal, _ = _make_executor(
            file_write_handler=_write,
            other_handler=_echo,
        )
        executor.execute_step(
            step_id=1,
            node_id="n0",
            sucker_id=SkillId("echo_text"),
            args={"text": "hi"},
            caller="t",
            task_id=TaskId(uuid4()),
            arm_id=ArmId("a1"),
            budget=_mk_budget(),
        )
        file_ops = [e for e in journal.read_all() if isinstance(e, FileOpEvent)]
        assert file_ops == []

    def test_file_skill_handler_failure_does_not_emit(self) -> None:
        def _boom(path: str, content: str) -> None:  # noqa: ARG001
            raise PermissionError("nope")

        executor, journal, _ = _make_executor(file_write_handler=_boom)
        executor.execute_step(
            step_id=1,
            node_id="n0",
            sucker_id=SkillId("write_text_file"),
            args={"path": "/forbidden", "content": "x"},
            caller="t",
            task_id=TaskId(uuid4()),
            arm_id=ArmId("a1"),
            budget=_mk_budget(),
        )
        file_ops = [e for e in journal.read_all() if isinstance(e, FileOpEvent)]
        assert file_ops == [], "失败的 file skill 不应 emit file_op"

    def test_file_write_lease_blocks_second_owner_in_same_turn(
        self,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "state.txt"
        calls: list[str] = []

        def _write(path: str, content: str) -> dict:
            calls.append(content)
            Path(path).write_text(content, encoding="utf-8")
            return {}

        executor, journal, _ = _make_executor(file_write_handler=_write)
        session = Session(
            thread_id="t1",
            turn_id="turn-1",
            metadata={
                "workspace_path": str(tmp_path),
                "_read_file_paths_this_turn": [
                    str(target.resolve(strict=False)).casefold(),
                ],
            },
        )

        with session_scope(session):
            first = executor.execute_step(
                step_id=1,
                node_id="n0",
                sucker_id=SkillId("write_text_file"),
                args={"path": str(target), "content": "agent-a\n"},
                caller="t",
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a1"),
                budget=_mk_budget(),
                actor="agent-a",
            )
            second = executor.execute_step(
                step_id=2,
                node_id="n0",
                sucker_id=SkillId("write_text_file"),
                args={"path": str(target), "content": "agent-b\n"},
                caller="t",
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a2"),
                budget=_mk_budget(),
                actor="agent-b",
            )

        assert first.result.status == "success"
        assert second.result.status == "failed"
        assert second.result.error_type == "FileWriteLeaseConflict"
        assert "file_write_lease_conflict" in second.result.stderr_tags
        assert calls == ["agent-a\n"]
        assert target.read_text(encoding="utf-8") == "agent-a\n"
        file_ops = [e for e in journal.read_all() if isinstance(e, FileOpEvent)]
        assert len(file_ops) == 1

    def test_file_write_lease_allows_same_owner_reentry(
        self,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "state.txt"

        def _write(path: str, content: str) -> dict:
            Path(path).write_text(content, encoding="utf-8")
            return {}

        executor, journal, _ = _make_executor(file_write_handler=_write)
        session = Session(
            thread_id="t1",
            turn_id="turn-2",
            metadata={
                "workspace_path": str(tmp_path),
                "_read_file_paths_this_turn": [
                    str(target.resolve(strict=False)).casefold(),
                ],
            },
        )

        with session_scope(session):
            first = executor.execute_step(
                step_id=1,
                node_id="n0",
                sucker_id=SkillId("write_text_file"),
                args={"path": str(target), "content": "one\n"},
                caller="t",
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a1"),
                budget=_mk_budget(),
                actor="agent-a",
            )
            second = executor.execute_step(
                step_id=2,
                node_id="n0",
                sucker_id=SkillId("write_text_file"),
                args={"path": str(target), "content": "two\n"},
                caller="t",
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a1"),
                budget=_mk_budget(),
                actor="agent-a",
            )

        assert first.result.status == "success"
        assert second.result.status == "success"
        assert target.read_text(encoding="utf-8") == "two\n"
        file_ops = [e for e in journal.read_all() if isinstance(e, FileOpEvent)]
        assert len(file_ops) == 2

    def test_file_write_lease_allows_explicit_owner_handoff(
        self,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "state.txt"

        def _write(path: str, content: str) -> dict:
            Path(path).write_text(content, encoding="utf-8")
            return {}

        executor, journal, _ = _make_executor(file_write_handler=_write)
        session = Session(
            thread_id="t1",
            turn_id="turn-3",
            metadata={
                "workspace_path": str(tmp_path),
                "_read_file_paths_this_turn": [
                    str(target.resolve(strict=False)).casefold(),
                ],
            },
        )

        with session_scope(session):
            first = executor.execute_step(
                step_id=1,
                node_id="n0",
                sucker_id=SkillId("write_text_file"),
                args={"path": str(target), "content": "agent-a\n"},
                caller="t",
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a1"),
                budget=_mk_budget(),
                actor="agent-a",
            )
            authorize_file_write_handoff(
                session,
                target,
                from_owner="agent-a",
                to_owner="agent-b",
            )
            second = executor.execute_step(
                step_id=2,
                node_id="n0",
                sucker_id=SkillId("write_text_file"),
                args={"path": str(target), "content": "agent-b\n"},
                caller="t",
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a2"),
                budget=_mk_budget(),
                actor="agent-b",
            )

        assert first.result.status == "success"
        assert second.result.status == "success"
        assert target.read_text(encoding="utf-8") == "agent-b\n"
        assert session.metadata["_file_write_lease_handoffs"] == {}
        lease = session.metadata["_file_write_leases"][str(target.resolve(strict=False)).casefold()]
        assert lease["owner"] == "agent-b"
        file_ops = [e for e in journal.read_all() if isinstance(e, FileOpEvent)]
        assert len(file_ops) == 2

    def test_diff_captured_when_file_preexists(
        self,
        tmp_path: Path,
    ) -> None:
        """Implementation note."""
        target = tmp_path / "greeting.txt"
        target.write_text("hello\nworld\n", encoding="utf-8")

        def _overwrite(path: str, content: str) -> dict:
            Path(path).write_text(content, encoding="utf-8")
            return {"ok": True}

        executor, journal, _ = _make_executor(file_write_handler=_overwrite)
        executor.execute_step(
            step_id=1,
            node_id="n0",
            sucker_id=SkillId("write_text_file"),
            args={"path": str(target), "content": "hello\nworld\nEcho\n"},
            caller="t",
            task_id=TaskId(uuid4()),
            arm_id=ArmId("a1"),
            budget=_mk_budget(),
        )
        file_ops = [e for e in journal.read_all() if isinstance(e, FileOpEvent)]
        assert len(file_ops) == 1
        ev = file_ops[0]
        assert ev.diff is not None
        assert "+Echo" in ev.diff
        assert ev.old_size == len(b"hello\nworld\n")
        assert ev.new_size == len(b"hello\nworld\nEcho\n")
        # Implementation note.
        assert ev.diff.startswith(f"--- a/{target}") or f"a/{target}" in ev.diff
        assert ev.rollback is not None
        assert ev.rollback["reversible"] is True
        assert ev.rollback["action"] == "write"
        assert ev.rollback["content"] == "hello\nworld\n"

    def test_file_rollback_ledger_restores_latest_write(
        self,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "state.txt"
        target.write_text("before\n", encoding="utf-8")

        def _overwrite(path: str, content: str) -> dict:
            Path(path).write_text(content, encoding="utf-8")
            return {}

        executor, journal, _ = _make_executor(file_write_handler=_overwrite)
        executor.execute_step(
            step_id=1,
            node_id="n0",
            sucker_id=SkillId("write_text_file"),
            args={"path": str(target), "content": "after\n"},
            caller="t",
            task_id=TaskId(uuid4()),
            arm_id=ArmId("a1"),
            budget=_mk_budget(),
        )

        result = apply_file_rollback_ledger(
            journal.read_by_type("file_op"),
            project_root=tmp_path,
        )

        assert result.applied == 1
        assert result.failed == 0
        assert target.read_text(encoding="utf-8") == "before\n"

    def test_file_rollback_ledger_deletes_created_file(
        self,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "created.txt"

        def _create(path: str, content: str) -> dict:
            Path(path).write_text(content, encoding="utf-8")
            return {}

        executor, journal, _ = _make_executor(file_write_handler=_create)
        executor.execute_step(
            step_id=1,
            node_id="n0",
            sucker_id=SkillId("write_text_file"),
            args={"path": str(target), "content": "new\n"},
            caller="t",
            task_id=TaskId(uuid4()),
            arm_id=ArmId("a1"),
            budget=_mk_budget(),
        )

        result = apply_file_rollback_ledger(
            journal.read_by_type("file_op"),
            project_root=tmp_path,
        )

        assert result.applied == 1
        assert result.failed == 0
        assert not target.exists()

    def test_file_rollback_ledger_skips_when_file_changed_after_event(
        self,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "state.txt"
        target.write_text("before\n", encoding="utf-8")

        def _overwrite(path: str, content: str) -> dict:
            Path(path).write_text(content, encoding="utf-8")
            return {}

        executor, journal, _ = _make_executor(file_write_handler=_overwrite)
        executor.execute_step(
            step_id=1,
            node_id="n0",
            sucker_id=SkillId("write_text_file"),
            args={"path": str(target), "content": "after\n"},
            caller="t",
            task_id=TaskId(uuid4()),
            arm_id=ArmId("a1"),
            budget=_mk_budget(),
        )
        target.write_text("user edit\n", encoding="utf-8")

        result = apply_file_rollback_ledger(
            journal.read_by_type("file_op"),
            project_root=tmp_path,
        )

        assert result.applied == 0
        assert result.skipped == 1
        assert result.failed == 0
        assert result.errors == (f"hash_mismatch:{target}",)
        assert target.read_text(encoding="utf-8") == "user edit\n"

    def test_diff_for_new_file_is_all_additions(
        self,
        tmp_path: Path,
    ) -> None:
        """Implementation note."""
        target = tmp_path / "new.txt"
        # Implementation note.

        def _create(path: str, content: str) -> dict:
            Path(path).write_text(content, encoding="utf-8")
            return {}

        executor, journal, _ = _make_executor(file_write_handler=_create)
        executor.execute_step(
            step_id=1,
            node_id="n0",
            sucker_id=SkillId("write_text_file"),
            args={"path": str(target), "content": "fresh line\n"},
            caller="t",
            task_id=TaskId(uuid4()),
            arm_id=ArmId("a1"),
            budget=_mk_budget(),
        )
        ev = [e for e in journal.read_all() if isinstance(e, FileOpEvent)][0]
        assert ev.diff is not None
        assert "+fresh line" in ev.diff
        assert ev.old_size is None  # Implementation note.

    def test_diff_is_none_when_content_unchanged(
        self,
        tmp_path: Path,
    ) -> None:
        """Implementation note."""
        target = tmp_path / "same.txt"
        content = "identical\n"
        target.write_text(content, encoding="utf-8")

        def _noop(path: str, content: str) -> dict:  # noqa: ARG001
            return {}

        executor, journal, _ = _make_executor(file_write_handler=_noop)
        executor.execute_step(
            step_id=1,
            node_id="n0",
            sucker_id=SkillId("write_text_file"),
            args={"path": str(target), "content": content},
            caller="t",
            task_id=TaskId(uuid4()),
            arm_id=ArmId("a1"),
            budget=_mk_budget(),
        )
        ev = [e for e in journal.read_all() if isinstance(e, FileOpEvent)][0]
        assert ev.diff is None

    def test_large_diff_gets_truncated(
        self,
        tmp_path: Path,
    ) -> None:
        """Implementation note."""
        target = tmp_path / "big.txt"
        # Implementation note.
        long_content = "".join(f"line {i:05d}\n" for i in range(3000))
        target.write_text("", encoding="utf-8")

        def _overwrite(path: str, content: str) -> dict:
            Path(path).write_text(content, encoding="utf-8")
            return {}

        executor, journal, _ = _make_executor(file_write_handler=_overwrite)
        executor.execute_step(
            step_id=1,
            node_id="n0",
            sucker_id=SkillId("write_text_file"),
            args={"path": str(target), "content": long_content},
            caller="t",
            task_id=TaskId(uuid4()),
            arm_id=ArmId("a1"),
            budget=_mk_budget(),
        )
        ev = [e for e in journal.read_all() if isinstance(e, FileOpEvent)][0]
        assert ev.diff is not None
        assert "truncated" in ev.diff
        assert len(ev.diff) < 25_000

    def test_no_path_in_args_skips_emit(self) -> None:
        """Implementation note."""

        def _write_noop(**kw) -> None:
            return None  # noqa: ANN003, ARG001

        executor, journal, _ = _make_executor(file_write_handler=_write_noop)
        executor.execute_step(
            step_id=1,
            node_id="n0",
            sucker_id=SkillId("write_text_file"),
            args={},  # Implementation note.
            caller="t",
            task_id=TaskId(uuid4()),
            arm_id=ArmId("a1"),
            budget=_mk_budget(),
        )
        file_ops = [e for e in journal.read_all() if isinstance(e, FileOpEvent)]
        assert file_ops == []


# Implementation note.


@pytest.fixture
def client(isolated_cwd: Path) -> TestClient:
    cfg = AgentConfig(
        planner=PlannerConfig(
            type="llm",
            model="mock/file",
            mock_response='{"reasoning":"r","nodes":[]}',
        )
    )
    stack = build_from_config(cfg)

    # Implementation note.
    def _w(path: str = "", content: str = "") -> dict:  # noqa: ARG001
        return {"bytes_written": len(content.encode("utf-8"))}

    stack.registry.register(
        Skill(
            name="write_text_file",
            description="test",
            trusted_source="builtin://write_text_file",
            affinity=["file", "write"],
            handler=_w,
        ),
        verify_tests=False,
        replace=True,
    )
    app = create_app(
        journal=stack.journal,
        registry=stack.registry,
        stack=stack,
    )
    # Implementation note.
    stack.journal.write_file_op(
        path="pre.txt",
        action="create",
        sucker_id="write_text_file",
        new_size=5,
    )
    return TestClient(app)


class TestFileStreamEndpoints:
    """Implementation note."""

    def test_files_stream_route_registered(self, client: TestClient) -> None:
        routes = set(client.app.openapi()["paths"])
        assert "/api/files/stream" in routes
        assert "/api/stream" in routes

    def test_journal_has_pre_injected_file_op(self, client: TestClient) -> None:
        """Implementation note."""
        r = client.get("/api/journal")
        assert r.status_code == 200
        data = r.json()
        assert data["counts"].get("file_op", 0) >= 1


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rollback_client(journal: InMemoryJournal) -> TestClient:
    app = FastAPI()
    streaming = StreamingJournal(journal)
    app.include_router(
        create_observability_router(journal=streaming, registry=SkillRegistry()),
    )
    return TestClient(app)


class TestFileRollbackApi:
    def test_rollback_preview_filters_task_and_redacts_content(
        self,
        tmp_path: Path,
    ) -> None:
        journal = InMemoryJournal()
        task_id = TaskId(uuid4())
        target = tmp_path / "state.txt"
        target.write_text("after\n", encoding="utf-8")
        journal.write_file_op(
            task_id=task_id,
            path=str(target),
            action="write",
            sucker_id="write_text_file",
            old_size=len(b"before\n"),
            new_size=len(b"after\n"),
            rollback={
                "reversible": True,
                "action": "write",
                "path": str(target),
                "content": "before\n",
                "expected_current_sha256": _sha("after\n"),
            },
        )
        journal.write_file_op(
            task_id=TaskId(uuid4()),
            path=str(tmp_path / "other.txt"),
            action="write",
            rollback={
                "reversible": True,
                "action": "write",
                "path": str(tmp_path / "other.txt"),
                "content": "other\n",
                "expected_current_sha256": "",
            },
        )

        r = _rollback_client(journal).get(
            "/api/files/rollback/preview",
            params={"task_id": str(task_id), "project_root": str(tmp_path)},
        )

        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is True
        assert data["matched_events"] == 1
        assert data["applied"] == 1
        assert data["failed"] == 0
        assert data["entries"][0]["path"] == str(target)
        assert data["entries"][0]["content_bytes"] == len(b"before\n")
        assert "content" not in data["entries"][0]
        assert target.read_text(encoding="utf-8") == "after\n"

    def test_rollback_apply_restores_files_for_task(
        self,
        tmp_path: Path,
    ) -> None:
        journal = InMemoryJournal()
        task_id = TaskId(uuid4())
        target = tmp_path / "state.txt"
        target.write_text("after\n", encoding="utf-8")
        journal.write_file_op(
            task_id=task_id,
            path=str(target),
            action="write",
            sucker_id="write_text_file",
            old_size=len(b"before\n"),
            new_size=len(b"after\n"),
            rollback={
                "reversible": True,
                "action": "write",
                "path": str(target),
                "content": "before\n",
                "expected_current_sha256": _sha("after\n"),
            },
        )

        r = _rollback_client(journal).post(
            "/api/files/rollback/apply",
            json={"task_id": str(task_id), "project_root": str(tmp_path)},
        )

        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is False
        assert data["matched_events"] == 1
        assert data["applied"] == 1
        assert data["failed"] == 0
        assert target.read_text(encoding="utf-8") == "before\n"

    def test_rollback_apply_writes_audit_event(
        self,
        tmp_path: Path,
    ) -> None:
        journal = InMemoryJournal()
        task_id = TaskId(uuid4())
        target = tmp_path / "state.txt"
        target.write_text("after\n", encoding="utf-8")
        journal.write_file_op(
            task_id=task_id,
            path=str(target),
            action="write",
            sucker_id="write_text_file",
            rollback={
                "reversible": True,
                "action": "write",
                "path": str(target),
                "content": "before\n",
                "expected_current_sha256": _sha("after\n"),
            },
        )
        source_event_id = str(journal.read_by_type("file_op")[0].event_id)

        r = _rollback_client(journal).post(
            "/api/files/rollback/apply",
            json={"event_id": source_event_id, "project_root": str(tmp_path)},
        )

        assert r.status_code == 200
        events = journal.read_by_type("file_rollback")
        assert len(events) == 1
        event = events[0]
        assert event.event_type == "file_rollback"
        assert event.dry_run is False
        assert event.project_root == str(tmp_path)
        assert event.applied == 1
        assert event.skipped == 0
        assert event.failed == 0
        assert event.source_event_ids == [source_event_id]
        assert event.paths == [str(target)]
        assert event.errors == []

    def test_rollback_history_lists_recent_audit_events(
        self,
        tmp_path: Path,
    ) -> None:
        journal = InMemoryJournal()
        task_id = TaskId(uuid4())
        target = tmp_path / "state.txt"
        target.write_text("after\n", encoding="utf-8")
        journal.write_file_op(
            task_id=task_id,
            path=str(target),
            action="write",
            sucker_id="write_text_file",
            rollback={
                "reversible": True,
                "action": "write",
                "path": str(target),
                "content": "before\n",
                "expected_current_sha256": _sha("after\n"),
            },
        )
        source_event_id = str(journal.read_by_type("file_op")[0].event_id)
        client = _rollback_client(journal)
        apply_response = client.post(
            "/api/files/rollback/apply",
            json={"event_id": source_event_id, "project_root": str(tmp_path)},
        )
        assert apply_response.status_code == 200

        r = client.get("/api/files/rollback/history")

        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        item = data["events"][0]
        assert item["event_type"] == "file_rollback"
        assert item["project_root"] == str(tmp_path)
        assert item["applied"] == 1
        assert item["source_event_ids"] == [source_event_id]
        assert item["paths"] == [str(target)]

    def test_rollback_apply_can_target_single_file_op_event(
        self,
        tmp_path: Path,
    ) -> None:
        journal = InMemoryJournal()
        task_id = TaskId(uuid4())
        first = tmp_path / "first.txt"
        second = tmp_path / "second.txt"
        first.write_text("after-1\n", encoding="utf-8")
        second.write_text("after-2\n", encoding="utf-8")
        journal.write_file_op(
            task_id=task_id,
            path=str(first),
            action="write",
            rollback={
                "reversible": True,
                "action": "write",
                "path": str(first),
                "content": "before-1\n",
                "expected_current_sha256": _sha("after-1\n"),
            },
        )
        journal.write_file_op(
            task_id=task_id,
            path=str(second),
            action="write",
            rollback={
                "reversible": True,
                "action": "write",
                "path": str(second),
                "content": "before-2\n",
                "expected_current_sha256": _sha("after-2\n"),
            },
        )
        event_id = str(journal.read_by_type("file_op")[0].event_id)

        r = _rollback_client(journal).post(
            "/api/files/rollback/apply",
            json={"event_id": event_id, "project_root": str(tmp_path)},
        )

        assert r.status_code == 200
        data = r.json()
        assert data["matched_events"] == 1
        assert data["entries"][0]["source_event_id"] == event_id
        assert first.read_text(encoding="utf-8") == "before-1\n"
        assert second.read_text(encoding="utf-8") == "after-2\n"

    def test_rollback_apply_requires_project_root(self) -> None:
        journal = InMemoryJournal()

        r = _rollback_client(journal).post(
            "/api/files/rollback/apply",
            json={"task_id": str(TaskId(uuid4()))},
        )

        assert r.status_code == 400
        assert "project_root required" in r.text
