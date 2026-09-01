from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CheckpointIntegrity:
    resume_safe: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    message_count: int = 0
    step_count: int = 0
    working_set_count: int = 0
    continue_from_iteration: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "resume_safe": self.resume_safe,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "message_count": self.message_count,
            "step_count": self.step_count,
            "working_set_count": self.working_set_count,
            "continue_from_iteration": self.continue_from_iteration,
        }


def validate_checkpoint_state(
    state: dict[str, Any] | None,
    *,
    iteration: int = 0,
) -> CheckpointIntegrity:
    raw = state if isinstance(state, dict) else {}
    errors: list[str] = []
    warnings: list[str] = []

    messages = raw.get("messages_snapshot")
    if messages is None:
        messages = []
        warnings.append("missing_messages_snapshot")
    if not isinstance(messages, list):
        messages = []
        errors.append("messages_snapshot_not_list")

    valid_messages = 0
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            errors.append(f"message_{idx}_not_object")
            continue
        role = msg.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            errors.append(f"message_{idx}_invalid_role")
        if "content" not in msg and not msg.get("tool_calls"):
            warnings.append(f"message_{idx}_empty")
        valid_messages += 1

    steps = raw.get("steps_snapshot")
    if steps is None:
        steps = []
        warnings.append("missing_steps_snapshot")
    if not isinstance(steps, list):
        steps = []
        errors.append("steps_snapshot_not_list")

    valid_steps = 0
    max_step_iteration = 0
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"step_{idx}_not_object")
            continue
        raw_iter = step.get("iteration", 0)
        try:
            step_iteration = int(raw_iter or 0)
        except (TypeError, ValueError):
            errors.append(f"step_{idx}_invalid_iteration")
            step_iteration = 0
        max_step_iteration = max(max_step_iteration, step_iteration)
        if not any(step.get(key) for key in ("thought", "action", "observation")):
            warnings.append(f"step_{idx}_empty")
        valid_steps += 1

    working_set = raw.get("working_set_snapshot")
    if working_set is None:
        working_set = []
    if not isinstance(working_set, list):
        working_set = []
        errors.append("working_set_snapshot_not_list")

    valid_working_set = 0
    for idx, item in enumerate(working_set):
        if isinstance(item, str) and item.strip():
            valid_working_set += 1
            continue
        if isinstance(item, dict) and str(item.get("path") or "").strip():
            valid_working_set += 1
            continue
        warnings.append(f"working_set_{idx}_missing_path")

    checkpoint_iteration = max(0, int(iteration or 0))
    if max_step_iteration > checkpoint_iteration > 0:
        errors.append("steps_ahead_of_checkpoint_iteration")
    if checkpoint_iteration > 0 and valid_steps == 0:
        warnings.append("iteration_without_steps")
    if valid_messages == 0:
        warnings.append("no_restorable_messages")

    return CheckpointIntegrity(
        resume_safe=not errors,
        errors=tuple(errors),
        warnings=tuple(dict.fromkeys(warnings)),
        message_count=valid_messages,
        step_count=valid_steps,
        working_set_count=valid_working_set,
        continue_from_iteration=max(checkpoint_iteration, max_step_iteration) + 1,
    )


def validate_trace_checkpoint(checkpoint: dict[str, Any]) -> CheckpointIntegrity:
    state = checkpoint.get("state") if isinstance(checkpoint, dict) else {}
    iteration = checkpoint.get("iteration") if isinstance(checkpoint, dict) else 0
    return validate_checkpoint_state(state, iteration=int(iteration or 0))
