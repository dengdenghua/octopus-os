"""Implementation note."""

from __future__ import annotations

from uuid import uuid4

from runtime.memory.journal.derive import (
    assert_logged_history_reconstructs,
    derive_model_messages,
)
from runtime.memory.journal.journal import InMemoryJournal
from runtime.platform.models import (
    ArmId,
    ExecutionResult,
    SkillId,
    Step,
    TaskId,
    ToolCall,
)


def _step(
    journal: InMemoryJournal,
    task_id: TaskId,
    step_id: int,
    node_id: str,
    sucker: str,
    args: dict,
    output: object,
    *,
    status: str = "success",
) -> None:
    call = ToolCall(
        caller="cerebrum",
        sucker_id=SkillId(sucker),
        args=args,
    )
    result = ExecutionResult(
        call_id=call.call_id,
        status=status,  # type: ignore[arg-type]
        output=output,
    )
    journal.write_step(
        task_id,
        ArmId("arm:test"),
        Step(
            step_id=step_id,
            node_id=node_id,
            action=call,
            result=result,
        ),
    )


class TestDeriveModelMessages:
    def test_empty_journal_yields_no_messages(self):
        journal = InMemoryJournal()
        assert derive_model_messages(journal) == []

    def test_user_intent_becomes_leading_message(self):
        journal = InMemoryJournal()
        messages = derive_model_messages(journal, user_intent="fix the bug")
        assert [m.role for m in messages] == ["user"]
        assert messages[0].content == "fix the bug"

    def test_step_projects_tool_use_and_tool_result_pair(self):
        journal = InMemoryJournal()
        task_id = TaskId(uuid4())
        _step(
            journal,
            task_id,
            step_id=0,
            node_id="n0",
            sucker="list_cwd",
            args={"path": "/tmp"},
            output={"count": 2},
        )
        messages = derive_model_messages(journal, user_intent="list files")
        assert [m.role for m in messages] == ["user", "assistant", "user"]

        assistant = messages[1]
        assert isinstance(assistant.content, list)
        block = assistant.content[0]
        assert block["type"] == "tool_use"
        assert block["name"] == "list_cwd"
        assert block["input"] == {"path": "/tmp"}

        result = messages[2]
        assert isinstance(result.content, list)
        result_block = result.content[0]
        assert result_block["type"] == "tool_result"
        assert result_block["tool_use_id"] == block["id"]
        assert '{"count": 2}' in result_block["content"]

    def test_task_id_filter(self):
        journal = InMemoryJournal()
        second_task = TaskId(uuid4())
        _step(
            journal,
            TaskId(uuid4()),
            step_id=0,
            node_id="n0",
            sucker="list_cwd",
            args={},
            output="a",
        )
        _step(
            journal,
            second_task,
            step_id=0,
            node_id="n0",
            sucker="read_file",
            args={},
            output="b",
        )
        messages = derive_model_messages(journal, task_id=second_task)
        tool_use = [
            b
            for m in messages
            if isinstance(m.content, list)
            for b in m.content
            if b.get("type") == "tool_use"
        ]
        assert [b["name"] for b in tool_use] == ["read_file"]

    def test_max_steps_keeps_tail(self):
        journal = InMemoryJournal()
        task_id = TaskId(uuid4())
        for index in range(3):
            _step(
                journal,
                task_id,
                step_id=index,
                node_id=f"n{index}",
                sucker="read_file",
                args={"path": f"p{index}"},
                output=f"o{index}",
            )
        messages = derive_model_messages(journal, task_id=task_id, max_steps=1)
        tool_use = [
            b
            for m in messages
            if isinstance(m.content, list)
            for b in m.content
            if b.get("type") == "tool_use"
        ]
        assert [b["input"] for b in tool_use] == [{"path": "p2"}]

    def test_round_trip_invariant(self):
        journal = InMemoryJournal()
        task_id = TaskId(uuid4())
        for index in range(2):
            _step(
                journal,
                task_id,
                step_id=index,
                node_id=f"n{index}",
                sucker="exec_shell",
                args={"command": [f"echo {index}"]},
                output=f"out {index}",
            )
        events = journal.read_all()
        steps = [e for e in events if e.event_type == "step"]
        assert_logged_history_reconstructs(journal, steps, task_id=task_id)

