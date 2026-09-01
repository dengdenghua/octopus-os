"""Server-owned provenance for ReAct execution receipts."""

from __future__ import annotations

from typing import Any

from runtime.execution.tool_engine.effect_receipts import EFFECT_RECEIPT_SCHEMA


def _execution_receipt_trust(beak_step: Any) -> tuple[bool, str]:
    """Read server-owned provenance from a completed ToolExecutor Step.

    Missing/legacy/failed dispatches fail closed. The executor computes these
    fields from the actual captured handler, so this layer never performs a
    second registry lookup or trusts model/plugin-controlled metadata.
    """

    result = getattr(beak_step, "result", None)
    if result is None or getattr(result, "trusted_execution", False) is not True:
        return False, str(getattr(result, "execution_source", "") or "untrusted")
    return True, str(getattr(result, "execution_source", "") or "canonical_builtin")


def _execution_effect_receipt(beak_step: Any) -> dict[str, object]:
    """Project only a server-stamped effect proof into the ReAct receipt."""

    result = getattr(beak_step, "result", None)
    raw = getattr(result, "effect_receipt", None)
    if not isinstance(raw, dict):
        return {}
    if raw.get("schema") != EFFECT_RECEIPT_SCHEMA:
        return {}
    if raw.get("emitted_by") not in {"tool_executor", "legacy_effect_replay"}:
        return {}
    allowed = {
        "schema",
        "sealed",
        "emitted_by",
        "tool_name",
        "call_id",
        "effect_key",
        "fencing_token",
        "effect_class",
        "state",
        "handler_entered",
        "retry_safe",
        "replayed_from_state",
        "reason",
    }
    return {str(key): value for key, value in raw.items() if key in allowed}


def _retry_safe_effect_receipt(beak_step: Any) -> bool:
    """Allow silent retry only for a sealed failed canonical read handler."""

    proof = _execution_effect_receipt(beak_step)
    return bool(
        proof.get("sealed") is True
        and proof.get("emitted_by") == "tool_executor"
        and proof.get("effect_class") == "read_only"
        and proof.get("state") == "failed"
        and proof.get("handler_entered") is True
        and proof.get("retry_safe") is True
    )


__all__ = [
    "_execution_effect_receipt",
    "_execution_receipt_trust",
    "_retry_safe_effect_receipt",
]
