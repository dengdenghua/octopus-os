"""Argument-validation rejections must coach a same-tool retry.

Regression cover for trn_634e5726d3c14e89-class failures: a single
``old``/``old_string`` argument-name mismatch produced the generic
"switch tools or just answer" coaching, and the model abandoned the only
write attempt of the session. A schema rejection means the tool never ran,
so the only correct next move is re-issuing the same call with fixed args.
"""

from __future__ import annotations

import pytest

from runtime.core.cerebrum._react_execution_dispatch import (
    _is_pre_execution_schema_rejection,
    _is_schema_rejection,
    _schema_repair_feedback,
)
from runtime.platform.models import ExecutionResult, Step, ToolCall


@pytest.mark.parametrize(
    "detail",
    [
        '{"error": "old_string must be non-empty"}',
        '{"error": "missing path"}',
        '{"error": "edits must be a non-empty list"}',
        '{"error": "edits[0].old_string must be non-empty"}',
        '{"error": "find must be non-empty"}',
        '{"error": "unknown argument: old"}',
    ],
)
def test_argument_validation_payloads_classify_as_schema(detail: str) -> None:
    assert _is_schema_rejection(detail, "structured_error") is True


@pytest.mark.parametrize(
    "detail",
    [
        '{"error": "file not found: /tmp/x.py"}',
        '{"error": "permission denied"}',
        '{"error": "sandbox_apply: Operation not permitted"}',
        '{"error": "connection reset by peer"}',
    ],
)
def test_execution_failures_do_not_classify_as_schema(detail: str) -> None:
    assert _is_schema_rejection(detail, "structured_error") is False


def test_non_structured_error_types_are_never_schema() -> None:
    """A non-zero exit whose stdout mentions a missing path is execution."""
    detail = '{"stdout": "missing path in config", "exit_code": 1}'
    assert _is_schema_rejection(detail, "non_zero_exit") is False


def test_command_output_mentioning_missing_path_is_not_schema() -> None:
    """Only the leading clause counts, so tool *output* cannot misclassify."""
    detail = '{"error": "command failed", "stderr": "missing path"}'
    assert _is_schema_rejection(detail, "structured_error") is False


def test_empty_detail_is_not_schema() -> None:
    assert _is_schema_rejection("", "structured_error") is False


def test_repair_feedback_closes_both_escape_hatches() -> None:
    """The generic message's two outs are exactly what caused the regression."""
    text = _schema_repair_feedback("edit_file")
    assert "edit_file" in text
    assert "禁止改用其他工具" in text
    assert "禁止在本轮给出 Final Answer" in text


def test_repair_feedback_names_the_failing_tool() -> None:
    assert "multi_edit_file" in _schema_repair_feedback("multi_edit_file")


def _step_with_execution_source(source: str) -> Step:
    call = ToolCall(caller="react_loop", sucker_id="edit_file", args={})
    return Step(
        step_id=1,
        node_id="react:1",
        action=call,
        result=ExecutionResult(
            call_id=call.call_id,
            status="failed",
            error_type="invalid_arguments",
            output={"error": "missing path"},
            execution_source=source,
        ),
    )


def test_schema_retry_coaching_requires_handler_not_executed_provenance() -> None:
    detail = '{"error": "missing path"}'

    assert _is_pre_execution_schema_rejection(
        _step_with_execution_source("handler_not_executed"),
        detail,
        "invalid_arguments",
    )
    assert not _is_pre_execution_schema_rejection(
        _step_with_execution_source("registered_noncanonical"),
        detail,
        "invalid_arguments",
    )

