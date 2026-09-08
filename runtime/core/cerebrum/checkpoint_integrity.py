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
    if not isinstance(state, dict):
        errors.append("checkpoint_state_not_object")
    # Checkpoints predating versioning are v1. An explicitly unknown or
    # malformed version must not be interpreted using today's recovery rules.
    version = raw.get("schema_version", 1)
    if type(version) is not int or version != 1:
        errors.append("unsupported_checkpoint_version")
    for field_name in ("progress_summary", "current_phase", "final_answer"):
        if field_name in raw and not isinstance(raw[field_name], str):
            errors.append(f"{field_name}_not_string")
    if "has_final_answer" in raw and type(raw["has_final_answer"]) is not bool:
        errors.append("has_final_answer_not_boolean")

    messages = raw.get("messages_snapshot")
    if messages is None:
        messages = []
        if "messages_snapshot" in raw:
            errors.append("messages_snapshot_not_list")
        else:
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
        if not isinstance(role, str) or role not in {"system", "user", "assistant"}:
            errors.append(f"message_{idx}_invalid_role")
        content = msg.get("content", "")
        if not isinstance(content, (str, list)) or (
            isinstance(content, list) and any(not isinstance(block, dict) for block in content)
        ):
            errors.append(f"message_{idx}_invalid_content")
        phase = msg.get("phase")
        if phase is not None and not isinstance(phase, str):
            errors.append(f"message_{idx}_invalid_phase")
        tool_calls = msg.get("tool_calls", [])
        if not isinstance(tool_calls, list) or any(
            not isinstance(call, dict)
            or not isinstance(call.get("id"), str)
            or not isinstance(call.get("name"), str)
            or not isinstance(call.get("input", {}), dict)
            for call in tool_calls
        ):
            errors.append(f"message_{idx}_invalid_tool_calls")
        if "content" not in msg and not msg.get("tool_calls"):
            warnings.append(f"message_{idx}_empty")
        valid_messages += 1

    steps = raw.get("steps_snapshot")
    if steps is None:
        steps = []
        if "steps_snapshot" in raw:
            errors.append("steps_snapshot_not_list")
        else:
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
        if type(raw_iter) is not int or raw_iter < 0:
            errors.append(f"step_{idx}_invalid_iteration")
            step_iteration = 0
        else:
            step_iteration = raw_iter
        for field_name in ("thought", "public_update", "action", "observation"):
            if field_name in step and not isinstance(step[field_name], str):
                errors.append(f"step_{idx}_{field_name}_not_string")
        for field_name, item_type in (("actions", str), ("action_results", dict)):
            values = step.get(field_name, [])
            if not isinstance(values, list) or any(
                not isinstance(item, item_type) for item in values
            ):
                errors.append(f"step_{idx}_{field_name}_invalid")
        max_step_iteration = max(max_step_iteration, step_iteration)
        if not any(step.get(key) for key in ("thought", "action", "observation")):
            warnings.append(f"step_{idx}_empty")
        valid_steps += 1

    working_set = raw.get("working_set_snapshot")
    if working_set is None:
        working_set = []
        if "working_set_snapshot" in raw:
            errors.append("working_set_snapshot_not_list")
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

    if type(iteration) is not int or iteration < 0:
        errors.append("invalid_checkpoint_iteration")
        checkpoint_iteration = 0
    else:
        checkpoint_iteration = iteration
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
    return validate_checkpoint_state(state, iteration=iteration or 0)
