from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from runtime.adapters.instrumentation import trace_stage
from runtime.platform.models import ArmId, ExecutionResult, SkillId, TaskId, ToolCall

HookPhase = Literal["pre", "post"]


@dataclass
class HookContext:
    phase: HookPhase
    task_id: TaskId
    arm_id: ArmId
    sucker_id: SkillId
    node_id: str
    step_id: int
    call: ToolCall
    result: ExecutionResult | None  # Implementation note.


@dataclass
class HookResult:
    replace_with: ExecutionResult | None = None  # Implementation note.
    metadata: dict[str, Any] = field(default_factory=dict)  # Implementation note.


class HookError(RuntimeError):
    def __init__(self, reason: str, *, block: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.block = block


Hook = Callable[[HookContext], HookResult | None]


@dataclass
class _RegisteredHook:
    name: str
    phase: HookPhase
    fn: Hook


class HookManager:
    def __init__(self) -> None:
        self._hooks: list[_RegisteredHook] = []
        self._lock = threading.RLock()

    def add_pre(self, name: str, fn: Hook) -> None:
        self._add("pre", name, fn)

    def add_post(self, name: str, fn: Hook) -> None:
        self._add("post", name, fn)

    def _add(self, phase: HookPhase, name: str, fn: Hook) -> None:
        with self._lock:
            if any(h.name == name and h.phase == phase for h in self._hooks):
                raise ValueError(f"duplicate {phase} hook name: {name!r}")
            self._hooks.append(_RegisteredHook(name=name, phase=phase, fn=fn))

    def remove(self, name: str, phase: HookPhase | None = None) -> bool:
        with self._lock:
            before = len(self._hooks)
            self._hooks = [
                h
                for h in self._hooks
                if not (h.name == name and (phase is None or h.phase == phase))
            ]
            return len(self._hooks) < before

    def names(self, phase: HookPhase | None = None) -> list[str]:
        with self._lock:
            return [h.name for h in self._hooks if phase is None or h.phase == phase]

    def run_pre(self, ctx: HookContext) -> HookResult | None:
        return self._run(ctx, "pre")

    def run_post(self, ctx: HookContext) -> HookResult | None:
        with self._lock:
            postk = [h for h in self._hooks if h.phase == "post"]
        final: HookResult | None = None
        current_result = ctx.result
        for hk in postk:
            new_ctx = HookContext(
                phase="post",
                task_id=ctx.task_id,
                arm_id=ctx.arm_id,
                sucker_id=ctx.sucker_id,
                node_id=ctx.node_id,
                step_id=ctx.step_id,
                call=ctx.call,
                result=current_result,
            )
            with trace_stage("nerves.hook") as span:
                span.set_attribute("echo.hook.name", hk.name)
                span.set_attribute("echo.hook.phase", "post")
                try:
                    out = hk.fn(new_ctx)
                except HookError:
                    out = None
                except Exception as e:  # noqa: BLE001
                    span.set_attribute("echo.hook.error", f"{type(e).__name__}: {e}")
                    out = None
            if out is not None and out.replace_with is not None:
                current_result = out.replace_with
                final = out
        return final

    def _run(self, ctx: HookContext, phase: HookPhase) -> HookResult | None:
        with self._lock:
            hooks = [h for h in self._hooks if h.phase == phase]
        for hk in hooks:
            with trace_stage("nerves.hook") as span:
                span.set_attribute("echo.hook.name", hk.name)
                span.set_attribute("echo.hook.phase", phase)
                try:
                    out = hk.fn(ctx)
                except HookError:
                    raise  # Implementation note.
                except Exception as e:  # noqa: BLE001
                    span.set_attribute("echo.hook.error", f"{type(e).__name__}: {e}")
                    continue
            if out is not None and out.replace_with is not None:
                return out
        return None
