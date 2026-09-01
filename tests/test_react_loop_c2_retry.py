"""C2 regression: failed write/exec tools must not be silently auto-retried.

``stream_react_loop`` re-runs a failed tool's action once. For non-idempotent
tools (write / edit / exec / delete / dangerous) a re-run would double any side
effects the first attempt already had (a partial write, or a shell command that
ran before its result failed to parse), so the loop now gates the retry on
``_retry_safe_affinity`` and the server-sealed effect receipt.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_execution_receipts import _retry_safe_effect_receipt
from runtime.core.cerebrum.react_loop import _retry_safe_affinity
from runtime.platform.models import CostEntry, ExecutionResult, Step, ToolCall


def test_idempotent_tools_are_retry_safe() -> None:
    assert _retry_safe_affinity(["read"]) is True
    assert _retry_safe_affinity(["search", "read"]) is True
    assert _retry_safe_affinity(["quote", "readonly"]) is True


def test_side_effecting_tools_are_not_retry_safe() -> None:
    for affinity in (
        ["write"],
        ["edit"],
        ["exec"],
        ["delete"],
        ["dangerous"],
        ["read", "write"],  # any single side-effecting tag is enough
        [],  # no explicit read-only evidence
        ["trading", "stock", "order", "trade", "position"],
        ["market", "quote"],  # domain tags alone are not an idempotency contract
    ):
        assert _retry_safe_affinity(affinity) is False, affinity


def test_unknown_affinity_is_fail_closed() -> None:
    # affinity we could not determine must NOT be auto-retried
    assert _retry_safe_affinity(None) is False


def test_auto_retry_requires_server_sealed_failed_read_receipt() -> None:
    call = ToolCall(caller="test", sucker_id="read_file", args={})
    result = ExecutionResult(
        call_id=call.call_id,
        status="failed",
        cost=CostEntry(),
        effect_receipt={
            "schema": "echo.tool.effect_receipt.v1",
            "sealed": True,
            "emitted_by": "tool_executor",
            "tool_name": "read_file",
            "call_id": str(call.call_id),
            "effect_class": "read_only",
            "state": "failed",
            "handler_entered": True,
            "retry_safe": True,
        },
    )
    step = Step(step_id=1, node_id="n1", action=call, result=result)

    assert _retry_safe_effect_receipt(step) is True
    forged = step.model_copy(
        update={
            "result": result.model_copy(
                update={"effect_receipt": {**result.effect_receipt, "sealed": False}}
            )
        }
    )
    assert _retry_safe_effect_receipt(forged) is False
    external = step.model_copy(
        update={
            "result": result.model_copy(
                update={
                    "effect_receipt": {
                        **result.effect_receipt,
                        "effect_class": "external_or_unknown",
                    }
                }
            )
        }
    )
    assert _retry_safe_effect_receipt(external) is False

