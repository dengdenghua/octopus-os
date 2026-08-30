from runtime.core.cerebrum.react_loop import (
    _quiet_evidence_checkpoint_due,
    _quiet_evidence_targets,
    _should_accumulate_quiet_evidence,
)
from runtime.core.cerebrum.react_types import ReActStep


def _step(action: str) -> ReActStep:
    return ReActStep(iteration=1, action=action, observation="evidence")


def test_two_distinct_quiet_reads_trigger_one_public_evidence_beat() -> None:
    steps = [
        _step('read_file({"path": "runtime/protocol/items.py"})'),
        _step('read_file({"path": "frontend/src/core/realtime/items.ts"})'),
    ]

    assert _quiet_evidence_targets(steps) == {
        "runtime/protocol/items.py",
        "frontend/src/core/realtime/items.ts",
    }
    assert _quiet_evidence_checkpoint_due(steps)


def test_parallel_quiet_reads_trigger_one_public_evidence_beat() -> None:
    step = ReActStep(
        iteration=1,
        action='read_file({"path": "runtime/protocol/items.py"})',
        actions=[
            'read_file({"path": "runtime/protocol/items.py"})',
            'read_file({"path": "frontend/src/core/realtime/items.ts"})',
            'read_file({"path": "frontend/src/core/threads/realtime-adapter.ts"})',
        ],
        observation="evidence",
    )

    assert _quiet_evidence_targets([step]) == {
        "runtime/protocol/items.py",
        "frontend/src/core/realtime/items.ts",
        "frontend/src/core/threads/realtime-adapter.ts",
    }
    assert _quiet_evidence_checkpoint_due([step])
    assert _should_accumulate_quiet_evidence(
        step,
        succeeded=True,
        observation="evidence",
    )


def test_repeated_read_of_one_target_does_not_manufacture_progress() -> None:
    steps = [
        _step('read_file({"path": "runtime/protocol/items.py"})'),
        _step('read_file({"path": "runtime/protocol/items.py"})'),
    ]

    assert not _quiet_evidence_checkpoint_due(steps)


def test_writes_and_commands_do_not_enter_the_quiet_read_window() -> None:
    steps = [
        _step('write_text_file({"path": "notes.md", "content": "x"})'),
        _step('exec_shell({"cmd": "pytest"})'),
    ]

    assert _quiet_evidence_targets(steps) == set()
    assert not _quiet_evidence_checkpoint_due(steps)
    assert not _should_accumulate_quiet_evidence(
        steps[1],
        succeeded=True,
        observation="command output",
    )

