"""Deterministic, bounded repair hints for production ReAct turns.

This evaluator never calls a model, network service, or tool. Version one is
deliberately narrow: it reacts only to server-owned receipts proving that a
tool operation did *not* execute (protocol rejection or argument validation).
Execution failures, timeouts, writes, commands, deletes, transactions, and
unknown outcomes remain with their existing recovery/approval paths.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

_SPACE_RE = re.compile(r"\s+")
_PROTOCOL_PREFIX = "[tool-call-protocol-error]"
_ARGUMENT_PREFIX = "(参数校验失败)"


def _clean(value: Any, *, limit: int = 2_000) -> str:
    text = _SPACE_RE.sub(" ", str(value or "").strip()).casefold()
    return text[:limit]


def _digest(category: str, action: str, observation: str) -> str:
    payload = f"{category}\n{action[:800]}\n{observation[:1_200]}".encode(
        "utf-8",
        errors="replace",
    )
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class StepEvaluation:
    """One advisory repair decision; it never authorizes tool execution."""

    score: float
    category: str
    hint: str
    dedupe_key: str


@dataclass
class RuntimeStepEvaluator:
    """Turn-local evaluator with bounded, digest-only deduplication."""

    max_hints: int = 3
    _seen: set[str] = field(default_factory=set)
    _hint_count: int = 0

    def __call__(self, step: dict[str, Any]) -> StepEvaluation | None:
        observation = _clean(step.get("observation"))
        action = _clean(step.get("action"))
        action_results = step.get("action_results")

        if not observation:
            return None
        category = ""
        score = 1.0
        hint = ""
        if observation.startswith(_PROTOCOL_PREFIX):
            # Protocol rejections are created before dispatch and therefore
            # must have the runtime's explicit empty receipt list.  Missing or
            # malformed receipt evidence fails closed.
            if not isinstance(action_results, list) or action_results:
                return None
            category = "protocol_error"
            score = 0.05
            hint = (
                "[evaluator:protocol_error] The previous tool-call envelope was "
                "rejected before any tool executed. Preserve the original goal and "
                "emit exactly one valid Action: tool_name({JSON}) with every required "
                "field. Do not narrate or repeat private tool markers."
            )
        elif observation.startswith(_ARGUMENT_PREFIX):
            # Text alone is not proof: a plugin could return a structured
            # "missing argument" error after already changing external state.
            # The executor stamps this server-owned provenance only when the
            # captured handler was never entered.
            if not isinstance(action_results, list) or not action_results:
                return None
            if not all(
                isinstance(result, dict)
                and result.get("ok") is False
                and result.get("execution_source") == "handler_not_executed"
                for result in action_results
            ):
                return None
            category = "invalid_arguments"
            score = 0.10
            hint = (
                "[evaluator:invalid_arguments] The runtime rejected the arguments "
                "before the tool operation executed. Re-read the declared schema and "
                "correct only the required argument names or values. Do not abandon "
                "the original task or claim completion."
            )
        else:
            return None

        dedupe_key = _digest(category, action, observation)
        if self._hint_count >= max(0, int(self.max_hints)) or dedupe_key in self._seen:
            return None
        self._seen.add(dedupe_key)
        self._hint_count += 1
        return StepEvaluation(
            score=score,
            category=category,
            hint=hint,
            dedupe_key=dedupe_key,
        )


def build_runtime_step_evaluator() -> RuntimeStepEvaluator:
    """Return fresh turn-local evaluator state for one ReAct invocation."""

    return RuntimeStepEvaluator()


__all__ = ["RuntimeStepEvaluator", "StepEvaluation", "build_runtime_step_evaluator"]
