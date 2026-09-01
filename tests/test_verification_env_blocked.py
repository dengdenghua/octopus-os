"""Tests for environment-blocked verification degradation.

Regression: a code-changing turn whose verifier could not run because of
an environment problem (missing tool, no network, corepack download
failure) was hard-failed, blaming the code for an environment issue.
Environment-blocked verification items now degrade to a
manual-confirmation turn instead.
"""

from __future__ import annotations

from runtime.protocol import ItemStatus, Turn, VerificationItem
from runtime.sensing.gateway.realtime_turn_outcome import (
    _turn_verification_environment_blocked,
    _verification_item_is_environment_blocked,
)


def _verification(
    command: str, text: str, status: ItemStatus = ItemStatus.FAILED
) -> VerificationItem:
    return VerificationItem(
        command=command,
        kind="manual",
        status=status,
        stderr_tail=text,
    )


def test_corepack_download_failure_is_environment_blocked() -> None:
    item = _verification(
        "pnpm exec commitlint --version",
        "Corepack is about to download https://registry.npmjs.org/pnpm/-/pnpm-10.26.2.tgz\n"
        "Error when performing the request",
    )
    assert _verification_item_is_environment_blocked(item)


def test_command_not_found_is_not_environment_blocked() -> None:
    # A missing CLI is something the agent can usually fix by picking an
    # installed equivalent — it must keep hard-failing (environment_missing
    # _tool repair route), NOT degrade to manual confirmation.
    item = _verification("pnpm typecheck", "command not found: pnpm")
    assert not _verification_item_is_environment_blocked(item)


def test_network_failure_is_environment_blocked() -> None:
    item = _verification("git clone ...", "fatal: unable to access ... Failed to connect")
    # connection refused / failed to connect are network markers
    assert _verification_item_is_environment_blocked(item)


def test_real_code_failure_is_not_environment_blocked() -> None:
    item = _verification("pytest", "E assert 0 == 1\nFAILED tests/test_x.py::test_y")
    assert not _verification_item_is_environment_blocked(item)


def test_syntax_error_is_not_environment_blocked() -> None:
    item = _verification("ruff check .", "SyntaxError: invalid syntax at tools/foo.py:10")
    assert not _verification_item_is_environment_blocked(item)


def test_turn_with_mixed_failures_is_not_all_blocked() -> None:
    turn = Turn.model_validate(
        {
            "id": "turn-mixed",
            "threadId": "thread-mixed",
            "status": "failed",
            "startedAt": "2026-06-01T18:53:24Z",
            "completedAt": "2026-06-01T19:03:00Z",
            "items": [
                {
                    "id": "v1",
                    "type": "verification",
                    "status": "failed",
                    "createdAt": "2026-06-01T19:00:00Z",
                    "command": "pnpm exec commitlint --version",
                    "kind": "manual",
                    "stderrTail": "Corepack is about to download ... Error when performing the request",
                    "relatedFiles": ["/repo/a.py"],
                    "relatedChangeItemIds": ["fc1"],
                },
                {
                    "id": "v2",
                    "type": "verification",
                    "status": "failed",
                    "createdAt": "2026-06-01T19:00:01Z",
                    "command": "pytest",
                    "kind": "test",
                    "stderrTail": "E assert 0 == 1",
                    "relatedFiles": ["/repo/a.py"],
                    "relatedChangeItemIds": ["fc1"],
                },
            ],
            "error": None,
        }
    )
    # One environment-blocked + one real code failure → NOT all blocked.
    assert _turn_verification_environment_blocked(turn) is False


def test_turn_all_environment_blocked_is_degradable() -> None:
    turn = Turn.model_validate(
        {
            "id": "turn-env",
            "threadId": "thread-env",
            "status": "failed",
            "startedAt": "2026-06-01T18:53:24Z",
            "completedAt": "2026-06-01T19:03:00Z",
            "items": [
                {
                    "id": "v1",
                    "type": "verification",
                    "status": "failed",
                    "createdAt": "2026-06-01T19:00:00Z",
                    "command": "pnpm typecheck",
                    "kind": "manual",
                    "stderrTail": (
                        "Corepack is about to download ... Error when performing the request"
                    ),
                    "relatedFiles": ["/repo/a.ts"],
                    "relatedChangeItemIds": ["fc1"],
                }
            ],
            "error": None,
        }
    )
    assert _turn_verification_environment_blocked(turn) is True


def test_command_execution_env_blocked_without_verification_item() -> None:
    """An agent that ran verification via exec_shell (which failed with an
    environment blockage) but never recorded a VerificationItem should
    still degrade instead of hard-failing."""
    turn = Turn.model_validate(
        {
            "id": "turn-cmd-env",
            "threadId": "thread-cmd-env",
            "status": "failed",
            "startedAt": "2026-06-01T18:53:24Z",
            "completedAt": "2026-06-01T19:03:00Z",
            "items": [
                {
                    "id": "fc1",
                    "type": "fileChange",
                    "status": "completed",
                    "createdAt": "2026-06-01T18:55:00Z",
                    "changes": [
                        {
                            "path": "/repo/commitlint.config.js",
                            "op": "update",
                            "diff": "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
                            "diffTruncated": False,
                            "hunks": [],
                        }
                    ],
                    "grantRoot": "/repo",
                },
                {
                    "id": "cmd1",
                    "type": "commandExecution",
                    "status": "failed",
                    "createdAt": "2026-06-01T18:56:00Z",
                    "command": "exec_shell",
                    "inputPreview": {"command": "cd frontend && pnpm typecheck"},
                    "aggregatedOutput": (
                        "(tool failed) Corepack is about to download "
                        "https://registry.npmjs.org/pnpm/-/pnpm-10.26.2.tgz "
                        "Error when performing the request"
                    ),
                },
            ],
            "error": None,
        }
    )
    assert _turn_verification_environment_blocked(turn) is True


def test_command_execution_real_failure_not_degradable() -> None:
    """A failed exec_shell that actually ran the verifier and found broken
    code (test assertion) must keep hard-failing."""
    turn = Turn.model_validate(
        {
            "id": "turn-cmd-real",
            "threadId": "thread-cmd-real",
            "status": "failed",
            "startedAt": "2026-06-01T18:53:24Z",
            "completedAt": "2026-06-01T19:03:00Z",
            "items": [
                {
                    "id": "fc1",
                    "type": "fileChange",
                    "status": "completed",
                    "createdAt": "2026-06-01T18:55:00Z",
                    "changes": [
                        {
                            "path": "/repo/foo.py",
                            "op": "update",
                            "diff": "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
                            "diffTruncated": False,
                            "hunks": [],
                        }
                    ],
                    "grantRoot": "/repo",
                },
                {
                    "id": "cmd1",
                    "type": "commandExecution",
                    "status": "failed",
                    "createdAt": "2026-06-01T18:56:00Z",
                    "command": "exec_shell",
                    "inputPreview": {"command": "pytest -x"},
                    "aggregatedOutput": ("E assert 0 == 1\nFAILED tests/test_foo.py::test_bar"),
                },
            ],
            "error": None,
        }
    )
    assert _turn_verification_environment_blocked(turn) is False


def test_no_failed_verification_is_not_blocked() -> None:
    turn = Turn.model_validate(
        {
            "id": "turn-ok",
            "threadId": "thread-ok",
            "status": "completed",
            "startedAt": "2026-06-01T18:53:24Z",
            "completedAt": "2026-06-01T19:03:00Z",
            "items": [
                {
                    "id": "v1",
                    "type": "verification",
                    "status": "completed",
                    "createdAt": "2026-06-01T19:00:00Z",
                    "command": "pytest",
                    "kind": "test",
                    "relatedFiles": ["/repo/a.py"],
                    "relatedChangeItemIds": ["fc1"],
                }
            ],
            "error": None,
        }
    )
    assert _turn_verification_environment_blocked(turn) is False

